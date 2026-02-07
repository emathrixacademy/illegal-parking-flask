import os
import logging
from flask import Blueprint, jsonify, request, make_response
import psycopg2
from datetime import datetime, timedelta
import io

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
    Generate a professional PDF report for violations with enhanced filtering.
    Query params:
      - period: 'day'|'week'|'month'|'custom'  (defaults to 'day')
      - date: optional ISO date YYYY-MM-DD (defaults to today) - for day/week/month
      - start_date: start date for custom range
      - end_date: end date for custom range
      - vehicle_type: 'all'|'CAR'|'MOTORCYCLE' (defaults to 'all')
      - camera: 'all'|'Camera_1'|'Camera_2' (defaults to 'all')
    """
    period = (request.args.get('period') or 'day').lower()
    date_str = request.args.get('date')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    vehicle_type = request.args.get('vehicle_type', 'all')
    camera_filter = request.args.get('camera', 'all')
    
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
            end_date = date_obj
    except Exception as e:
        return jsonify({"error": f"invalid date format: {str(e)}"}), 400

    # Build WHERE clause based on period
    where_clauses = []
    params = []
    
    if period == 'day':
        where_clauses.append("timestamp::date = %s")
        params.append(start_date)
    elif period == 'week':
        where_clauses.append("date_trunc('week', timestamp)::date = date_trunc('week', %s::date)::date")
        params.append(start_date)
    elif period == 'month':
        where_clauses.append("date_trunc('month', timestamp)::date = date_trunc('month', %s::date)::date")
        params.append(start_date)
    elif period == 'custom':
        where_clauses.append("timestamp::date BETWEEN %s AND %s")
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

    where_sql = " AND ".join(where_clauses)

    try:
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
        
        # Per-label counts
        cur.execute(f"""
            SELECT COALESCE(label,'UNKNOWN'), COUNT(*) 
            FROM violations 
            WHERE {where_sql} 
            GROUP BY label 
            ORDER BY COUNT(*) DESC;
        """, params)
        label_rows = cur.fetchall()
        
        # Summary stats
        cur.execute(f"""
            SELECT
                COUNT(DISTINCT (camera, tracker_id)) AS unique_violations,
                COUNT(*) AS total_records,
                SUM(CASE WHEN enforced = TRUE THEN 1 ELSE 0 END) AS enforced_count,
                SUM(COALESCE(fine_amount, 0.0)) AS total_fines,
                AVG(confidence_score) AS avg_confidence,
                AVG(duration_minutes) AS avg_duration
            FROM violations
            WHERE {where_sql};
        """, params)
        stats_row = cur.fetchone()
        
        # Daily breakdown for week/month/custom
        daily_data = []
        if period in ['week', 'month', 'custom']:
            cur.execute(f"""
                SELECT 
                    timestamp::date as date,
                    COUNT(DISTINCT (camera, tracker_id)) as unique_count,
                    COUNT(*) as total_count
                FROM violations
                WHERE {where_sql}
                GROUP BY timestamp::date
                ORDER BY timestamp::date;
            """, params)
            daily_data = cur.fetchall()
        
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Failed to query DB for PDF report: {e}")
        return jsonify({"error": "database query failed"}), 500

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
        width, height = letter
        
        # Colors
        primary_color = HexColor('#ff9800')  # Orange accent
        dark_bg = HexColor('#232733')
        text_color = HexColor('#f8f9fa')
        
        # Header with accent bar
        c.setFillColor(primary_color)
        c.rect(0, height - 80, width, 80, fill=True, stroke=False)
        
        c.setFillColor(HexColor('#000000'))
        c.setFont("Helvetica-Bold", 24)
        c.drawString(72, height - 50, "Illegal Parking Detection Report")
        
        # Date range subtitle
        c.setFont("Helvetica", 12)
        if period == 'custom':
            date_range_text = f"{start_date.strftime('%B %d, %Y')} - {end_date.strftime('%B %d, %Y')}"
        elif period == 'month':
            date_range_text = date_obj.strftime('%B %Y')
        elif period == 'week':
            week_end = date_obj + timedelta(days=6)
            date_range_text = f"Week of {date_obj.strftime('%B %d')} - {week_end.strftime('%B %d, %Y')}"
        else:  # day
            date_range_text = date_obj.strftime('%B %d, %Y')
        
        c.drawString(72, height - 68, date_range_text)
        
        # Current position
        y = height - 110
        
        # Filters applied section
        c.setFillColor(HexColor('#000000'))
        c.setFont("Helvetica-Bold", 10)
        filters_text = f"Filters: {vehicle_type if vehicle_type != 'all' else 'All Vehicles'}"
        if camera_filter != 'all':
            filters_text += f" | {camera_filter}"
        c.drawString(72, y, filters_text)
        y -= 8
        
        c.setFont("Helvetica", 8)
        c.setFillColor(HexColor('#666666'))
        c.drawString(72, y, f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}")
        y -= 30
        
        # Summary box
        c.setFillColor(dark_bg)
        c.roundRect(72, y - 120, width - 144, 120, 10, fill=True, stroke=False)
        
        c.setFillColor(primary_color)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(92, y - 25, "SUMMARY")
        
        unique_violations = int(stats_row[0] or 0)
        total_records = int(stats_row[1] or 0)
        enforced_count = int(stats_row[2] or 0)
        total_fines = float(stats_row[3] or 0.0)
        avg_conf = float(stats_row[4] or 0.0)
        avg_dur = float(stats_row[5] or 0.0)
        
        c.setFillColor(text_color)
        c.setFont("Helvetica", 10)
        
        summary_lines = [
            f"Total Violations: {unique_violations}",
            f"Total Captures Recorded: {total_records}",
            f"Enforced: {enforced_count}"
        ]
        
        line_y = y - 50
        col1_x = 92
        
        # Now we have only 3 items - display them in a single column
        for i, line in enumerate(summary_lines):
            c.drawString(col1_x, line_y - (i * 20), line)
        
        y -= 150
        
        # Total Violators section
        c.setFillColor(HexColor('#000000'))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "TOTAL VIOLATORS")
        y -= 5
        c.setStrokeColor(primary_color)
        c.setLineWidth(2)
        c.line(72, y, width - 72, y)
        y -= 20
        
        # Daily breakdown for week/month/custom
        if period in ['week', 'month', 'custom'] and daily_data:
            # Build table data
            table_data = [['Date', 'Violators', 'Total Captures Recorded']]
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
                ('BACKGROUND', (0, 0), (-1, 0), HexColor('#232733')),
                ('TEXTCOLOR', (0, 0), (-1, 0), HexColor('#ff9800')),
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
                ('LINEBELOW', (0, 0), (-1, 0), 1.5, HexColor('#ff9800')),
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
        
        y -= 20
        
        # Type of Violators section
        if y < 200:
            c.showPage()
            y = height - 72
        
        c.setFillColor(HexColor('#000000'))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "TYPE OF VIOLATORS")
        y -= 5
        c.setStrokeColor(primary_color)
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
                
                # Draw colored dot for vehicle type
                if lbl == 'CAR':
                    c.setFillColor(HexColor('#4da6ff'))
                elif lbl == 'MOTORCYCLE':
                    c.setFillColor(HexColor('#ffd11a'))
                else:
                    c.setFillColor(HexColor('#ff4d4d'))
                
                c.circle(82, y + 3, 4, fill=True, stroke=False)
                
                c.setFillColor(HexColor('#000000'))
                c.drawString(92, y, f"{lbl}: {int(cnt)}")
                y -= 18
        
        # Footer
        c.setFont("Helvetica", 8)
        c.setFillColor(HexColor('#666666'))
        footer_text = "Illegal Parking Detection System - Automated Violation Tracking"
        c.drawString(72, 40, footer_text)
        c.drawRightString(width - 72, 40, f"Page 1")
        
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
    filename = f"violations_{period}_{date_obj.isoformat()}.pdf"
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp