import cv2
import numpy as np
import json
import sys
import os
import requests

import config

RAILWAY_API_URL = os.environ.get("RAILWAY_API_URL", "https://web-production-dbb23.up.railway.app")
PI_API_KEY = os.environ.get("PI_API_KEY", "dcgl-pi-secret-2026")

CAM1_URL = getattr(config, "CAM1_URL", "rtsp://192.168.8.2:554/stream")
CAM2_URL = getattr(config, "CAM2_URL", "rtsp://192.168.8.199:554/stream")

window_base = "Zone Selector"
points = []
current_cam_key = None
DISPLAY_SCALE = 0.5
frame_w = 640
frame_h = 480

def mouse_callback(event, x, y, flags, param):
    global points
    if event == cv2.EVENT_LBUTTONDOWN:
        full_x = int(x / DISPLAY_SCALE)
        full_y = int(y / DISPLAY_SCALE)
        points.append((full_x, full_y))
        norm_x = round(full_x / frame_w, 4)
        norm_y = round(full_y / frame_h, 4)
        print(f"Point added: ({full_x}, {full_y}) normalized: ({norm_x}, {norm_y})")

def run_for_camera(cam_key, cam_url, existing_points):
    global points, current_cam_key, frame_w, frame_h
    current_cam_key = cam_key
    window_name = f"{window_base} - {cam_key}"
    cv2.namedWindow(window_name)
    cv2.setMouseCallback(window_name, mouse_callback)
    print(f"\n--- Selecting zone for {cam_key} ---")
    print("Instructions:")
    print("- Click to add points for the zone polygon.")
    print("- Press 'u' to undo last point.")
    print("- Press 'c' to clear all points.")
    print("- Press 'n' to finish this camera and move to next.")
    print("- Press 'q' to quit without saving.")

    cap = cv2.VideoCapture(cam_url)
    if not cap.isOpened():
        print(f"Failed to open camera {cam_key}. Using blank canvas.")
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame_w, frame_h = 640, 480
        ret = False
    else:
        ret = True
        ok, frame = cap.read()
        if ok:
            frame_h, frame_w = frame.shape[:2]
            print(f"Camera resolution: {frame_w}x{frame_h}")
        else:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            frame_w, frame_h = 640, 480

    # Convert existing normalized points to pixel for display
    if existing_points:
        points = [(int(p[0] * frame_w), int(p[1] * frame_h)) for p in existing_points]
    else:
        points = []

    while True:
        if ret:
            ok, frame = cap.read()
            if not ok:
                frame = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)

        display = cv2.resize(frame, (0, 0), fx=DISPLAY_SCALE, fy=DISPLAY_SCALE)

        for pt in points:
            dp = (int(pt[0] * DISPLAY_SCALE), int(pt[1] * DISPLAY_SCALE))
            cv2.circle(display, dp, 4, (0, 0, 255), -1)
        if len(points) > 1:
            scaled_pts = [(int(p[0] * DISPLAY_SCALE), int(p[1] * DISPLAY_SCALE)) for p in points]
            cv2.polylines(display, [np.array(scaled_pts, np.int32)], isClosed=True, color=(0, 255, 0), thickness=2)

        cv2.imshow(window_name, display)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('u') and points:
            points.pop()
        elif key == ord('c'):
            points = []
        elif key == ord('n'):
            break
        elif key == ord('q'):
            points = None
            break

    if ret:
        cap.release()
    cv2.destroyWindow(window_name)
    if points is None:
        return None
    # Return normalized (0-1) coordinates
    return [[round(x / frame_w, 4), round(y / frame_h, 4)] for (x, y) in points]

def load_existing_zones():
    try:
        resp = requests.get(
            f"{RAILWAY_API_URL}/api/db_settings",
            headers={"X-API-Key": PI_API_KEY},
            timeout=10
        )
        if resp.ok:
            settings = resp.json()
            zones = settings.get("PARKING_ZONES", {})
            if isinstance(zones, dict):
                return zones
    except Exception as e:
        print(f"Could not load existing zones from Railway: {e}")
    return {}

def save_zones(zones):
    try:
        resp = requests.post(
            f"{RAILWAY_API_URL}/api/db_settings",
            json={"PARKING_ZONES": zones},
            headers={"X-API-Key": PI_API_KEY, "Content-Type": "application/json"},
            timeout=10
        )
        if resp.ok:
            print("Saved PARKING_ZONES to Railway DB.")
            return True
        else:
            print(f"Failed to save zones: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Failed to save to Railway: {e}")
    return False

def main():
    existing = load_existing_zones()

    cameras = {
        "Camera_1": CAM1_URL,
        "Camera_2": CAM2_URL
    }

    zones = existing.copy()

    for cam_key, cam_url in cameras.items():
        existing_pts = zones.get(cam_key, [])
        selected = run_for_camera(cam_key, cam_url, existing_pts)
        if selected is None:
            print("Aborted by user. Exiting without saving.")
            return
        if selected:
            zones[cam_key] = selected
        else:
            if cam_key not in zones:
                zones[cam_key] = []

    if not save_zones(zones):
        print("Fallback - zones JSON (copy manually):")
        print(json.dumps(zones))
    else:
        print(json.dumps(zones))

if __name__ == "__main__":
    main()
