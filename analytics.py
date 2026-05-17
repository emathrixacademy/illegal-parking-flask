import os
import logging
from flask import Blueprint, jsonify, request, make_response
import psycopg2
from datetime import datetime, timedelta
import io
from auth import login_required
from db import get_connection, return_connection

analytics_bp = Blueprint('analytics', __name__)

POSTGRES_URL = os.environ.get("DATABASE_URL") or "postgresql://postgres:osBsKkhgPnjxUCtzUDZFSLAVTEvuqCNH@postgres.railway.internal:5432/railway"

logger = logging.getLogger("Analytics")


@analytics_bp.route('/api/violation_counts', methods=['GET'])
@login_required
def api_violation_counts():
    """
    Return counts of violations grouped by label from the local PostgreSQL `violations` table.
    """
    try:
        CLASS_MAP = {
            2: "CAR", 3: "MOTORCYCLE",
            "2": "CAR", "3": "MOTORCYCLE",
            "car": "CAR", "motorcycle": "MOTORCYCLE",
            "CAR": "CAR", "MOTORCYCLE": "MOTORCYCLE"
        }
        CCTV_AI_LABELS = [
            "BIKE", "CHAIR", "JEEP", "ROCK", "TRASH",
            "TREE", "TRICYCLE", "VENDOR",
        ]
        for lbl in CCTV_AI_LABELS:
            CLASS_MAP[lbl] = lbl
            CLASS_MAP[lbl.lower()] = lbl
        conn = get_connection()
        try:
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
        finally:
            return_connection(conn)

        counts = {"CAR": 0, "MOTORCYCLE": 0}
        for lbl in CCTV_AI_LABELS:
            counts[lbl] = 0
        for label, distinct_ids, null_count in rows:
            mapped = CLASS_MAP.get(label)
            if mapped is None:
                mapped = CLASS_MAP.get(str(label).lower())
            if mapped in counts:
                d = int(distinct_ids) if distinct_ids is not None else 0
                n = int(null_count) if null_count is not None else 0
                counts[mapped] += (d + n)

        try:
            conn2 = get_connection()
            try:
                cur2 = conn2.cursor()
                cur2.execute("SELECT COUNT(DISTINCT (camera, tracker_id)) FROM violations WHERE tracker_id IS NOT NULL;")
                uniq_row = cur2.fetchone()
                cur2.close()
                total_unique = int(uniq_row[0]) if uniq_row and uniq_row[0] is not None else 0
            finally:
                return_connection(conn2)
        except Exception:
            total_unique = 0

        counts["TOTAL_UNIQUE_TRACKERS"] = total_unique

        try:
            conn3 = get_connection()
            try:
                cur3 = conn3.cursor()
                cur3.execute("""
                    SELECT COUNT(DISTINCT (camera, tracker_id)) AS unique_today,
                           SUM(CASE WHEN tracker_id IS NULL THEN 1 ELSE 0 END) AS null_today
                    FROM violations
                    WHERE (timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Manila')::date;
                """)
                today_row = cur3.fetchone()
                cur3.close()
                unique_today = int(today_row[0]) if today_row and today_row[0] is not None else 0
                null_today = int(today_row[1]) if today_row and today_row[1] is not None else 0
                recent_total = unique_today + null_today
            finally:
                return_connection(conn3)
        except Exception:
            recent_total = 0

        counts["RECENT_VIOLATORS_TODAY"] = recent_total
        return jsonify(counts)
    except Exception as e:
        logger.error(f"Failed to query violation counts: {e}")
        return jsonify({"CAR":0, "MOTORCYCLE":0})


@analytics_bp.route('/api/violation_stats', methods=['GET'])
@login_required
def api_violation_stats():
    """
    Return basic violation statistics from the local PostgreSQL `violations` table.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(DISTINCT (camera, tracker_id)) AS total_violations,
                COUNT(DISTINCT camera) AS active_cameras,
                COUNT(DISTINCT barangay) AS affected_barangays,
                AVG(confidence_score) AS avg_confidence,
                AVG(duration_minutes) AS avg_duration_minutes,
                SUM(CASE WHEN enforced = TRUE THEN 1 ELSE 0 END) AS enforced_count
            FROM violations;
        """)
        row = cur.fetchone()
        cur.close()

        if not row:
            return jsonify({
                "total_violations": 0,
                "active_cameras": 0,
                "affected_barangays": 0,
                "avg_confidence": 0.0,
                "avg_duration_minutes": 0.0,
                "enforced_count": 0
            })

        total_violations, active_cameras, affected_barangays, avg_confidence, avg_duration_minutes, enforced_count = row

        return jsonify({
            "total_violations": int(total_violations or 0),
            "active_cameras": int(active_cameras or 0),
            "affected_barangays": int(affected_barangays or 0),
            "avg_confidence": float(avg_confidence or 0.0),
            "avg_duration_minutes": float(avg_duration_minutes or 0.0),
            "enforced_count": int(enforced_count or 0)
        })
    except Exception as e:
        logger.error(f"Failed to query violation stats: {e}")
        return jsonify({
            "total_violations": 0,
            "active_cameras": 0,
            "affected_barangays": 0,
            "avg_confidence": 0.0,
            "avg_duration_minutes": 0.0,
            "enforced_count": 0
        })
    finally:
        return_connection(conn)


# ==================================================
# Intelligent Analytics Endpoints
# ==================================================

@analytics_bp.route('/api/analytics/heatmap')
@login_required
def api_analytics_heatmap():
    """Returns a 7x24 grid of violation counts (day-of-week x hour)."""
    conn = None
    try:
        days_back = int(request.args.get('days', 7))
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                EXTRACT(DOW FROM timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila') AS dow,
                EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila') AS hour,
                COUNT(DISTINCT (camera, tracker_id)) AS violation_count
            FROM violations
            WHERE timestamp >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Manila' - make_interval(days => %s))
            GROUP BY dow, hour
            ORDER BY dow, hour;
        """, (days_back,))
        rows = cur.fetchall()
        cur.close()

        day_remap = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
        day_labels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        data = []
        max_count = 0
        for dow, hour, count in rows:
            c = int(count)
            if c > max_count:
                max_count = c
            ri = day_remap[int(dow)]
            data.append({"day": day_labels[ri], "day_index": ri, "hour": int(hour), "count": c})

        return jsonify({"data": data, "max_count": max_count, "period": f"last_{days_back}_days"})
    except Exception as e:
        logger.error(f"Heatmap query failed: {e}")
        return jsonify({"data": [], "max_count": 0, "error": str(e)}), 500
    finally:
        if conn is not None:
            return_connection(conn)


@analytics_bp.route('/api/analytics/daily_trend')
@login_required
def api_analytics_daily_trend():
    """Returns daily violation counts for trend chart."""
    conn = None
    try:
        days_back = int(request.args.get('days', 30))
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                (timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila')::date AS violation_date,
                COUNT(DISTINCT (camera, tracker_id)) AS daily_count
            FROM violations
            WHERE timestamp >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Manila' - make_interval(days => %s))
            GROUP BY violation_date
            ORDER BY violation_date;
        """, (days_back,))
        rows = cur.fetchall()
        cur.close()

        labels = [r[0].strftime('%b %d') for r in rows]
        dates = [r[0].isoformat() for r in rows]
        counts = [int(r[1]) for r in rows]
        return jsonify({"labels": labels, "dates": dates, "counts": counts, "period_days": days_back})
    except Exception as e:
        logger.error(f"Daily trend query failed: {e}")
        return jsonify({"labels": [], "counts": [], "error": str(e)}), 500
    finally:
        if conn is not None:
            return_connection(conn)


@analytics_bp.route('/api/analytics/hourly')
@login_required
def api_analytics_hourly():
    """Returns violation counts per hour of day (0-23)."""
    conn = None
    try:
        days_back = int(request.args.get('days', 30))
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila') AS hour,
                COUNT(DISTINCT (camera, tracker_id)) AS hourly_count
            FROM violations
            WHERE timestamp >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Manila' - make_interval(days => %s))
            GROUP BY hour
            ORDER BY hour;
        """, (days_back,))
        rows = cur.fetchall()
        cur.close()

        hourly = {i: 0 for i in range(24)}
        for hour, count in rows:
            hourly[int(hour)] = int(count)
        return jsonify({"hours": list(range(24)), "counts": [hourly[h] for h in range(24)]})
    except Exception as e:
        logger.error(f"Hourly query failed: {e}")
        return jsonify({"hours": [], "counts": [], "error": str(e)}), 500
    finally:
        if conn is not None:
            return_connection(conn)


@analytics_bp.route('/api/analytics/camera_comparison')
@login_required
def api_analytics_camera_comparison():
    """Returns per-camera, per-vehicle-type violation counts."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT camera, UPPER(label) AS vehicle_type,
                   COUNT(DISTINCT (camera, tracker_id)) AS count
            FROM violations
            GROUP BY camera, UPPER(label)
            ORDER BY camera, vehicle_type;
        """)
        rows = cur.fetchall()
        cur.close()

        cameras = []
        data = {}
        for cam, vtype, count in rows:
            if cam not in data:
                cameras.append(cam)
                data[cam] = {}
            data[cam][vtype] = int(count)
        return jsonify({"cameras": cameras, "data": data})
    except Exception as e:
        logger.error(f"Camera comparison query failed: {e}")
        return jsonify({"cameras": [], "data": {}, "error": str(e)}), 500
    finally:
        return_connection(conn)


@analytics_bp.route('/api/analytics/duration_distribution')
@login_required
def api_analytics_duration_distribution():
    """Returns violation counts in duration buckets."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT bucket, cnt FROM (
                SELECT
                    CASE
                        WHEN duration_minutes < 5 THEN '0-5 min'
                        WHEN duration_minutes < 10 THEN '5-10'
                        WHEN duration_minutes < 15 THEN '10-15'
                        WHEN duration_minutes < 20 THEN '15-20'
                        WHEN duration_minutes < 30 THEN '20-30'
                        WHEN duration_minutes < 60 THEN '30-60'
                        ELSE '60+'
                    END AS bucket,
                    CASE
                        WHEN duration_minutes < 5 THEN 1
                        WHEN duration_minutes < 10 THEN 2
                        WHEN duration_minutes < 15 THEN 3
                        WHEN duration_minutes < 20 THEN 4
                        WHEN duration_minutes < 30 THEN 5
                        WHEN duration_minutes < 60 THEN 6
                        ELSE 7
                    END AS sort_order,
                    COUNT(DISTINCT (camera, tracker_id)) AS cnt
                FROM violations
                GROUP BY bucket, sort_order
            ) sub ORDER BY sort_order;
        """)
        rows = cur.fetchall()
        cur.close()

        buckets = [r[0] for r in rows]
        counts = [int(r[1]) for r in rows]
        return jsonify({"buckets": buckets, "counts": counts})
    except Exception as e:
        logger.error(f"Duration distribution query failed: {e}")
        return jsonify({"buckets": [], "counts": [], "error": str(e)}), 500
    finally:
        return_connection(conn)


@analytics_bp.route('/api/analytics/insights')
@login_required
def api_analytics_insights():
    """Analyzes violation data and returns AI-generated insights."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        insights = []

        # Overnight vs Daytime pattern
        cur.execute("""
            SELECT
                CASE
                    WHEN EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila') >= 22
                      OR EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila') <= 5
                    THEN 'night' ELSE 'day'
                END AS period,
                COUNT(DISTINCT (camera, tracker_id)) AS count
            FROM violations GROUP BY period;
        """)
        period_counts = {r[0]: int(r[1]) for r in cur.fetchall()}
        night = period_counts.get('night', 0)
        day = period_counts.get('day', 0)
        total = night + day
        if total > 0:
            night_pct = round(night * 100 / total)
            if night_pct > 50:
                insights.append({"type": "info", "title": "Overnight pattern detected",
                    "description": f"{night_pct}% of violations happen between 10 PM and 6 AM. Residents may be using the road as overnight parking."})
            elif night_pct < 30:
                insights.append({"type": "info", "title": "Daytime violation pattern",
                    "description": f"{100 - night_pct}% of violations occur during daytime hours (6 AM - 10 PM). Commercial activity or delivery vehicles may be the primary cause."})

        # Camera imbalance
        cur.execute("""
            SELECT camera, COUNT(DISTINCT (camera, tracker_id)) AS count
            FROM violations GROUP BY camera ORDER BY count DESC;
        """)
        cam_rows = cur.fetchall()
        if len(cam_rows) >= 2 and cam_rows[1][1] > 0:
            ratio = round(cam_rows[0][1] / cam_rows[1][1], 1)
            if ratio > 2.0:
                insights.append({"type": "warning", "title": "Repeat offender zone",
                    "description": f"{cam_rows[0][0].replace('_', ' ')} captures {ratio}x more violations than {cam_rows[1][0].replace('_', ' ')}. This zone is the primary hotspot."})

        # Deterrent effect (30-day comparison)
        cur.execute("""
            SELECT
                CASE
                    WHEN timestamp >= (CURRENT_TIMESTAMP - INTERVAL '30 days') THEN 'recent'
                    WHEN timestamp >= (CURRENT_TIMESTAMP - INTERVAL '60 days') THEN 'previous'
                END AS period,
                AVG(duration_minutes) AS avg_dur,
                COUNT(DISTINCT (camera, tracker_id)) AS count
            FROM violations
            WHERE timestamp >= (CURRENT_TIMESTAMP - INTERVAL '60 days')
            GROUP BY period;
        """)
        trend_data = {r[0]: {"avg_dur": float(r[1] or 0), "count": int(r[2])} for r in cur.fetchall() if r[0]}
        if 'recent' in trend_data and 'previous' in trend_data:
            recent_dur = round(trend_data['recent']['avg_dur'], 1)
            prev_dur = round(trend_data['previous']['avg_dur'], 1)
            recent_count = trend_data['recent']['count']
            prev_count = trend_data['previous']['count']
            if recent_dur < prev_dur and prev_dur > 0:
                drop_pct = round((prev_dur - recent_dur) / prev_dur * 100)
                insights.append({"type": "success", "title": "Deterrent effect observed",
                    "description": f"Average violation duration dropped from {prev_dur} min to {recent_dur} min ({drop_pct}% decrease). Awareness of the system may be increasing."})
            elif prev_count > 0 and recent_count > prev_count * 1.3:
                increase_pct = round((recent_count - prev_count) / prev_count * 100)
                insights.append({"type": "warning", "title": "Violations increasing",
                    "description": f"Violations increased by {increase_pct}% compared to the previous period ({prev_count} → {recent_count}). Enforcement actions may need to be strengthened."})
            elif prev_count > 0 and recent_count < prev_count * 0.8:
                decrease_pct = round((prev_count - recent_count) / prev_count * 100)
                insights.append({"type": "success", "title": "Violations decreasing",
                    "description": f"Violations decreased by {decrease_pct}% ({prev_count} → {recent_count}). The system appears to be working as a deterrent."})

        # Peak hour identification
        cur.execute("""
            SELECT EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila') AS hour,
                   COUNT(DISTINCT (camera, tracker_id)) AS count
            FROM violations WHERE timestamp >= (CURRENT_TIMESTAMP - INTERVAL '30 days')
            GROUP BY hour ORDER BY count DESC LIMIT 1;
        """)
        peak_row = cur.fetchone()
        if peak_row:
            ph = int(peak_row[0])
            pc = int(peak_row[1])
            td = "late night/early morning" if (ph >= 22 or ph <= 5) else "morning" if ph <= 11 else "afternoon" if ph <= 17 else "evening"
            insights.append({"type": "info", "title": f"Peak violation hour: {ph}:00-{ph+1}:00",
                "description": f"Highest concentration of violations occurs during {td} hours ({pc} violations in the last 30 days). Enforcers should prioritize monitoring during this window."})

        # Worst day of week
        cur.execute("""
            SELECT TO_CHAR(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila', 'Day') AS day_name,
                   COUNT(DISTINCT (camera, tracker_id)) AS count
            FROM violations WHERE timestamp >= (CURRENT_TIMESTAMP - INTERVAL '30 days')
            GROUP BY day_name ORDER BY count DESC LIMIT 1;
        """)
        worst_row = cur.fetchone()
        if worst_row:
            insights.append({"type": "warning", "title": f"Busiest day: {worst_row[0].strip()}",
                "description": f"{worst_row[0].strip()} has the most violations ({int(worst_row[1])} in the last 30 days). Consider scheduling enforcement patrols on {worst_row[0].strip()}s."})

        cur.close()
        return jsonify({"insights": insights[:3]})
    except Exception as e:
        logger.error(f"Insights generation failed: {e}")
        return jsonify({"insights": [], "error": str(e)}), 500
    finally:
        return_connection(conn)


@analytics_bp.route('/api/analytics/summary')
@login_required
def api_analytics_summary():
    """Returns enhanced summary metrics for top metric cards."""
    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(DISTINCT (camera, tracker_id)) FROM violations;")
        total = int(cur.fetchone()[0] or 0)

        cur.execute("""
            SELECT
                COUNT(DISTINCT (camera, tracker_id)) FILTER (
                    WHERE timestamp >= CURRENT_TIMESTAMP - INTERVAL '30 days'
                ) AS recent,
                COUNT(DISTINCT (camera, tracker_id)) FILTER (
                    WHERE timestamp >= CURRENT_TIMESTAMP - INTERVAL '60 days'
                    AND timestamp < CURRENT_TIMESTAMP - INTERVAL '30 days'
                ) AS previous
            FROM violations;
        """)
        row = cur.fetchone()
        recent_30 = int(row[0] or 0)
        prev_30 = int(row[1] or 0)
        change = recent_30 - prev_30

        cur.execute("SELECT AVG(duration_minutes) FROM violations;")
        avg_dur = round(float(cur.fetchone()[0] or 0), 1)

        cur.execute("""
            SELECT EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila') AS h, COUNT(*) AS c
            FROM violations WHERE timestamp >= CURRENT_TIMESTAMP - INTERVAL '30 days'
            GROUP BY h ORDER BY c DESC LIMIT 1;
        """)
        peak = cur.fetchone()
        peak_h = int(peak[0]) if peak else 0
        ampm = 'AM' if peak_h < 12 else 'PM'
        disp_h = peak_h if peak_h <= 12 else peak_h - 12
        if disp_h == 0:
            disp_h = 12
        peak_label = f"{disp_h}-{disp_h + 1 if disp_h < 12 else 1} {ampm}"
        peak_note = "Overnight parking" if (peak_h >= 22 or peak_h <= 5) else "Active hours"

        cur.execute("""
            SELECT TO_CHAR(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila', 'Day') AS d,
                   COUNT(DISTINCT (camera, tracker_id)) AS c
            FROM violations WHERE timestamp >= CURRENT_TIMESTAMP - INTERVAL '30 days'
            GROUP BY d ORDER BY c DESC LIMIT 1;
        """)
        worst = cur.fetchone()
        worst_day = worst[0].strip() if worst else "N/A"
        worst_day_avg = round(int(worst[1] or 0) / 4.3) if worst else 0

        cur.close()

        return jsonify({
            "total_violations": total, "change_vs_last_month": change,
            "avg_duration": avg_dur, "peak_hour": peak_label,
            "peak_hour_note": peak_note, "worst_day": worst_day,
            "worst_day_avg": worst_day_avg
        })
    except Exception as e:
        logger.error(f"Summary query failed: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        return_connection(conn)


@analytics_bp.route('/api/generate_report', methods=['GET'])
@login_required
def api_generate_report():
    """
    Generate a professional PDF report for violations with enhanced filtering.
    Query params:
      - period: 'day'|'week'|'month'|'custom'  (defaults to 'day')
      - date: optional ISO date YYYY-MM-DD (defaults to today) - for day/week/month
      - start_date: start date for custom range
      - end_date: end date for custom range
      - vehicle_type: 'all'|'CAR'|'MOTORCYCLE' (defaults to 'all')
      - camera: 'all'|'Camera_1'|'Camera_2' (defaults to 'all')
      - review_status: 'all'|'approved'|'rejected'|'for_review'|'needs_investigation'
    """
    period = (request.args.get('period') or 'day').lower()
    date_str = request.args.get('date')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    vehicle_type = request.args.get('vehicle_type', 'all')
    camera_filter = request.args.get('camera', 'all')
    review_status = request.args.get('review_status', 'all')
    
    # Parse dates
    try:
        if period == 'custom':
            if not start_date_str or not end_date_str:
                return jsonify({"error": "start_date and end_date required for custom range"}), 400
            start_date = datetime.fromisoformat(start_date_str).date()
            end_date = datetime.fromisoformat(end_date_str).date()
            date_obj = start_date  # For filename
        else:
            if date_str:
                date_obj = datetime.fromisoformat(date_str).date()
            else:
                date_obj = datetime.now().date()
            start_date = date_obj
            if period == 'week':
                end_date = start_date + timedelta(days=6)
            elif period == 'month':
                next_month = (start_date.replace(day=1) + timedelta(days=32)).replace(day=1)
                end_date = next_month - timedelta(days=1)
            else:
                end_date = date_obj
    except Exception as e:
        return jsonify({"error": f"invalid date format: {str(e)}"}), 400

    # Build WHERE clause based on period
    where_clauses = []
    params = []

    if period == 'day':
        where_clauses.append("(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila')::date = %s")
        params.append(start_date)
    elif period in ('week', 'month', 'custom'):
        where_clauses.append("(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila')::date BETWEEN %s AND %s")
        params.extend([start_date, end_date])
    else:
        return jsonify({"error": "invalid period, use day|week|month|custom"}), 400

    # Add vehicle type filter
    if vehicle_type != 'all':
        where_clauses.append("label = %s")
        params.append(vehicle_type)

    # Add camera filter
    if camera_filter != 'all':
        where_clauses.append("camera = %s")
        params.append(camera_filter)

    # Add review status filter
    if review_status != 'all':
        where_clauses.append("COALESCE(review_status, 'for_review') = %s")
        params.append(review_status)

    where_sql = " AND ".join(where_clauses)

    logging.info(f"PDF Report: period={period}, start_date={start_date}, end_date={end_date}, where_sql={where_sql}, params={params}")

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Debug: check what dates exist in the range
        cur.execute("""
            SELECT (timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila')::date as d, COUNT(*)
            FROM violations
            GROUP BY d ORDER BY d DESC LIMIT 10
        """)
        recent_dates = cur.fetchall()
        logging.info(f"PDF Report: recent violation dates in DB: {recent_dates}")

        # Per-label counts
        cur.execute(f"""
            SELECT COALESCE(label,'UNKNOWN'), COUNT(*)
            FROM violations
            WHERE {where_sql}
            GROUP BY label
            ORDER BY COUNT(*) DESC;
        """, params)
        label_rows = cur.fetchall()
        logging.info(f"PDF Report: label_rows count={len(label_rows)}, data={label_rows[:5]}")

        # Summary stats
        cur.execute(f"""
            SELECT
                COUNT(DISTINCT (camera, tracker_id)) AS unique_violations,
                COUNT(*) AS total_records,
                SUM(CASE WHEN enforced = TRUE THEN 1 ELSE 0 END) AS enforced_count,
                AVG(confidence_score) AS avg_confidence,
                AVG(duration_minutes) AS avg_duration
            FROM violations
            WHERE {where_sql};
        """, params)
        stats_row = cur.fetchone()
        logging.info(f"PDF Report: stats_row={stats_row}")

        # Daily breakdown for week/month/custom
        daily_data = []
        if period in ['week', 'month', 'custom']:
            cur.execute(f"""
                SELECT
                    (timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila')::date as date,
                    COUNT(DISTINCT (camera, tracker_id)) as unique_count,
                    COUNT(*) as total_count
                FROM violations
                WHERE {where_sql}
                GROUP BY (timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila')::date
                ORDER BY (timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Manila')::date;
            """, params)
            daily_data = cur.fetchall()

        # Detailed violations list (unique per camera+tracker)
        cur.execute(f"""
            SELECT sub.id, sub.camera, sub.label, sub.timestamp,
                   sub.confidence_score, sub.duration_minutes,
                   COALESCE(sub.review_status, 'for_review'),
                   COALESCE(NULLIF(sub.image_url, ''), sub.image_path, '') as img,
                   COALESCE(sub.video_url, '') as vid,
                   bp.plate_number
            FROM (
                SELECT DISTINCT ON (camera, tracker_id) *
                FROM violations
                WHERE {where_sql}
                ORDER BY camera, tracker_id, timestamp DESC
            ) sub
            LEFT JOIN LATERAL (
                SELECT plate_number FROM plate_records
                WHERE violation_id = sub.id ORDER BY confidence DESC LIMIT 1
            ) bp ON true
            ORDER BY sub.timestamp DESC
            LIMIT 500
        """, params)
        detail_rows = cur.fetchall()

        cur.close()
    except Exception as e:
        logging.error(f"Failed to query DB for PDF report: {e}")
        return jsonify({"error": "database query failed"}), 500
    finally:
        return_connection(conn)

    # Generate PDF using reportlab
    try:
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import Table, TableStyle
    except Exception:
        return jsonify({"error": "reportlab library required (pip install reportlab)"}), 500

    buf = io.BytesIO()
    try:
        c = canvas.Canvas(buf, pagesize=letter)
        c.setTitle(f"BRGYKANLURAN_VIOLATION_REPORT_{date_obj.strftime('%Y-%m-%d')}")
        width, height = letter

        # Colors — purple theme matching website
        primary_color = HexColor('#2d1b69')
        primary_light = HexColor('#5b3fbc')
        dark_bg = HexColor('#2d1b69')
        text_color = HexColor('#f8f9fa')

        # Header with purple accent bar
        c.setFillColor(primary_color)
        c.rect(0, height - 80, width, 80, fill=True, stroke=False)

        c.setFillColor(HexColor('#ffffff'))
        c.setFont("Helvetica-Bold", 24)
        c.drawString(72, height - 50, "Illegal Parking Detection Report")
        
        # Date range subtitle
        c.setFont("Helvetica", 12)
        if period == 'custom':
            date_range_text = f"{start_date.strftime('%B %d, %Y')} - {end_date.strftime('%B %d, %Y')}"
        elif period == 'month':
            date_range_text = date_obj.strftime('%B %Y')
        elif period == 'week':
            date_range_text = f"Week of {start_date.strftime('%B %d')} - {end_date.strftime('%B %d, %Y')}"
        else:  # day
            date_range_text = date_obj.strftime('%B %d, %Y')
        
        c.setFillColor(HexColor('#e0d8f0'))
        c.drawString(72, height - 68, date_range_text)

        # Current position
        y = height - 110

        # Filters applied section
        c.setFillColor(HexColor('#000000'))
        c.setFont("Helvetica-Bold", 10)
        filters_text = f"Filters: {vehicle_type if vehicle_type != 'all' else 'All Vehicles'}"
        if camera_filter != 'all':
            filters_text += f" | {camera_filter}"
        if review_status != 'all':
            filters_text += f" | Status: {review_status.replace('_', ' ').title()}"
        c.drawString(72, y, filters_text)
        y -= 8
        
        c.setFont("Helvetica", 8)
        c.setFillColor(HexColor('#666666'))
        c.drawString(72, y, f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}")
        y -= 30
        
        # Summary box
        summary_box_h = 85
        c.setFillColor(dark_bg)
        c.roundRect(72, y - summary_box_h, width - 144, summary_box_h, 10, fill=True, stroke=False)

        c.setFillColor(HexColor('#ffffff'))
        c.setFont("Helvetica-Bold", 14)
        c.drawString(92, y - 22, "SUMMARY")

        unique_violations = int(stats_row[0] or 0)
        total_records = int(stats_row[1] or 0)
        enforced_count = int(stats_row[2] or 0)
        avg_conf = float(stats_row[3] or 0.0)
        avg_dur = float(stats_row[4] or 0.0)

        c.setFillColor(text_color)
        c.setFont("Helvetica", 10)

        summary_lines = [
            f"Total Violations: {unique_violations}",
            f"Total Detection Records: {total_records}",
            f"Enforced: {enforced_count}"
        ]

        line_y = y - 42
        col1_x = 92

        for i, line in enumerate(summary_lines):
            c.drawString(col1_x, line_y - (i * 16), line)

        y -= (summary_box_h + 20)
        
        # Total Violators section
        c.setFillColor(HexColor('#000000'))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "TOTAL VIOLATORS")
        y -= 5
        c.setStrokeColor(primary_light)
        c.setLineWidth(2)
        c.line(72, y, width - 72, y)
        y -= 15
        
        # Daily breakdown for week/month/custom
        if period in ['week', 'month', 'custom'] and daily_data:
            # Build table data
            table_data = [['Date', 'Violators', 'Total Detection Records']]
            for date_val, unique_count, total_count in daily_data:
                day_name = date_val.strftime('%A, %B %d, %Y')
                table_data.append([day_name, str(unique_count), str(total_count)])
            
            # Calculate table dimensions
            col_widths = [230, 100, 140]
            row_height = 22
            table_height = len(table_data) * row_height
            
            # Check if table fits on current page
            if y - table_height < 100:
                c.showPage()
                y = height - 72
            
            table = Table(table_data, colWidths=col_widths, rowHeights=[row_height] * len(table_data))
            
            table_style = TableStyle([
                # Header row
                ('BACKGROUND', (0, 0), (-1, 0), HexColor('#2d1b69')),
                ('TEXTCOLOR', (0, 0), (-1, 0), HexColor('#ffffff')),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                # Data rows
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 10),
                ('TEXTCOLOR', (0, 1), (-1, -1), HexColor('#000000')),
                # Alternating row colors
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), HexColor('#f0f0f0')]),
                # Alignment
                ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                # Borders
                ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cccccc')),
                ('LINEBELOW', (0, 0), (-1, 0), 1.5, HexColor('#5b3fbc')),
                # Padding
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
                ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ])
            table.setStyle(table_style)
            
            # Draw table
            table_width = sum(col_widths)
            table_x = 72
            table_y = y - table_height
            table.wrapOn(c, table_width, table_height)
            table.drawOn(c, table_x, table_y)
            
            y = table_y - 10
        else:
            # Single day
            c.setFont("Helvetica", 10)
            c.setFillColor(HexColor('#000000'))
            c.drawString(92, y, f"Total Violators: {unique_violations}")
            y -= 20

        y -= 10

        # Type of Violators section
        if y < 150:
            c.showPage()
            y = height - 72
        
        # Color map for violation types
        type_colors = {
            'CAR': '#2d1b69', 'MOTORCYCLE': '#d97706', 'JEEPNEY': '#0891b2',
            'BIKE': '#059669', 'TRICYCLE': '#7c3aed', 'TRUCK': '#dc2626',
            'BUS': '#0d9488', 'VENDOR': '#e11d48', 'FALLEN_TREE': '#15803d',
            'GARBAGE': '#78716c', 'ROCK': '#57534e', 'OPEN_CANAL': '#0369a1',
        }

        c.setFillColor(HexColor('#000000'))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "TYPE OF VIOLATORS")
        y -= 5
        c.setStrokeColor(primary_light)
        c.setLineWidth(2)
        c.line(72, y, width - 72, y)
        y -= 20

        if not label_rows:
            c.setFont("Helvetica", 10)
            c.setFillColor(HexColor('#666666'))
            c.drawString(92, y, "No violations recorded for this period.")
            y -= 20
        else:
            c.setFont("Helvetica", 10)
            c.setFillColor(HexColor('#000000'))

            for lbl, cnt in label_rows:
                if y < 80:
                    c.showPage()
                    y = height - 72
                    c.setFont("Helvetica", 10)
                    c.setFillColor(HexColor('#000000'))

                dot_color = type_colors.get(lbl, '#5b3fbc')
                c.setFillColor(HexColor(dot_color))
                c.circle(82, y + 3, 4, fill=True, stroke=False)

                c.setFillColor(HexColor('#000000'))
                c.drawString(92, y, f"{lbl}: {int(cnt)}")
                y -= 18
        
        # === DETAILED INCIDENTS TABLE ===
        if detail_rows:
            page_num = 1
            if y < 150:
                c.setFont("Helvetica", 8)
                c.setFillColor(HexColor('#666666'))
                c.drawString(72, 40, "Illegal Parking Detection System - Automated Violation Tracking")
                c.drawRightString(width - 72, 40, f"Page {page_num}")
                c.showPage()
                page_num += 1
                y = height - 72

            y -= 10
            c.setFillColor(HexColor('#000000'))
            c.setFont("Helvetica-Bold", 12)
            c.drawString(72, y, "DETAILED INCIDENT LOG")
            y -= 5
            c.setStrokeColor(primary_light)
            c.setLineWidth(2)
            c.line(72, y, width - 72, y)
            y -= 15

            detail_headers = ['ID', 'Camera', 'Vehicle', 'Date & Time', 'Plate', 'Conf.', 'Duration', 'Status', 'Evidence']
            detail_col_widths = [35, 55, 60, 95, 55, 35, 45, 55, 40]
            detail_row_height = 20

            detail_table_data = [detail_headers]
            base_url = os.environ.get("RAILWAY_PUBLIC_URL", "https://web-production-dbb23.up.railway.app")

            for dr in detail_rows:
                v_id, v_cam, v_label, v_ts, v_conf, v_dur, v_status, v_img, v_vid, v_plate = dr
                ts_str = v_ts.strftime('%m/%d %I:%M %p') if v_ts else ''
                conf_str = f"{float(v_conf or 0)*100:.0f}%"
                dur_str = f"{float(v_dur or 0):.1f}m"
                status_map = {'for_review': 'Review', 'approved': 'Approved', 'rejected': 'Rejected', 'needs_investigation': 'Investigate'}
                st = status_map.get(v_status, v_status or 'Review')
                evidence = []
                if v_img:
                    evidence.append('IMG')
                if v_vid:
                    evidence.append('VID')
                ev_str = ', '.join(evidence) if evidence else 'None'

                detail_table_data.append([
                    str(v_id), v_cam or '', v_label or '', ts_str,
                    v_plate or '—', conf_str, dur_str, st, ev_str
                ])

            rows_per_page = int((y - 80) / detail_row_height)
            chunks = [detail_table_data[i:i+rows_per_page] for i in range(1, len(detail_table_data), rows_per_page)]

            for chunk_idx, chunk in enumerate(chunks):
                if chunk_idx > 0:
                    c.setFont("Helvetica", 8)
                    c.setFillColor(HexColor('#666666'))
                    c.drawString(72, 40, "Illegal Parking Detection System - Automated Violation Tracking")
                    c.drawRightString(width - 72, 40, f"Page {page_num}")
                    c.showPage()
                    page_num += 1
                    y = height - 72

                page_data = [detail_headers] + chunk
                tbl = Table(page_data, colWidths=detail_col_widths, rowHeights=[detail_row_height] * len(page_data))
                tbl.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), HexColor('#2d1b69')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), HexColor('#ffffff')),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 7),
                    ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 1), (-1, -1), 7),
                    ('TEXTCOLOR', (0, 1), (-1, -1), HexColor('#000000')),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), HexColor('#f8f6ff')]),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('ALIGN', (3, 0), (3, -1), 'LEFT'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('GRID', (0, 0), (-1, -1), 0.4, HexColor('#cccccc')),
                    ('LINEBELOW', (0, 0), (-1, 0), 1.5, HexColor('#5b3fbc')),
                    ('LEFTPADDING', (0, 0), (-1, -1), 4),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                    ('TOPPADDING', (0, 0), (-1, -1), 2),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ]))
                tbl_h = len(page_data) * detail_row_height
                tbl.wrapOn(c, sum(detail_col_widths), tbl_h)
                tbl.drawOn(c, 72, y - tbl_h)
                y = y - tbl_h - 10

            c.setFont("Helvetica", 7)
            c.setFillColor(HexColor('#888888'))
            c.drawString(72, y - 5, "Evidence: IMG = Captured Image available, VID = Video Clip available. Access full evidence at the web dashboard.")

        # Footer
        c.setFont("Helvetica", 8)
        c.setFillColor(HexColor('#666666'))
        footer_text = "Illegal Parking Detection System - Automated Violation Tracking"
        c.drawString(72, 40, footer_text)
        c.drawRightString(width - 72, 40, f"Page {page_num if detail_rows else 1}")

        c.showPage()
        c.save()
        buf.seek(0)
        pdf_bytes = buf.getvalue()
    except Exception as e:
        logging.error(f"Failed to build PDF: {e}")
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500
    finally:
        try:
            buf.close()
        except Exception:
            pass

    resp = make_response(pdf_bytes)
    resp.headers['Content-Type'] = 'application/pdf'
    filename = f"BRGYKANLURAN_VIOLATION_REPORT_{date_obj.strftime('%Y-%m-%d')}.pdf"
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp