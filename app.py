import os
import re
import logging
import traceback
import subprocess
from datetime import datetime
from flask import (
    Flask, request, jsonify, Response,
    render_template, render_template_string,
    stream_with_context, make_response
)
import requests
import base64
import config
import psycopg2

# --------------------------------------------------
# Logging
# --------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ParkingApp")

# --------------------------------------------------
# Flask App
# --------------------------------------------------
app = Flask(__name__)

# --------------------------------------------------
# Environment / Defaults
# --------------------------------------------------
DEFAULT_PORT = int(os.environ.get("PORT", 5000))
DEFAULT_RASPI_IP = os.environ.get("RASPI_IP", "192.168.18.32")
DEFAULT_RASPI_PORT = os.environ.get("RASPI_PORT", "5000")
DEFAULT_RAILWAY_API_URL = os.environ.get(
    "RAILWAY_API_URL", "https://illegal-parking-detection-flask.up.railway.app"
)
CLOUDFLARE_TUNNEL_CMD = ["cloudflared", "tunnel", "--url", f"http://localhost:{DEFAULT_PORT}"]
STATIC_EVENTS_DIR = "static/events"
EVENT_IMAGE_FORMAT = "{camera_id}_{timestamp}.jpg"
EVENT_IMAGE_TIMESTAMP_REPL = lambda ts: ts.replace(":", "-").replace(".", "-")

POSTGRES_URL = os.environ.get(
    "POSTGRES_URL",
    "postgresql://postgres:ltymHUMvXphOojaHeJRJGnyQUfWsghwq@mainline.proxy.rlwy.net:42362/railway"
)

# --------------------------------------------------
# Config helpers (local config.py)
# --------------------------------------------------
def get_current_settings():
    return {
        "VIOLATION_TIME_THRESHOLD": getattr(config, "VIOLATION_TIME_THRESHOLD", 10),
        "REPEAT_CAPTURE_INTERVAL": getattr(config, "REPEAT_CAPTURE_INTERVAL", 60),
        "PARKING_ZONES": getattr(config, "PARKING_ZONES", {})
    }

def update_config(new_settings):
    import importlib
    import json as pyjson
    import re
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
PI_PUBLIC_URL = ""
PI_URL_NOT_SET_LOGGED = False

@app.route('/api/set_pi_url', methods=['POST'])
def set_pi_url():
    global PI_PUBLIC_URL, PI_URL_NOT_SET_LOGGED
    data = request.get_json(force=True)
    new_url = data.get("public_url", "")
    if new_url and new_url != PI_PUBLIC_URL:
        logger.info(f"Received new Pi public URL: {new_url} (old: {PI_PUBLIC_URL})")
        PI_PUBLIC_URL = new_url
    PI_URL_NOT_SET_LOGGED = False
    return jsonify({"success": True, "public_url": PI_PUBLIC_URL})

@app.route('/api/get_pi_url')
def get_pi_url():
    resp = jsonify({"public_url": PI_PUBLIC_URL})
    resp.headers.update(cors_headers())
    return resp

# --------------------------------------------------
# Helper Functions
# --------------------------------------------------
def cors_headers():
    return {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type,Authorization'
    }

@app.before_request
def log_request_info():
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
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS violations (
                id SERIAL PRIMARY KEY,
                camera VARCHAR(32),
                tracker_id INTEGER,
                label VARCHAR(32),
                timestamp TIMESTAMP,
                image_path TEXT
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Ensured 'violations' table exists in PostgreSQL.")
    except Exception as e:
        logger.error(f"Failed to ensure violations table: {e}")

# --------------------------------------------------
# Routes – UI
# --------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html', public_url=PI_PUBLIC_URL or "")

@app.route('/settings')
def settings_page():
    return render_template('settings.html')

@app.route('/violations')
def violations_page():
    return render_template('violations.html')

@app.route('/ping')
def ping():
    return "pong"

# --------------------------------------------------
# Routes – Proxy to Pi
# --------------------------------------------------
@app.route('/api/<path:path>', methods=['GET','POST','OPTIONS'])
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
def proxy_video_feed_c1():
    return proxy_video_feed("video_feed_c1")

@app.route('/video_feed_c2')
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
# Events
# --------------------------------------------------
EVENTS = []

@app.route('/api/upload_event', methods=['POST'])
def upload_event():
    try:
        ensure_violations_table()
        data = request.get_json(force=True)
        camera_id = data.get("camera_id")
        tracker_id = data.get("tracker_id")
        label = data.get("label")
        timestamp = data.get("timestamp", datetime.utcnow().isoformat())
        image_b64 = data.get("image")
        meta = data.get("meta", {})

        logger.info(f"Received violation event: camera_id={camera_id}, timestamp={timestamp}, meta={meta}")

        # NOTE: The image is saved only on the Raspberry Pi, not on the Railway server.
        # The image_path stored in the database refers to the path on the Pi, not on Railway.
        # This endpoint only receives and stores the image if the event is sent from the Pi directly.
        if not os.path.exists(STATIC_EVENTS_DIR):
            os.makedirs(STATIC_EVENTS_DIR)
        fname = EVENT_IMAGE_FORMAT.format(
            camera_id=camera_id,
            timestamp=EVENT_IMAGE_TIMESTAMP_REPL(timestamp)
        )
        img_path = os.path.join(STATIC_EVENTS_DIR, fname)

        with open(img_path, "wb") as f:
            f.write(base64.b64decode(image_b64))
        logger.info(f"Saved violation image to {img_path} (local to Railway server, not Pi)")

        # Insert into PostgreSQL
        try:
            conn = psycopg2.connect(POSTGRES_URL)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO violations (camera, tracker_id, label, timestamp, image_path)
                VALUES (%s, %s, %s, %s, %s)
            """, (camera_id, tracker_id, label, timestamp, img_path))
            conn.commit()
            cur.close()
            conn.close()
            logger.info("Inserted violation event into PostgreSQL. (Image path is local to Pi, not Railway)")
        except Exception as e:
            logger.error(f"Failed to insert violation event into PostgreSQL: {e}")

        EVENTS.append({
            "camera_id": camera_id,
            "timestamp": timestamp,
            "image_url": f"/{STATIC_EVENTS_DIR}/{fname}",
            "meta": meta
        })
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Upload event failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/image_from_db')
def api_image_from_db():
    """
    Serve an image stored in Railway's static/events directory by its relative path.
    Usage: /api/image_from_db?image_path=static/events/Camera_2_2026-01-03T11-47-00-155665.jpg
    """
    image_path = request.args.get("image_path")
    if not image_path:
        return jsonify({"success": False, "error": "Missing image_path"}), 400
    safe_path = os.path.normpath(image_path)
    if ".." in safe_path or safe_path.startswith("/"):
        return jsonify({"success": False, "error": "Invalid image_path"}), 400
    abs_path = os.path.join(os.path.dirname(__file__), safe_path)
    if not os.path.exists(abs_path):
        return jsonify({"success": False, "error": "Image not found"}), 404
    return Response(open(abs_path, "rb").read(), mimetype="image/jpeg")

@app.route('/api/proxy_image')
def api_proxy_image():
    """
    Proxy an image from the Pi given its image_path.
    Usage: /api/proxy_image?image_path=...
    """
    try:
        image_path = request.args.get("image_path")
        if not image_path:
            return jsonify({"success": False, "error": "Missing image_path"}), 400
        pi_base = get_pi_base()
        url = f"{pi_base}/api/get_image"
        resp = requests.get(url, params={"image_path": image_path}, timeout=10)
        if resp.status_code == 200:
            return Response(resp.content, mimetype="image/jpeg")
        else:
            return Response("Image not found", 404)
    except Exception as e:
        logger.error(f"Proxy image error: {e}")
        return Response("Image unavailable", 502)

@app.route('/api/events')
def api_events():
    # Add proxy_image_url for each event (from Pi) and local_image_url for Railway
    events_with_proxy = []
    for ev in EVENTS:
        proxy_url = ""
        local_url = ""
        if "image_url" in ev:
            image_path = ev["image_url"].lstrip("/")
            proxy_url = f"/api/proxy_image?image_path={image_path}"
            local_url = f"/api/image_from_db?image_path={image_path}"
        ev_copy = dict(ev)
        ev_copy["proxy_image_url"] = proxy_url
        ev_copy["local_image_url"] = local_url
        events_with_proxy.append(ev_copy)
    return jsonify(events_with_proxy)

# Optional: Serve static files directly (for debugging or fallback)
@app.route('/static/<path:filename>')
def static_files(filename):
    abs_path = os.path.join(os.path.dirname(__file__), "static", filename)
    if not os.path.exists(abs_path):
        return "Not found", 404
    return Response(open(abs_path, "rb").read(), mimetype="image/jpeg")

# --------------------------------------------------
# Camera Status
# --------------------------------------------------
@app.route('/api/camera_status', methods=['GET','OPTIONS'])
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
@app.route('/api/settings', methods=['GET'])
def api_settings():
    try:
        pi_base = get_pi_base()
        url = f"{pi_base}/api/settings"
        resp = requests.get(url, timeout=10)
        # Forward the Pi's config settings to the frontend
        return jsonify(resp.json())
    except Exception as e:
        logger.error(f"Failed to fetch settings from Pi: {e}")
        return jsonify({"success": False, "error": str(e)}), 502

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
