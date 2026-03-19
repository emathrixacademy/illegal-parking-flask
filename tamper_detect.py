"""
Camera Tampering Detection Module (Feature 14)
Runs on the Raspberry Pi edge device.
Detects obstruction, defocus/spray, and camera movement.
"""
import cv2
import numpy as np
import time
import logging

logger = logging.getLogger("TamperDetect")

# Try to import SSIM; fall back to manual comparison if unavailable
try:
    from skimage.metrics import structural_similarity as ssim
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False
    logger.warning("scikit-image not installed. SSIM-based scene change detection disabled.")


class TamperDetector:
    def __init__(self, camera_id, sensitivity=0.5):
        self.camera_id = camera_id
        self.reference_frame = None
        self.last_good_frame = None
        self.sensitivity = sensitivity
        self.blur_threshold = 100      # Laplacian variance threshold
        self.ssim_threshold = 0.4      # Below this = scene changed
        self.dark_threshold = 15       # Average pixel value below this = covered
        self.last_alert_time = 0
        self.cooldown = 60             # seconds between alerts

    def set_reference(self, frame):
        """Set the reference frame (call once when camera is properly positioned)."""
        self.reference_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.reference_frame = cv2.resize(self.reference_frame, (320, 240))

    def check(self, frame):
        """
        Check frame for tampering.
        Returns: (is_tampered: bool, tamper_type: str or None, details: dict)
        """
        now = time.time()
        if now - self.last_alert_time < self.cooldown:
            return False, None, {}

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (320, 240))
        self.last_good_frame = frame.copy()

        # 1. OBSTRUCTION CHECK — is the lens covered?
        avg_brightness = np.mean(small)
        if avg_brightness < self.dark_threshold:
            self.last_alert_time = now
            return True, "OBSTRUCTION", {"brightness": float(avg_brightness)}

        # 2. BLUR CHECK — is the lens sprayed or defocused?
        laplacian_var = cv2.Laplacian(small, cv2.CV_64F).var()
        if laplacian_var < self.blur_threshold:
            self.last_alert_time = now
            return True, "DEFOCUS", {"blur_score": float(laplacian_var)}

        # 3. SCENE CHANGE — has the camera been moved?
        if self.reference_frame is not None and HAS_SKIMAGE:
            score = ssim(self.reference_frame, small)
            if score < self.ssim_threshold:
                self.last_alert_time = now
                return True, "SCENE_CHANGE", {"ssim_score": float(score)}

        return False, None, {}
