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


def list_violations(page: int = 1, per_page: int = 30) -> Dict[str, Any]:
    """Return paginated violations (latest row per camera+tracker_id).

    Returns dict with keys: items, page, per_page, total, total_pages.
    Sorted by timestamp descending (newest first).
    """
    conn = get_connection()
    try:
        cur = conn.cursor()

        # Count total distinct violations
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT DISTINCT ON (camera, tracker_id) id
                FROM violations
                ORDER BY camera, tracker_id, timestamp DESC
            ) sub
        """)
        total = cur.fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page

        # Fetch paginated results, newest first
        cur.execute("""
            SELECT * FROM (
                SELECT DISTINCT ON (camera, tracker_id)
                    id, camera, tracker_id, label, timestamp, image_path,
                    confidence_score, duration_minutes, fine_amount, barangay, enforced,
                    COALESCE(review_status, 'for_review') as review_status,
                    COALESCE(review_notes, '') as review_notes
                FROM violations
                ORDER BY camera, tracker_id, timestamp DESC
            ) sub
            ORDER BY timestamp DESC
            LIMIT %s OFFSET %s
        """, (per_page, offset))
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
