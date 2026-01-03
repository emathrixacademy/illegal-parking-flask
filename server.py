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

app = Flask(__name__)

CLASS_NAMES = {0: "PERSON", 2: "CAR", 3: "MOTORCYCLE", 5: "BUS", 7: "TRUCK"}

# --- CONFIGURABLE VARIABLES (edit these as needed) ---
CAM1_URL = getattr(config, "CAM1_URL", None)
CAM2_URL = getattr(config, "CAM2_URL", None)
MODEL_PATH = getattr(config, "MODEL_PATH", "")
SAVE_DIR = getattr(config, "SAVE_DIR", "static/violations")
DETECTION_THRESHOLD = getattr(config, "DETECTION_THRESHOLD", 0.3)
VIOLATION_TIME_THRESHOLD = getattr(config, "VIOLATION_TIME_THRESHOLD", 10)
REPEAT_CAPTURE_INTERVAL = getattr(config, "REPEAT_CAPTURE_INTERVAL", 60)
PARKING_ZONES = getattr(config, "PARKING_ZONES", {})
PORT = int(os.environ.get("PORT", 5000))
RAILWAY_API_URL = os.environ.get("RAILWAY_API_URL", "https://illegal-parking-detection-flask.up.railway.app")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PiCameraServer")
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

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

@app.route('/api/zone_selector', methods=['POST'])
def api_zone_selector():
    try:
        data = request.get_json(force=True)
        cam = data.get("camera")
        zone = data.get("zone")
        if not cam or not isinstance(zone, list) or len(zone) < 3:
            return jsonify({"success": False, "error": "Invalid camera or zone"}), 400

        import os, re, ast, json as pyjson
        config_path = os.path.join(os.path.dirname(__file__), "config.py")

        with open(config_path, "r") as f:
            lines = f.readlines()

        zones = {}
        start_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith("PARKING_ZONES"):
                start_idx = i
                break

        if start_idx is not None:
            dict_lines = []
            for line in lines[start_idx:]:
                dict_lines.append(line)
                if "}" in line:
                    break
            dict_str = "".join(dict_lines)
            try:
                zones = ast.literal_eval(dict_str.split("=",1)[1].strip())
            except Exception:
                zones = {}
        else:
            zones = {}

        zones[cam] = zone

        new_zones_str = f'PARKING_ZONES = {pyjson.dumps(zones, separators=(",", ":"))}\n'
        if start_idx is not None:
            end_idx = start_idx
            for i in range(start_idx, len(lines)):
                if "}" in lines[i]:
                    end_idx = i
                    break
            lines = lines[:start_idx] + [new_zones_str] + lines[end_idx+1:]
        else:
            lines.append(new_zones_str)

        with open(config_path, "w") as f:
            f.writelines(lines)

        import importlib
        import config as config_mod
        importlib.reload(config_mod)
        # No monitor.zones here, but you can reload config if needed

        return jsonify({"success": True, "zone": zone})
    except Exception as e:
        logger.error(f"Zone selector error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    import importlib
    import json as pyjson
    import re
    config_path = os.path.join(os.path.dirname(__file__), "config.py")
    if request.method == 'POST':
        data = request.get_json(force=True)
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

        if "VIOLATION_TIME_THRESHOLD" in data:
            replace_line("VIOLATION_TIME_THRESHOLD", data["VIOLATION_TIME_THRESHOLD"])
        if "REPEAT_CAPTURE_INTERVAL" in data:
            replace_line("REPEAT_CAPTURE_INTERVAL", data["REPEAT_CAPTURE_INTERVAL"])
        if "PARKING_ZONES" in data:
            current_zones = getattr(config, "PARKING_ZONES", {})
            updated_zones = current_zones.copy()
            for cam, val in data["PARKING_ZONES"].items():
                if val is None:
                    updated_zones.pop(cam, None)
                else:
                    updated_zones[cam] = val
            replace_line("PARKING_ZONES", updated_zones)

        with open(config_path, "w") as f:
            f.writelines(lines)
        importlib.reload(config)
        # No monitor.zones here, but you can reload config if needed
        return jsonify({"success": True})

    return jsonify({
        "CAM1_URL": getattr(config, "CAM1_URL", ""),
        "CAM2_URL": getattr(config, "CAM2_URL", ""),
        "MODEL_PATH": getattr(config, "MODEL_PATH", ""),
        "SAVE_DIR": getattr(config, "SAVE_DIR", ""),
        "DETECTION_THRESHOLD": getattr(config, "DETECTION_THRESHOLD", 0.3),
        "VIOLATION_TIME_THRESHOLD": getattr(config, "VIOLATION_TIME_THRESHOLD", 10),
        "REPEAT_CAPTURE_INTERVAL": getattr(config, "REPEAT_CAPTURE_INTERVAL", 60),
        "PARKING_ZONES": getattr(config, "PARKING_ZONES", {})
    })

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
    def __init__(self):
        self.tracked_objects = {}
        self.frame_count = 0
        self.next_id = 0
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
                new_tracks[best_id] = {'box': box, 'cls': cid, 'last_seen': self.frame_count}
                self.tracked_objects.pop(best_id, None)
            elif score >= config.DETECTION_THRESHOLD:
                new_tracks[self.next_id] = {'box': box, 'cls': cid, 'last_seen': self.frame_count}
                self.next_id += 1
        for tid, t in self.tracked_objects.items():
            if self.frame_count - t['last_seen'] < self.buffer:
                new_tracks[tid] = t
        self.tracked_objects = new_tracks
        return {k: v for k, v in new_tracks.items() if v['last_seen'] == self.frame_count}

# --- Parking Monitor ---
class ParkingMonitor:
    def __init__(self):
        self.trackers = {"Camera_1": ByteTrackLite(), "Camera_2": ByteTrackLite()}
        self.timers = {}
        self.last_upload_time = {}
        self.zones = {cam: np.array(points) for cam, points in getattr(config, "PARKING_ZONES", {}).items()}

    def process(self, name, res, frame):
        if name not in self.zones: return
        fh, fw = frame.shape[:2]
        cv2.polylines(frame, [self.zones[name]], True, (0, 0, 255), 2)
        pixel_boxes = [[b[0]*fw, b[1]*fh, b[2]*fw, b[3]*fh] for b in res.xyxy]
        tracked = self.trackers[name].update(pixel_boxes, res.conf, res.cls)
        now = time.time()
        for tid, d in tracked.items():
            x1, y1, x2, y2 = map(int, d['box'])
            label = CLASS_NAMES.get(d['cls'], "OBJ")
            center = ((x1+x2)//2, (y1+y2)//2)
            if d['cls'] == 0:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 0), 1)
                continue
            in_zone = cv2.pointPolygonTest(self.zones[name], center, False) >= 0
            if in_zone:
                self.timers.setdefault((name, tid), now)
                dur = int(now - self.timers[(name, tid)])
                is_violation = dur >= config.VIOLATION_TIME_THRESHOLD
                color = (0, 0, 255) if is_violation else (0, 255, 255)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"{label} #{tid}: {dur}s", (x1, y1-8), 0, 0.6, color, 2)
                if is_violation:
                    last_up = self.last_upload_time.get((name, tid), 0)
                    if now - last_up > config.REPEAT_CAPTURE_INTERVAL:
                        import datetime
                        now_dt = datetime.datetime.now()
                        date_folder = now_dt.strftime("%B %d, %Y (%A)")
                        date_dir = os.path.join(SAVE_DIR, date_folder)
                        os.makedirs(date_dir, exist_ok=True)
                        img_filename = f"{name}-{now_dt.strftime('%H_%M_%S')}.jpg"
                        img_path = os.path.join(date_dir, img_filename)
                        cv2.imwrite(img_path, frame)
                        logger.info(f"Violation detected: camera={name}, tracker_id={tid}, label={label}, time={now_dt.isoformat()}")
                        logger.info(f"Saved violation image to {img_path}")

                        # --- Send violation event to Railway API ---
                        try:
                            _, buf = cv2.imencode('.jpg', frame)
                            img_b64 = base64.b64encode(buf).decode('utf-8')
                            payload = {
                                "camera_id": name,
                                "tracker_id": tid,
                                "label": label,
                                "timestamp": now_dt.isoformat(),
                                "image": img_b64,
                                "meta": {}
                            }
                            api_url = f"{RAILWAY_API_URL}/api/upload_event"
                            resp = requests.post(api_url, json=payload, timeout=10)
                            if resp.ok:
                                logger.info(f"Uploaded violation event to Railway: {resp.status_code}")
                            else:
                                logger.error(f"Failed to upload event to Railway: {resp.status_code} {resp.text}")
                        except Exception as e:
                            logger.error(f"Exception uploading event to Railway: {e}")

                        # --- Optionally: remove or comment out direct DB insert ---
                        # insert_violation_event(
                        #     camera=name,
                        #     tracker_id=tid,
                        #     label=label,
                        #     timestamp=now_dt,
                        #     image_path=img_path
                        # )

                        self.last_upload_time[(name, tid)] = now
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                self.timers.pop((name, tid), None)


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

# --- Initialize ---
monitor = ParkingMonitor()

# Use config values directly, fallback to defaults if missing
CAM1_URL = getattr(config, "CAM1_URL", None)
CAM2_URL = getattr(config, "CAM2_URL", None)

if not CAM1_URL or not CAM2_URL:
    logger.error("CAM1_URL and/or CAM2_URL are not set in config.py. Please set valid RTSP URLs.")
    raise SystemExit(1)

c1, c2 = Stream(CAM1_URL), Stream(CAM2_URL)
latest_processed = {"Camera_1": None, "Camera_2": None}
proc_lock = threading.Lock()

def processing_worker(cam_name, stream):
    while True:
        frame = stream.get_frame()
        if frame is not None and stream.is_online():
            try:
                res = detect([frame])
                if res:
                    frame_disp = frame.copy()
                    monitor.process(cam_name, res[0], frame_disp)
                    with proc_lock:
                        latest_processed[cam_name] = frame_disp
            except Exception as e:
                logger.error(f"Detection error: {e}")
        time.sleep(0.1)

threading.Thread(target=processing_worker, args=("Camera_1", c1), daemon=True).start()
threading.Thread(target=processing_worker, args=("Camera_2", c2), daemon=True).start()

def gen_single(stream, cam_name):
    FRAME_INTERVAL = 1.0 / 10  # 10 FPS for cloud streaming
    JPEG_QUALITY = 80  # Lower quality for smoother streaming (range: 0-100)
    last_frame_time = 0
    while True:
        start_time = time.time()
        with proc_lock:
            frame = latest_processed.get(cam_name)
        if frame is None:
            frame = stream.get_frame()
        if frame is not None:
            frame = cv2.resize(frame, (1280, 720))
        else:
            frame = np.zeros((720, 1280, 3), dtype=np.uint8)
            cv2.putText(frame, f"{cam_name} OFFLINE", (400, 360), 0, 1.5, (0,0,255), 3)
        # Use JPEG quality to reduce bandwidth and smoothen streaming
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

def class_from_filename(fname):
    # Example: Camera_1-12_00_00-3.jpg or Camera_1-12_00_00-3-123.jpg
    m = re.match(r".*-(\d+)\.jpg$", fname)
    if m:
        cls_id = int(m.group(1))
        return CLASS_NAMES.get(cls_id, str(cls_id))
    return None

@app.route('/api/list_images')
def api_list_images():
    """
    List all images in the SAVE_DIR directory (recursively).
    Returns a JSON list of dicts: { path, class_label }
    """
    image_dir = SAVE_DIR
    image_files = []
    for root, dirs, files in os.walk(image_dir):
        for fname in files:
            if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
                rel_path = os.path.relpath(os.path.join(root, fname), os.path.dirname(__file__))
                rel_path = rel_path.replace("\\", "/")
                class_label = class_from_filename(fname)
                image_files.append({
                    "path": rel_path,
                    "class_label": class_label
                })
    return jsonify(sorted(image_files, key=lambda x: x["path"], reverse=True))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Flask on 0.0.0.0:{port}")

    # Start cloudflared first
    try:
        cf_proc, public_url = start_cloudflared(port)
        app.config["PUBLIC_URL"] = public_url

        # Notify Railway app of the public URL
        RAILWAY_API_URL = os.environ.get("RAILWAY_API_URL", "https://illegal-parking-detection-flask.up.railway.app")
        max_retries = 10
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    f"{RAILWAY_API_URL}/api/set_pi_url",
                    json={"public_url": public_url},
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

    except Exception as e:
        print("Failed to start Cloudflare Tunnel:", e)
        app.config["PUBLIC_URL"] = ""

    app.run(host='0.0.0.0', port=port, threaded=True)
