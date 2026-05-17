import os
import re
import logging
import traceback
import subprocess
import json
import base64
from datetime import datetime, timedelta
from flask import (
    Flask, request, jsonify, Response,
    render_template, render_template_string,
    stream_with_context, make_response,
    session, redirect, url_for, send_file
)
import requests
import config
import psycopg2
import urllib.parse
import threading
import time
from db import ensure_tables, get_all_settings, save_settings, init_default_settings, get_connection, return_connection, get_config_value, set_config_value
from analytics import analytics_bp
from admin_config import list_violations, mark_enforced
from auth import (
    authenticate, login_required, role_required, log_activity,
    list_users, create_user, update_user, delete_user
)
from alerts import (
    get_alert_config, save_alert_config, send_violation_alert, send_test_email
)
import cloudinary
import cloudinary.uploader
import cloudinary.api

cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.environ.get("CLOUDINARY_API_KEY", ""),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET", ""),
    secure=True
)

# --------------------------------------------------
# Logging
# --------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ParkingApp")

# --------------------------------------------------
# Cloudinary Auto-Cleanup (14-day retention)
# --------------------------------------------------
CLOUDINARY_RETENTION_DAYS = 14

def is_cloudinary_paused():
    return get_config_value("CLOUDINARY_UPLOADS_PAUSED", "false").lower() == "true"

def cloudinary_cleanup():
    """Delete Cloudinary assets and clear DB URLs for violations older than 14 days."""
    while True:
        time.sleep(6 * 3600)  # Run every 6 hours
        try:
            cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
            if not cloud_name:
                continue
            cutoff = datetime.utcnow() - timedelta(days=CLOUDINARY_RETENTION_DAYS)
            conn = get_connection()
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, image_url, video_url FROM violations
                    WHERE timestamp < %s
                      AND (image_url != '' OR video_url != '')
                """, (cutoff,))
                rows = cur.fetchall()
                if not rows:
                    cur.close()
                    continue
                deleted_count = 0
                for vid_id, image_url, video_url in rows:
                    for url, res_type in [(image_url, "image"), (video_url, "video")]:
                        if not url or 'cloudinary' not in url:
                            continue
                        try:
                            public_id = _extract_cloudinary_public_id(url, res_type)
                            if public_id:
                                cloudinary.uploader.destroy(public_id, resource_type=res_type)
                        except Exception as e:
                            logger.warning(f"Cloudinary delete failed for {url}: {e}")
                cur.execute("""
                    UPDATE violations SET image_url = '', video_url = ''
                    WHERE timestamp < %s
                      AND (image_url != '' OR video_url != '')
                """, (cutoff,))
                deleted_count = cur.rowcount
                conn.commit()
                cur.close()
                logger.info(f"Cloudinary cleanup: cleared media for {deleted_count} violations older than {CLOUDINARY_RETENTION_DAYS} days")
            finally:
                return_connection(conn)
        except Exception as e:
            logger.error(f"Cloudinary cleanup error: {e}")

def _extract_cloudinary_public_id(url, resource_type):
    """Extract the public_id from a Cloudinary URL."""
    try:
        path = urllib.parse.urlparse(url).path
        if resource_type == "video":
            marker = "/video/upload/"
        else:
            marker = "/image/upload/"
        idx = path.find(marker)
        if idx == -1:
            return None
        after = path[idx + len(marker):]
        parts = after.split("/", 1)
        if len(parts) == 2 and parts[0].startswith("v"):
            after = parts[1]
        public_id = os.path.splitext(after)[0]
        return public_id
    except Exception:
        return None

threading.Thread(target=cloudinary_cleanup, daemon=True, name="cloudinary-cleanup").start()

# --------------------------------------------------
# Flask App
# --------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'f7a3b9c1d4e8f2a6b0c5d9e3f1a7b4c8')
app.permanent_session_lifetime = timedelta(minutes=30)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB for 5-min video uploads

# Register analytics blueprint
app.register_blueprint(analytics_bp)

# --------------------------------------------------
# Environment / Defaults
# --------------------------------------------------
DEFAULT_PORT = int(os.environ.get("PORT", 5000))
DEFAULT_RASPI_IP = os.environ.get("RASPI_IP", "192.168.8.101")
DEFAULT_RASPI_PORT = os.environ.get("RASPI_PORT", "5000")
DEFAULT_RAILWAY_API_URL = os.environ.get(
    "RAILWAY_API_URL", "https://web-production-dbb23.up.railway.app"
)
CLOUDFLARE_TUNNEL_CMD = ["cloudflared", "tunnel", "--url", f"http://localhost:{DEFAULT_PORT}"]
STATIC_EVENTS_DIR = "static/events"
EVENT_IMAGE_FORMAT = "{camera_id}_{timestamp}.jpg"
EVENT_IMAGE_TIMESTAMP_REPL = lambda ts: ts.replace(":", "-").replace(".", "-")

POSTGRES_URL = os.environ.get("DATABASE_URL") or "postgresql://postgres:osBsKkhgPnjxUCtzUDZFSLAVTEvuqCNH@postgres.railway.internal:5432/railway"

# Initialize database tables and default settings on startup
ensure_tables()
init_default_settings()

# --------------------------------------------------
# Config helpers (local config.py)
# --------------------------------------------------
def update_config(new_settings):
    import importlib
    import json as pyjson
    config_path = os.path.join(os.path.dirname(__file__), "config.py")
    with open(config_path, "r") as f:
        lines = f.readlines()

    def replace_line(key, value):
        pattern = re.compile(rf"^{key}\s*=\s*.*$")
        for i, line in enumerate(lines):
            if pattern.match(line):
                if key == "PARKING_ZONES":
                    lines[i] = f"{key} = {pyjson.dumps(value)}\n"
                else:
                    lines[i] = f"{key} = {value}\n"
                return
        lines.append(f"{key} = {pyjson.dumps(value) if key=='PARKING_ZONES' else value}\n")

    if "VIOLATION_TIME_THRESHOLD" in new_settings:
        replace_line("VIOLATION_TIME_THRESHOLD", new_settings["VIOLATION_TIME_THRESHOLD"])
    if "REPEAT_CAPTURE_INTERVAL" in new_settings:
        replace_line("REPEAT_CAPTURE_INTERVAL", new_settings["REPEAT_CAPTURE_INTERVAL"])
    if "PARKING_ZONES" in new_settings:
        current_zones = getattr(config, "PARKING_ZONES", {})
        updated_zones = current_zones.copy()
        for cam, val in new_settings["PARKING_ZONES"].items():
            if val is None:
                updated_zones.pop(cam, None)
            else:
                updated_zones[cam] = val
        replace_line("PARKING_ZONES", updated_zones)

    with open(config_path, "w") as f:
        f.writelines(lines)
    importlib.reload(config)

# --------------------------------------------------
# Cloudflare Tunnel
# --------------------------------------------------
def start_cloudflared(port=DEFAULT_PORT):
    process = subprocess.Popen(
        CLOUDFLARE_TUNNEL_CMD,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    url = None
    for line in iter(process.stdout.readline, ""):
        print(line.strip())
        match = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", line)
        if match:
            url = match.group(0)
            break
    if not url:
        raise RuntimeError("Failed to start cloudflared tunnel")
    print(f"Cloudflared tunnel running at: {url}")
    return process, url

# --------------------------------------------------
# Pi Public URL Storage
# --------------------------------------------------
PI_PUBLIC_URL = get_config_value("PI_PUBLIC_URL", "")
PI_URL_NOT_SET_LOGGED = False
PI_LAST_HEARTBEAT = None
logger.info(f"Loaded PI_PUBLIC_URL from database: {PI_PUBLIC_URL or '(empty)'}")

PI_API_KEY = os.environ.get("PI_API_KEY", "dcgl-pi-secret-2026")

def pi_api_key_required(f):
    """Decorator: requires valid PI_API_KEY header for machine-to-machine routes."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        if key != PI_API_KEY:
            return jsonify({"error": "Invalid API key"}), 403
        return f(*args, **kwargs)
    return decorated

def login_or_api_key(f):
    """Decorator: allows access if the user is logged in OR provides a valid PI API key.
    Use on routes that both browser users and the Pi need to call."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        # Accept if valid API key is provided
        key = request.headers.get("X-API-Key", "")
        if key == PI_API_KEY:
            return f(*args, **kwargs)
        # Otherwise fall back to login_required
        return login_required(f)(*args, **kwargs)
    return decorated

@app.route('/api/set_pi_url', methods=['POST'])
@pi_api_key_required
def set_pi_url():
    global PI_PUBLIC_URL, PI_URL_NOT_SET_LOGGED
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400
    new_url = data.get("public_url", "")
    if new_url and new_url != PI_PUBLIC_URL:
        logger.info(f"Received new Pi public URL: {new_url} (old: {PI_PUBLIC_URL})")
        PI_PUBLIC_URL = new_url
        set_config_value("PI_PUBLIC_URL", new_url)
    PI_URL_NOT_SET_LOGGED = False
    return jsonify({"success": True, "public_url": PI_PUBLIC_URL})

@app.route('/api/get_pi_url')
@login_required
def get_pi_url():
    resp = jsonify({"public_url": PI_PUBLIC_URL})
    resp.headers.update(cors_headers())
    return resp

@app.route('/api/heartbeat', methods=['POST'])
@pi_api_key_required
def pi_heartbeat():
    global PI_LAST_HEARTBEAT
    PI_LAST_HEARTBEAT = datetime.utcnow()
    data = request.get_json(silent=True) or {}
    set_config_value("PI_LAST_HEARTBEAT", PI_LAST_HEARTBEAT.isoformat())
    if data.get("uptime"):
        set_config_value("PI_UPTIME", str(data["uptime"]))
    if data.get("cpu_temp"):
        set_config_value("PI_CPU_TEMP", str(data["cpu_temp"]))
    logger.info(f"Pi heartbeat received at {PI_LAST_HEARTBEAT.isoformat()}")
    return jsonify({"success": True, "received_at": PI_LAST_HEARTBEAT.isoformat()})

@app.route('/api/pi_status')
@login_or_api_key
def pi_status():
    global PI_LAST_HEARTBEAT
    if PI_LAST_HEARTBEAT is None:
        saved = get_config_value("PI_LAST_HEARTBEAT", "")
        if saved:
            try:
                PI_LAST_HEARTBEAT = datetime.fromisoformat(saved)
            except ValueError:
                pass
    if PI_LAST_HEARTBEAT is None:
        return jsonify({"alive": False, "status": "never_seen", "last_heartbeat": None})
    age_seconds = (datetime.utcnow() - PI_LAST_HEARTBEAT).total_seconds()
    if age_seconds < 90:
        status = "online"
    elif age_seconds < 300:
        status = "stale"
    else:
        status = "offline"
    return jsonify({
        "alive": status == "online",
        "status": status,
        "last_heartbeat": PI_LAST_HEARTBEAT.isoformat(),
        "age_seconds": int(age_seconds),
        "uptime": get_config_value("PI_UPTIME", ""),
        "cpu_temp": get_config_value("PI_CPU_TEMP", "")
    })

# --------------------------------------------------
# Helper Functions
# --------------------------------------------------
def cors_headers():
    origin = request.headers.get('Origin', '')
    allowed_origins = [
        DEFAULT_RAILWAY_API_URL,
        f"http://localhost:{DEFAULT_PORT}",
        "http://127.0.0.1:5000",
    ]
    if PI_PUBLIC_URL:
        allowed_origins.append(PI_PUBLIC_URL.rstrip("/"))
    allow = origin if origin in allowed_origins else allowed_origins[0]
    return {
        'Access-Control-Allow-Origin': allow,
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-API-Key',
        'Access-Control-Allow-Credentials': 'true'
    }

@app.before_request
def before_request_handler():
    session.permanent = True
    logger.info(f"Incoming request: {request.method} {request.path} | IP: {request.remote_addr}")

@app.after_request
def after_request_func(response):
    logger.info(f"Response: {request.method} {request.path} | Status: {response.status_code}")
    response.headers.update(cors_headers())
    return response

def get_pi_base():
    if not PI_PUBLIC_URL:
        raise RuntimeError("Pi public URL not set")
    return PI_PUBLIC_URL.rstrip("/")

def ensure_violations_table():
    """Create the violations table if it does not exist."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS violations (
                id SERIAL PRIMARY KEY,
                camera VARCHAR(32),
                tracker_id INTEGER,
                label VARCHAR(32),
                timestamp TIMESTAMP,
                image_path TEXT,
                confidence_score REAL DEFAULT 0.0,
                barangay TEXT DEFAULT 'Brgy. Kanluran',
                duration_minutes REAL,
                fine_amount REAL DEFAULT 0.0,
                enforced BOOLEAN DEFAULT FALSE,
                review_status VARCHAR(32) DEFAULT 'for_review',
                review_notes TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                video_url TEXT DEFAULT ''
            );
        """)
        for col_def in [
            ("duration_minutes", "REAL"),
            ("fine_amount", "REAL DEFAULT 0.0"),
            ("enforced", "BOOLEAN DEFAULT FALSE"),
            ("review_status", "VARCHAR(32) DEFAULT 'for_review'"),
            ("review_notes", "TEXT DEFAULT ''"),
            ("image_url", "TEXT DEFAULT ''"),
            ("video_url", "TEXT DEFAULT ''"),
        ]:
            try:
                cur.execute(f"ALTER TABLE violations ADD COLUMN IF NOT EXISTS {col_def[0]} {col_def[1]}")
            except Exception:
                conn.rollback()
        cur.execute("""
            UPDATE violations SET image_url = image_path
            WHERE (image_url IS NULL OR image_url = '') AND image_path IS NOT NULL AND image_path != ''
        """)
        conn.commit()
        cur.close()
        return_connection(conn)
        logger.info("Ensured 'violations' table exists in PostgreSQL.")
    except Exception as e:
        logger.error(f"Failed to ensure violations table: {e}")

# --- Image Cache ---
IMAGE_CACHE = {}
IMAGE_CACHE_LOCK = threading.Lock()
IMAGE_CACHE_MAX_SIZE = 200
IMAGE_CACHE_TTL = 60 * 5    # 5 minutes

def get_cached_image(image_path):
    now = time.time()
    with IMAGE_CACHE_LOCK:
        entry = IMAGE_CACHE.get(image_path)
        if entry:
            data, ts = entry
            if now - ts < IMAGE_CACHE_TTL:
                return data
            else:
                del IMAGE_CACHE[image_path]
        return None

def set_cached_image(image_path, data):
    with IMAGE_CACHE_LOCK:
        if len(IMAGE_CACHE) >= IMAGE_CACHE_MAX_SIZE:
            if IMAGE_CACHE:
                oldest = min(IMAGE_CACHE.items(), key=lambda x: x[1][1])[0]
                del IMAGE_CACHE[oldest]
        IMAGE_CACHE[image_path] = (data, time.time())

# ==================================================
# Routes – Authentication (Feature 18)
# ==================================================
@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if 'user' in session:
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        try:
            user = authenticate(username, password)
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            user = None
            error = 'System error. Please try again.'
        if user:
            session['user'] = user
            try:
                log_activity(user['id'], 'login')
            except Exception:
                pass
            return redirect(url_for('index'))
        if not error:
            error = 'Invalid username or password'
    return render_template('login.html', error=error)

@app.route('/api/cleanup-demo-data', methods=['POST'])
@login_required
@role_required('admin')
def cleanup_demo_data():
    """One-time cleanup: remove demo/seed records from the database."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        demo_plates = ('ABC-1234', 'XYZ-5678', 'TRC-9012', 'DEF-3456', 'GHI-7890')
        demo_trackers = (101, 202, 303, 404, 505, 606, 707, 808, 909, 1010)
        cur.execute("DELETE FROM plate_records WHERE plate_number IN %s", (demo_plates,))
        plate_del = cur.rowcount
        cur.execute("DELETE FROM plate_records WHERE violation_id IN (SELECT id FROM violations WHERE tracker_id IN %s AND barangay = 'Brgy. Kanluran')", (demo_trackers,))
        plate_del += cur.rowcount
        cur.execute("DELETE FROM violations WHERE tracker_id IN %s AND barangay = 'Brgy. Kanluran'", (demo_trackers,))
        viol_del = cur.rowcount
        cur.execute("DELETE FROM violations WHERE barangay = 'Brgy. Kanluran'")
        viol_del += cur.rowcount
        conn.commit()
        cur.close()
        return_connection(conn)
        return jsonify(ok=True, deleted_violations=viol_del, deleted_plates=plate_del)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route('/logout')
def logout():
    if 'user' in session:
        log_activity(session['user']['id'], 'logout')
    session.clear()
    return redirect(url_for('login_page'))

# ==================================================
# Routes – UI (all protected by login_required)
# ==================================================
@app.route('/')
def landing():
    if 'user' in session:
        return redirect(url_for('index'))
    return render_template('landing.html')

@app.route('/dashboard')
@login_required
def index():
    return render_template('index.html', user=session.get('user'), public_url=PI_PUBLIC_URL or "", active_page='home')

@app.route('/calendar')
@login_required
def calendar_page():
    return render_template('calendar.html', user=session.get('user'), active_page='calendar')

@app.route('/settings')
@login_required
@role_required('operator')
def settings_page():
    return render_template('settings.html', user=session.get('user'), active_page='settings')

@app.route('/8f3c9a2d71b4e6c0f9d2a8b7c4e1')
@login_required
@role_required('admin')
def admin_page():
    return render_template('admin.html', user=session.get('user'), public_url=PI_PUBLIC_URL or "", active_page='admin')

@app.route('/violations')
@login_required
def violations_page():
    return render_template('violations.html', user=session.get('user'), active_page='violations')

@app.route('/playback')
@login_required
def playback_page():
    return redirect(url_for('calendar_page'))

@app.route('/user-management')
@login_required
@role_required('admin')
def user_management_page():
    return render_template('user_management.html', user=session.get('user'), active_page='users')

@app.route('/ping')
def ping():
    return "pong"

@app.route('/sw.js')
def service_worker():
    return app.send_static_file('sw.js'), 200, {'Content-Type': 'application/javascript', 'Service-Worker-Allowed': '/'}

# ==================================================
# Routes – User Management API (Feature 18)
# ==================================================
@app.route('/api/users', methods=['GET'])
@login_required
@role_required('admin')
def api_list_users():
    return jsonify(list_users())

@app.route('/api/users', methods=['POST'])
@login_required
@role_required('admin')
def api_create_user():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400
    if not data.get('username') or not data.get('password'):
        return jsonify({"success": False, "error": "Username and password required"}), 400
    try:
        user_id = create_user(
            data['username'], data['password'],
            data.get('role', 'viewer'), data.get('display_name'), data.get('email')
        )
        log_activity(session['user']['id'], 'create_user', f"Created user {data['username']}")
        return jsonify({"success": True, "id": user_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
@role_required('admin')
def api_update_user(user_id):
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400
    update_user(user_id, data)
    log_activity(session['user']['id'], 'update_user', f"Updated user ID {user_id}")
    return jsonify({"success": True})

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_user(user_id):
    delete_user(user_id)
    log_activity(session['user']['id'], 'delete_user', f"Deleted user ID {user_id}")
    return jsonify({"success": True})

@app.route('/api/activity_log')
@login_required
@role_required('admin')
def api_activity_log():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT a.id, a.user_id, u.username, a.action, a.details, a.ip_address, a.timestamp
            FROM activity_log a
            LEFT JOIN users u ON a.user_id = u.id
            ORDER BY a.timestamp DESC LIMIT 200
        """)
        rows = cur.fetchall()
        cols = ['id', 'user_id', 'username', 'action', 'details', 'ip_address', 'timestamp']
        cur.close()
        return jsonify([dict(zip(cols, r)) for r in rows])
    finally:
        return_connection(conn)

# ==================================================
# Routes – Alert Config API (Feature 17)
# ==================================================
@app.route('/api/alert_config', methods=['GET', 'POST'])
@login_required
@role_required('operator')
def api_alert_config():
    if request.method == 'POST':
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON"}), 400
        save_alert_config(data)
        log_activity(session['user']['id'], 'update_alert_config')
        return jsonify({"success": True})
    return jsonify(get_alert_config())

@app.route('/api/test_smtp', methods=['POST'])
@login_required
@role_required('operator')
def api_test_smtp():
    """Send a test email to verify SMTP configuration."""
    config = get_alert_config()
    if not config.get('smtp_email') or not config.get('smtp_password'):
        return jsonify({"success": False, "error": "SMTP email and password not configured"}), 400
    recipients = config.get('email_recipients', [])
    if not recipients:
        return jsonify({"success": False, "error": "No email recipients configured"}), 400
    try:
        send_test_email(config)
        return jsonify({"success": True, "message": f"Test email sent to {', '.join(recipients)}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/alert_log')
@login_required
@role_required('operator')
def api_alert_log():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM alert_log ORDER BY timestamp DESC LIMIT 100")
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]
        cur.close()
        return jsonify([dict(zip(cols, r)) for r in rows])
    finally:
        return_connection(conn)

# ==================================================
# Routes – Tamper Events API (Feature 14)
# ==================================================
@app.route('/api/tamper_event', methods=['POST'])
@pi_api_key_required
def api_tamper_event():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400

    if data.get('tamper_type') == 'SCENE_CHANGE':
        return jsonify({"success": True, "note": "SCENE_CHANGE disabled"}), 200

    image_url = ""
    image_b64 = data.get("image")
    if image_b64:
        try:
            ts = data.get('timestamp', datetime.utcnow().isoformat()).replace(':', '-').replace('.', '-')
            result = cloudinary.uploader.upload(
                f"data:image/jpeg;base64,{image_b64}",
                folder="tamper",
                public_id=f"{data['camera']}_{ts}_last_good",
                overwrite=True,
                resource_type="image"
            )
            image_url = result.get("secure_url", "")
            logger.info(f"Uploaded tamper image to Cloudinary: {image_url}")
        except Exception as e:
            logger.error(f"Cloudinary tamper image upload failed: {e}")

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tamper_events (camera, tamper_type, details, last_good_frame_path, timestamp)
            VALUES (%s, %s, %s::jsonb, %s, %s)
        """, (data['camera'], data['tamper_type'], json.dumps(data.get('details', {})),
              image_url, data.get('timestamp', datetime.utcnow().isoformat())))
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)
    return jsonify({"success": True})

@app.route('/api/tamper_events')
@login_required
def api_tamper_events():
    unresolved = request.args.get('unresolved')
    conn = get_connection()
    try:
        cur = conn.cursor()
        if unresolved:
            cur.execute("SELECT * FROM tamper_events WHERE resolved = FALSE AND tamper_type != 'SCENE_CHANGE' ORDER BY timestamp DESC LIMIT 50")
        else:
            cur.execute("SELECT * FROM tamper_events ORDER BY timestamp DESC LIMIT 50")
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]
        cur.close()
        return jsonify([dict(zip(cols, r)) for r in rows])
    finally:
        return_connection(conn)

@app.route('/api/tamper_events/<int:event_id>/resolve', methods=['POST'])
@login_required
@role_required('operator')
def api_resolve_tamper(event_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE tamper_events SET resolved = TRUE WHERE id = %s", (event_id,))
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)
    return jsonify({"success": True})

@app.route('/api/cloudinary_toggle', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def api_cloudinary_toggle():
    if request.method == 'GET':
        paused = is_cloudinary_paused()
        return jsonify({"paused": paused})
    data = request.get_json(force=True)
    paused = bool(data.get("paused", False))
    set_config_value("CLOUDINARY_UPLOADS_PAUSED", str(paused).lower())
    logger.info(f"Cloudinary uploads {'PAUSED' if paused else 'RESUMED'}")
    return jsonify({"success": True, "paused": paused})

@app.route('/api/cloudinary_cleanup', methods=['POST'])
@login_required
@role_required('admin')
def api_cloudinary_cleanup():
    """Manually trigger Cloudinary cleanup for assets older than 14 days."""
    try:
        cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
        if not cloud_name:
            return jsonify({"ok": False, "error": "Cloudinary not configured"}), 400
        cutoff = datetime.utcnow() - timedelta(days=CLOUDINARY_RETENTION_DAYS)
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, image_url, video_url FROM violations
                WHERE timestamp < %s
                  AND (image_url != '' OR video_url != '')
            """, (cutoff,))
            rows = cur.fetchall()
            assets_deleted = 0
            for vid_id, image_url, video_url in rows:
                for url, res_type in [(image_url, "image"), (video_url, "video")]:
                    if not url or 'cloudinary' not in url:
                        continue
                    try:
                        public_id = _extract_cloudinary_public_id(url, res_type)
                        if public_id:
                            cloudinary.uploader.destroy(public_id, resource_type=res_type)
                            assets_deleted += 1
                    except Exception as e:
                        logger.warning(f"Cloudinary delete failed for {url}: {e}")
            cur.execute("""
                UPDATE violations SET image_url = '', video_url = ''
                WHERE timestamp < %s
                  AND (image_url != '' OR video_url != '')
            """, (cutoff,))
            violations_cleared = cur.rowcount
            conn.commit()
            cur.close()
        finally:
            return_connection(conn)
        return jsonify({
            "ok": True,
            "violations_cleared": violations_cleared,
            "assets_deleted": assets_deleted,
            "retention_days": CLOUDINARY_RETENTION_DAYS,
            "cutoff_date": cutoff.isoformat()
        })
    except Exception as e:
        logger.error(f"Manual cloudinary cleanup error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/cloudinary_media')
@login_required
@role_required('admin')
def api_cloudinary_media():
    """List violations that have Cloudinary media, with optional age filter."""
    try:
        filter_val = request.args.get('filter', 'all')
        conn = get_connection()
        try:
            cur = conn.cursor()
            if filter_val == 'all':
                cur.execute("""
                    SELECT v.id, v.camera, v.label, v.timestamp, v.image_url, v.video_url, p.plate_number
                    FROM violations v
                    LEFT JOIN plate_records p ON p.violation_id = v.id
                    WHERE (v.image_url != '' AND v.image_url IS NOT NULL)
                       OR (v.video_url != '' AND v.video_url IS NOT NULL)
                    ORDER BY v.timestamp DESC LIMIT 500
                """)
            else:
                days = int(filter_val)
                cutoff = datetime.utcnow() - timedelta(days=days)
                cur.execute("""
                    SELECT v.id, v.camera, v.label, v.timestamp, v.image_url, v.video_url, p.plate_number
                    FROM violations v
                    LEFT JOIN plate_records p ON p.violation_id = v.id
                    WHERE v.timestamp < %s
                      AND ((v.image_url != '' AND v.image_url IS NOT NULL)
                        OR (v.video_url != '' AND v.video_url IS NOT NULL))
                    ORDER BY v.timestamp DESC LIMIT 500
                """, (cutoff,))
            rows = cur.fetchall()
            cur.close()
            violations = []
            for r in rows:
                violations.append({
                    "id": r[0], "camera": r[1], "label": r[2],
                    "timestamp": r[3].isoformat() if r[3] else None,
                    "image_url": r[4] or '', "video_url": r[5] or '',
                    "plate_number": r[6] or ''
                })
            return jsonify({"violations": violations})
        finally:
            return_connection(conn)
    except Exception as e:
        logger.error(f"Cloudinary media list error: {e}")
        return jsonify({"violations": [], "error": str(e)}), 500

@app.route('/api/cloudinary_delete_selected', methods=['POST'])
@login_required
@role_required('admin')
def api_cloudinary_delete_selected():
    """Delete Cloudinary assets for specific violation IDs."""
    try:
        data = request.get_json(force=True)
        violation_ids = data.get('violation_ids', [])
        if not violation_ids:
            return jsonify({"ok": False, "error": "No violations selected"}), 400

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, image_url, video_url FROM violations
                WHERE id = ANY(%s)
                  AND ((image_url != '' AND image_url IS NOT NULL)
                    OR (video_url != '' AND video_url IS NOT NULL))
            """, (violation_ids,))
            rows = cur.fetchall()
            assets_deleted = 0
            for vid_id, image_url, video_url in rows:
                for url, res_type in [(image_url, "image"), (video_url, "video")]:
                    if not url or 'cloudinary' not in url:
                        continue
                    try:
                        public_id = _extract_cloudinary_public_id(url, res_type)
                        if public_id:
                            cloudinary.uploader.destroy(public_id, resource_type=res_type)
                            assets_deleted += 1
                    except Exception as e:
                        logger.warning(f"Cloudinary delete failed for {url}: {e}")
            cur.execute("""
                UPDATE violations SET image_url = '', video_url = ''
                WHERE id = ANY(%s)
            """, (violation_ids,))
            violations_cleared = cur.rowcount
            conn.commit()
            cur.close()
        finally:
            return_connection(conn)
        logger.info(f"Manual media delete: {assets_deleted} assets from {violations_cleared} violations")
        return jsonify({"ok": True, "assets_deleted": assets_deleted, "violations_cleared": violations_cleared})
    except Exception as e:
        logger.error(f"Cloudinary delete selected error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# ==================================================
# Routes – System Health Proxy (Feature 16)
# ==================================================
@app.route('/api/cloudinary_test')
@login_or_api_key
def api_cloudinary_test():
    try:
        cn = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
        ak = os.environ.get("CLOUDINARY_API_KEY", "")
        ase = os.environ.get("CLOUDINARY_API_SECRET", "")
        if not cn or not ak or not ase:
            return jsonify({"ok": False, "error": f"Missing env vars: cloud_name={'set' if cn else 'MISSING'}, api_key={'set' if ak else 'MISSING'}, api_secret={'set' if ase else 'MISSING'}"})
        tiny_purple = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
        result = cloudinary.uploader.upload(
            f"data:image/png;base64,{tiny_purple}",
            folder="test",
            public_id="cloudinary_test",
            overwrite=True,
            resource_type="image"
        )
        url = result.get("secure_url", "")
        return jsonify({"ok": True, "url": url, "cloud_name": cn})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "type": type(e).__name__})

@app.route('/api/system_health')
@login_required
def api_system_health():
    try:
        pi_base = get_pi_base()
        resp = requests.get(f"{pi_base}/api/health", timeout=10)
        return Response(resp.content, resp.status_code,
                        content_type=resp.headers.get('Content-Type', 'application/json'))
    except Exception:
        return jsonify({"overall_status": "unreachable", "error": "Pi offline"})




# ==================================================
# Routes – Plate Search API (Feature 13)
# ==================================================
@app.route('/api/plates/search')
@login_required
def search_plates():
    query = request.args.get('q', '').upper()
    if not query:
        return jsonify([])
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.violation_id, p.plate_number, p.confidence, p.plate_image_path,
                   p.camera, p.timestamp, v.label, v.duration_minutes
            FROM plate_records p
            LEFT JOIN violations v ON p.violation_id = v.id
            WHERE p.plate_number ILIKE %s
            ORDER BY p.timestamp DESC
            LIMIT 50
        """, (f"%{query}%",))
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]
        cur.close()
        return_connection(conn)
        return jsonify([dict(zip(cols, r)) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================================================
# PART 3: Remote AI Detection Control Proxies
# ==================================================
@app.route('/api/detection_control', methods=['GET', 'POST'])
@login_required
@role_required('operator')
def api_detection_control():
    """Proxy detection controls to Pi."""
    try:
        pi_base = get_pi_base()
        if request.method == 'POST':
            resp = requests.post(f"{pi_base}/api/detection_control",
                json=request.get_json(force=True), timeout=10)
        else:
            resp = requests.get(f"{pi_base}/api/detection_control", timeout=10)
        return Response(resp.content, resp.status_code,
            content_type=resp.headers.get('Content-Type', 'application/json'))
    except Exception:
        return jsonify({"error": "Pi unreachable"}), 502

@app.route('/api/detection_snapshot')
@login_required
def api_detection_snapshot():
    """Proxy snapshot from Pi."""
    try:
        pi_base = get_pi_base()
        resp = requests.get(f"{pi_base}/api/detection_snapshot", timeout=10)
        return Response(resp.content, mimetype='image/jpeg')
    except Exception:
        return Response("Snapshot unavailable", status=502)

@app.route('/api/restart_detection', methods=['POST'])
@login_required
@role_required('admin')
def api_restart_detection():
    """Proxy restart command to Pi. Admin only."""
    try:
        pi_base = get_pi_base()
        resp = requests.post(f"{pi_base}/api/restart_detection", timeout=15)
        return Response(resp.content, resp.status_code,
            content_type=resp.headers.get('Content-Type', 'application/json'))
    except Exception:
        return jsonify({"error": "Pi unreachable"}), 502

# ==================================================
# PART 2: Daily Health Summary Email
# ==================================================
@app.route('/api/health_summary_email', methods=['POST'])
@login_or_api_key
def api_health_summary_email():
    """Receive health summary from Pi and email it."""
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400
    summary = data.get('summary', '')
    status = data.get('status', 'unknown')

    from alerts import get_alert_config
    alert_cfg = get_alert_config()
    if not alert_cfg.get('email_enabled') or not alert_cfg.get('email_recipients'):
        return jsonify({"success": False, "reason": "Email not configured"})

    try:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(summary)
        msg['Subject'] = f"[DECONGESTILAGUNA] Daily Health Report — {status.upper()}"
        msg['From'] = alert_cfg['smtp_email']
        msg['To'] = ', '.join(alert_cfg['email_recipients'])

        server = smtplib.SMTP(alert_cfg.get('smtp_server', 'smtp.gmail.com'), alert_cfg.get('smtp_port', 587))
        server.starttls()
        server.login(alert_cfg['smtp_email'], alert_cfg['smtp_password'])
        server.send_message(msg)
        server.quit()

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Health summary email failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================================================
# Routes – Proxy to Pi
# ==================================================
@app.route('/api/<path:path>', methods=['GET','POST','OPTIONS'])
@login_required
def proxy_api(path):
    if request.method == 'OPTIONS':
        return jsonify({}), 200, cors_headers()
    try:
        pi_base = get_pi_base()
        url = f"{pi_base}/api/{path}"
        if request.method == 'GET':
            resp = requests.get(url, params=request.args, timeout=10)
        else:
            resp = requests.post(url, json=request.get_json(force=True), timeout=10)
        return Response(resp.content, resp.status_code, resp.headers.items())
    except Exception as e:
        if str(e) == "Pi public URL not set":
            return jsonify({"success": False, "error": str(e)}), 502
        logger.error(f"Proxy API error: {e}")
        return jsonify({"success": False, "error": str(e)}), 502

# --------------------------------------------------
# Video Feed Proxy
# --------------------------------------------------
@app.route('/video_feed_c1')
@login_required
def proxy_video_feed_c1():
    return proxy_video_feed("video_feed_c1")

@app.route('/video_feed_c2')
@login_required
def proxy_video_feed_c2():
    return proxy_video_feed("video_feed_c2")

def proxy_video_feed(feed_path):
    try:
        pi_base = get_pi_base()
        url = f"{pi_base}/{feed_path}"
        resp = requests.get(url, stream=True, timeout=10)
        return Response(
            stream_with_context(resp.iter_content(chunk_size=4096)),
            content_type=resp.headers.get('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        )
    except Exception as e:
        if str(e) == "Pi public URL not set":
            return Response("Camera feed unavailable", 502)
        logger.error(f"Proxy {feed_path} error: {e}")
        return Response("Camera feed unavailable", 502)

# --------------------------------------------------
# Events / Upload
# --------------------------------------------------
@app.route('/api/upload_event', methods=['POST'])
@pi_api_key_required
def upload_event():
    try:
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON"}), 400
        camera_id = data.get("camera_id")
        tracker_id = data.get("tracker_id")
        label = data.get("label")
        timestamp = data.get("timestamp", datetime.utcnow().isoformat())
        image_b64 = data.get("image")
        meta = data.get("meta", {})

        logger.info(f"Received violation event: camera_id={camera_id}, timestamp={timestamp}, meta={meta}")

        VEHICLE_LABELS = {'CAR', 'MOTORCYCLE', 'BUS', 'TRUCK', 'BIKE'}
        if label and label.upper() in VEHICLE_LABELS:
            plate_number = data.get("plate_number")
            plate_confidence = data.get("plate_confidence", 0)
            video_b64 = data.get("video")
            missing = []
            if not image_b64:
                missing.append("image")
            if not plate_number or float(plate_confidence) < 0.4:
                missing.append("plate_number")
            if not video_b64:
                missing.append("video")
            if missing:
                logger.warning(f"INTEGRITY REJECT: {label} violation missing {missing}")
                return jsonify({"error": f"Vehicle violation requires: {', '.join(missing)}", "missing": missing}), 422

        image_bytes = None
        image_url = ''
        video_url = ''

        if image_b64:
            image_bytes = base64.b64decode(image_b64)
            try:
                cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
                if cloud_name and not is_cloudinary_paused():
                    result = cloudinary.uploader.upload(
                        f"data:image/jpeg;base64,{image_b64}",
                        folder="violations",
                        public_id=f"{camera_id}_{timestamp.replace(':', '-').replace('.', '-')}",
                        overwrite=True,
                        resource_type="image"
                    )
                    image_url = result.get("secure_url", "")
                    logger.info(f"Uploaded violation image to Cloudinary: {image_url}")
                elif is_cloudinary_paused():
                    logger.info("Cloudinary uploads paused — skipping image upload")
                else:
                    logger.warning("Cloudinary not configured, saving image locally")
            except Exception as e:
                logger.error(f"Cloudinary image upload failed: {e}")

            if not image_url and image_bytes:
                try:
                    img_dir = os.path.join(os.path.dirname(__file__), 'static', 'violations')
                    os.makedirs(img_dir, exist_ok=True)
                    safe_ts = timestamp.replace(':', '-').replace('.', '-')
                    img_filename = f"{camera_id}_{safe_ts}.jpg"
                    img_filepath = os.path.join(img_dir, img_filename)
                    with open(img_filepath, 'wb') as f:
                        f.write(image_bytes)
                    image_url = f"/static/violations/{img_filename}"
                    logger.info(f"Saved violation image locally: {image_url}")
                except Exception as e:
                    logger.error(f"Local image save failed: {e}")

        video_b64 = data.get("video")
        if video_b64:
            try:
                import tempfile
                video_bytes = base64.b64decode(video_b64)
                with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
                    tmp.write(video_bytes)
                    tmp_path = tmp.name

                cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
                if cloud_name and not is_cloudinary_paused():
                    result = cloudinary.uploader.upload_large(
                        tmp_path,
                        folder="violations/videos",
                        public_id=f"{camera_id}_{timestamp.replace(':', '-').replace('.', '-')}_clip",
                        overwrite=True,
                        resource_type="video",
                        chunk_size=6000000,
                        format="mp4",
                        eager=[{"format": "mp4", "video_codec": "h264"}],
                        eager_async=True
                    )
                    video_url = result.get("secure_url", "")
                    logger.info(f"Uploaded violation video to Cloudinary: {video_url}")
                elif is_cloudinary_paused():
                    logger.info("Cloudinary uploads paused — skipping video upload")
                else:
                    logger.warning("Cloudinary not configured, saving video locally")

                if not video_url:
                    vid_dir = os.path.join(os.path.dirname(__file__), 'static', 'violations', 'videos')
                    os.makedirs(vid_dir, exist_ok=True)
                    safe_ts = timestamp.replace(':', '-').replace('.', '-')
                    vid_filename = f"{camera_id}_{safe_ts}_5min.mp4"
                    vid_filepath = os.path.join(vid_dir, vid_filename)
                    os.rename(tmp_path, vid_filepath)
                    video_url = f"/static/violations/videos/{vid_filename}"
                    logger.info(f"Saved violation video locally: {video_url}")
                    tmp_path = None

                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
            except Exception as e:
                logger.error(f"Video upload/save failed: {e}")

        img_path = image_url or ''

        # --- OCR plate extraction (Railway-side) ---
        plate_number = data.get('plate_number')
        plate_confidence = data.get('plate_confidence', 0.0)
        bbox = data.get('bbox')

        if plate_number:
            from ocr_module import format_ph_plate
            validated = format_ph_plate(plate_number)
            if not validated:
                logger.info(f"Rejected invalid plate from Pi: '{plate_number}'")
                plate_number = None
                plate_confidence = 0.0
            else:
                plate_number = validated

        if not plate_number and image_bytes and bbox:
            try:
                import cv2
                import numpy as np
                nparr = np.frombuffer(image_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if frame is not None:
                    from ocr_module import extract_plate
                    x1, y1, x2, y2 = bbox
                    plate_text, plate_conf = extract_plate(frame, bbox=(x1, y1, x2, y2))
                    if plate_text:
                        plate_number = plate_text
                        plate_confidence = plate_conf
                        logger.info(f"OCR plate detected on Railway: {plate_text} (conf={plate_conf:.2f})")
            except ImportError:
                logger.info("OCR dependencies not available on Railway, skipping plate detection")
            except Exception as ocr_err:
                logger.warning(f"Railway OCR failed: {ocr_err}")

        # Insert into PostgreSQL
        violation_id = None
        try:
            conn = get_connection()
            cur = conn.cursor()
            confidence_score = data.get('confidence_score', 0.0)
            duration_minutes = data.get('duration_minutes', 0.0)
            barangay = data.get('barangay', None)
            enforced = data.get('enforced', False)

            cur.execute("""
                INSERT INTO violations (camera, tracker_id, label, timestamp, image_path, image_url, video_url, confidence_score, duration_minutes, barangay, enforced)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (camera_id, tracker_id, label, timestamp, img_path, image_url, video_url, confidence_score, duration_minutes, barangay or 'Brgy. Kanluran', enforced))
            violation_id = cur.fetchone()[0]
            conn.commit()

            if plate_number and violation_id:
                cur.execute("""
                    INSERT INTO plate_records (violation_id, plate_number, confidence, plate_image_path, camera, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (violation_id, plate_number, plate_confidence,
                      '', camera_id, timestamp))
                conn.commit()
                logger.info(f"Saved plate record: {plate_number} for violation {violation_id}")

            cur.close()
            return_connection(conn)
            logger.info("Inserted violation event into PostgreSQL.")
        except Exception as e:
            logger.error(f"Failed to insert violation event into PostgreSQL: {e}")

        # Feature 17: Send alerts
        try:
            send_violation_alert(
                violation_data={
                    "camera": camera_id,
                    "tracker_id": tracker_id,
                    "label": label,
                    "duration_minutes": duration_minutes,
                    "timestamp": timestamp,
                    "plate_number": plate_number or '',
                    "confidence_score": confidence_score,
                },
                image_bytes=image_bytes
            )
        except Exception as e:
            logger.warning(f"Alert sending failed: {e}")

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Upload event failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500




@app.route('/api/image_from_db')
@login_required
def api_image_from_db():
    image_path = request.args.get("image_path")
    if not image_path:
        return jsonify({"success": False, "error": "Missing image_path"}), 400
    if image_path.startswith("http"):
        from urllib.parse import urlparse
        parsed = urlparse(image_path)
        if parsed.hostname and 'cloudinary' in parsed.hostname:
            return redirect(image_path)
        return jsonify({"success": False, "error": "Invalid URL"}), 400
    safe_path = os.path.normpath(image_path)
    if ".." in safe_path or os.path.isabs(safe_path):
        return jsonify({"success": False, "error": "Invalid image_path"}), 400
    base_dir = os.path.realpath(os.path.dirname(__file__))
    abs_path = os.path.realpath(os.path.join(base_dir, safe_path))
    if not abs_path.startswith(base_dir):
        return jsonify({"success": False, "error": "Invalid image_path"}), 400
    if not os.path.exists(abs_path):
        return jsonify({"success": False, "error": "Image not found"}), 404
    return send_file(abs_path, mimetype="image/jpeg")

@app.route('/api/proxy_image')
@login_required
def api_proxy_image():
    try:
        image_path = request.args.get("image_path")
        if not image_path:
            return jsonify({"success": False, "error": "Missing image_path"}), 400
        cached = get_cached_image(image_path)
        if cached:
            return Response(cached, mimetype="image/jpeg")
        pi_base = get_pi_base()
        url = f"{pi_base}/api/get_image"
        resp = requests.get(url, params={"image_path": image_path}, timeout=10)
        if resp.status_code == 200:
            set_cached_image(image_path, resp.content)
            return Response(resp.content, mimetype="image/jpeg")
        else:
            return Response("Image not found", 404)
    except Exception as e:
        logger.error(f"Proxy image error: {e}")
        return Response("Image unavailable", 502)

@app.route('/api/events')
@login_required
def api_events():
    try:
        pi_base = get_pi_base()
        url = f"{pi_base}/api/list_images"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return jsonify([])
        image_files = resp.json()

        def date_label_from_path(p):
            try:
                parts = p.replace("\\", "/").split("/")
                if len(parts) >= 3:
                    return parts[-2]
                return os.path.dirname(p)
            except Exception:
                return ""

        if request.args.get("dates_only"):
            labels = sorted({date_label_from_path(p) for p in image_files}, reverse=True)
            return jsonify(labels)

        req_date = request.args.get("date")
        events = []
        for rel_path in image_files:
            label = date_label_from_path(rel_path)
            if req_date and label != req_date:
                continue
            fname = os.path.basename(rel_path)
            match = re.match(r"([A-Za-z0-9_]+)[-_](\d{2}_\d{2}_\d{2})", fname)
            camera_id = match.group(1) if match else ""
            timestamp = label
            encoded = urllib.parse.quote_plus(rel_path)
            events.append({
                "camera_id": camera_id,
                "timestamp": timestamp,
                "image_url": "",
                "meta": {},
                "proxy_image_url": f"/api/proxy_image?image_path={encoded}",
                "local_image_url": ""
            })
        events.sort(key=lambda ev: ev["timestamp"], reverse=True)
        return jsonify(events)
    except Exception as e:
        logger.error(f"Failed to fetch events from Pi: {e}")
        return jsonify([])

# Optional: Serve static files directly
@app.route('/static/<path:filename>')
def static_files(filename):
    safe_name = os.path.normpath(filename)
    if ".." in safe_name or os.path.isabs(safe_name):
        return "Forbidden", 403
    base_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "static"))
    abs_path = os.path.realpath(os.path.join(base_dir, safe_name))
    if not abs_path.startswith(base_dir):
        return "Forbidden", 403
    if not os.path.exists(abs_path):
        return "Not found", 404
    import mimetypes
    mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    return send_file(abs_path, mimetype=mimetype)

# --------------------------------------------------
# Camera Status
# --------------------------------------------------
@app.route('/api/camera_status', methods=['GET','OPTIONS'])
@login_required
def api_camera_status():
    if request.method == 'OPTIONS':
        return jsonify({}), 200, cors_headers()
    try:
        pi_base = get_pi_base()
        url = f"{pi_base}/api/camera_status"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        return jsonify({
            "Camera_1": {"reconnecting": data.get("Camera_1", {}).get("reconnecting", False),
                         "online": data.get("Camera_1", {}).get("online", False)},
            "Camera_2": {"reconnecting": data.get("Camera_2", {}).get("reconnecting", False),
                         "online": data.get("Camera_2", {}).get("online", False)}
        })
    except Exception:
        return jsonify({
            "Camera_1": {"reconnecting": True, "online": False},
            "Camera_2": {"reconnecting": True, "online": False}
        })

# --------------------------------------------------
# Settings
# --------------------------------------------------
@app.route('/api/settings', methods=['GET', 'POST'])
@login_required
def api_settings():
    if request.method == 'POST':
        try:
            data = request.get_json(force=True)
            if not isinstance(data, dict):
                return jsonify({"error": "Invalid JSON"}), 400
            logger.info(f"Received settings update: {data}")
            current = get_all_settings()
            if "PARKING_ZONES" in data:
                current_zones = current.get("PARKING_ZONES", {})
                if isinstance(current_zones, str):
                    current_zones = json.loads(current_zones)
                for cam, val in data["PARKING_ZONES"].items():
                    if val is None:
                        current_zones.pop(cam, None)
                    else:
                        current_zones[cam] = val
                data["PARKING_ZONES"] = current_zones
            save_settings(data)
            logger.info(f"Settings saved to database: {data}")
            try:
                pi_base = get_pi_base()
                url = f"{pi_base}/api/settings"
                resp = requests.post(url, json=data, timeout=10)
                logger.info(f"Forwarded to Pi, status: {resp.status_code}")
            except Exception as pi_err:
                logger.warning(f"Could not forward to Pi: {pi_err}")
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    try:
        try:
            pi_base = get_pi_base()
            url = f"{pi_base}/api/settings"
            resp = requests.get(url, timeout=5)
            if resp.ok and resp.text:
                pi_settings = resp.json()
                sync_data = {}
                if "VIOLATION_TIME_THRESHOLD" in pi_settings:
                    sync_data["VIOLATION_TIME_THRESHOLD"] = pi_settings["VIOLATION_TIME_THRESHOLD"]
                if "REPEAT_CAPTURE_INTERVAL" in pi_settings:
                    sync_data["REPEAT_CAPTURE_INTERVAL"] = pi_settings["REPEAT_CAPTURE_INTERVAL"]
                if "PARKING_ZONES" in pi_settings:
                    sync_data["PARKING_ZONES"] = pi_settings["PARKING_ZONES"]
                if sync_data:
                    save_settings(sync_data)
                return jsonify(pi_settings)
        except Exception as pi_err:
            logger.warning(f"Could not fetch from Pi: {pi_err}, using database")
        db_settings = get_all_settings()
        return jsonify(db_settings)
    except Exception as e:
        logger.error(f"Failed to get settings: {e}")
        return jsonify({"success": False, "error": str(e)}), 502

@app.route('/api/db_settings', methods=['GET', 'POST'])
@login_or_api_key
def api_db_settings():
    if request.method == 'POST':
        try:
            data = request.get_json(force=True)
            if not isinstance(data, dict):
                return jsonify({"error": "Invalid JSON"}), 400
            if "PARKING_ZONES" in data:
                current = get_all_settings()
                current_zones = current.get("PARKING_ZONES", {})
                for cam, val in data["PARKING_ZONES"].items():
                    if val is None:
                        current_zones.pop(cam, None)
                    else:
                        current_zones[cam] = val
                data["PARKING_ZONES"] = current_zones
            save_settings(data)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    return jsonify(get_all_settings())

@app.route('/api/db_violations_count')
@login_required
def api_db_violations_count():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT (camera, tracker_id)) FROM violations;")
        count = cur.fetchone()[0] or 0
        cur.close()
        return_connection(conn)
        return jsonify({"count": int(count)})
    except Exception as e:
        logger.error(f"Failed to get violations count: {e}")
        return jsonify({"count": 0}), 500

# --------------------------------------------------
# Error Handling
# --------------------------------------------------
@app.errorhandler(Exception)
def handle_exception(e):
    logger.error("Unhandled Exception: %s\n%s", e, traceback.format_exc())
    logger.error(f"Request: {request.method} {request.path} | IP: {request.remote_addr}")
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "error": str(e)}), 500
    return make_response("Internal Server Error", 500)

@app.errorhandler(404)
def not_found(e):
    logger.warning(f"404 Not Found: {request.method} {request.path} | IP: {request.remote_addr}")
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "error": "Not Found"}), 404
    return render_template_string("<h1>404 Not Found</h1><p>The requested URL was not found on the server.</p>"), 404

# --------------------------------------------------
# List Images
# --------------------------------------------------
@app.route('/api/list_images')
@login_required
def api_list_images():
    try:
        pi_base = get_pi_base()
        url = f"{pi_base}/api/list_images"
        resp = requests.get(url, timeout=10)
        return Response(resp.content, resp.status_code, resp.headers.items())
    except Exception as e:
        logger.error(f"Proxy list_images error: {e}")
        return jsonify([])

@app.route('/api/recent_incidents')
@login_required
def api_recent_incidents():
    """Return recent incidents for notification dropdown."""
    limit = min(30, int(request.args.get('limit', 20)))
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            # Get recent incidents with plate info
            cur.execute("""
                SELECT v.id, v.camera, v.label, v.timestamp, v.duration_minutes,
                       v.confidence_score, COALESCE(v.review_status, 'for_review'),
                       p.plate_number
                FROM violations v
                LEFT JOIN plate_records p ON p.violation_id = v.id
                ORDER BY v.timestamp DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            # Pending count
            cur.execute("SELECT COUNT(*) FROM violations WHERE COALESCE(review_status, 'for_review') = 'for_review'")
            pending = cur.fetchone()[0]
            cur.close()
            incidents = [{
                "id": r[0], "camera": r[1], "label": r[2],
                "timestamp": r[3].isoformat() if r[3] else None,
                "duration_minutes": r[4], "confidence_score": r[5],
                "review_status": r[6], "plate_number": r[7]
            } for r in rows]
            return jsonify({"incidents": incidents, "pending_count": pending})
        finally:
            return_connection(conn)
    except Exception as e:
        logger.error(f"Recent incidents error: {e}")
        return jsonify({"incidents": [], "pending_count": 0})

@app.route('/api/cameras', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def api_cameras():
    if request.method == 'POST':
        data = request.get_json(force=True)
        if not isinstance(data, dict) or not data.get('id'):
            return jsonify({"success": False, "error": "Camera ID required"}), 400
        cameras = get_config_value("CAMERAS", [])
        if not isinstance(cameras, list):
            cameras = []
        if any(c['id'] == data['id'] for c in cameras):
            return jsonify({"success": False, "error": "Camera ID already exists"}), 400
        cameras.append({
            "id": data['id'],
            "name": data.get('name', ''),
            "location": data.get('location', ''),
            "url": data.get('url', ''),
            "status": data.get('status', 'active')
        })
        set_config_value("CAMERAS", cameras)
        return jsonify({"success": True})
    cameras = get_config_value("CAMERAS", [])
    if not isinstance(cameras, list):
        cameras = []
    if not cameras:
        cameras = [
            {"id": "Camera_1", "name": "Camera 1", "location": "Brgy. Kanluran", "url": "rtsp://192.168.8.2:554/stream", "status": "active"},
            {"id": "Camera_2", "name": "Camera 2", "location": "Brgy. Kanluran", "url": "rtsp://192.168.8.199:554/stream", "status": "active"},
        ]
        set_config_value("CAMERAS", cameras)
    return jsonify(cameras)

@app.route('/api/cameras/<camera_id>', methods=['PUT', 'DELETE'])
@login_or_api_key
def api_modify_camera(camera_id):
    cameras = get_config_value("CAMERAS", [])
    if not isinstance(cameras, list):
        cameras = []
    if request.method == 'DELETE':
        if camera_id == 'Camera_1':
            return jsonify({"success": False, "error": "Camera 1 (MAIN) cannot be removed"}), 403
        cameras = [c for c in cameras if c.get('id') != camera_id]
        set_config_value("CAMERAS", cameras)
        return jsonify({"success": True})
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Invalid JSON"}), 400
    found = False
    for c in cameras:
        if c.get('id') == camera_id:
            if data.get('name') is not None: c['name'] = data['name']
            if data.get('location') is not None: c['location'] = data['location']
            if data.get('url') is not None: c['url'] = data['url']
            if data.get('status') is not None: c['status'] = data['status']
            found = True
            break
    if not found:
        return jsonify({"success": False, "error": "Camera not found"}), 404
    set_config_value("CAMERAS", cameras)
    return jsonify({"success": True})

@app.route('/api/pending_review_count')
@login_required
def api_pending_review_count():
    """Return count of violations pending admin review."""
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM violations WHERE COALESCE(review_status, 'for_review') = 'for_review'")
            count = cur.fetchone()[0]
            cur.close()
            return jsonify({"count": count})
        finally:
            return_connection(conn)
    except Exception as e:
        return jsonify({"count": 0})

@app.route('/api/model_performance')
@login_required
def api_model_performance():
    """Return YOLOv8 detection and OCR performance metrics."""
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            # YOLOv8 detection stats from violations table
            cur.execute("""
                SELECT COUNT(*) as total,
                       AVG(confidence_score) as avg_conf,
                       COUNT(CASE WHEN confidence_score >= 0.7 THEN 1 END) as high_conf,
                       COUNT(CASE WHEN confidence_score < 0.5 THEN 1 END) as low_conf
                FROM violations
            """)
            yolo = cur.fetchone()

            # OCR stats from plate_records table
            cur.execute("""
                SELECT COUNT(*) as total,
                       AVG(confidence) as avg_conf,
                       COUNT(DISTINCT plate_number) as unique_plates
                FROM plate_records
                WHERE plate_number IS NOT NULL AND plate_number != ''
            """)
            ocr = cur.fetchone()
            cur.close()

            return jsonify({
                "yolo_total": yolo[0] or 0,
                "yolo_avg_confidence": float(yolo[1] or 0),
                "yolo_high_conf": yolo[2] or 0,
                "yolo_low_conf": yolo[3] or 0,
                "ocr_total": ocr[0] or 0,
                "ocr_avg_confidence": float(ocr[1] or 0),
                "ocr_unique_plates": ocr[2] or 0,
            })
        finally:
            return_connection(conn)
    except Exception as e:
        logger.error(f"Model performance API error: {e}")
        return jsonify({"yolo_total": 0, "yolo_avg_confidence": 0, "yolo_high_conf": 0, "yolo_low_conf": 0, "ocr_total": 0, "ocr_avg_confidence": 0, "ocr_unique_plates": 0})

@app.route('/api/calendar')
@login_required
def api_calendar():
    """Return incident counts per day for a given month (YYYY-MM)."""
    month = request.args.get('month', '')
    if not month:
        from datetime import datetime as dt
        month = dt.now().strftime('%Y-%m')
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT DATE(timestamp) as day, COUNT(DISTINCT (camera, tracker_id)) as cnt
                FROM violations
                WHERE TO_CHAR(timestamp, 'YYYY-MM') = %s
                GROUP BY DATE(timestamp)
                ORDER BY day
            """, (month,))
            rows = cur.fetchall()
            cur.close()
            result = {}
            for day, cnt in rows:
                result[str(day)] = {"count": cnt}
            return jsonify(result)
        finally:
            return_connection(conn)
    except Exception as e:
        logger.error(f"Calendar API error: {e}")
        return jsonify({})

@app.route('/api/calendar/details')
@login_required
def api_calendar_details():
    """Return incident details for a specific date."""
    date = request.args.get('date', '')
    if not date:
        return jsonify({"total": 0, "incidents": []})
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT v.id, v.camera, v.tracker_id, v.label, v.timestamp,
                       COALESCE(NULLIF(v.image_url, ''), v.image_path, '') as img,
                       v.confidence_score, v.duration_minutes,
                       p.plate_number, COALESCE(v.video_url, '') as vid
                FROM violations v
                LEFT JOIN plate_records p ON p.violation_id = v.id
                WHERE DATE(v.timestamp) = %s
                ORDER BY v.timestamp DESC
            """, (date,))
            rows = cur.fetchall()
            cur.close()
            incidents = []
            for r in rows:
                img_url = r[5] or ''
                incidents.append({
                    "id": r[0], "camera": r[1], "tracker_id": r[2],
                    "label": r[3], "timestamp": r[4].isoformat() if r[4] else None,
                    "image_url": img_url,
                    "video_url": r[9] or '',
                    "confidence": r[6], "duration_minutes": r[7],
                    "plate_number": r[8]
                })
            return jsonify({"total": len(incidents), "incidents": incidents})
        finally:
            return_connection(conn)
    except Exception as e:
        logger.error(f"Calendar details API error: {e}")
        return jsonify({"total": 0, "incidents": [], "error": str(e)})

@app.route('/api/violations_list')
@login_required
def api_violations_list():
    try:
        page = max(1, int(request.args.get('page', 1)))
        per_page = min(100, max(1, int(request.args.get('per_page', 30))))
        filters = {}
        if request.args.get('label'):
            filters['label'] = request.args['label']
        if request.args.get('hour_start') is not None and request.args.get('hour_end') is not None:
            filters['hour_start'] = request.args['hour_start']
            filters['hour_end'] = request.args['hour_end']
        if request.args.get('day_of_week'):
            filters['day_of_week'] = request.args['day_of_week']
        if request.args.get('today'):
            filters['today'] = request.args['today']
        if request.args.get('camera'):
            filters['camera'] = request.args['camera']
        if request.args.get('duration_sort'):
            filters['duration_sort'] = request.args['duration_sort']
        if request.args.get('review_status'):
            filters['review_status'] = request.args['review_status']
        if request.args.get('date_start'):
            filters['date_start'] = request.args['date_start']
        if request.args.get('date_end'):
            filters['date_end'] = request.args['date_end']
        if request.args.get('date'):
            filters['date'] = request.args['date']
        result = list_violations(page=page, per_page=per_page, filters=filters)
        resp = jsonify(result)
        resp.headers.update(cors_headers())
        resp.headers['Cache-Control'] = 'private, max-age=15'
        return resp
    except Exception as e:
        logger.error(f"Failed to fetch violations: {e}")
        resp = jsonify({'error': 'failed to fetch violations'})
        resp.headers.update(cors_headers())
        return resp, 500

@app.route('/api/review_incident', methods=['POST'])
@login_required
@role_required('operator')
def api_review_incident():
    """Admin reviews an incident: approved, rejected, or needs_investigation."""
    try:
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON"}), 400
        incident_id = data.get('id')
        status = data.get('status')
        notes = data.get('notes', '')
        if not incident_id or status not in ('approved', 'rejected', 'needs_investigation', 'for_review'):
            return jsonify({"success": False, "error": "Invalid id or status"}), 400
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE violations SET review_status = %s, review_notes = %s WHERE id = %s
            """, (status, notes, incident_id))
            conn.commit()
            cur.close()
        finally:
            return_connection(conn)
        log_activity(session['user']['id'], 'review_incident', f"ID {incident_id} -> {status}")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Review incident error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/mark_enforced', methods=['POST'])
@login_required
@role_required('operator')
def api_mark_enforced():
    try:
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON"}), 400
        ids = data.get('ids') if data else None
        if ids is None:
            return jsonify({'success': False, 'error': 'missing ids'}), 400
        if isinstance(ids, int):
            ids = [ids]
        if not isinstance(ids, (list, tuple)) or not ids:
            return jsonify({'success': False, 'error': 'ids must be a non-empty list'}), 400
        updated = mark_enforced(ids)
        log_activity(session['user']['id'], 'mark_enforced', f"Marked {len(ids)} violations enforced")
        resp = jsonify({'success': True, 'updated': updated})
        resp.headers.update(cors_headers())
        return resp
    except Exception as e:
        logger.error(f"Failed to mark enforced: {e}")
        resp = jsonify({'success': False, 'error': str(e)})
        resp.headers.update(cors_headers())
        return resp, 500

# --------------------------------------------------
# Main
# --------------------------------------------------
if __name__ == "__main__":
    try:
        cf_proc, public_url = start_cloudflared(DEFAULT_PORT)
        app.config["PUBLIC_URL"] = public_url
    except Exception as e:
        print("Failed to start Cloudflare Tunnel:", e)
        app.config["PUBLIC_URL"] = ""

    app.run(host='0.0.0.0', port=DEFAULT_PORT, threaded=True)
