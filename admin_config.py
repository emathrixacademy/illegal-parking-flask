from db import get_config_value, set_config_value, get_connection
from typing import List, Dict, Any

DEFAULT_FINE_MAP = {
    'CAR': 100,
    'MOTORCYCLE': 50
}

_CONFIG_KEY = 'FINE_MAP'


def get_fine_map():
    """Return the fine map from DB config, falling back to DEFAULT_FINE_MAP."""
    val = get_config_value(_CONFIG_KEY, None)
    if isinstance(val, dict):
        # normalize keys to uppercase
        return {k.upper(): float(v) for k, v in val.items()}
    return DEFAULT_FINE_MAP.copy()


def set_fine_map(mapping):
    """Save the fine map (dict) to DB config."""
    if not isinstance(mapping, dict):
        raise ValueError('mapping must be a dict')
    # ensure numeric values
    cleaned = {k.upper(): float(v) for k, v in mapping.items()}
    set_config_value(_CONFIG_KEY, cleaned)
    return cleaned


def list_violations(page: int = 1, per_page: int = 30, filters: dict = None) -> Dict[str, Any]:
    """Return paginated violations (latest row per camera+tracker_id).

    Returns dict with keys: items, page, per_page, total, total_pages.
    Sorted by timestamp descending (newest first).

    Supported filters:
      - label: vehicle type e.g. 'CAR', 'MOTORCYCLE'
      - hour_start/hour_end: hour range (0-23)
      - day_of_week: day name e.g. 'Friday'
      - today: if '1', only today's violations
      - camera: camera id e.g. 'Camera_1'
      - duration_sort: if '1', order by duration_minutes DESC
    """
    filters = filters or {}
    conn = get_connection()
    try:
        cur = conn.cursor()

        where_clauses = []
        params = []

        if filters.get('label'):
            where_clauses.append("UPPER(label) = UPPER(%s)")
            params.append(filters['label'])

        if filters.get('hour_start') is not None and filters.get('hour_end') is not None:
            where_clauses.append(
                "EXTRACT(HOUR FROM timestamp AT TIME ZONE 'Asia/Manila') >= %s "
                "AND EXTRACT(HOUR FROM timestamp AT TIME ZONE 'Asia/Manila') < %s"
            )
            params.append(int(filters['hour_start']))
            params.append(int(filters['hour_end']))

        if filters.get('day_of_week'):
            where_clauses.append(
                "TRIM(TO_CHAR(timestamp AT TIME ZONE 'Asia/Manila', 'Day')) = %s"
            )
            params.append(filters['day_of_week'])

        if filters.get('today') == '1':
            where_clauses.append(
                "(timestamp AT TIME ZONE 'Asia/Manila')::date = "
                "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Manila')::date"
            )

        if filters.get('camera'):
            where_clauses.append("camera = %s")
            params.append(filters['camera'])

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        order_col = "duration_minutes DESC NULLS LAST" if filters.get('duration_sort') == '1' else "timestamp DESC"

        cur.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT DISTINCT ON (camera, tracker_id) id
                FROM violations
                {where_sql}
                ORDER BY camera, tracker_id, timestamp DESC
            ) sub
        """, params)
        total = cur.fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page

        cur.execute(f"""
            SELECT * FROM (
                SELECT DISTINCT ON (camera, tracker_id)
                    id, camera, tracker_id, label, timestamp, image_path,
                    confidence_score, duration_minutes, fine_amount, barangay, enforced,
                    COALESCE(review_status, 'for_review') as review_status,
                    COALESCE(review_notes, '') as review_notes
                FROM violations
                {where_sql}
                ORDER BY camera, tracker_id, timestamp DESC
            ) sub
            ORDER BY {order_col}
            LIMIT %s OFFSET %s
        """, params + [per_page, offset])
        rows = cur.fetchall()
        cols = ['id', 'camera', 'tracker_id', 'label', 'timestamp', 'image_path',
                'confidence_score', 'duration_minutes', 'fine_amount', 'barangay', 'enforced',
                'review_status', 'review_notes']
        items = [dict(zip(cols, r)) for r in rows]
        cur.close()
        return {
            'items': items,
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': total_pages
        }
    finally:
        conn.close()


def mark_enforced(ids: List[int]) -> int:
    """Mark given violation ids as enforced=True. Returns number updated."""
    if isinstance(ids, int):
        ids = [ids]
    if not isinstance(ids, (list, tuple)) or not ids:
        raise ValueError('ids must be a non-empty list')
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE violations SET enforced = TRUE WHERE id = ANY(%s)", (ids,))
        updated = cur.rowcount if cur.rowcount is not None else len(ids)
        conn.commit()
        cur.close()
        return updated
    finally:
        conn.close()
