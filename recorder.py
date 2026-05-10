"""
Continuous Recording Module (Feature 13 - Playback)
Runs on the Raspberry Pi edge device.
Records RTSP streams to 15-minute MP4 segments.
Uploads each segment to Cloudinary via Railway, then deletes locally.
"""
import cv2
import os
import time
import threading
import shutil
import logging
import requests
import base64
from datetime import datetime

logger = logging.getLogger("Recorder")

RECORDING_DIR = os.environ.get("RECORDING_DIR", "/mnt/recording")
SEGMENT_DURATION = 900  # 15 minutes per segment
RAILWAY_API_URL = os.environ.get("RAILWAY_API_URL", "https://web-production-dbb23.up.railway.app")
PI_API_KEY = os.environ.get("PI_API_KEY", "dcgl-pi-secret-2026")
RAILWAY_HEADERS = {"X-API-Key": PI_API_KEY}


class ContinuousRecorder:
    def __init__(self, camera_id, rtsp_url):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._record_loop, daemon=True)
        self.thread.start()
        logger.info(f"Recorder started for {self.camera_id}")

    def stop(self):
        self.running = False
        logger.info(f"Recorder stopped for {self.camera_id}")

    def _record_loop(self):
        retry_delay = 30
        while self.running:
            try:
                now = datetime.now()
                date_str = now.strftime("%Y-%m-%d")
                time_str = now.strftime('%H-%M-%S')
                date_dir = os.path.join(RECORDING_DIR, self.camera_id, date_str)
                os.makedirs(date_dir, exist_ok=True)

                filename = os.path.join(date_dir, f"{time_str}.mp4")
                cap = cv2.VideoCapture(self.rtsp_url)

                if not cap.isOpened():
                    logger.warning(f"Cannot open stream for {self.camera_id}, retrying in {retry_delay}s")
                    time.sleep(retry_delay)
                    continue

                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                fps = 10
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

                if w == 0 or h == 0:
                    w, h = 960, 540

                writer = cv2.VideoWriter(filename, fourcc, fps, (w, h))
                start_time = time.time()
                frame_count = 0

                while self.running and (time.time() - start_time) < SEGMENT_DURATION:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    resized = cv2.resize(frame, (960, 540))
                    writer.write(resized)
                    frame_count += 1
                    time.sleep(1.0 / fps)

                writer.release()
                cap.release()

                if os.path.exists(filename):
                    fsize = os.path.getsize(filename)
                    if fsize > 10000 and frame_count > 10:
                        logger.info(f"Segment ready: {self.camera_id}/{date_str}/{time_str} ({frame_count} frames, {fsize//1024}KB)")
                        threading.Thread(
                            target=self._upload_and_delete,
                            args=(filename, self.camera_id, date_str, time_str),
                            daemon=True
                        ).start()
                        retry_delay = 30
                    else:
                        try:
                            os.remove(filename)
                        except OSError:
                            pass
                        logger.debug(f"Removed tiny segment {filename} ({fsize} bytes, {frame_count} frames)")
                        retry_delay = min(retry_delay + 10, 60)
                        time.sleep(retry_delay)

            except Exception as e:
                logger.error(f"Recording error {self.camera_id}: {e}")
                time.sleep(retry_delay)

    def _upload_and_delete(self, filepath, camera_id, date_str, time_str):
        try:
            with open(filepath, 'rb') as f:
                video_b64 = base64.b64encode(f.read()).decode('utf-8')

            resp = requests.post(
                f"{RAILWAY_API_URL}/api/upload_recording",
                json={
                    "camera_id": camera_id,
                    "date": date_str,
                    "time": time_str,
                    "video": video_b64
                },
                headers=RAILWAY_HEADERS,
                timeout=300
            )
            if resp.ok:
                logger.info(f"Uploaded recording to Cloudinary: {camera_id}/{date_str}/{time_str}")
                try:
                    os.remove(filepath)
                    parent = os.path.dirname(filepath)
                    if os.path.isdir(parent) and not os.listdir(parent):
                        os.rmdir(parent)
                except OSError:
                    pass
            else:
                logger.error(f"Recording upload failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"Recording upload error: {e}")


def get_recording_dates(camera_id=None):
    """List available recording dates."""
    dates = {}
    try:
        if camera_id:
            cameras = [camera_id]
        else:
            if not os.path.exists(RECORDING_DIR):
                return dates
            cameras = [d for d in os.listdir(RECORDING_DIR)
                       if os.path.isdir(os.path.join(RECORDING_DIR, d))]

        for cam in cameras:
            cam_dir = os.path.join(RECORDING_DIR, cam)
            if os.path.exists(cam_dir):
                cam_dates = sorted([d for d in os.listdir(cam_dir)
                                    if os.path.isdir(os.path.join(cam_dir, d))], reverse=True)
                dates[cam] = cam_dates
    except Exception as e:
        logger.error(f"Error listing recording dates: {e}")

    return dates


def get_recording_segments(camera_id, date_str):
    """List available recording segments for a camera and date."""
    segments = []
    try:
        date_dir = os.path.join(RECORDING_DIR, camera_id, date_str)
        if os.path.exists(date_dir):
            for f in sorted(os.listdir(date_dir)):
                if f.endswith('.mp4'):
                    segments.append({
                        "filename": f,
                        "time": f.replace('.mp4', '').replace('-', ':'),
                        "path": os.path.join(date_dir, f),
                        "size_mb": round(os.path.getsize(os.path.join(date_dir, f)) / 1048576, 1)
                    })
    except Exception as e:
        logger.error(f"Error listing segments: {e}")
    return segments
