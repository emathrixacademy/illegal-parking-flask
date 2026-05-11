"""
Vision analysis module for DECONGESTILAGUNA.
Uses cloud vision API for plate reading and violation classification.
"""
import os
import cv2
import base64
import logging
import threading
import json
import re

logger = logging.getLogger("VisionAnalysis")

_VISION_KEY = os.environ.get("DCGL_VISION_KEY", "")
_lock = threading.Lock()
_client = None


def _get_client():
    global _client
    if _client is None and _VISION_KEY:
        try:
            import anthropic
            _client = anthropic.Anthropic(api_key=_VISION_KEY)
            logger.info("Vision analysis engine initialized.")
        except ImportError:
            logger.warning("anthropic package not installed. Vision analysis disabled.")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize vision engine: {e}")
            return None
    return _client


def analyze_violation(frame, bbox=None, camera_id="", vehicle_label="", duration_seconds=0):
    """
    Analyze a violation frame using cloud vision.
    Returns dict with plate_number, plate_confidence, violation_type, description, obstacles.
    Falls back gracefully if unavailable.
    """
    client = _get_client()
    if client is None:
        return None

    try:
        if bbox:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            h, w = frame.shape[:2]
            pad_x = int((x2 - x1) * 0.3)
            pad_y = int((y2 - y1) * 0.3)
            cx1 = max(0, x1 - pad_x)
            cy1 = max(0, y1 - pad_y)
            cx2 = min(w, x2 + pad_x)
            cy2 = min(h, y2 + pad_y)
            crop = frame[cy1:cy2, cx1:cx2]
        else:
            crop = frame

        _, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        img_b64 = base64.b64encode(buf).decode('utf-8')

        prompt = f"""Analyze this CCTV image of a parking violation in the Philippines.

Vehicle type detected: {vehicle_label}
Camera: {camera_id}
Duration parked: {duration_seconds} seconds

Respond ONLY with valid JSON (no markdown, no code blocks):
{{
  "plate_number": "detected plate text or null if unreadable",
  "plate_confidence": 0.0 to 1.0,
  "violation_type": "one of: ILLEGAL_PARKING, DOUBLE_PARKING, BLOCKING_TRAFFIC, NO_PARKING_ZONE, OBSTRUCTION, UNKNOWN",
  "description": "brief one-line description of what you see",
  "obstacles": ["list of any obstacles or objects near the vehicle"]
}}

Rules:
- Philippine plates follow format: ABC 1234 or 1234-AB (3 letters + 3-4 digits)
- If plate is partially visible, give your best reading with lower confidence
- If plate is not visible at all, set null and confidence 0
- Classify the violation based on context clues (road markings, signs, position)"""

        with _lock:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_b64
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }]
            )

        raw_text = response.content[0].text.strip()
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = json.loads(raw_text)

        result.setdefault("plate_number", None)
        result.setdefault("plate_confidence", 0.0)
        result.setdefault("violation_type", "UNKNOWN")
        result.setdefault("description", "")
        result.setdefault("obstacles", [])

        logger.info(f"Vision analysis: plate={result['plate_number']} "
                     f"conf={result['plate_confidence']} type={result['violation_type']}")
        return result

    except Exception as e:
        logger.error(f"Vision analysis failed: {e}")
        return None


_garbage_cache = {}
_garbage_cache_lock = threading.Lock()
GARBAGE_SCAN_INTERVAL = 30


def detect_garbage(frame, camera_id=""):
    """
    Scan full frame for garbage/trash using Claude Haiku vision.
    Returns list of dicts: [{label, description, region: [x1,y1,x2,y2]}]
    Caches results per camera to avoid excessive API calls.
    """
    import time
    now = time.time()
    with _garbage_cache_lock:
        cached = _garbage_cache.get(camera_id)
        if cached and (now - cached["time"]) < GARBAGE_SCAN_INTERVAL:
            return cached["results"]

    client = _get_client()
    if client is None:
        return []

    try:
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (640, int(640 * h / w)))
        _, buf = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 75])
        img_b64 = base64.b64encode(buf).decode('utf-8')

        prompt = f"""You are a CCTV garbage/trash detection system. Analyze this street camera image.
Camera: {camera_id}, Image size: {w}x{h}

Find ALL garbage, trash, litter, trash cans, debris, plastic bags, scattered waste, or any form of waste/rubbish visible in the image.

Respond ONLY with valid JSON (no markdown, no code blocks):
{{
  "garbage_found": true or false,
  "items": [
    {{
      "label": "type of garbage (e.g. TRASH_CAN, GARBAGE, PLASTIC_BAG, DEBRIS, LITTER)",
      "confidence": 0.0 to 1.0,
      "description": "brief description",
      "region": [x1_percent, y1_percent, x2_percent, y2_percent]
    }}
  ]
}}

Rules:
- region coordinates are PERCENTAGES (0-100) of image width/height
- Include even small items like scattered papers or plastic
- TRASH_CAN means a garbage bin/container
- GARBAGE means loose garbage/waste on the ground
- Be thorough — detect everything that is waste or waste-related"""

        with _lock:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_b64
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }]
            )

        raw_text = response.content[0].text.strip()
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = json.loads(raw_text)

        items = result.get("items", [])
        detections = []
        for item in items:
            region = item.get("region", [0, 0, 100, 100])
            x1 = int(region[0] / 100 * w)
            y1 = int(region[1] / 100 * h)
            x2 = int(region[2] / 100 * w)
            y2 = int(region[3] / 100 * h)
            detections.append({
                "label": item.get("label", "GARBAGE"),
                "confidence": item.get("confidence", 0.5),
                "description": item.get("description", ""),
                "region": [x1, y1, x2, y2]
            })

        logger.info(f"Garbage scan {camera_id}: found {len(detections)} items")
        with _garbage_cache_lock:
            _garbage_cache[camera_id] = {"time": now, "results": detections}
        return detections

    except Exception as e:
        logger.error(f"Garbage detection failed: {e}")
        return []


def is_available():
    """Check if vision analysis is configured and available."""
    return bool(_VISION_KEY)
