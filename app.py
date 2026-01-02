import os
import re
import json
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
from sqlalchemy import create_engine, Column, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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

# --------------------------------------------------
# Database Setup
# --------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set")

engine = create_engine(DATABASE_URL)
Base = declarative_base()
Session = sessionmaker(bind=engine)

class Config(Base):
    __tablename__ = 'config'
    key = Column(String, primary_key=True)
    value = Column(Text)

Base.metadata.create_all(engine)

def get_config_value(key, default=None):
    session = Session()
    try:
        row = session.query(Config).filter_by(key=key).first()
        return json.loads(row.value) if row else default
    finally:
        session.close()

def set_config_value(key, value):
    session = Session()
    try:
        row = session.query(Config).filter_by(key=key).first()
        if row:
            row.value = json.dumps(value)
        else:
            session.add(Config(key=key, value=json.dumps(value)))
        session.commit()
    finally:
        session.close()

def get_current_settings():
    return {
        "VIOLATION_TIME_THRESHOLD": get_config_value("VIOLATION_TIME_THRESHOLD", 10),
        "REPEAT_CAPTURE_INTERVAL": get_config_value("REPEAT_CAPTURE_INTERVAL", 60),
        "PARKING_ZONES": get_config_value("PARKING_ZONES", {})
    }

def update_config(new_settings):
    if "VIOLATION_TIME_THRESHOLD" in new_settings:
        set_config_value("VIOLATION_TIME_THRESHOLD", new_settings["VIOLATION_TIME_THRESHOLD"])
    if "REPEAT_CAPTURE_INTERVAL" in new_settings:
        set_config_value("REPEAT_CAPTURE_INTERVAL", new_settings["REPEAT_CAPTURE_INTERVAL"])
    if "PARKING_ZONES" in new_settings:
        current_zones = get_config_value("PARKING_ZONES", {})
        updated_zones = current_zones.copy()
        for cam, val in new_settings["PARKING_ZONES"].items():
            if val is None:
                updated_zones.pop(cam, None)
            else:
                updated_zones[cam] = val
        set_config_value("PARKING_ZONES", updated_zones)

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

@app.after_request
def after_request_func(response):
    response.headers.update(cors_headers())
    return response

def get_pi_base():
    if not PI_PUBLIC_URL:
        raise RuntimeError("Pi public URL not set")
    return PI_PUBLIC_URL.rstrip("/")

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
        data = request.get_json(force=True)
        camera_id = data.get("camera_id")
        timestamp = data.get("timestamp", datetime.utcnow().isoformat())
        image_b64 = data.get("image")
        meta = data.get("meta", {})

        if not os.path.exists(STATIC_EVENTS_DIR):
            os.makedirs(STATIC_EVENTS_DIR)
        fname = EVENT_IMAGE_FORMAT.format(
            camera_id=camera_id,
            timestamp=EVENT_IMAGE_TIMESTAMP_REPL(timestamp)
        )
        img_path = os.path.join(STATIC_EVENTS_DIR, fname)

        with open(img_path, "wb") as f:
            f.write(base64.b64decode(image_b64))

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

@app.route('/api/events')
def api_events():
    return jsonify(EVENTS)

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
# Error Handling
# --------------------------------------------------
@app.errorhandler(Exception)
def handle_exception(e):
    logger.error("Unhandled Exception: %s\n%s", e, traceback.format_exc())
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "error": str(e)}), 500
    return make_response("Internal Server Error", 500)

@app.errorhandler(404)
def not_found(e):
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
