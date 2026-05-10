import io
import base64
import numpy as np
import cv2
import threading
import time
import os
import logging
import subprocess
import re
import requests
from flask import Flask, request, jsonify, Response, render_template_string, render_template
import config
from app_detect import detect
import signal
from cloudlink import start_cloudflared
from db import insert_violation_event  # (You can remove this import if not used elsewhere)
from admin_config import get_fine_map
try:
    from ocr_module import extract_plate
except ImportError:
    extract_plate = None
try:
    from claude_vision import analyze_violation, is_available as vision_available
except ImportError:
    analyze_violation = None
    vision_available = lambda: False
from tamper_detect import TamperDetector
from health_monitor import HealthMonitor
try:
    from recorder import ContinuousRecorder, get_recording_dates, get_recording_segments
except ImportError:
    ContinuousRecorder = None
    get_recording_dates = lambda c=None: []
    get_recording_segments = lambda c, d: []
import sys

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Key'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response

# Vehicle classes from yolov8s (COCO IDs)
CLASS_NAMES = {0: "PERSON", 1: "BIKE", 2: "CAR", 3: "MOTORCYCLE", 5: "BUS", 7: "TRUCK"}

# CCTV AI classes (offset by +100 to avoid ID conflicts with COCO)
CCTV_AI_CLASS_NAMES = {
    100: "BASKET", 101: "BOTTLE", 102: "BOX", 103: "BUCKET",
    104: "CAN", 105: "CANAL", 106: "CARDBOARD", 107: "CHAIR",
    108: "CONTAINER", 109: "CRATE", 110: "CUP", 111: "FALLEN_TREE",
    112: "GARBAGE", 113: "GROCERY_BAG", 114: "LEAVES", 115: "OPEN_CANAL",
    116: "PAPER", 117: "PLASTIC", 118: "PLASTIC_BOTTLE", 119: "PLASTIC_CONTAINER",
    120: "PLASTIC_BAG", 121: "POT", 122: "ROCK", 123: "SACK",
    124: "TISSUE", 125: "TRASH", 126: "TRASH_CAN", 127: "VENDOR"
}

# Combined lookup for all class names
ALL_CLASS_NAMES = {**CLASS_NAMES, **CCTV_AI_CLASS_NAMES}

# --- CONFIGURABLE VARIABLES (edit these as needed) ---
CAM1_URL = getattr(config, "CAM1_URL", None)
CAM2_URL = getattr(config, "CAM2_URL", None)
MODEL_PATH = getattr(config, "MODEL_PATH", "")
SAVE_DIR = getattr(config, "SAVE_DIR", "static/violations")
DETECTION_THRESHOLD = getattr(config, "DETECTION_THRESHOLD", 0.3)
VIOLATION_TIME_THRESHOLD = getattr(config, "VIOLATION_TIME_THRESHOLD", 100)
REPEAT_CAPTURE_INTERVAL = getattr(config, "REPEAT_CAPTURE_INTERVAL", 60)
PARKING_ZONES = getattr(config, "PARKING_ZONES", {})
PORT = int(os.environ.get("PORT", 5000))
RAILWAY_API_URL = os.environ.get("RAILWAY_API_URL", "https://web-production-dbb23.up.railway.app")
PI_API_KEY = os.environ.get("PI_API_KEY", "dcgl-pi-secret-2026")
RAILWAY_HEADERS = {"X-API-Key": PI_API_KEY}

# Detection control flags (remote-controllable)
DETECTION_ENABLED = True
DETECTION_CONFIDENCE = DETECTION_THRESHOLD

# Resolution constants for zone selection (matching zone_selector.py)
ACTUAL_WIDTH = 1280
ACTUAL_HEIGHT = 720

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PiCameraServer")
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

# --------------------------------------------------
# Sync settings from Railway database
# --------------------------------------------------
def fetch_settings_from_railway():
    """Fetch settings from Railway database and update local config.py"""
    try:
        logger.info("Fetching settings from Railway database...")
        resp = requests.get(f"{RAILWAY_API_URL}/api/db_settings", headers=RAILWAY_HEADERS, timeout=10)
        if resp.ok:
            data = resp.json()
            logger.info(f"Received settings from Railway: {data}")
            
            # Update local config.py
            update_local_config(data)
            return True
        else:
            logger.warning(f"Failed to fetch settings from Railway: {resp.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Could not fetch settings from Railway: {e}")
        return False

def update_local_config(data):
    """Update local config.py with settings from database"""
    import importlib
    import json as pyjson
    import re as re_mod
    
    config_path = os.path.join(os.path.dirname(__file__), "config.py")
    
    try:
        with open(config_path, "r") as f:
            lines = f.readlines()

        def replace_line(key, value):
            pattern = re_mod.compile(rf"^{key}\s*=\s*.*$")
            found = False
            for i, line in enumerate(lines):
                if pattern.match(line):
                    if key == "PARKING_ZONES":
                        lines[i] = f"{key} = {pyjson.dumps(value)}\n"
                    else:
                        lines[i] = f"{key} = {value}\n"
                    found = True
                    break
            if not found:
                if key == "PARKING_ZONES":
                    lines.append(f"{key} = {pyjson.dumps(value)}\n")
                else:
                    lines.append(f"{key} = {value}\n")

        if "VIOLATION_TIME_THRESHOLD" in data:
            replace_line("VIOLATION_TIME_THRESHOLD", int(data["VIOLATION_TIME_THRESHOLD"]))
        if "REPEAT_CAPTURE_INTERVAL" in data:
            replace_line("REPEAT_CAPTURE_INTERVAL", int(data["REPEAT_CAPTURE_INTERVAL"]))
        if "PARKING_ZONES" in data and data["PARKING_ZONES"]:
            replace_line("PARKING_ZONES", data["PARKING_ZONES"])

        with open(config_path, "w") as f:
            f.writelines(lines)
        
        # Reload config module
        importlib.reload(config)
        
        # Update monitor zones if it exists
        if 'monitor' in globals() and hasattr(monitor, 'raw_zones'):
            monitor.raw_zones = getattr(config, "PARKING_ZONES", {})
        
        logger.info(f"Local config updated: VIOLATION_TIME_THRESHOLD={getattr(config, 'VIOLATION_TIME_THRESHOLD', 100)}, REPEAT_CAPTURE_INTERVAL={getattr(config, 'REPEAT_CAPTURE_INTERVAL', 60)}")
        return True
    except Exception as e:
        logger.error(f"Failed to update local config: {e}")
        return False

def send_heartbeat():
    """Send heartbeat to Railway so dashboard knows the Pi is alive."""
    try:
        uptime_sec = None
        cpu_temp = None
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_sec = int(float(f.read().split()[0]))
        except Exception:
            pass
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                cpu_temp = round(int(f.read().strip()) / 1000.0, 1)
        except Exception:
            pass
        payload = {}
        if uptime_sec is not None:
            payload["uptime"] = uptime_sec
        if cpu_temp is not None:
            payload["cpu_temp"] = cpu_temp
        requests.post(f"{RAILWAY_API_URL}/api/heartbeat", json=payload, headers=RAILWAY_HEADERS, timeout=5)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")

def periodic_settings_sync():
    """Periodically sync settings from Railway database, re-post tunnel URL, and send heartbeat"""
    while True:
        time.sleep(30)
        try:
            send_heartbeat()
        except Exception:
            pass
        try:
            fetch_settings_from_railway()
        except Exception as e:
            logger.warning(f"Periodic sync failed: {e}")
        try:
            public_url = app.config.get("PUBLIC_URL", "")
            if public_url:
                RAILWAY_API_URL = os.environ.get("RAILWAY_API_URL", "https://web-production-dbb23.up.railway.app")
                requests.post(f"{RAILWAY_API_URL}/api/set_pi_url", json={"public_url": public_url}, headers=RAILWAY_HEADERS, timeout=5)
        except Exception:
            pass

@app.route('/api/health', methods=['GET'])
def health():
    """Feature 16: Full system health endpoint."""
    cameras = {
        "Camera_1": getattr(config, "CAM1_URL", ""),
        "Camera_2": getattr(config, "CAM2_URL", ""),
    }
    return jsonify(health_mon.get_full_health(cameras))

# Feature 13: Recording/Playback routes
@app.route('/api/recording_dates')
def api_recording_dates():
    """List available recording dates per camera."""
    camera = request.args.get('camera')
    return jsonify(get_recording_dates(camera))

@app.route('/api/recording_segments')
def api_recording_segments():
    """List available segments for a camera and date."""
    camera = request.args.get('camera', 'Camera_1')
    date = request.args.get('date')
    if not date:
        return jsonify({"error": "date parameter required"}), 400
    return jsonify(get_recording_segments(camera, date))

@app.route('/api/playback')
def api_playback():
    """Serve a recorded video file."""
    camera = request.args.get('camera', 'Camera_1')
    date = request.args.get('date')
    time_str = request.args.get('time')
    if not date or not time_str:
        return jsonify({"error": "date and time parameters required"}), 400
    try:
        from recorder import RECORDING_DIR
    except ImportError:
        return jsonify({"error": "Recording module not available"}), 404
    filename = f"{time_str.replace(':', '-')}.mp4"
    filepath = os.path.join(RECORDING_DIR, camera, date, filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "Recording not found"}), 404
    def generate():
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk
    return Response(generate(), content_type='video/mp4')

@app.route('/api/get_pi_url')
def get_pi_url():
    public_url = app.config.get("PUBLIC_URL", "")
    return jsonify({"public_url": public_url})

@app.route('/api/camera_status')
def camera_status():
    # Dummy response for compatibility
    return jsonify({
        "Camera_1": {"reconnecting": False, "online": True},
        "Camera_2": {"reconnecting": False, "online": True}
    })

# ---- PART 3: Remote AI Detection Controls ----
@app.route('/api/detection_control', methods=['GET', 'POST'])
def api_detection_control():
    global DETECTION_ENABLED, DETECTION_CONFIDENCE
    if request.method == 'POST':
        data = request.get_json(force=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON"}), 400
        if 'enabled' in data:
            DETECTION_ENABLED = bool(data['enabled'])
            logger.info(f"Detection {'ENABLED' if DETECTION_ENABLED else 'DISABLED'} via remote control")
        if 'confidence' in data:
            DETECTION_CONFIDENCE = max(0.1, min(0.9, float(data['confidence'])))
            config.DETECTION_THRESHOLD = DETECTION_CONFIDENCE
            logger.info(f"Detection confidence set to {DETECTION_CONFIDENCE}")
        return jsonify({"success": True, "enabled": DETECTION_ENABLED, "confidence": DETECTION_CONFIDENCE})
    return jsonify({
        "enabled": DETECTION_ENABLED,
        "confidence": DETECTION_CONFIDENCE,
        "violation_threshold": getattr(config, 'VIOLATION_TIME_THRESHOLD', 100),
        "repeat_interval": getattr(config, 'REPEAT_CAPTURE_INTERVAL', 60),
    })

@app.route('/api/detection_snapshot')
def api_detection_snapshot():
    """Grab a single annotated frame showing what the AI currently sees."""
    try:
        with proc_lock:
            frame = latest_processed.get("Camera_1")
        if frame is None:
            return jsonify({"error": "No frame available"}), 404
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(buf.tobytes(), mimetype='image/jpeg')
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/restart_detection', methods=['POST'])
def api_restart_detection():
    """Restart the detection pipeline."""
    global DETECTION_ENABLED
    logger.info("Detection restart requested via remote control")
    DETECTION_ENABLED = False
    time.sleep(2)
    DETECTION_ENABLED = True
    logger.info("Detection restarted")
    return jsonify({"success": True, "message": "Detection pipeline restarted"})

@app.route('/detect', methods=['POST'])
def detect_endpoint():
    img = None
    if 'image' in request.files:
        img = decode_image(request.files['image'])
    else:
        data = request.get_json()
        img = decode_image(data.get('image', '')) if data else None

    if img is None:
        return jsonify({'success': False, 'error': 'No image provided'}), 400

    try:
        results = detect([img])
        if not results:
            raise RuntimeError("Detection returned no results")
        res = results[0]
        return jsonify({
            'success': True,
            'boxes': res.xyxy.tolist(),
            'confidences': res.conf.tolist(),
            'classes': res.cls.tolist()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def decode_image(data):
    if isinstance(data, str):
        img_bytes = base64.b64decode(data)
        img_array = np.frombuffer(img_bytes, np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    elif hasattr(data, 'read'):
        img_bytes = data.read()
        img_array = np.frombuffer(img_bytes, np.uint8)
        return cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    return None

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    import importlib
    import json as pyjson
    import re as re_mod
    config_path = os.path.join(os.path.dirname(__file__), "config.py")
    
    if request.method == 'POST':
        try:
            data = request.get_json(force=True)
            if not isinstance(data, dict):
                return jsonify({"error": "Invalid JSON"}), 400
            logger.info(f"Received settings update request: {data}")
            
            # Update local config
            update_local_config(data)
            # Reset monitor countdowns so new thresholds take effect immediately
            if 'monitor' in globals():
                try:
                    mlock = getattr(monitor, 'lock', None)
                    if mlock:
                        with mlock:
                            monitor.timers.clear()
                            monitor.last_upload_time.clear()
                    else:
                        monitor.timers.clear()
                        monitor.last_upload_time.clear()
                    logger.info("Monitor timers and last_upload_time cleared after settings change.")
                except Exception as e:
                    logger.warning(f"Failed to reset monitor timers after settings change: {e}")
            
            # Sync to Railway database
            sync_data = {
                "VIOLATION_TIME_THRESHOLD": getattr(config, 'VIOLATION_TIME_THRESHOLD', 100),
                "REPEAT_CAPTURE_INTERVAL": getattr(config, 'REPEAT_CAPTURE_INTERVAL', 60),
                "PARKING_ZONES": getattr(config, 'PARKING_ZONES', {})
            }
            
            def sync_to_db():
                try:
                    requests.post(f"{RAILWAY_API_URL}/api/db_settings", json=sync_data, headers=RAILWAY_HEADERS, timeout=5)
                    logger.info("Synced settings to Railway database")
                except Exception as e:
                    logger.warning(f"Could not sync to Railway: {e}")
            
            threading.Thread(target=sync_to_db, daemon=True).start()
            
            logger.info(f"Settings updated: {sync_data}")
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

    # GET request - return current settings
    try:
        importlib.reload(config)
    except Exception as e:
        logger.warning(f"Could not reload config: {e}")
    
    response_data = {
        "CAM1_URL": getattr(config, "CAM1_URL", ""),
        "CAM2_URL": getattr(config, "CAM2_URL", ""),
        "MODEL_PATH": getattr(config, "MODEL_PATH", ""),
        "SAVE_DIR": getattr(config, "SAVE_DIR", ""),
        "DETECTION_THRESHOLD": getattr(config, "DETECTION_THRESHOLD", 0.3),
        "VIOLATION_TIME_THRESHOLD": getattr(config, "VIOLATION_TIME_THRESHOLD", 100),
        "REPEAT_CAPTURE_INTERVAL": getattr(config, "REPEAT_CAPTURE_INTERVAL", 60),
        "PARKING_ZONES": getattr(config, "PARKING_ZONES", {})
    }
    logger.info(f"Returning settings: {response_data}")
    return jsonify(response_data)

# Endpoint to manually trigger sync from Railway
@app.route('/api/sync_from_railway', methods=['POST'])
def api_sync_from_railway():
    """Manually trigger sync from Railway database"""
    success = fetch_settings_from_railway()
    if success:
        return jsonify({"success": True, "message": "Settings synced from Railway"})
    else:
        return jsonify({"success": False, "error": "Failed to sync from Railway"}), 500

@app.route('/')
def index():
    public_url = app.config.get("PUBLIC_URL", "")
    index_html = open("templates/index.html").read()
    html_with_url = index_html.replace(
        'const RASPI_BASE = "{{ public_url }}";',
        f'const RASPI_BASE = "{public_url}";'
    )
    return render_template_string(html_with_url)

# --- Simple Tracker ---
class ByteTrackLite:
    # Global counter shared across all tracker instances to avoid ID collisions between cameras
    global_next_id = 0
    _id_lock = threading.Lock()

    def __init__(self):
        self.tracked_objects = {}
        self.frame_count = 0
        self.buffer = 30

    def get_iou(self, b1, b2):
        xA, yA = max(b1[0], b2[0]), max(b1[1], b2[1])
        xB, yB = min(b1[2], b2[2]), min(b1[3], b2[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        a1 = (b1[2]-b1[0])*(b1[3]-b1[1])
        a2 = (b2[2]-b2[0])*(b2[3]-b2[1])
        return inter / (a1 + a2 - inter + 1e-6)

    def update(self, boxes, scores, clss):
        self.frame_count += 1
        new_tracks = {}
        for box, score, cid in zip(boxes, scores, clss):
            best_id, best_iou = None, 0.3
            for tid, t in self.tracked_objects.items():
                iou = self.get_iou(box, t['box'])
                if iou > best_iou:
                    best_iou, best_id = iou, tid
            if best_id is not None:
                new_tracks[best_id] = {'box': box, 'cls': cid, 'conf': float(score), 'last_seen': self.frame_count}
                self.tracked_objects.pop(best_id, None)
            elif score >= config.DETECTION_THRESHOLD:
                with ByteTrackLite._id_lock:
                    nid = ByteTrackLite.global_next_id
                    ByteTrackLite.global_next_id += 1
                new_tracks[nid] = {'box': box, 'cls': cid, 'conf': float(score), 'last_seen': self.frame_count}
        for tid, t in self.tracked_objects.items():
            if self.frame_count - t['last_seen'] < self.buffer:
                new_tracks[tid] = t
        self.tracked_objects = new_tracks
        return {k: v for k, v in new_tracks.items() if v['last_seen'] == self.frame_count}

# --- Violation Video Recorder ---
VIOLATION_VIDEO_DURATION = 300  # 5 minutes in seconds
VIOLATION_VIDEO_FPS = 5

class ViolationRecorder:
    """Records a 5-minute MP4 video per violation, writing frames to /tmp as they come."""
    def __init__(self, camera, tid, label, frame):
        self.camera = camera
        self.tid = tid
        self.label = label
        self.start_time = time.time()
        self.frame_count = 0
        self.finished = False
        h, w = frame.shape[:2]
        self.path = f"/tmp/violation_{camera}_{tid}_{int(self.start_time)}.mp4"
        self.writer = cv2.VideoWriter(
            self.path, cv2.VideoWriter_fourcc(*'mp4v'),
            VIOLATION_VIDEO_FPS, (w, h)
        )
        self.writer.write(frame)
        self.frame_count += 1
        self._last_write = time.time()

    def add_frame(self, frame):
        if self.finished:
            return
        now = time.time()
        if now - self._last_write < (1.0 / VIOLATION_VIDEO_FPS):
            return
        self.writer.write(frame)
        self.frame_count += 1
        self._last_write = now

    def is_done(self):
        return (time.time() - self.start_time) >= VIOLATION_VIDEO_DURATION

    def finalize(self):
        if self.finished:
            return None
        self.finished = True
        self.writer.release()
        if self.frame_count < 5:
            try:
                os.remove(self.path)
            except OSError:
                pass
            return None
        return self.path

    def get_video_b64(self):
        path = self.finalize()
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, 'rb') as f:
                data = base64.b64encode(f.read()).decode('utf-8')
            os.remove(path)
            return data
        except Exception:
            return ""

# Active violation recorders: keyed by (camera, tracker_id)
violation_recorders = {}
violation_recorders_lock = threading.Lock()


# --- Parking Monitor ---
class ParkingMonitor:
    def __init__(self):
        self.trackers = {"Camera_1": ByteTrackLite(), "Camera_2": ByteTrackLite()}
        self.timers = {}
        self.last_upload_time = {}
        self.raw_zones = getattr(config, "PARKING_ZONES", {})
        self.zones = {}
        import threading as _threading
        self.lock = _threading.Lock()

    def _get_zone(self, name, fw, fh):
        """Scale normalized (0-1) zone points to pixel coordinates for the given frame size."""
        if name not in self.raw_zones:
            self.raw_zones = getattr(config, "PARKING_ZONES", {})
        if name not in self.raw_zones:
            return None
        pts = self.raw_zones[name]
        if not pts:
            return None
        arr = np.array(pts, dtype=np.float64)
        if arr.max() <= 1.0:
            scaled = arr * np.array([fw, fh])
        else:
            scaled = arr
        return scaled.astype(np.int32)

    def _upload_violation(self, name, tid, label, frame, d, dur, now, video_b64=""):
        """Upload violation event to Railway in a background thread."""
        try:
            import datetime
            now_dt = datetime.datetime.now()

            ocr_frame = frame
            _, buf = cv2.imencode('.jpg', frame)
            img_b64 = base64.b64encode(buf).decode('utf-8')
            logger.info(f"Violation frame for {name} tid={tid}: {frame.shape[1]}x{frame.shape[0]}")

            with self.lock:
                start_times = [t for (cam2, tid2), t in self.timers.items() if tid2 == tid]
            if start_times:
                min_start = min(start_times)
                duration_minutes = round((now - min_start) / 60.0, 2)
            else:
                duration_minutes = round(dur / 60.0, 2)

            fine_map = get_fine_map()
            fine_amount = float(fine_map.get(label.upper(), 0))

            plate_number = None
            plate_confidence = 0.0
            bbox = list(map(int, d['box']))

            ocr_bbox = bbox

            try:
                from ocr_module import extract_plate
                plate_number, plate_confidence = extract_plate(ocr_frame, ocr_bbox)
                if plate_number:
                    logger.info(f"EasyOCR plate: {plate_number} (conf={plate_confidence:.2f}) tid={tid} res={ocr_frame.shape[1]}x{ocr_frame.shape[0]}")
            except Exception as ocr_err:
                logger.warning(f"EasyOCR failed: {ocr_err}")

            if (not plate_number or plate_confidence < 0.6):
                try:
                    from claude_vision import analyze_violation, is_available
                    if is_available():
                        vision_result = analyze_violation(
                            ocr_frame, bbox=ocr_bbox, camera_id=name,
                            vehicle_label=label, duration_seconds=int(dur)
                        )
                        if vision_result and vision_result.get("plate_number"):
                            vp = vision_result["plate_number"]
                            vc = float(vision_result.get("plate_confidence", 0))
                            if vc > plate_confidence:
                                plate_number = vp
                                plate_confidence = vc
                                logger.info(f"Vision plate: {plate_number} (conf={plate_confidence:.2f}) tid={tid}")
                except Exception as vis_err:
                    logger.warning(f"Vision analysis failed: {vis_err}")

            payload = {
                "camera_id": name,
                "tracker_id": tid,
                "label": label,
                "timestamp": now_dt.isoformat(),
                "image": img_b64,
                "bbox": bbox,
                "duration_minutes": duration_minutes,
                "confidence_score": float(d.get('conf', 0)),
                "fine_amount": fine_amount,
                "enforced": False,
                "meta": {}
            }
            if plate_number and plate_confidence > 0.4:
                payload["plate_number"] = plate_number
                payload["plate_confidence"] = plate_confidence
            if video_b64:
                payload["video"] = video_b64

            api_url = f"{RAILWAY_API_URL}/api/upload_event"
            timeout = 120 if video_b64 else 30
            resp = requests.post(api_url, json=payload, headers=RAILWAY_HEADERS, timeout=timeout)
            if resp.ok:
                logger.info(f"Uploaded violation to Railway: camera={name} tid={tid} plate={plate_number} video={'yes' if video_b64 else 'no'}")
            else:
                logger.error(f"Failed upload to Railway: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"Exception uploading event to Railway: {e}")

    def process(self, name, res, frame):
        violation_threshold = getattr(config, "VIOLATION_TIME_THRESHOLD", 100)
        repeat_interval = getattr(config, "REPEAT_CAPTURE_INTERVAL", 60)

        fh, fw = frame.shape[:2]
        zone = self._get_zone(name, fw, fh)
        if zone is None:
            return
        cv2.polylines(frame, [zone], True, (0, 0, 255), 2)
        pixel_boxes = [[b[0]*fw, b[1]*fh, b[2]*fw, b[3]*fh] for b in res.xyxy]
        tracked = self.trackers[name].update(pixel_boxes, res.conf, res.cls)
        now = time.time()
        active_violation_keys = set()

        for tid, d in tracked.items():
            x1, y1, x2, y2 = map(int, d['box'])
            label = ALL_CLASS_NAMES.get(d['cls'], "OBJ")
            center = ((x1+x2)//2, (y1+y2)//2)
            if d['cls'] == 0:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 0), 1)
                continue
            in_zone = cv2.pointPolygonTest(zone, center, False) >= 0
            if not in_zone:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, f"{label} #{tid}", (x1, y1-8), 0, 0.6, (0, 255, 0), 2)
                continue

            with self.lock:
                if (name, tid) not in self.timers:
                    self.timers[(name, tid)] = now
                dur = int(now - self.timers[(name, tid)])
            is_violation = dur >= violation_threshold
            color = (0, 0, 255) if is_violation else (0, 255, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{label} #{tid}: {dur}s", (x1, y1-8), 0, 0.6, color, 2)

            if is_violation:
                rec_key = (name, tid)
                active_violation_keys.add(rec_key)

                with violation_recorders_lock:
                    rec = violation_recorders.get(rec_key)

                # Start recording if not already
                if rec is None:
                    rec = ViolationRecorder(name, tid, label, frame)
                    with violation_recorders_lock:
                        violation_recorders[rec_key] = rec
                    logger.info(f"Started 5-min video recording: camera={name} tid={tid} label={label}")

                    # Upload image immediately on first detection
                    self._upload_violation(name, tid, label, frame, d, dur, now)
                    with self.lock:
                        self.last_upload_time[(name, tid)] = now
                else:
                    rec.add_frame(frame)

                # Check if 5-minute recording is complete
                if rec.is_done() and not rec.finished:
                    logger.info(f"5-min recording complete: camera={name} tid={tid}, uploading video...")
                    video_b64 = rec.get_video_b64()
                    with violation_recorders_lock:
                        violation_recorders.pop(rec_key, None)
                    if video_b64:
                        threading.Thread(
                            target=self._upload_violation,
                            args=(name, tid, label, frame, d, dur, now, video_b64),
                            daemon=True
                        ).start()

                # Also re-upload image at repeat_interval
                last_up = 0
                with self.lock:
                    last_up = self.last_upload_time.get((name, tid), 0)
                if now - last_up > repeat_interval:
                    self._upload_violation(name, tid, label, frame, d, dur, now)
                    with self.lock:
                        self.last_upload_time[(name, tid)] = now
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                with self.lock:
                    self.timers.pop((name, tid), None)

        # Finalize recorders for vehicles that left the zone
        with violation_recorders_lock:
            gone_keys = [k for k in violation_recorders if k[0] == name and k not in active_violation_keys]
            for k in gone_keys:
                rec = violation_recorders.pop(k)
                if not rec.finished:
                    logger.info(f"Vehicle left zone, finalizing video: camera={k[0]} tid={k[1]}")
                    video_b64 = rec.get_video_b64()
                    if video_b64:
                        threading.Thread(
                            target=self._upload_violation,
                            args=(k[0], k[1], rec.label, frame, {'box': [0,0,0,0], 'conf': 0}, 0, now, video_b64),
                            daemon=True
                        ).start()


# --- Stream handler ---
class Stream:
    def __init__(self, url):
        self.url = url
        self.cap = cv2.VideoCapture(url)
        self.frame_buffer = None
        self.last_update = 0
        self.reconnecting = False
        self.read_lock = threading.Lock()
        self.reconnect_event = threading.Event()
        self.running = True
        threading.Thread(target=self._io_thread, daemon=True).start()

    def _io_thread(self):
        while self.running:
            if self.reconnect_event.is_set():
                self.cap.release()
                self.cap = cv2.VideoCapture(self.url)
                self.reconnect_event.clear()
            ret, f = self.cap.read()
            if ret:
                with self.read_lock:
                    self.frame_buffer = f
                    self.last_update = time.time()
                self.reconnecting = False
            else:
                self.reconnecting = True
                time.sleep(1)
                self.cap.release()
                self.cap = cv2.VideoCapture(self.url)

    def is_online(self):
        return (time.time() - self.last_update) < 3.0

    def get_frame(self):
        with self.read_lock:
            return self.frame_buffer.copy() if self.frame_buffer is not None else None

    def reconnect(self):
        self.reconnect_event.set()


class HiResCapture:
    """On-demand high-resolution frame grabber for plate OCR.
    Only opens the main stream when a frame is needed, then caches briefly."""
    def __init__(self, url):
        self.url = url
        self._lock = threading.Lock()
        self._last_frame = None
        self._last_time = 0
        self._cache_ttl = 5

    def grab_frame(self):
        with self._lock:
            if self._last_frame is not None and (time.time() - self._last_time) < self._cache_ttl:
                return self._last_frame.copy()
        try:
            cap = cv2.VideoCapture(self.url)
            if not cap.isOpened():
                return None
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                with self._lock:
                    self._last_frame = frame
                    self._last_time = time.time()
                return frame
        except Exception as e:
            logger.warning(f"HiRes capture failed: {e}")
        return None

# --- Initialize ---
monitor = ParkingMonitor()

# Feature 14: Tamper detection
tamper_detectors = {
    "Camera_1": TamperDetector("Camera_1"),
    "Camera_2": TamperDetector("Camera_2"),
}

# Feature 16: Health monitoring
health_mon = HealthMonitor()

# Use config values directly, fallback to defaults if missing
CAM1_URL = getattr(config, "CAM1_URL", None)
CAM2_URL = getattr(config, "CAM2_URL", None)
CAM1_HIRES_URL = getattr(config, "CAM1_HIRES_URL", None)
CAM2_HIRES_URL = getattr(config, "CAM2_HIRES_URL", None)

# Camera 1 uses main stream (4MP) for display + OCR; Hailo resizes internally for detection
if CAM1_URL and "/stream1" in CAM1_URL:
    logger.info(f"Camera_1 using main stream for full resolution: {CAM1_URL}")

if not CAM1_URL or not CAM2_URL:
    logger.error("CAM1_URL and/or CAM2_URL are not set in config.py. Please set valid RTSP URLs.")
    raise SystemExit(1)

c1, c2 = Stream(CAM1_URL), Stream(CAM2_URL)
latest_processed = {"Camera_1": None, "Camera_2": None}
proc_lock = threading.Lock()

# Hi-res capture for plate OCR (on-demand, not continuous)
hires_captures = {}
if CAM1_HIRES_URL:
    hires_captures["Camera_1"] = HiResCapture(CAM1_HIRES_URL)
    logger.info(f"HiRes capture ready for Camera_1: {CAM1_HIRES_URL}")
if CAM2_HIRES_URL:
    hires_captures["Camera_2"] = HiResCapture(CAM2_HIRES_URL)

# Feature 13: Start continuous recorders
recorders = {}
if ContinuousRecorder is not None:
    try:
        rec_cam1_url = CAM1_HIRES_URL or CAM1_URL
        recorders["Camera_1"] = ContinuousRecorder("Camera_1", rec_cam1_url)
        recorders["Camera_2"] = ContinuousRecorder("Camera_2", CAM2_URL)
        for rec in recorders.values():
            rec.start()
        logger.info("Continuous recorders started.")
    except Exception as e:
        logger.warning(f"Could not start recorders: {e}")
else:
    logger.info("ContinuousRecorder not available, skipping.")

# Feature 14: Set reference frames after a short delay
def _set_reference_frames():
    time.sleep(5)  # Wait for cameras to stabilize
    for cam_name, stream in [("Camera_1", c1), ("Camera_2", c2)]:
        frame = stream.get_frame()
        if frame is not None:
            tamper_detectors[cam_name].set_reference(frame)
            logger.info(f"Reference frame set for {cam_name}")
threading.Thread(target=_set_reference_frames, daemon=True).start()

def processing_worker(cam_name, stream):
    frame_count = 0
    while True:
        if not DETECTION_ENABLED:
            time.sleep(0.5)
            continue
        frame = stream.get_frame()
        if frame is not None and stream.is_online():
            try:
                res = detect([frame])
                if res:
                    frame_disp = frame.copy()
                    monitor.process(cam_name, res[0], frame_disp)
                    with proc_lock:
                        latest_processed[cam_name] = frame_disp

                # Feature 14: Check for tampering every 30 frames (~3 seconds)
                frame_count += 1
                if frame_count % 30 == 0:
                    is_tampered, tamper_type, details = tamper_detectors[cam_name].check(frame)
                    if is_tampered:
                        import datetime as dt
                        now_dt = dt.datetime.now()
                        logger.warning(f"TAMPER DETECTED on {cam_name}: {tamper_type} - {details}")
                        tamper_payload = {
                            "camera": cam_name,
                            "tamper_type": tamper_type,
                            "details": details,
                            "timestamp": now_dt.isoformat()
                        }
                        if tamper_detectors[cam_name].last_good_frame is not None:
                            _, tbuf = cv2.imencode('.jpg', tamper_detectors[cam_name].last_good_frame)
                            tamper_payload["image"] = base64.b64encode(tbuf).decode('utf-8')
                        try:
                            requests.post(f"{RAILWAY_API_URL}/api/tamper_event", json=tamper_payload,
                                          headers=RAILWAY_HEADERS, timeout=15)
                        except Exception as te:
                            logger.warning(f"Failed to upload tamper event: {te}")
            except Exception as e:
                logger.error(f"Detection error: {e}")
        time.sleep(0.1)

threading.Thread(target=processing_worker, args=("Camera_1", c1), daemon=True).start()
threading.Thread(target=processing_worker, args=("Camera_2", c2), daemon=True).start()

def cleanup_local_files():
    """Periodically remove old local tamper images and leftover /tmp violation videos."""
    import glob
    while True:
        try:
            for f in glob.glob("static/tamper/*.jpg"):
                try:
                    if os.path.getmtime(f) < time.time() - 3600:
                        os.remove(f)
                except OSError:
                    pass
            for f in glob.glob("/tmp/violation_*.mp4"):
                try:
                    if os.path.getmtime(f) < time.time() - 3600:
                        os.remove(f)
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")
        time.sleep(1800)

threading.Thread(target=cleanup_local_files, daemon=True).start()

STREAM_RESOLUTION = {
    "Camera_1": (1280, 720),
    "Camera_2": (960, 540),
}

def gen_single(stream, cam_name):
    res = STREAM_RESOLUTION.get(cam_name, (960, 540))
    FRAME_INTERVAL = 1.0 / 15
    JPEG_QUALITY = 80
    while True:
        start_time = time.time()
        with proc_lock:
            frame = latest_processed.get(cam_name)
        if frame is None:
            frame = stream.get_frame()
        if frame is not None:
            frame = cv2.resize(frame, res)
        else:
            frame = np.zeros((res[1], res[0], 3), dtype=np.uint8)
            cv2.putText(frame, f"{cam_name} OFFLINE", (res[0]//3, res[1]//2), 0, 1.5, (0,0,255), 3)

        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        _, buf = cv2.imencode('.jpg', frame, encode_param)
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
        elapsed = time.time() - start_time
        sleep_time = max(0, FRAME_INTERVAL - elapsed)
        time.sleep(sleep_time)

@app.route('/video_feed_c1')
def video_feed_c1():
    return Response(gen_single(c1, "Camera_1"), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/video_feed_c2')
def video_feed_c2():
    return Response(gen_single(c2, "Camera_2"), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/list_images')
def api_list_images():
    """
    List all images in the SAVE_DIR directory (recursively).
    Returns a JSON list of relative paths.
    """
    image_dir = SAVE_DIR
    image_files = []
    for root, dirs, files in os.walk(image_dir):
        for fname in files:
            if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
                rel_path = os.path.relpath(os.path.join(root, fname), os.path.dirname(__file__))
                image_files.append(rel_path.replace("\\", "/"))
    return jsonify(sorted(image_files, reverse=True))

# Endpoint to serve images (kept) - previously used by UI; not zone-related
@app.route('/api/get_image')
def api_get_image():
    """
    Serve a violation image given its image_path (relative to project root).
    Usage: /api/get_image?image_path=...
    """
    image_path = request.args.get("image_path")
    if not image_path:
        return jsonify({"success": False, "error": "Missing image_path"}), 400
    # Sanitize path to prevent directory traversal
    safe_path = os.path.normpath(image_path)
    if ".." in safe_path or safe_path.startswith("/"):
        return jsonify({"success": False, "error": "Invalid image_path"}), 400
    abs_path = os.path.join(os.path.dirname(__file__), safe_path)
    if not os.path.exists(abs_path):
        return jsonify({"success": False, "error": "Image not found"}), 404
    return Response(open(abs_path, "rb").read(), mimetype="image/jpeg")

@app.route('/api/capture_frame/<camera>')
def capture_frame(camera):
    """Capture a single frame from the specified camera for zone selection."""
    try:
        stream = c1 if camera == "Camera_1" else c2
        frame = stream.get_frame()
        if frame is None:
            return jsonify({"success": False, "error": "No frame available"}), 500
        
        # Resize to actual camera resolution (1280x720)
        frame = cv2.resize(frame, (ACTUAL_WIDTH, ACTUAL_HEIGHT))
        
        # Encode as JPEG with high quality for accurate selection
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 95]
        _, buf = cv2.imencode('.jpg', frame, encode_param)
        img_b64 = base64.b64encode(buf).decode('utf-8')
        
        return jsonify({
            "success": True,
            "image": img_b64,
            "width": ACTUAL_WIDTH,
            "height": ACTUAL_HEIGHT
        })
    except Exception as e:
        logger.error(f"Capture frame error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ---- PART 2: Daily Health Summary Email ----
def daily_health_email_loop():
    """Send health summary email every 24 hours at 6 AM Philippine time."""
    from datetime import datetime as _dt, timedelta as _td
    while True:
        now = _dt.now()
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now.hour >= 6:
            next_6am += _td(days=1)
        sleep_seconds = (next_6am - now).total_seconds()
        time.sleep(sleep_seconds)
        try:
            summary_text, status = health_mon.generate_daily_summary()
            requests.post(
                f"{RAILWAY_API_URL}/api/health_summary_email",
                json={"summary": summary_text, "status": status},
                headers=RAILWAY_HEADERS,
                timeout=15
            )
            logger.info("Daily health summary sent to Railway.")
        except Exception as e:
            logger.error(f"Failed to send daily health email: {e}")

threading.Thread(target=daily_health_email_loop, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask on 0.0.0.0:{port}")

    # Start cloudflared first
    try:
        cf_proc, public_url = start_cloudflared(port)
        app.config["PUBLIC_URL"] = public_url

        # Notify Railway app of the public URL
        RAILWAY_API_URL = os.environ.get("RAILWAY_API_URL", "https://web-production-dbb23.up.railway.app")
        max_retries = 10
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    f"{RAILWAY_API_URL}/api/set_pi_url",
                    json={"public_url": public_url},
                    headers=RAILWAY_HEADERS,
                    timeout=5
                )
                print("Posted public URL to Railway:", resp.status_code, resp.text)
                if resp.status_code == 200:
                    break
            except Exception as e:
                print(f"Failed to notify Railway app (attempt {attempt+1}):", e)
            time.sleep(2)
        else:
            print("Failed to notify Railway app after retries.")

        # Fetch initial settings from Railway database
        print("Fetching initial settings from Railway database...")
        fetch_settings_from_railway()
        
        # Start periodic sync thread
        threading.Thread(target=periodic_settings_sync, daemon=True).start()

    except Exception as e:
        print("Failed to start Cloudflare Tunnel:", e)
        app.config["PUBLIC_URL"] = ""

    app.run(host='0.0.0.0', port=port, threaded=True)
