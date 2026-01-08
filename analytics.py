import os
import logging
from flask import Blueprint, jsonify
import psycopg2

analytics_bp = Blueprint('analytics', __name__)

POSTGRES_URL = os.environ.get(
    "POSTGRES_URL",
    "postgresql://postgres:ltymHUMvXphOojaHeJRJGnyQUfWsghwq@mainline.proxy.rlwy.net:42362/railway"
)

logger = logging.getLogger("Analytics")


@analytics_bp.route('/api/violation_counts', methods=['GET'])
def api_violation_counts():
    """
    Return counts of violations grouped by label from the local PostgreSQL `violations` table.
    """
    try:
        CLASS_MAP = {
            2: "CAR", 3: "MOTORCYCLE", 5: "BUS", 7: "TRUCK",
            "2": "CAR", "3": "MOTORCYCLE", "5": "BUS", "7": "TRUCK",
            "car": "CAR", "motorcycle": "MOTORCYCLE", "bus": "BUS", "truck": "TRUCK",
            "CAR": "CAR", "MOTORCYCLE": "MOTORCYCLE", "BUS": "BUS", "TRUCK": "TRUCK"
        }
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT label,
                   COUNT(DISTINCT (camera, tracker_id)) AS distinct_ids,
                   SUM(CASE WHEN tracker_id IS NULL THEN 1 ELSE 0 END) AS null_count
            FROM violations
            GROUP BY label;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        counts = {"CAR": 0, "MOTORCYCLE": 0, "TRUCK": 0, "BUS": 0}
        for label, distinct_ids, null_count in rows:
            mapped = CLASS_MAP.get(label)
            if mapped is None:
                mapped = CLASS_MAP.get(str(label).lower())
            if mapped in counts:
                d = int(distinct_ids) if distinct_ids is not None else 0
                n = int(null_count) if null_count is not None else 0
                counts[mapped] += (d + n)

        try:
            conn2 = psycopg2.connect(POSTGRES_URL)
            cur2 = conn2.cursor()
            cur2.execute("SELECT COUNT(DISTINCT (camera, tracker_id)) FROM violations WHERE tracker_id IS NOT NULL;")
            uniq_row = cur2.fetchone()
            cur2.close()
            conn2.close()
            total_unique = int(uniq_row[0]) if uniq_row and uniq_row[0] is not None else 0
        except Exception:
            total_unique = 0

        counts["TOTAL_UNIQUE_TRACKERS"] = total_unique

        try:
            conn3 = psycopg2.connect(POSTGRES_URL)
            cur3 = conn3.cursor()
            cur3.execute("""
                SELECT COUNT(DISTINCT (camera, tracker_id)) AS unique_today,
                       SUM(CASE WHEN tracker_id IS NULL THEN 1 ELSE 0 END) AS null_today
                FROM violations
                WHERE (timestamp::date) = CURRENT_DATE;
            """)
            today_row = cur3.fetchone()
            cur3.close()
            conn3.close()
            unique_today = int(today_row[0]) if today_row and today_row[0] is not None else 0
            null_today = int(today_row[1]) if today_row and today_row[1] is not None else 0
            recent_total = unique_today + null_today
        except Exception:
            recent_total = 0

        counts["RECENT_VIOLATORS_TODAY"] = recent_total
        return jsonify(counts)
    except Exception as e:
        logger.error(f"Failed to query violation counts: {e}")
        return jsonify({"CAR":0, "MOTORCYCLE":0, "TRUCK":0, "BUS":0})


@analytics_bp.route('/api/violation_stats', methods=['GET'])
def api_violation_stats():
    """
    Return basic violation statistics from the local PostgreSQL `violations` table.
    """
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(DISTINCT (camera, tracker_id)) AS total_violations,
                COUNT(DISTINCT camera) AS active_cameras,
                COUNT(DISTINCT barangay) AS affected_barangays,
                AVG(confidence_score) AS avg_confidence,
                AVG(duration_minutes) AS avg_duration_minutes,
                SUM(CASE WHEN enforced = TRUE THEN 1 ELSE 0 END) AS enforced_count,
                SUM(CASE WHEN enforced = TRUE THEN COALESCE(fine_amount, 0.0) ELSE 0.0 END) AS total_fines_collected
            FROM violations;
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return jsonify({
                "total_violations": 0,
                "active_cameras": 0,
                "affected_barangays": 0,
                "avg_confidence": 0.0,
                "avg_duration_minutes": 0.0,
                "enforced_count": 0,
                "total_fines_collected": 0.0
            })

        total_violations, active_cameras, affected_barangays, avg_confidence, avg_duration_minutes, enforced_count, total_fines_collected = row

        return jsonify({
            "total_violations": int(total_violations or 0),
            "active_cameras": int(active_cameras or 0),
            "affected_barangays": int(affected_barangays or 0),
            "avg_confidence": float(avg_confidence or 0.0),
            "avg_duration_minutes": float(avg_duration_minutes or 0.0),
            "enforced_count": int(enforced_count or 0),
            "total_fines_collected": float(total_fines_collected or 0.0)
        })
    except Exception as e:
        logger.error(f"Failed to query violation stats: {e}")
        return jsonify({
            "total_violations": 0,
            "active_cameras": 0,
            "affected_barangays": 0,
            "avg_confidence": 0.0,
            "avg_duration_minutes": 0.0,
            "enforced_count": 0,
            "total_fines_collected": 0.0
        })
