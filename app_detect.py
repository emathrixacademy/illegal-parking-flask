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
    "https://web-production-dbb23.up.railway.app/api/upload_event"
)

class DetectionResult:
    def __init__(self, xyxy, confs, clss):
        self.xyxy = xyxy
        self.conf = confs
        self.cls = clss

if HAILO_AVAILABLE and not USE_REMOTE_DETECTION:
    _shared_vdevice = None
    _shared_lock = threading.Lock()

    def _get_vdevice():
        global _shared_vdevice
        if _shared_vdevice is None:
            _shared_vdevice = VDevice()
        return _shared_vdevice

    # Hailo-based detection
    VEHICLE_CLASSES = [0, 1, 2, 3, 5, 7]
    CCTV_AI_CLASSES = list(range(10))

    class HailoDetector:
        def __init__(self, hef_path, monitored_classes=None, conf_threshold=None):
            self.hef = HEF(hef_path)
            self.target = _get_vdevice()
            self.lock = _shared_lock
            params = ConfigureParams.create_from_hef(self.hef, interface=HailoStreamInterface.PCIe)
            self.network_group = self.target.configure(self.hef, params)[0]
            self.input_vstreams_params = InputVStreamParams.make(self.network_group)
            self.output_vstreams_params = OutputVStreamParams.make(self.network_group)
            self.input_info = self.hef.get_input_vstream_infos()[0]
            self.height, self.width, _ = self.input_info.shape
            self.monitored_classes = monitored_classes if monitored_classes is not None else VEHICLE_CLASSES
            self.conf_threshold = conf_threshold if conf_threshold is not None else config.DETECTION_THRESHOLD

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
                if class_id not in self.monitored_classes:
                    continue
                for det in class_detections:
                    score = float(det[4])
                    if score >= self.conf_threshold:
                        all_boxes.append([det[1], det[0], det[3], det[2]])
                        all_confs.append(score)
                        all_clss.append(class_id)
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

    _detector = None
    _cctv_detector = None
    CCTV_AI_MODEL = getattr(config, "CCTV_AI_MODEL_PATH", "models/cctv_ai.hef")
    CCTV_AI_CLASS_OFFSET = 100

    def detect(frames):
        global _detector, _cctv_detector
        if _detector is None:
            _detector = HailoDetector(config.MODEL_PATH, monitored_classes=VEHICLE_CLASSES)
        results = _detector.run_detection(frames)
        if os.path.exists(CCTV_AI_MODEL):
            try:
                if _cctv_detector is None:
                    _cctv_ai_threshold = getattr(config, "CCTV_AI_DETECTION_THRESHOLD", 0.1)
                    _cctv_detector = HailoDetector(CCTV_AI_MODEL, monitored_classes=CCTV_AI_CLASSES, conf_threshold=_cctv_ai_threshold)
                cctv_results = _cctv_detector.run_detection(frames)
                for i in range(len(results)):
                    if i < len(cctv_results):
                        cr = cctv_results[i]
                        if len(cr.xyxy) > 0:
                            offset_cls = cr.cls + CCTV_AI_CLASS_OFFSET
                            results[i] = DetectionResult(
                                np.concatenate([results[i].xyxy, cr.xyxy]) if len(results[i].xyxy) else cr.xyxy,
                                np.concatenate([results[i].conf, cr.conf]) if len(results[i].conf) else cr.conf,
                                np.concatenate([results[i].cls, offset_cls]) if len(results[i].cls) else offset_cls,
                            )
            except Exception as e:
                logger.warning(f"CCTV AI model failed: {e}")
        return results

else:
    # CPU fallback using YOLOv8
    import warnings
    warnings.warn("Hailo detection unavailable, using YOLOv8 CPU fallback.")
    from ultralytics import YOLO

    _model_path = config.MODEL_PATH
    if _model_path.endswith(".hef"):
        _model_path = "yolov8s.pt"
    _model = YOLO(_model_path)
    _lock = threading.Lock()

    CCTV_AI_MODEL = getattr(config, "CCTV_AI_MODEL_PATH", "models/cctv_ai.hef")
    _cctv_model = None
    CCTV_AI_CLASS_OFFSET = 100
    if CCTV_AI_MODEL and not CCTV_AI_MODEL.endswith(".hef"):
        try:
            _cctv_model = YOLO(CCTV_AI_MODEL)
            logger.info(f"Loaded CCTV AI model: {CCTV_AI_MODEL}")
        except Exception as e:
            logger.warning(f"Could not load CCTV AI model: {e}")
    elif CCTV_AI_MODEL and CCTV_AI_MODEL.endswith(".hef"):
        _cctv_pt = CCTV_AI_MODEL.replace(".hef", ".pt")
        if os.path.exists(_cctv_pt):
            try:
                _cctv_model = YOLO(_cctv_pt)
                logger.info(f"Loaded CCTV AI model (CPU fallback): {_cctv_pt}")
            except Exception as e:
                logger.warning(f"Could not load CCTV AI CPU model: {e}")

    def detect(frames):
        results = []
        with _lock:
            for frame in frames:
                try:
                    res = _model(frame)[0]
                    if hasattr(res, 'boxes') and len(res.boxes):
                        xyxy = res.boxes.xyxy.cpu().numpy()
                        conf = res.boxes.conf.cpu().numpy()
                        clss = res.boxes.cls.cpu().numpy().astype(int)
                    else:
                        xyxy, conf, clss = np.array([]), np.array([]), np.array([])

                    if _cctv_model is not None:
                        try:
                            _cctv_ai_thresh = getattr(config, "CCTV_AI_DETECTION_THRESHOLD", 0.1)
                            cctv_res = _cctv_model(frame, conf=_cctv_ai_thresh)[0]
                            if hasattr(cctv_res, 'boxes') and len(cctv_res.boxes):
                                c_xyxy = cctv_res.boxes.xyxy.cpu().numpy()
                                c_conf = cctv_res.boxes.conf.cpu().numpy()
                                c_cls = cctv_res.boxes.cls.cpu().numpy().astype(int) + CCTV_AI_CLASS_OFFSET
                                xyxy = np.concatenate([xyxy, c_xyxy]) if len(xyxy) else c_xyxy
                                conf = np.concatenate([conf, c_conf]) if len(conf) else c_conf
                                clss = np.concatenate([clss, c_cls]) if len(clss) else c_cls
                        except Exception as e:
                            logger.warning(f"CCTV AI detection failed: {e}")

                    results.append(DetectionResult(xyxy, conf, clss))
                except Exception as e:
                    logger.error(f"YOLOv8 CPU detection failed: {e}")
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