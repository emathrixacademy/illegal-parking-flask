from db import get_config_value, set_config_value, get_connection
from typing import List, Dict, Any

DEFAULT_FINE_MAP = {
    'CAR': 100,
    'MOTORCYCLE': 50,
    'TRUCK': 200,
    'BUS': 250
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


def list_violations() -> List[Dict[str, Any]]:
    """Return all rows from local `violations` table as list of dicts."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, camera, tracker_id, label, timestamp, image_path, confidence_score, duration_minutes, fine_amount, barangay, enforced
            FROM violations
            ORDER BY timestamp DESC
        """)
        rows = cur.fetchall()
        cols = ['id','camera','tracker_id','label','timestamp','image_path','confidence_score','duration_minutes','fine_amount','barangay','enforced']
        result = [dict(zip(cols, r)) for r in rows]
        cur.close()
        return result
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
