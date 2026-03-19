import smtplib
import requests
import logging
import time
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime
from db import get_config_value, set_config_value, get_connection

logger = logging.getLogger("Alerts")

ALERT_CONFIG_KEY = "ALERT_CONFIG"

DEFAULT_ALERT_CONFIG = {
    "email_enabled": False,
    "sms_enabled": False,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_email": "",
    "smtp_password": "",
    "textbee_api_key": "",
    "textbee_device_id": "",
    "email_recipients": [],
    "sms_recipients": [],
    "cooldown_seconds": 300,
}

_last_alert_time = {}


def get_alert_config():
    config = get_config_value(ALERT_CONFIG_KEY, None)
    if not isinstance(config, dict):
        return DEFAULT_ALERT_CONFIG.copy()
    merged = DEFAULT_ALERT_CONFIG.copy()
    merged.update(config)
    return merged


def save_alert_config(config):
    set_config_value(ALERT_CONFIG_KEY, config)


def _can_send_alert(camera_id):
    """Check cooldown period."""
    config = get_alert_config()
    cooldown = config.get("cooldown_seconds", 300)
    now = time.time()
    last = _last_alert_time.get(camera_id, 0)
    if now - last < cooldown:
        return False
    _last_alert_time[camera_id] = now
    return True


# =========================================================
# HTML Email Templates (matching PDF report style)
# =========================================================

def _email_base_style():
    """Shared inline CSS for email templates."""
    return """
    <style>
      body { margin:0; padding:0; background:#f4f4f4; font-family:'Segoe UI','Roboto',Arial,sans-serif; }
      .email-wrapper { max-width:600px; margin:0 auto; background:#ffffff; }
      .header { background:#ff9800; padding:24px 32px; }
      .header h1 { margin:0; color:#000; font-size:22px; font-weight:800; }
      .header .subtitle { margin:4px 0 0; color:#000; font-size:13px; font-weight:500; opacity:0.8; }
      .content { padding:24px 32px; }
      .summary-box { background:#232733; border-radius:10px; padding:20px 24px; margin:16px 0; }
      .summary-box .label { color:#ff9800; font-size:14px; font-weight:700; margin-bottom:10px; text-transform:uppercase; }
      .summary-box .row { color:#f8f9fa; font-size:13px; padding:3px 0; }
      .summary-box .row strong { color:#ffffff; }
      .detail-table { width:100%; border-collapse:collapse; margin:16px 0; font-size:13px; }
      .detail-table th { background:#232733; color:#ff9800; padding:10px 14px; text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; }
      .detail-table td { padding:10px 14px; border-bottom:1px solid #e8e8e8; color:#333; }
      .detail-table tr:nth-child(even) td { background:#f9f9f9; }
      .badge { display:inline-block; padding:3px 10px; border-radius:12px; font-size:11px; font-weight:600; }
      .badge-violation { background:#fff3e0; color:#e65100; }
      .badge-enforced { background:#e8f5e9; color:#2e7d32; }
      .footer { background:#232733; padding:16px 32px; text-align:center; }
      .footer p { color:#bfc7d5; font-size:11px; margin:4px 0; }
      .footer .brand { color:#ff9800; font-weight:700; font-size:12px; }
      .divider { height:3px; background:linear-gradient(90deg, #ff9800, #e68a00); margin:0; }
      .section-title { color:#232733; font-size:16px; font-weight:700; margin:20px 0 8px; padding-bottom:6px; border-bottom:2px solid #ff9800; display:inline-block; }
      .highlight { color:#ff9800; font-weight:700; }
      .text-muted { color:#888; font-size:12px; }
    </style>
    """


def _build_violation_email_html(violation_data, has_image=False):
    """Build rich HTML email for a violation alert."""
    camera = violation_data.get('camera', 'N/A')
    label = violation_data.get('label', 'Vehicle')
    duration = violation_data.get('duration_minutes', 0)
    fine = violation_data.get('fine_amount', 0)
    timestamp = violation_data.get('timestamp', 'N/A')
    barangay = violation_data.get('barangay', 'Barangay Kanluran')
    confidence = violation_data.get('confidence_score', 0)
    tracker_id = violation_data.get('tracker_id', 'N/A')

    now = datetime.now()

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {_email_base_style()}
</head>
<body>
<div class="email-wrapper">
    <!-- Header -->
    <div class="header">
        <h1>Illegal Parking Violation Alert</h1>
        <div class="subtitle">{now.strftime('%A, %B %d, %Y at %I:%M %p')}</div>
    </div>
    <div class="divider"></div>

    <div class="content">
        <!-- Summary Box -->
        <div class="summary-box">
            <div class="label">Violation Summary</div>
            <div class="row"><strong>Camera:</strong> {camera}</div>
            <div class="row"><strong>Vehicle Type:</strong> <span class="highlight">{label}</span></div>
            <div class="row"><strong>Duration:</strong> {duration} minutes</div>
            <div class="row"><strong>Fine Amount:</strong> &#8369;{fine:.2f}</div>
            <div class="row"><strong>Location:</strong> {barangay}, Santa Rosa, Laguna</div>
        </div>

        <!-- Details Table -->
        <div class="section-title">Violation Details</div>
        <table class="detail-table">
            <tr>
                <th>Field</th>
                <th>Value</th>
            </tr>
            <tr>
                <td>Tracker ID</td>
                <td>{tracker_id}</td>
            </tr>
            <tr>
                <td>Camera</td>
                <td>{camera}</td>
            </tr>
            <tr>
                <td>Vehicle Type</td>
                <td><span class="badge badge-violation">{label}</span></td>
            </tr>
            <tr>
                <td>Detection Time</td>
                <td>{timestamp}</td>
            </tr>
            <tr>
                <td>Duration</td>
                <td>{duration} min</td>
            </tr>
            <tr>
                <td>Confidence Score</td>
                <td>{float(confidence):.3f}</td>
            </tr>
            <tr>
                <td>Fine Amount</td>
                <td>&#8369;{fine:.2f}</td>
            </tr>
            <tr>
                <td>Enforcement Status</td>
                <td><span class="badge badge-violation">Pending</span></td>
            </tr>
        </table>

        {"<p style='margin:16px 0 0;color:#888;font-size:12px;'><em>Violation photo attached.</em></p>" if has_image else ""}

        <p class="text-muted" style="margin-top:20px;">
            This is an automated alert from the DECONGESTILAGUNA Illegal Parking Detection System.
            Please review and take appropriate enforcement action.
        </p>
    </div>

    <!-- Footer -->
    <div class="footer">
        <p class="brand">DECONGESTILAGUNA</p>
        <p>Illegal Parking Detection System</p>
        <p>Barangay Kanluran, Santa Rosa, Laguna</p>
    </div>
</div>
</body>
</html>"""


def _build_test_email_html():
    """Build rich HTML email for SMTP test."""
    now = datetime.now()
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {_email_base_style()}
</head>
<body>
<div class="email-wrapper">
    <!-- Header -->
    <div class="header">
        <h1>SMTP Configuration Test</h1>
        <div class="subtitle">{now.strftime('%A, %B %d, %Y at %I:%M %p')}</div>
    </div>
    <div class="divider"></div>

    <div class="content">
        <div class="summary-box">
            <div class="label">Test Result</div>
            <div class="row" style="font-size:15px;"><strong style="color:#4caf50;">&#10003; SMTP Configuration is Working Correctly</strong></div>
            <div class="row" style="margin-top:8px;">Your email alert system is properly configured and ready to send violation notifications.</div>
        </div>

        <div class="section-title">System Status</div>
        <table class="detail-table">
            <tr>
                <th>Component</th>
                <th>Status</th>
            </tr>
            <tr>
                <td>SMTP Connection</td>
                <td><span class="badge badge-enforced">Connected</span></td>
            </tr>
            <tr>
                <td>Authentication</td>
                <td><span class="badge badge-enforced">Verified</span></td>
            </tr>
            <tr>
                <td>Email Delivery</td>
                <td><span class="badge badge-enforced">Successful</span></td>
            </tr>
            <tr>
                <td>Tested At</td>
                <td>{now.strftime('%B %d, %Y at %I:%M:%S %p')}</td>
            </tr>
        </table>

        <p class="text-muted" style="margin-top:20px;">
            You will receive email alerts like this whenever a parking violation is detected by the system.
            Alerts include violation details, camera info, and attached photos when available.
        </p>
    </div>

    <!-- Footer -->
    <div class="footer">
        <p class="brand">DECONGESTILAGUNA</p>
        <p>Illegal Parking Detection System</p>
        <p>Barangay Kanluran, Santa Rosa, Laguna</p>
    </div>
</div>
</body>
</html>"""


# =========================================================
# Email & SMS Sending
# =========================================================

def send_email_alert(violation_data, image_bytes=None):
    """Send violation email with optional photo attachment."""
    config = get_alert_config()
    if not config.get("email_enabled") or not config.get("email_recipients"):
        return False

    try:
        msg = MIMEMultipart('related')
        msg['From'] = config['smtp_email']
        msg['To'] = ', '.join(config['email_recipients'])
        msg['Subject'] = (
            f"Parking Violation Alert - {violation_data.get('camera', 'Unknown')} "
            f"- {violation_data.get('label', 'Vehicle')}"
        )

        html = _build_violation_email_html(violation_data, has_image=bool(image_bytes))
        msg.attach(MIMEText(html, 'html'))

        if image_bytes:
            img = MIMEImage(image_bytes, name="violation.jpg")
            img.add_header('Content-Disposition', 'attachment', filename='violation.jpg')
            msg.attach(img)

        server = smtplib.SMTP(config['smtp_server'], config['smtp_port'])
        server.starttls()
        server.login(config['smtp_email'], config['smtp_password'])
        server.send_message(msg)
        server.quit()

        _log_alert("email", violation_data, True)
        return True

    except Exception as e:
        logger.error(f"Email alert failed: {e}")
        _log_alert("email", violation_data, False, str(e))
        return False


def send_test_email(config):
    """Send a test email with the styled HTML template."""
    msg = MIMEMultipart('related')
    msg['From'] = config['smtp_email']
    msg['To'] = ', '.join(config['email_recipients'])
    msg['Subject'] = 'DECONGESTILAGUNA - SMTP Test'

    html = _build_test_email_html()
    msg.attach(MIMEText(html, 'html'))

    server = smtplib.SMTP(config.get('smtp_server', 'smtp.gmail.com'), config.get('smtp_port', 587))
    server.starttls()
    server.login(config['smtp_email'], config['smtp_password'])
    server.send_message(msg)
    server.quit()


def send_sms_alert(violation_data):
    """Send SMS via TextBee Android gateway."""
    config = get_alert_config()
    if not config.get("sms_enabled") or not config.get("sms_recipients"):
        return False

    try:
        message = (
            f"PARKING VIOLATION\n"
            f"{violation_data.get('label', 'Vehicle')} on {violation_data.get('camera', 'N/A')}\n"
            f"Duration: {violation_data.get('duration_minutes', 0)} min\n"
            f"Fine: P{violation_data.get('fine_amount', 0):.0f}\n"
            f"Time: {violation_data.get('timestamp', 'N/A')}"
        )

        api_key = config['textbee_api_key']
        device_id = config['textbee_device_id']

        resp = requests.post(
            f"https://api.textbee.dev/api/v1/gateway/devices/{device_id}/send-sms",
            json={
                "recipients": config['sms_recipients'],
                "message": message,
            },
            headers={"x-api-key": api_key},
            timeout=15
        )

        success = resp.ok
        _log_alert("sms", violation_data, success, None if success else resp.text)
        return success

    except Exception as e:
        logger.error(f"SMS alert failed: {e}")
        _log_alert("sms", violation_data, False, str(e))
        return False


def send_violation_alert(violation_data, image_bytes=None):
    """Main function — called when a violation is confirmed."""
    camera_id = violation_data.get('camera', 'unknown')
    if not _can_send_alert(camera_id):
        logger.info(f"Alert cooldown active for {camera_id}, skipping")
        return

    send_email_alert(violation_data, image_bytes)
    send_sms_alert(violation_data)


def _log_alert(alert_type, violation_data, success, error=None):
    """Log alert to database for history."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO alert_log (alert_type, camera, vehicle_type, success, error_message, timestamp)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        """, (alert_type, violation_data.get('camera'), violation_data.get('label'), success, error))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log alert: {e}")
