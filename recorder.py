"""
Continuous Recording Module (Feature 13 - Playback)
Runs on the Raspberry Pi edge device.
Records RTSP streams to 1-hour MP4 segments on USB HDD.
Automatically cleans up recordings older than MAX_DAYS.
"""
import cv2
import os
import time
import threading
import shutil
import logging
from datetime import datetime

logger = logging.getLogger("Recorder")

RECORDING_DIR = os.environ.get("RECORDING_DIR", "/mnt/recording")
SEGMENT_DURATION = 3600  # 1 hour per file
MAX_DAYS = 7


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
        while self.running:
            try:
                now = datetime.now()
                date_dir = os.path.join(RECORDING_DIR, self.camera_id, now.strftime("%Y-%m-%d"))
                os.makedirs(date_dir, exist_ok=True)

                filename = os.path.join(date_dir, f"{now.strftime('%H-%M-%S')}.mp4")
                cap = cv2.VideoCapture(self.rtsp_url)

                if not cap.isOpened():
                    logger.warning(f"Cannot open stream for {self.camera_id}, retrying in 5s")
                    time.sleep(5)
                    continue

                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                fps = 15
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

                if w == 0 or h == 0:
                    w, h = 1280, 720

                writer = cv2.VideoWriter(filename, fourcc, fps, (w, h))
                start_time = time.time()

                while self.running and (time.time() - start_time) < SEGMENT_DURATION:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    writer.write(frame)
                    time.sleep(1.0 / fps)

                writer.release()
                cap.release()
                self._cleanup_old_recordings()

            except Exception as e:
                logger.error(f"Recording error {self.camera_id}: {e}")
                time.sleep(5)

    def _cleanup_old_recordings(self):
        """Delete recordings older than MAX_DAYS."""
        base = os.path.join(RECORDING_DIR, self.camera_id)
        if not os.path.exists(base):
            return
        cutoff = time.time() - (MAX_DAYS * 86400)
        for date_folder in os.listdir(base):
            folder_path = os.path.join(base, date_folder)
            if os.path.isdir(folder_path):
                folder_time = os.path.getmtime(folder_path)
                if folder_time < cutoff:
                    shutil.rmtree(folder_path)
                    logger.info(f"Deleted old recording: {folder_path}")


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
