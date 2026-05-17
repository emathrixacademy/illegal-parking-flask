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


PH_PLATE_PATTERN = re.compile(r'^([A-Z]{2,3})\s*[\-]?\s*(\d{3,4})$')


def format_ph_plate(raw_text):
    """Validate and format as Philippine plate (e.g., 'NAZ 312'). Returns None if invalid."""
    cleaned = raw_text.upper().strip()
    cleaned = re.sub(r'[^A-Z0-9]', '', cleaned)
    m = PH_PLATE_PATTERN.match(cleaned)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    if len(cleaned) >= 5:
        for i in range(2, 4):
            letters = cleaned[:i]
            digits = cleaned[i:]
            if letters.isalpha() and digits.isdigit() and 2 <= len(letters) <= 3 and 3 <= len(digits) <= 4:
                return f"{letters} {digits}"
    return None


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

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 11, 17, 17)

        results = reader.readtext(gray)

        best_plate = None
        best_conf = 0.0

        for (box, text, conf) in results:
            plate = format_ph_plate(text)
            if plate and conf > 0.4 and conf > best_conf:
                best_plate = plate
                best_conf = conf

        if best_plate:
            return best_plate, best_conf
        return None, 0.0

    except Exception as e:
        logger.error(f"OCR extraction failed: {e}")
        return None, 0.0
