"""
vision.py -- Thin Streamlit-facing wrapper around Task 1's real YOLO
detection code, imported directly from the root-level lensarak_vision.py
(same file LensaRak.bat's CLI options use) -- same model, same
CONFIDENCE_THRESHOLD, same confidence-gated review flow as the CLI
checkout/return options. No copy of that logic lives inside engine/.

The CLI version drives a live webcam/phone-stream loop, which doesn't map
onto Streamlit's request/response model without extra dependencies
(streamlit-webrtc etc.). This wrapper instead supports a SNAPSHOT flow:
capture one photo (st.camera_input, or a plain file upload), run detection
on it once, let the operator confirm quantities, then apply exactly the same
lensarak_ops.check_out()/bulk_return() calls the live CLI tool uses -- so the
ledger, confidence gate and review queue behave identically either way.
"""

from __future__ import annotations

import numpy as np

import lensarak_vision as vision_core  # the same root-level module the CLI (LensaRak.bat) uses

CONFIDENCE_THRESHOLD = vision_core.CONFIDENCE_THRESHOLD
TEST_MODE = vision_core.TEST_MODE  # True when best.pt isn't present yet (generic COCO classes only)
MODEL_PATH = vision_core.MODEL_PATH


def load_model():
    """Loads the trained YOLO model (best.pt if present, else the generic
    yolov8n.pt fallback). Call once and cache the result (e.g. with
    st.cache_resource) -- loading it fresh per request is slow."""
    return vision_core.load_model()


def decode_image(image_bytes: bytes):
    """Turns raw image bytes (from st.camera_input / st.file_uploader) into
    the BGR numpy frame the model expects."""
    import cv2
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def detect(model, frame) -> list[tuple]:
    """Raw per-box detections: [(item_code, confidence, bbox), ...]."""
    return vision_core.detect_items(model, frame)


def group(detections_raw: list[tuple]) -> list[dict]:
    """Groups raw detections by item_code -- returns
    [{"item_code", "qty", "confidence", "bbox"}, ...], exactly the "scans"
    shape lensarak_ops.check_out()/bulk_return() expect."""
    return vision_core.group_detections(detections_raw)


def annotate(frame, detections_raw: list[tuple]):
    """Draws bounding boxes + labels (green = confident, orange = below
    CONFIDENCE_THRESHOLD) -- pass the RAW detect() output, not the grouped one."""
    return vision_core.draw_detections(frame, detections_raw)


def encode_image(frame) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".png", frame)
    return buf.tobytes() if ok else b""
