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
    Generate a PDF report for violations.

    Query params:
      - period: 'day'|'week'|'month'|'range'  (defaults to 'day')
      - date: optional ISO date YYYY-MM-DD (for day/week/month; defaults to today)
      - start_date, end_date: ISO dates for arbitrary range (used when period=range)
      - labels: optional comma-separated labels to include (e.g. CAR,MOTORCYCLE)
      - camera: optional camera id to filter
      - barangay: optional barangay filter
    """
    period = (request.args.get('period') or 'day').lower()
    date_str = request.args.get('date')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    labels_filter = request.args.get('labels')  # comma separated
    camera_filter = request.args.get('camera')
    barangay_filter = request.args.get('barangay')

    # parse dates
    try:
        if period == 'range' and start_date and end_date:
            start_obj = datetime.date.fromisoformat(start_date)
            end_obj = datetime.date.fromisoformat(end_date)
            date_param_vals = (start_obj.isoformat(), end_obj.isoformat())
            where_clause = "timestamp::date BETWEEN %s AND %s"
        else:
            if date_str:
                date_obj = datetime.date.fromisoformat(date_str)
            else:
                date_obj = datetime.date.today()
            date_param_vals = (date_obj.isoformat(),)
            if period == 'day':
                where_clause = "timestamp::date = %s"
            elif period == 'week':
                where_clause = "date_trunc('week', timestamp)::date = date_trunc('week', %s::date)::date"
            elif period == 'month':
                where_clause = "date_trunc('month', timestamp)::date = date_trunc('month', %s::date)::date"
            else:
                # fallback to day
                where_clause = "timestamp::date = %s"
    except Exception:
        return jsonify({"error": "invalid date format, use YYYY-MM-DD"}), 400

    # additional filters
    extra_clauses = []
    params = list(date_param_vals)
    if labels_filter:
        # basic support: match exact label values OR comma-separated normalized names
        lbls = [l.strip() for l in labels_filter.split(',') if l.strip()]
        if lbls:
            # use ILIKE for case-insensitive match
            placeholders = ",".join(["%s"] * len(lbls))
            extra_clauses.append(f"label IN ({placeholders})")
            params.extend(lbls)
    if camera_filter:
        extra_clauses.append("camera = %s"); params.append(camera_filter)
    if barangay_filter:
        extra_clauses.append("barangay = %s"); params.append(barangay_filter)

    full_where = where_clause
    if extra_clauses:
        full_where = f"{full_where} AND " + " AND ".join(extra_clauses)

    try:
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()

        # Per-day totals (for week/month/range). For single day this returns that date.
        cur.execute(f"""
            SELECT DATE(timestamp) AS day, COUNT(*) AS cnt
            FROM violations
            WHERE {full_where}
            GROUP BY day
            ORDER BY day;
        """, tuple(params))
        day_rows = cur.fetchall()

        # Type breakdown by raw label -> later normalized to CAR/MOTORCYCLE where possible
        cur.execute(f"""
            SELECT COALESCE(label,'UNKNOWN') AS label, COUNT(*) AS cnt
            FROM violations
            WHERE {full_where}
            GROUP BY label
            ORDER BY cnt DESC;
        """, tuple(params))
        label_rows = cur.fetchall()

        # overall totals
        cur.execute(f"SELECT COUNT(*) FROM violations WHERE {full_where};", tuple(params))
        total_row = cur.fetchone()
        total_violators = int(total_row[0] or 0)

        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to query DB for PDF report: {e}")
        return jsonify({"error": "database query failed"}), 500

    # Normalize labels into main types
    CLASS_MAP = {
        2: "CAR", 3: "MOTORCYCLE",
        "2": "CAR", "3": "MOTORCYCLE",
        "car": "CAR", "motorcycle": "MOTORCYCLE",
        "CAR": "CAR", "MOTORCYCLE": "MOTORCYCLE"
    }
    # include CCTV_AI_LABELS if needed (keeps original mapping behavior)
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

    type_counts = {"CAR": 0, "MOTORCYCLE": 0}
    other_types = {}
    for label, cnt in label_rows:
        mapped = CLASS_MAP.get(label) or CLASS_MAP.get(str(label).lower()) or str(label)
        if mapped in type_counts:
            type_counts[mapped] += int(cnt or 0)
        else:
            other_types[mapped] = other_types.get(mapped, 0) + int(cnt or 0)

    # Build textual period description for title
    if period == 'range' and start_date and end_date:
        period_desc = f"{start_date} → {end_date}"
    elif period in ("day", "week", "month"):
        if date_str:
            period_desc = f"{date_str}"
        else:
            period_desc = datetime.date.today().isoformat()
    else:
        period_desc = "All Time"

    # Generate PDF using reportlab platypus for nicer layout
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.lib.units import mm
    except Exception:
        return jsonify({"error": "reportlab library required (pip install reportlab)"}), 500

    buf = io.BytesIO()
    try:
        doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        title_style = styles['Title']
        title_style.fontSize = 16
        normal = styles['Normal']
        normal.fontSize = 10

        elements = []
        title_text = f"Illegal Parking Detection — {period.title()} Report"
        elements.append(Paragraph(title_text, title_style))
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(f"Period: <b>{period_desc}</b>", normal))
        elements.append(Spacer(1, 8))

        # Summary box
        elements.append(Paragraph(f"<b>Total Violators: {total_violators}</b>", normal))
        elements.append(Spacer(1, 6))

        # Per-day table
        elements.append(Paragraph("<b>Violators by Day</b>", normal))
        table_data = [["Date", "Violators"]]
        if not day_rows:
            table_data.append(["—", "0"])
        else:
            for day, cnt in day_rows:
                # day may be datetime.date
                day_label = day.isoformat() if hasattr(day, 'isoformat') else str(day)
                table_data.append([day_label, str(int(cnt or 0))])
        tbl = Table(table_data, colWidths=[120*mm, 40*mm])
        tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#f3f3f3")),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('ALIGN', (1,1), (-1,-1), 'RIGHT'),
            ('BOX', (0,0), (-1,-1), 0.5, colors.grey),
            ('INNERGRID', (0,0), (-1,-1), 0.25, colors.grey),
            ('PAD', (0,0), (-1,-1), 6),
        ]))
        elements.append(tbl)
        elements.append(Spacer(1, 8))

        # Type breakdown
        elements.append(Paragraph("<b>Type of Violators</b>", normal))
        type_table = [["Type", "Count"]]
        type_table.append(["Car", str(type_counts.get("CAR", 0))])
        type_table.append(["Motorcycle", str(type_counts.get("MOTORCYCLE", 0))])
        # include any other types
        for t, c in other_types.items():
            type_table.append([str(t), str(c)])
        tbl2 = Table(type_table, colWidths=[120*mm, 40*mm])
        tbl2.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#f3f3f3")),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('ALIGN', (1,1), (-1,-1), 'RIGHT'),
            ('BOX', (0,0), (-1,-1), 0.5, colors.grey),
            ('INNERGRID', (0,0), (-1,-1), 0.25, colors.grey),
            ('PAD', (0,0), (-1,-1), 6),
        ]))
        elements.append(tbl2)
        elements.append(Spacer(1, 8))

        # Footer / generation timestamp
        elements.append(Paragraph(f"Generated: {datetime.datetime.utcnow().isoformat()} UTC", ParagraphStyle('footer', fontSize=8, textColor=colors.grey)))
        doc.build(elements)
        buf.seek(0)
        pdf_bytes = buf.getvalue()
    except Exception as e:
        logger.error(f"Failed to build PDF: {e}")
        return jsonify({"error": "PDF generation failed"}), 500
    finally:
        try:
            buf.close()
        except Exception:
            pass

    resp = make_response(pdf_bytes)
    resp.headers['Content-Type'] = 'application/pdf'
    filename = f"violations_{period}_{period_desc.replace(' ','_')}.pdf"
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp
