# Camera RTSP URLs
CAM1_URL = "rtsp://192.168.8.2:554/stream"
CAM2_URL = "rtsp://192.168.8.199:554/stream"

# Path to YOLO model (vehicle detection)
MODEL_PATH = "models/yolov8s.hef"

# Path to CCTV AI model (garbage/trash detection)
CCTV_AI_MODEL_PATH = "models/cctv_ai.hef"

# Directory to save violation images
SAVE_DIR = "static/violations"

DETECTION_THRESHOLD = 0.3      # Minimum confidence (0.0 to 1.0)
VIOLATION_TIME_THRESHOLD = 100
REPEAT_CAPTURE_INTERVAL = 60
# Define parking zones for each camera


#reverted to commit 881c3d6
PARKING_ZONES = {"Camera_1": [[182, 359], [634, 434], [646, 200], [590, 194]], "Camera_2": [[6, 289], [303, 239], [291, 99], [368, 93], [554, 191], [616, 193], [790, 279], [425, 441], [9, 437], [6, 288]]}
