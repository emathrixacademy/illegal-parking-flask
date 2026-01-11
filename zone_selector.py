import cv2
import numpy as np
import json
import sys
import db  # ...existing code...

# Camera RTSP URLs
CAM1_URL = "rtsp://192.168.8.2:554/stream"
CAM2_URL = "rtsp://192.168.8.199:554/stream"

window_base = "Zone Selector"
points = []
current_cam_key = None

def mouse_callback(event, x, y, flags, param):
    global points
    if event == cv2.EVENT_LBUTTONDOWN:
        points.append((x, y))
        print(f"Point added: ({x}, {y})")

def run_for_camera(cam_key, cam_url, existing_points):
    global points, current_cam_key
    points = [tuple(p) for p in existing_points] if existing_points else []
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
        # create a blank frame to allow drawing if stream not available
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        ret = False
    else:
        ret = True

    while True:
        if ret:
            ok, frame = cap.read()
            if not ok:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
        else:
            # keep blank frame
            pass

        # Draw points and polygon
        for pt in points:
            cv2.circle(frame, pt, 5, (0, 0, 255), -1)
        if len(points) > 1:
            cv2.polylines(frame, [np.array(points, np.int32)], isClosed=True, color=(0, 255, 0), thickness=2)

        cv2.imshow(window_name, frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('u') and points:
            points.pop()
        elif key == ord('c'):
            points = []
        elif key == ord('n'):
            break
        elif key == ord('q'):
            # signal to abort entire flow
            points = None
            break

    if ret:
        cap.release()
    cv2.destroyWindow(window_name)
    return None if points is None else [ [int(x), int(y)] for (x,y) in points ]

def main():
    # Load existing zones from DB (if present)
    try:
        existing = db.get_config_value("PARKING_ZONES", {})
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}

    cameras = {
        "Camera_1": CAM1_URL,
        "Camera_2": CAM2_URL
    }

    zones = existing.copy()

    # Iterate all cameras automatically
    for cam_key, cam_url in cameras.items():
        existing_pts = zones.get(cam_key, [])
        selected = run_for_camera(cam_key, cam_url, existing_pts)
        if selected is None:
            print("Aborted by user. Exiting without saving.")
            return
        # If user selected nothing and there were existing points, keep them; otherwise store selected (may be empty list)
        if selected:
            zones[cam_key] = selected
        else:
            # keep existing if exists, else set empty list
            if cam_key not in zones:
                zones[cam_key] = []

    # Save to Railway DB
    try:
        db.set_config_value("PARKING_ZONES", zones)
        print(json.dumps(zones))
        print("Saved PARKING_ZONES to Railway DB.")
    except Exception as e:
        print(f"Failed to save to DB: {e}")
        print(json.dumps(zones))

if __name__ == "__main__":
    main()
