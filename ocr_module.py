"""
OCR Module for License Plate Recognition (Feature 13)
Runs on the Raspberry Pi edge device.
Uses EasyOCR for offline plate text extraction.
"""
import cv2
import re
import logging

logger = logging.getLogger("OCR")

# Lazy-load EasyOCR reader to avoid slow startup when not needed
_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        try:
            import easyocr
            _reader = easyocr.Reader(['en'], gpu=False)
            logger.info("EasyOCR reader initialized.")
        except ImportError:
            logger.warning("easyocr not installed. OCR features disabled.")
            return None
    return _reader


# Philippine plate patterns: ABC 1234, ABC-1234, 1234-AB, etc.
PH_PLATE_PATTERN = re.compile(
    r'^[A-Z]{2,3}[\s\-]?\d{3,4}$|^\d{3,4}[\s\-]?[A-Z]{2,3}$'
)


def extract_plate(frame, bbox=None):
    """
    Extract license plate text from a frame.
    bbox: optional (x1, y1, x2, y2) to crop the region of interest
    Returns: (plate_text, confidence) or (None, 0.0)
    """
    reader = _get_reader()
    if reader is None:
        return None, 0.0

    try:
        if bbox:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            crop = frame[y1:y2, x1:x2]
        else:
            crop = frame

        if crop.size == 0:
            return None, 0.0

        # Preprocess for better OCR
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 11, 17, 17)

        results = reader.readtext(gray)

        for (box, text, conf) in results:
            cleaned = text.upper().replace(' ', '').replace('-', '')
            # Re-add dash for standard format
            if len(cleaned) >= 6:
                plate = cleaned[:3] + '-' + cleaned[3:] if cleaned[:3].isalpha() else cleaned
                if conf > 0.4:
                    return plate, conf

        return None, 0.0

    except Exception as e:
        logger.error(f"OCR extraction failed: {e}")
        return None, 0.0
