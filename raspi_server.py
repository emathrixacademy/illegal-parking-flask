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
import signal
import json
from datetime import datetime
from flask import Flask, request, jsonify, Response, render_template
import config
from app_detect import detect

# --- Logging & directories ---
SAVE_DIR = getattr(config, "SAVE_DIR", "events")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PiCameraServer")
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

CLASS_NAMES = {
    0: "PERSON",
    2: "CAR",
    3: "MOTORCYCLE",
    5: "BUS",
    7: "TRUCK"
}

RAILWAY_API_URL = os.environ.get(
    "RAILWAY_API_URL",
    "https://illegal-parking-detection-flask.up.railway.app"
)

# ===============================
# Cloudflare Tunnel
# ===============================
def start_cloudflared(port=5000):
    process = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    url = None
    for line in iter(process.stdout.readline, ''):
        print(line.strip())
        match = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", line)
        if match:
            url = match.group(0)
            break

    if not url:
        raise RuntimeError("Cloudflare tunnel failed")

    logger.info("Cloudflare tunnel started: %s", url)
    return process, url

# ===============================
# Sync Config From Railway
# ===============================
def sync_config_from_railway():
    try:
        r = requests.get(f"{RAILWAY_API_URL}/api/settings", timeout=10)
        data = r.json()

        # Ensure required keys exist with defaults if missing
        defaults = {
            "CAM1_URL": "rtsp://localhost:8554/cam1",
            "CAM2_URL": "rtsp://localhost:8554/cam2",
            "VIOLATION_TIME_THRESHOLD": 10,
            "REPEAT_CAPTURE_INTERVAL": 60,
            "PARKING_ZONES": {},
            "DETECTION_THRESHOLD": 0.5,
            "SAVE_DIR": "events"
        }
        for k, v in defaults.items():
            data.setdefault(k, v)

        with open("config.py", "w") as f:
            for k, v in data.items():
                f.write(f"{k} = {repr(v)}\n")

        import importlib
        importlib.reload(config)
        logger.info("Config synced from Railway")

    except Exception as e:
        logger.error("Failed to sync config: %s", e)

# ===============================
# Upload Violation to Railway
# ===============================
def upload_event_to_cloud(camera_id, frame, meta):
    try:
        _, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        image_b64 = base64.b64encode(buf).decode()

        payload = {
            "camera_id": camera_id,
            "timestamp": datetime.utcnow().isoformat(),
            "image": image_b64,
            "meta": meta
        }

        r = requests.post(
            f"{RAILWAY_API_URL}/api/upload_event",
            json=payload,
            timeout=5
        )

        logger.info("Violation uploaded: %s %s", r.status_code, r.text)

    except Exception as e:
        logger.error("Upload failed: %s", e)

# ===============================
# Tracker
# ===============================
class ByteTrackLite:
    def __init__(self):
        self.tracked = {}
        self.frame_id = 0
        self.next_id = 0
        self.buffer = 30

    def iou(self, a, b):
        xA, yA = max(a[0], b[0]), max(a[1], b[1])
        xB, yB = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        areaA = (a[2]-a[0])*(a[3]-a[1])
        areaB = (b[2]-b[0])*(b[3]-b[1])
        return inter / (areaA + areaB - inter + 1e-6)

    def update(self, boxes, scores, clss):
        self.frame_id += 1
        updated = {}

        for box, score, cls in zip(boxes, scores, clss):
            best, best_iou = None, 0.3
            for tid, t in self.tracked.items():
                i = self.iou(box, t["box"])
                if i > best_iou:
                    best, best_iou = tid, i

            if best is not None:
                updated[best] = {"box": box, "cls": cls, "last": self.frame_id}
                self.tracked.pop(best, None)
            elif score >= config.DETECTION_THRESHOLD:
                updated[self.next_id] = {"box": box, "cls": cls, "last": self.frame_id}
                self.next_id += 1

        for tid, t in self.tracked.items():
            if self.frame_id - t["last"] < self.buffer:
                updated[tid] = t

        self.tracked = updated
        return {k: v for k, v in updated.items() if v["last"] == self.frame_id}

# ===============================
# Parking Monitor
# ===============================
class ParkingMonitor:
    def __init__(self):
        self.trackers = {
            "Camera_1": ByteTrackLite(),
            "Camera_2": ByteTrackLite()
        }
        self.timers = {}
        self.last_upload = {}
        self.zones = {
            cam: np.array(zone)
            for cam, zone in getattr(config, "PARKING_ZONES", {}).items()
        }

    def process(self, cam, res, frame):
        if cam not in self.zones:
            return

        fh, fw = frame.shape[:2]
        zone = self.zones[cam]
        cv2.polylines(frame, [zone], True, (0, 0, 255), 2)

        boxes = [[b[0]*fw, b[1]*fh, b[2]*fw, b[3]*fh] for b in res.xyxy]
        tracked = self.trackers[cam].update(boxes, res.conf, res.cls)

        now = time.time()
        for tid, d in tracked.items():
            x1, y1, x2, y2 = map(int, d["box"])
            cls = d["cls"]
            label = CLASS_NAMES.get(cls, "OBJ")
            center = ((x1+x2)//2, (y1+y2)//2)

            if cls == 0:
                continue

            inside = cv2.pointPolygonTest(zone, center, False) >= 0
            if inside:
                self.timers.setdefault((cam, tid), now)
                duration = int(now - self.timers[(cam, tid)])
                violation = duration >= config.VIOLATION_TIME_THRESHOLD

                color = (0, 0, 255) if violation else (0, 255, 255)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"{label} {duration}s", (x1, y1-8),
                            0, 0.6, color, 2)

                if violation:
                    last = self.last_upload.get((cam, tid), 0)
                    if now - last > config.REPEAT_CAPTURE_INTERVAL:
                        meta = {
                            "tracker_id": tid,
                            "label": label,
                            "confidence": float(res.conf[0])
                        }
                        upload_event_to_cloud(cam, frame, meta)
                        self.last_upload[(cam, tid)] = now
            else:
                self.timers.pop((cam, tid), None)

    def log_violation(self, cam, tid, label, frame):
        import datetime
        now = datetime.datetime.now()
        date_folder = now.strftime("%B %d, %Y (%A)")
        date_dir = os.path.join(SAVE_DIR, date_folder)
        os.makedirs(date_dir, exist_ok=True)
        path = os.path.join(date_dir, f"{cam}-{now.strftime('%H_%M_%S')}.jpg")
        cv2.imwrite(path, frame)
        meta = {"tracker_id": tid, "label": label, "timestamp": now.isoformat()}
        upload_event_to_cloud(cam, frame, meta)

# ===============================
# Camera Streams
# ===============================
class Stream:
    def __init__(self, url):
        self.cap = cv2.VideoCapture(url)
        self.frame = None
        self.last = 0
        self.lock = threading.Lock()
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            ret, f = self.cap.read()
            if ret:
                with self.lock:
                    self.frame = f
                    self.last = time.time()
            else:
                time.sleep(1)
                self.cap.release()
                self.cap = cv2.VideoCapture(self.cap)

    def get(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

    def online(self):
        return time.time() - self.last < 3

# ===============================
# Init
# ===============================
sync_config_from_railway()
monitor = ParkingMonitor()

# Use defaults if config is missing attributes
CAM1_URL = getattr(config, "CAM1_URL", "rtsp://localhost:8554/cam1")
CAM2_URL = getattr(config, "CAM2_URL", "rtsp://localhost:8554/cam2")

c1 = Stream(CAM1_URL)
c2 = Stream(CAM2_URL)

latest = {"Camera_1": None, "Camera_2": None}
lock = threading.Lock()

def worker(name, stream):
    while True:
        frame = stream.get()
        if frame is not None and stream.online():
            res = detect([frame])
            if res:
                out = frame.copy()
                monitor.process(name, res[0], out)
                with lock:
                    latest[name] = out
        time.sleep(0.1)

threading.Thread(target=worker, args=("Camera_1", c1), daemon=True).start()
threading.Thread(target=worker, args=("Camera_2", c2), daemon=True).start()

# ===============================
# Flask Routes
# ===============================
def gen(cam):
    while True:
        with lock:
            frame = latest.get(cam)
        if frame is None:
            frame = np.zeros((720,1280,3), dtype=np.uint8)
        _, buf = cv2.imencode(".jpg", frame)
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"

@app.route("/video_feed_c1")
def v1():
    return Response(gen("Camera_1"), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/video_feed_c2")
def v2():
    return Response(gen("Camera_2"), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/api/camera_status")
def status():
    return jsonify({
        "Camera_1": {"online": c1.online()},
        "Camera_2": {"online": c2.online()}
    })

@app.route("/")
def index():
    return render_template("index.html")

# ===============================
# Shutdown
# ===============================
def shutdown(sig, frame):
    logger.info("Shutting down")
    os._exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

# ===============================
# Main
# ===============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    cf, public_url = start_cloudflared(port)
    app.config["PUBLIC_URL"] = public_url

    requests.post(
        f"{RAILWAY_API_URL}/api/set_pi_url",
        json={"public_url": public_url},
        timeout=5
    )

    app.run(host="0.0.0.0", port=port, threaded=True)
