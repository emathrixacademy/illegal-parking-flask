import os
import logging
from flask import Blueprint, jsonify
import psycopg2
from flask import request, make_response  # added
import io
import datetime

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
            2: "CAR", 3: "MOTORCYCLE",
            "2": "CAR", "3": "MOTORCYCLE",
            "car": "CAR", "motorcycle": "MOTORCYCLE",
            "CAR": "CAR", "MOTORCYCLE": "MOTORCYCLE"
        }
        # Also map CCTV AI labels stored as strings in the database
        CCTV_AI_LABELS = [
            "BASKET", "BOTTLE", "BOX", "BUCKET", "CAN", "CANAL",
            "CARDBOARD", "CHAIR", "CONTAINER", "CRATE", "CUP",
            "FALLEN_TREE", "GARBAGE", "GROCERY_BAG", "LEAVES",
            "OPEN_CANAL", "PAPER", "PLASTIC", "PLASTIC_BOTTLE",
            "PLASTIC_CONTAINER", "PLASTIC_BAG", "POT", "ROCK",
            "SACK", "TISSUE", "TRASH", "TRASH_CAN", "VENDOR"
        ]
        for lbl in CCTV_AI_LABELS:
            CLASS_MAP[lbl] = lbl
            CLASS_MAP[lbl.lower()] = lbl
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

        counts = {"CAR": 0, "MOTORCYCLE": 0}
        # Initialize CCTV AI class counts
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
        return jsonify({"CAR":0, "MOTORCYCLE":0})


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


@analytics_bp.route('/api/generate_report', methods=['GET'])
def api_generate_report():
    """
    Generate a simple PDF report for violations for the requested period.
    Query params:
      - period: 'day'|'week'|'month'  (defaults to 'day')
      - date: optional ISO date YYYY-MM-DD (defaults to today)
    """
    period = (request.args.get('period') or 'day').lower()
    date_str = request.args.get('date')
    try:
        if date_str:
            date_obj = datetime.date.fromisoformat(date_str)
        else:
            date_obj = datetime.date.today()
    except Exception:
        return jsonify({"error": "invalid date format, use YYYY-MM-DD"}), 400

    date_param = date_obj.isoformat()
    if period == 'day':
        where = "timestamp::date = %s"
    elif period == 'week':
        where = "date_trunc('week', timestamp)::date = date_trunc('week', %s::date)::date"
    elif period == 'month':
        where = "date_trunc('month', timestamp)::date = date_trunc('month', %s::date)::date"
    else:
        return jsonify({"error": "invalid period, use day|week|month"}), 400

    try:
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
        # per-label counts
        cur.execute(f"SELECT COALESCE(label,'UNKNOWN'), COUNT(*) FROM violations WHERE {where} GROUP BY label ORDER BY COUNT(*) DESC;", (date_param,))
        label_rows = cur.fetchall()
        # summary stats
        cur.execute(f"""
            SELECT
                COUNT(DISTINCT (camera, tracker_id)) AS unique_violations,
                COUNT(*) AS total_records,
                SUM(CASE WHEN enforced = TRUE THEN 1 ELSE 0 END) AS enforced_count,
                SUM(COALESCE(fine_amount, 0.0)) AS total_fines,
                AVG(confidence_score) AS avg_confidence,
                AVG(duration_minutes) AS avg_duration
            FROM violations
            WHERE {where};
        """, (date_param,))
        stats_row = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Failed to query DB for PDF report: {e}")
        return jsonify({"error": "database query failed"}), 500

    # Try to import reportlab and generate PDF
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except Exception:
        return jsonify({"error": "reportlab library required (pip install reportlab)"}), 500

    buf = io.BytesIO()
    try:
        c = canvas.Canvas(buf, pagesize=letter)
        width, height = letter
        x = 72
        y = height - 72

        c.setFont("Helvetica-Bold", 16)
        c.drawString(x, y, f"Violations Report - {period.title()}")
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(x, y, f"Period Date: {date_param}")
        y -= 16
        c.drawString(x, y, f"Generated: {datetime.datetime.utcnow().isoformat()} UTC")
        y -= 24

        # summary
        unique_violations = int(stats_row[0] or 0)
        total_records = int(stats_row[1] or 0)
        enforced_count = int(stats_row[2] or 0)
        total_fines = float(stats_row[3] or 0.0)
        avg_conf = float(stats_row[4] or 0.0)
        avg_dur = float(stats_row[5] or 0.0)

        c.setFont("Helvetica-Bold", 12)
        c.drawString(x, y, "Summary")
        y -= 16
        c.setFont("Helvetica", 10)
        lines = [
            f"Total records: {total_records}",
            f"Unique violations (camera+tracker): {unique_violations}",
            f"Enforced count: {enforced_count}",
            f"Total fines collected: {total_fines:.2f}",
            f"Avg confidence: {avg_conf:.2f}",
            f"Avg duration (min): {avg_dur:.2f}"
        ]
        for ln in lines:
            c.drawString(x+8, y, ln)
            y -= 14

        y -= 8
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x, y, "Counts by Label")
        y -= 16
        c.setFont("Helvetica", 10)
        if not label_rows:
            c.drawString(x+8, y, "No data for this period.")
            y -= 14
        else:
            for lbl, cnt in label_rows:
                if y < 80:
                    c.showPage()
                    y = height - 72
                    c.setFont("Helvetica", 10)
                c.drawString(x+8, y, f"{lbl}: {int(cnt)}")
                y -= 14

        c.showPage()
        c.save()
        buf.seek(0)
        pdf_bytes = buf.getvalue()
    except Exception as e:
        logging.error(f"Failed to build PDF: {e}")
        return jsonify({"error": "PDF generation failed"}), 500
    finally:
        try:
            buf.close()
        except Exception:
            pass

    resp = make_response(pdf_bytes)
    resp.headers['Content-Type'] = 'application/pdf'
    filename = f"violations_{period}_{date_param}.pdf"
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp
