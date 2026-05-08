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

PARKING_ZONES = {"Camera_1": [[330, 280], [300, 450], [700, 430], [620, 260]], "Camera_2": [[150, 380], [150, 440], [550, 310], [550, 250]]}
