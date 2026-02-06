import numpy as np
import cv2
import threading
import logging
import os
import requests
import base64
from datetime import datetime
import config

logger = logging.getLogger("ParkingApp")

# Try to import Hailo SDK
try:
    from hailo_platform import HEF, VDevice, InferVStreams, ConfigureParams, InputVStreamParams, OutputVStreamParams, HailoStreamInterface
    HAILO_AVAILABLE = True
except ImportError:
    logger.warning("Hailo SDK not found, falling back to remote or CPU detection.")
    HAILO_AVAILABLE = False

USE_REMOTE_DETECTION = os.environ.get("USE_REMOTE_DETECTION", "0") == "1"
RASPI_URL = os.environ.get("RASPI_URL", "http://192.168.18.32:5000/detect")
CLOUD_URL = os.environ.get(
    "CLOUD_URL",
    "https://illegal-parking-detection-flask.up.railway.app/api/upload_event"
)

# Offset applied to cctv_ai model class IDs to avoid conflicts with yolov8s COCO IDs
CCTV_AI_CLASS_OFFSET = 100

class DetectionResult:
    def __init__(self, xyxy, confs, clss):
        self.xyxy = xyxy
        self.conf = confs
        self.cls = clss


if HAILO_AVAILABLE and not USE_REMOTE_DETECTION:
    # Hailo-based detection with dual models
    class HailoDetector:
        def __init__(self, hef_path, monitored_classes=None, class_offset=0):
            self.hef = HEF(hef_path)
            self.target = VDevice()
            self.lock = threading.Lock()
            params = ConfigureParams.create_from_hef(self.hef, interface=HailoStreamInterface.PCIe)
            self.network_group = self.target.configure(self.hef, params)[0]
            self.input_vstreams_params = InputVStreamParams.make(self.network_group)
            self.output_vstreams_params = OutputVStreamParams.make(self.network_group)
            self.input_info = self.hef.get_input_vstream_infos()[0]
            self.height, self.width, _ = self.input_info.shape
            # Classes to monitor (None = all classes)
            self.monitored_classes = monitored_classes
            # Offset to add to class IDs (for distinguishing models)
            self.class_offset = class_offset

        def preprocess(self, frame):
            resized = cv2.resize(frame, (self.width, self.height))
            return np.expand_dims(resized, axis=0).astype(np.uint8)

        def postprocess(self, raw_out):
            all_boxes, all_confs, all_clss = [], [], []
            nms_keys = [k for k in raw_out.keys() if 'nms' in k.lower() or 'output' in k.lower()]
            if not nms_keys:
                return DetectionResult(np.array([]), np.array([]), np.array([]))
            detections_by_class = raw_out[nms_keys[0]]
            batch_detections = detections_by_class[0]
            for class_id, class_detections in enumerate(batch_detections):
                if self.monitored_classes is not None and class_id not in self.monitored_classes:
                    continue
                for det in class_detections:
                    score = float(det[4])
                    if score >= config.DETECTION_THRESHOLD:
                        all_boxes.append([det[1], det[0], det[3], det[2]])
                        all_confs.append(score)
                        all_clss.append(class_id + self.class_offset)
            return DetectionResult(np.array(all_boxes), np.array(all_confs), np.array(all_clss))

        def run_detection(self, frames):
            results = []
            with self.lock:
                with self.network_group.activate():
                    with InferVStreams(self.network_group, self.input_vstreams_params, self.output_vstreams_params) as pipeline:
                        for frame in frames:
                            input_data = {self.input_info.name: self.preprocess(frame)}
                            raw_out = pipeline.infer(input_data)
                            results.append(self.postprocess(raw_out))
            return results

    # Vehicle detector (yolov8s) - person, car, motorcycle only (no bus=5, no truck=7)
    _vehicle_detector = None
    # CCTV AI detector (cctv_ai) - garbage/trash classes with offset
    _cctv_ai_detector = None

    def _merge_results(result_a, result_b):
        """Merge two DetectionResult objects into one."""
        if result_a.xyxy.size == 0 and result_b.xyxy.size == 0:
            return DetectionResult(np.array([]), np.array([]), np.array([]))
        if result_a.xyxy.size == 0:
            return result_b
        if result_b.xyxy.size == 0:
            return result_a
        return DetectionResult(
            np.concatenate([result_a.xyxy, result_b.xyxy]),
            np.concatenate([result_a.conf, result_b.conf]),
            np.concatenate([result_a.cls, result_b.cls])
        )

    def detect(frames):
        global _vehicle_detector, _cctv_ai_detector
        if _vehicle_detector is None:
            _vehicle_detector = HailoDetector(
                config.MODEL_PATH,
                monitored_classes=[0, 2, 3],  # person, car, motorcycle (no bus=5, no truck=7)
                class_offset=0
            )
        if _cctv_ai_detector is None:
            cctv_ai_path = getattr(config, "CCTV_AI_MODEL_PATH", "models/cctv_ai.hef")
            _cctv_ai_detector = HailoDetector(
                cctv_ai_path,
                monitored_classes=None,  # all 28 classes
                class_offset=CCTV_AI_CLASS_OFFSET
            )

        # Run both models and merge results
        vehicle_results = _vehicle_detector.run_detection(frames)
        cctv_ai_results = _cctv_ai_detector.run_detection(frames)

        merged = []
        for vr, cr in zip(vehicle_results, cctv_ai_results):
            merged.append(_merge_results(vr, cr))
        return merged

else:
    # CPU fallback using YOLOv8
    import warnings
    warnings.warn("Hailo detection unavailable, using YOLOv8 CPU fallback.")
    from ultralytics import YOLO

    _model = YOLO(config.MODEL_PATH)  # Make sure MODEL_PATH points to .pt model (not .hef)
    _cctv_ai_model = None
    _lock = threading.Lock()

    # Classes to keep from yolov8s (no bus=5, no truck=7)
    YOLO_KEEP_CLASSES = {0, 2, 3}  # person, car, motorcycle

    def detect(frames):
        global _cctv_ai_model
        if _cctv_ai_model is None:
            cctv_ai_pt = getattr(config, "CCTV_AI_MODEL_PATH", "models/cctv_ai.pt")
            # For CPU fallback, try .pt version
            if cctv_ai_pt.endswith('.hef'):
                cctv_ai_pt = cctv_ai_pt.replace('.hef', '.pt')
            if os.path.exists(cctv_ai_pt):
                _cctv_ai_model = YOLO(cctv_ai_pt)
            else:
                logger.warning(f"CCTV AI model not found at {cctv_ai_pt}, skipping.")

        results = []
        with _lock:
            for frame in frames:
                try:
                    # --- Vehicle detection (yolov8s) ---
                    res = _model(frame)[0]
                    if hasattr(res, 'boxes') and len(res.boxes):
                        xyxy = res.boxes.xyxy.cpu().numpy()
                        conf = res.boxes.conf.cpu().numpy()
                        clss = res.boxes.cls.cpu().numpy().astype(int)
                        # Filter to keep only person, car, motorcycle
                        mask = np.isin(clss, list(YOLO_KEEP_CLASSES))
                        xyxy = xyxy[mask]
                        conf = conf[mask]
                        clss = clss[mask]
                    else:
                        xyxy, conf, clss = np.array([]), np.array([]), np.array([])

                    # --- CCTV AI detection ---
                    if _cctv_ai_model is not None:
                        cctv_res = _cctv_ai_model(frame)[0]
                        if hasattr(cctv_res, 'boxes') and len(cctv_res.boxes):
                            c_xyxy = cctv_res.boxes.xyxy.cpu().numpy()
                            c_conf = cctv_res.boxes.conf.cpu().numpy()
                            c_clss = cctv_res.boxes.cls.cpu().numpy().astype(int) + CCTV_AI_CLASS_OFFSET
                            if xyxy.size > 0:
                                xyxy = np.concatenate([xyxy, c_xyxy])
                                conf = np.concatenate([conf, c_conf])
                                clss = np.concatenate([clss, c_clss])
                            else:
                                xyxy, conf, clss = c_xyxy, c_conf, c_clss

                    results.append(DetectionResult(xyxy, conf, clss))
                except Exception as e:
                    logger.error(f"Detection failed: {e}")
                    results.append(DetectionResult(np.array([]), np.array([]), np.array([])))
        return results

def upload_event_to_cloud(camera_id, frame, meta=None):
    """Uploads a violation event to the cloud."""
    try:
        _, buf = cv2.imencode('.jpg', frame)
        img_b64 = base64.b64encode(buf).decode('utf-8')
        payload = {
            "camera_id": camera_id,
            "timestamp": datetime.utcnow().isoformat(),
            "image": img_b64,
            "meta": meta or {}
        }
        resp = requests.post(CLOUD_URL, json=payload, timeout=10)
        if resp.ok:
            return True
        else:
            logger.error(f"Cloud upload failed: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Exception in upload_event_to_cloud: {e}")
        return False
