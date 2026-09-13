"""
LensaRak — Layer 2: Vision (camera-agnostic detection -> ledger)

Reads frames from ANY camera source (webcam, phone stream, or a folder of test
photos — all behind one .read() interface, per the camera-agnostic architecture
decision), runs YOLOv8 detection, and feeds what it recognises straight into
check_out() / bulk_return() from lensarak_ops.py.

SETUP
-----
    pip install ultralytics opencv-python

TWO WAYS TO RUN THIS TODAY
---------------------------
1. TEST MODE (works right now, zero setup, before Roboflow training is done):
   No "best.pt" in this folder -> falls back to the generic pretrained
   "yolov8n.pt" (COCO's 80 everyday classes: person, cup, scissors, laptop...).
   It will NOT recognise your real storeroom items correctly by item_code —
   that's expected. This mode only proves the camera -> detection -> ledger
   PLUMBING works end-to-end, so you can build/debug this file in parallel
   while Roboflow trains your real model.

2. REAL MODE (once your Roboflow/Colab training is done):
   Drop your trained weights file next to this script, named "best.pt".
   The moment it exists, this script automatically switches to it.

   IMPORTANT — when you label in Roboflow, name each class EXACTLY as the
   item_code from the database (e.g. "E001", "T009", "S005") instead of a
   human name like "arduino_uno". That makes the model's output already be
   an item_code — CLASS_TO_ITEM_CODE below stays empty and there is one
   less thing that can silently go wrong.

OPTIONAL — GEMINI SECOND OPINION (off unless you set an API key)
------------------------------------------------------------------
YOLOv8 alone can be low-confidence on a still-small dataset. When a
detection falls BELOW CONFIDENCE_THRESHOLD, this script can optionally
crop that region and ask Gemini vision to confirm (or correct) which
catalogue item it actually is, BEFORE deciding whether to flag it to
needs_review. This is a genuine second opinion, not a silent override:

  - Gemini is only consulted for detections that already failed the
    confidence gate — it never touches a confident YOLO read.
  - If Gemini is confident too, the item is recorded with
    recognition_source="vision_auto+gemini_assist" (fully traceable —
    never blended into a plain "vision_auto" record) and its confidence
    is raised enough to clear the gate.
  - If Gemini is also unsure, or disagrees without confidence, the
    original YOLO result is used unchanged and it still goes to
    needs_review — nothing is ever forced through.
  - No API key set -> this whole feature is skipped automatically and
    behaviour is identical to before (safe to leave off, e.g. for a
    demo run with no internet).

Setup (only if you want this on):
    pip install google-genai
    Get a free API key: https://aistudio.google.com/apikey
    python lensarak_vision.py --source webcam --mode bulk_return --live --gemini-key AIza...
    (or set it once: set GEMINI_API_KEY=AIza...   [Windows]  /  export GEMINI_API_KEY=AIza... [mac/Linux])

USAGE EXAMPLES
---------------
    # Laptop webcam, single scan, check something out:
    python lensarak_vision.py --source webcam --arg 0 --mode check_out

    # Phone via an IP-camera app (e.g. "IP Webcam" on Android — use its
    # MJPEG stream URL), live preview, press SPACE to check out, q to quit:
    python lensarak_vision.py --source phone --arg http://192.168.1.23:8080/video --live --mode check_out

    # No camera yet — replay a folder of test photos as a bulk return:
    python lensarak_vision.py --source folder --arg ./test_photos --mode bulk_return
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import cv2

import lensarak_ops as ops

# --------------------------------------------------------------------------- #
# CONFIG
# --------------------------------------------------------------------------- #

DB_PATH = Path(__file__).parent / "lensarak_inventory.db"

_TRAINED_WEIGHTS = Path(__file__).parent / "best.pt"
MODEL_PATH = str(_TRAINED_WEIGHTS) if _TRAINED_WEIGHTS.exists() else "yolov8n.pt"
TEST_MODE = not _TRAINED_WEIGHTS.exists()

# Only needed if your Roboflow class names are NOT already item_codes.
# Example: {"arduino_uno": "E001", "scissors": "T001"}
CLASS_TO_ITEM_CODE: dict[str, str] = {
    # Labeling typo caught during a pre-demo sanity check: one class in the
    # trained model came out as "S13" instead of "S013" (Clipboard). Without
    # this, a detected clipboard would fail with "No such item: S13" instead
    # of being logged -- verified against every one of the model's 61 classes
    # against the catalogue; this was the only mismatch.
    "S13": "S013",
    # Deliberate confusion-merge, NOT a labeling typo: the model's top guess
    # for a real Arduino Uno (E001) keeps coming out as "E003" (Motor
    # Shield) -- they look alike to it. Arduino Uno matters for the demo and
    # Motor Shield isn't shown as its own item, so every "E003" detection is
    # forced to register as E001 instead. Trade-off: Motor Shield can no
    # longer be logged as itself under this build -- remove this line first
    # if Motor Shield ever needs to be scanned/checked out for real.
    "E003": "E001",
}

# item_codes that are dropped from detection output entirely, before they
# ever reach the screen or the ledger -- as if the model didn't have that
# class at all. Use this for a weak class that (a) isn't needed for the
# demo and (b) keeps firing confidently on the WRONG object (e.g. a sensor
# module getting called "E009 DC Motor" at 0.97), since that's more
# disruptive during recording than simply detecting nothing for that spot.
# Safe to add to any time -- doesn't touch training data or best.pt, and an
# excluded class never gets silently misapplied to stock, it just never
# shows up.
EXCLUDED_ITEM_CODES: set[str] = {
    "E009",  # DC Motor 3V -- not used in the demo; kept confusing IR sensor modules for it
}

CONFIDENCE_THRESHOLD = ops.CONFIDENCE_THRESHOLD  # same 0.75 used in lensarak_ops.py

# Optional Gemini second-opinion for low-confidence detections (see the
# OPTIONAL -- GEMINI SECOND OPINION note at the top of this file). Empty by
# default -- set via --gemini-key or the GEMINI_API_KEY environment variable.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"
# Confidence assigned to a detection Gemini confirms/corrects -- deliberately
# just above CONFIDENCE_THRESHOLD, not a fake 0.99, so it's honest about
# being an AI-assisted read rather than a high-certainty camera read.
GEMINI_ASSIST_CONFIDENCE = 0.80

# item_code -> item_name, loaded once from the DB in main(). The model only
# ever outputs item_code (that's what it was trained on) — this dict is what
# lets the screen and console show a human name next to it, so staff aren't
# stuck memorising "E003 = Motor Shield".
ITEM_NAMES: dict[str, str] = {}


def load_item_names(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT item_code, item_name FROM items").fetchall()
    return {row[0]: row[1] for row in rows}


def item_label(item_code: str) -> str:
    """'E003' -> 'E003 Motor Shield' (falls back to just the code if unknown)."""
    name = ITEM_NAMES.get(item_code)
    return f"{item_code} {name}" if name else item_code


# How long the live scan waits, per item, for the worker to press a key
# before auto-confirming the camera's own count (e.g. 1 for a sealed bag).
# Sits inside the 3-5s range requested — tune freely.
QUANTITY_CONFIRM_TIMEOUT_SEC = 4.0


# --------------------------------------------------------------------------- #
# Camera sources — the camera-agnostic layer.
# Every source below exposes the same .read() -> frame-or-None interface, so
# nothing past this point cares whether the frame came from a phone, a
# webcam, or a folder of test photos.
# --------------------------------------------------------------------------- #

class CameraSource:
    def read(self):
        raise NotImplementedError

    def release(self):
        pass


class WebcamSource(CameraSource):
    """Laptop's built-in camera, or a USB webcam, by device index (usually 0)."""

    def __init__(self, index: int = 0):
        # Windows' default backend (MSMF) is flaky with some webcam drivers —
        # DirectShow is usually more reliable there. Try it first, fall back
        # to the plain default backend (fine on macOS/Linux) if that fails.
        import platform
        self.cap = None
        if platform.system() == "Windows":
            self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if not self.cap.isOpened():
                self.cap.release()
                self.cap = None
        if self.cap is None:
            self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(
                f"Could not open webcam index {index}. Check: (1) Windows Settings > "
                f"Privacy & security > Camera has 'Let desktop apps access your camera' ON, "
                f"(2) no other app (Teams/Zoom/Camera) is currently using it, "
                f"(3) try --arg 1 in case your camera isn't index 0."
            )
        # Ask for a bigger capture size — most webcams default to a small
        # resolution (e.g. 640x480), which is both a tiny preview window AND
        # coarser detail for the model to detect small items with. The camera
        # ignores this if it doesn't support 720p; harmless either way.
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def release(self):
        self.cap.release()


_ROTATE_CODES = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


class PhoneStreamSource(CameraSource):
    """
    Phone as camera, via an IP-camera app (e.g. "IP Webcam" on Android,
    or "DroidCam"). Point `url` at that app's video stream.

    Many phone IP-camera apps send the raw sensor frame (landscape) even
    when the phone is being held upright (portrait), with no rotation
    metadata OpenCV respects -- the preview window then looks sideways.
    `rotate` (0/90/180/270, degrees clockwise) corrects that in software;
    it has nothing to do with the network connection, so if the stream
    looks rotated but otherwise works, just re-run with a different
    --phone-rotate value (try 90 first, then 270) until it looks upright.
    """

    def __init__(self, url: str, rotate: int = 0):
        if rotate not in (0, 90, 180, 270):
            raise ValueError("rotate must be one of 0, 90, 180, 270")
        self.rotate_code = _ROTATE_CODES.get(rotate)

        # Force the FFMPEG backend explicitly -- on Windows, cv2.VideoCapture(url)
        # with no backend hint sometimes picks a backend that can't handle an
        # MJPEG-over-HTTP stream and fails instantly, even though the exact same
        # URL opens fine in a browser. CAP_FFMPEG is the one that reliably
        # understands this kind of stream.
        self.cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            # Retry once without forcing a backend, in case this OpenCV build's
            # FFMPEG support is missing/broken and the default backend would
            # have worked.
            self.cap = cv2.VideoCapture(url)
        if not self.cap.isOpened():
            raise RuntimeError(
                f"Could not open phone stream at {url}\n"
                "Checklist:\n"
                f"  1. Open {url} directly in a browser on THIS computer first.\n"
                "     If it doesn't load there, this is a network problem, not a script problem\n"
                "     (phone/laptop not on the same WiFi, IP changed, firewall blocking it).\n"
                "  2. Make sure the URL ends in the MJPEG endpoint (usually /video),\n"
                "     not a bare host:port.\n"
                "  3. If it DOES load in a browser but this still fails, your OpenCV build\n"
                "     may be missing FFMPEG support -- try: pip install opencv-python --upgrade --force-reinstall"
            )

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        if self.rotate_code is not None:
            frame = cv2.rotate(frame, self.rotate_code)
        return frame

    def release(self):
        self.cap.release()


class ImageFolderSource(CameraSource):
    """No camera at all — replay a folder of test photos, one per .read() call."""

    def __init__(self, folder: str):
        folder_path = Path(folder)
        self.paths = sorted(folder_path.glob("*.jpg")) + sorted(folder_path.glob("*.png")) \
            + sorted(folder_path.glob("*.jpeg"))
        if not self.paths:
            raise RuntimeError(f"No .jpg/.jpeg/.png files found in {folder}")
        self.i = 0

    def read(self):
        if self.i >= len(self.paths):
            return None
        frame = cv2.imread(str(self.paths[self.i]))
        self.i += 1
        return frame


def make_source(kind: str, arg: str, phone_rotate: int = 0) -> CameraSource:
    if kind == "webcam":
        return WebcamSource(int(arg))
    if kind == "phone":
        return PhoneStreamSource(arg, rotate=phone_rotate)
    if kind == "folder":
        return ImageFolderSource(arg)
    raise ValueError(f"Unknown source kind: {kind}")


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

def load_model():
    from ultralytics import YOLO
    print(f"Loading model: {MODEL_PATH}"
          + ("  <-- TEST MODE: generic COCO classes, NOT your real items yet" if TEST_MODE else ""))
    return YOLO(MODEL_PATH)


def detect_items(model, frame) -> list[tuple[str, float, tuple]]:
    """
    Runs YOLO on one frame. Returns a list of (item_code, confidence, bbox) —
    one per detected object, with the model's raw class name already mapped
    to your item_code (via CLASS_TO_ITEM_CODE, or the identity mapping if your
    Roboflow classes were already named as item_codes).
    """
    results = model.predict(frame, verbose=False)[0]
    detections = []
    for box in results.boxes:
        class_name = model.names[int(box.cls[0])]
        confidence = float(box.conf[0])
        item_code = CLASS_TO_ITEM_CODE.get(class_name, class_name)
        if item_code in EXCLUDED_ITEM_CODES:
            continue
        xyxy = tuple(int(v) for v in box.xyxy[0].tolist())
        detections.append((item_code, confidence, xyxy))
    return detections


def draw_detections(frame, detections):
    """Draws boxes + labels on a copy of the frame — green if confident, orange if not."""
    annotated = frame.copy()
    for item_code, confidence, (x1, y1, x2, y2) in detections:
        ok = confidence >= CONFIDENCE_THRESHOLD
        color = (60, 180, 75) if ok else (14, 133, 226)  # BGR: green / orange
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        label = f"{item_label(item_code)}  {confidence:.2f}"
        cv2.putText(annotated, label, (x1, max(y1 - 8, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    return annotated


# --------------------------------------------------------------------------- #
# Ledger integration
# --------------------------------------------------------------------------- #

def group_detections(detections):
    """
    Groups raw (item_code, confidence, bbox) detections by item_code into
    scans: [{"item_code", "qty", "confidence", "bbox"}, ...].

    Default qty per group = how many boxes the camera actually saw for that
    item_code. For loose items on a table that's the real count. For a
    sealed bag/box (one bbox, contents not individually visible) it's
    always 1 — which is exactly the number confirm_quantities() below lets
    OCR suggest and a human correct/confirm before it hits the ledger.
    "bbox" keeps the first box seen for that item_code, so OCR has
    something to crop and read a packaging label from.
    """
    if not detections:
        return []
    counts = Counter()
    min_conf: dict[str, float] = {}
    bbox_map: dict[str, tuple] = {}
    for item_code, confidence, bbox in detections:
        counts[item_code] += 1
        min_conf[item_code] = min(min_conf.get(item_code, 1.0), confidence)
        bbox_map.setdefault(item_code, bbox)
    return [
        {"item_code": code, "qty": qty, "confidence": min_conf[code], "bbox": bbox_map[code]}
        for code, qty in counts.items()
    ]


_TESSERACT_READY = None  # None = not checked yet; True/False after the first attempt


def _ensure_tesseract():
    """
    Confirms pytesseract can actually find the tesseract.exe binary, and
    fixes the #1 Windows gotcha automatically: the installer very often
    does NOT add tesseract.exe to PATH, so a plain `pip install pytesseract`
    still fails at runtime with TesseractNotFoundError even though the
    program is sitting right there on disk. Tries the two standard install
    locations before giving up. Result is cached — this only probes once.
    """
    global _TESSERACT_READY
    if _TESSERACT_READY is not None:
        return _TESSERACT_READY

    import pytesseract
    try:
        pytesseract.get_tesseract_version()
        _TESSERACT_READY = True
        return True
    except Exception:
        pass

    import platform
    if platform.system() == "Windows":
        for candidate in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if Path(candidate).exists():
                pytesseract.pytesseract.tesseract_cmd = candidate
                try:
                    pytesseract.get_tesseract_version()
                    _TESSERACT_READY = True
                    return True
                except Exception:
                    continue

    _TESSERACT_READY = False
    print("  (OCR unavailable — Tesseract not found on PATH or in its default install "
          "folder. Quantity prompts will still work, just without an OCR-suggested "
          "default. See the README note in this file for how to fix it.)")
    return False


def ocr_suggest_qty(frame, bbox):
    """
    Offline OCR (Tesseract — no internet, no API) on the cropped item
    region. Tries to read a packaging label like "Arduino UNO (30)" and
    pull out the quantity, satisfying the FAQ's "interpret packaging
    labels, OCR text... visual context" requirement without depending on
    WiFi — this runs the same at Store 3 / the Chemical Room as anywhere
    else.

    Returns an int if it found a plausible quantity, else None. A miss (or
    a wrong read) is never applied silently — confirm_quantities() always
    shows it as a *suggestion* the worker confirms with ENTER or overrides
    with U, which is the "allow human confirmation when visual ambiguity
    is too high" half of the same requirement.
    """
    try:
        import pytesseract
    except ImportError:
        return None
    if not _ensure_tesseract():
        return None
    if bbox is None:
        return None
    x1, y1, x2, y2 = bbox
    h_frame, w_frame = frame.shape[:2]
    x1, y1 = max(x1, 0), max(y1, 0)
    x2, y2 = min(x2, w_frame), min(y2, h_frame)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # Normalise to a consistent size regardless of how big the crop already
    # is — Tesseract wants a specific text-height range, and a crop that's
    # merely "not tiny" (e.g. ~380px) can still read empty without this.
    h, w = gray.shape[:2]
    scale = 600 / max(h, w)
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    try:
        text = pytesseract.image_to_string(gray)
    except Exception:
        return None

    # A label like "Arduino UNO (30)" -> prefer the number in parentheses
    # (that's the quantity convention on the storeroom's own bags); fall
    # back to the largest bare number found if there are no parentheses.
    paren = re.findall(r"\((\d+)\)", text)
    candidates = paren if paren else re.findall(r"\d+", text)
    if not candidates:
        return None
    try:
        qty = max(int(n) for n in candidates)
    except ValueError:
        return None
    return qty if qty > 0 else None


# --------------------------------------------------------------------------- #
# Gemini second opinion (optional -- see the module docstring)
# --------------------------------------------------------------------------- #

_gemini_client = None       # None = not tried yet; False = tried and unavailable; else the client
_gemini_warned = False      # so the "unavailable" note only prints once, not per detection


def _get_gemini_client():
    """Lazily creates the Gemini client on first use. Returns None if no API
    key is set or the SDK/network isn't available -- callers must treat that
    as "skip the second opinion", never as an error."""
    global _gemini_client, _gemini_warned
    if _gemini_client is not None:
        return _gemini_client or None
    if not GEMINI_API_KEY:
        _gemini_client = False
        return None
    try:
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        if not _gemini_warned:
            print(f"  (Gemini second opinion unavailable — {e}. Continuing without it.)")
            _gemini_warned = True
        _gemini_client = False
    return _gemini_client or None


def gemini_second_opinion(frame, bbox, yolo_guess_code):
    """
    For ONE low-confidence YOLO detection: crops that region and asks Gemini
    to confirm or correct which catalogue item_code it is. Returns
    (item_code, raw_reply) if Gemini gave a confident, valid answer, else
    None -- a None here changes nothing, the caller just keeps the original
    YOLO result and it still goes to needs_review as before.
    """
    client = _get_gemini_client()
    if client is None or bbox is None or not ITEM_NAMES:
        return None

    x1, y1, x2, y2 = bbox
    h, w = frame.shape[:2]
    x1, y1 = max(x1, 0), max(y1, 0)
    x2, y2 = min(x2, w), min(y2, h)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    ok, buf = cv2.imencode(".jpg", crop)
    if not ok:
        return None

    catalogue = "\n".join(f"{code} = {name}" for code, name in sorted(ITEM_NAMES.items()))
    prompt = (
        "You are double-checking a low-confidence object detector reading for a "
        "storeroom inventory system. The detector's best guess for this cropped "
        f"photo was item_code '{yolo_guess_code}', but its confidence was too low "
        "to trust automatically.\n\n"
        "Catalogue of possible items (item_code = name):\n"
        f"{catalogue}\n\n"
        "Look only at the photo. Reply with EXACTLY one line, nothing else:\n"
        "  CONFIRM <item_code>   if you can confidently match it to ONE catalogue item\n"
        "  UNKNOWN                if you cannot tell confidently\n"
    )

    try:
        from google.genai import types
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Part.from_bytes(data=buf.tobytes(), mime_type="image/jpeg"), prompt],
        )
        text = (response.text or "").strip()
    except Exception as e:
        global _gemini_warned
        if not _gemini_warned:
            print(f"  (Gemini call failed — {e}. Continuing without it.)")
            _gemini_warned = True
        return None

    if text.upper().startswith("CONFIRM"):
        parts = text.split()
        if len(parts) >= 2 and parts[1] in ITEM_NAMES:
            return parts[1], text
    return None


def apply_gemini_assist(frame, grouped):
    """
    Runs gemini_second_opinion() over every group that's currently below
    CONFIDENCE_THRESHOLD. Mutates nothing silently into a "confident" result
    that hides where it came from: a Gemini-assisted item gets its own
    gemini_note field and a deliberately modest bumped confidence, so
    apply_check_out()/apply_bulk_return() can record it as
    "vision_auto+gemini_assist" rather than plain "vision_auto". Anything
    Gemini can't confirm is returned completely untouched.
    """
    if _get_gemini_client() is None:
        return grouped
    for g in grouped:
        if g["confidence"] >= CONFIDENCE_THRESHOLD:
            continue
        result = gemini_second_opinion(frame, g.get("bbox"), g["item_code"])
        if result is None:
            continue
        confirmed_code, raw_reply = result
        if confirmed_code != g["item_code"]:
            print(f"  Gemini second opinion: corrected {item_label(g['item_code'])} "
                  f"-> {item_label(confirmed_code)}")
            g["item_code"] = confirmed_code
        else:
            print(f"  Gemini second opinion: confirmed {item_label(g['item_code'])} "
                  f"(camera confidence was {g['confidence']:.2f})")
        g["confidence"] = max(g["confidence"], GEMINI_ASSIST_CONFIDENCE)
        g["gemini_note"] = raw_reply
        # Canonical field apply_check_out()/apply_bulk_return() read to record
        # provenance honestly -- never silently blended into plain "vision_auto".
        g["recognition_source"] = "vision_auto+gemini_assist"
    return grouped


def _draw_banner(frame, lines):
    """Translucent bar across the bottom of the frame with the given text lines. Mutates frame in place."""
    h, w = frame.shape[:2]
    bar_h = 26 + 28 * len(lines)
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - bar_h), (w, h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.78, frame, 0.22, 0, frame)
    y = h - bar_h + 26
    for line in lines:
        cv2.putText(frame, line, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        y += 28


def confirm_quantities(frozen_frame, grouped):
    """
    Interactive per-item quantity check, run once per SPACE-scan in run_live()
    on the frame that was just captured (camera pauses on it while the worker
    responds — nothing is lost by looking away from a live feed).

    Before showing the prompt, tries offline OCR (ocr_suggest_qty) on that
    item's crop — if a packaging label is readable ("Arduino UNO (30)"), the
    suggested default becomes 30 instead of the raw box count (1). Either
    way:
      ENTER           confirm the suggested count immediately.
      U                switch to typing a real count (e.g. if OCR misread
                        the label, or there's no label to read) — digits +
                        Backspace + ENTER to save, ESC to cancel typing.
      (no key pressed) after QUANTITY_CONFIRM_TIMEOUT_SEC, auto-confirms the
                        suggested count — exactly as if ENTER was pressed —
                        so one distracted worker never blocks the line.

    Returns the same scans shape as group_detections(), qty overridden where
    OCR read a label or the worker typed one.
    """
    confirmed = []
    for g in grouped:
        item_code, confidence = g["item_code"], g["confidence"]
        ocr_qty = ocr_suggest_qty(frozen_frame, g.get("bbox"))
        default_qty = ocr_qty if ocr_qty else g["qty"]
        qty_source = "OCR label" if ocr_qty else "camera count"
        typed = ""
        typing = False
        start = time.time()
        confirmed_qty = default_qty

        while True:
            remaining = QUANTITY_CONFIRM_TIMEOUT_SEC - (time.time() - start)
            display = frozen_frame.copy()
            if typing:
                lines = [
                    f"{item_label(item_code)}  ({qty_source}: {default_qty})",
                    f"Type quantity: {typed}_   [ENTER] save   [ESC] cancel",
                ]
            else:
                timer = f"auto-confirm in {remaining:.1f}s" if remaining > 0 else "confirming..."
                lines = [
                    f"{item_label(item_code)}  ({qty_source}: {default_qty})",
                    f"[ENTER] confirm {default_qty}    [U] change quantity    {timer}",
                ]
            _draw_banner(display, lines)
            cv2.imshow(WINDOW_NAME, display)
            key = cv2.waitKey(30) & 0xFF

            if typing:
                if key == 13:  # ENTER — save typed value (or default if nothing typed)
                    confirmed_qty = int(typed) if typed else default_qty
                    break
                elif key == 8:  # Backspace
                    typed = typed[:-1]
                elif key == 27:  # ESC — cancel typing, back to idle with a fresh timeout
                    typing, typed = False, ""
                    start = time.time()
                elif ord("0") <= key <= ord("9") and len(typed) < 4:
                    typed += chr(key)
            else:
                if key == 13:  # ENTER — confirm the default now, no need to wait
                    confirmed_qty = default_qty
                    break
                elif key in (ord("u"), ord("U")):
                    typing, typed = True, ""
                elif remaining <= 0:  # timeout — same as pressing ENTER
                    confirmed_qty = default_qty
                    break

        print(f"  confirmed: {item_label(item_code)} x{confirmed_qty}")
        scan = {"item_code": item_code, "qty": confirmed_qty, "confidence": confidence}
        # Carry the Gemini-assist provenance through, if apply_gemini_assist() set
        # it on this group earlier -- otherwise this key is simply absent and
        # downstream code falls back to its normal "vision_auto" behaviour.
        if g.get("recognition_source"):
            scan["recognition_source"] = g["recognition_source"]
        confirmed.append(scan)

    return confirmed


def apply_check_out(conn, scans, actor, location_code):
    if not scans:
        print("  Nothing detected.")
        return
    for s in scans:
        item_code, qty, confidence = s["item_code"], s["qty"], s["confidence"]
        # "vision_auto" by default; becomes "vision_auto+gemini_assist" when
        # apply_gemini_assist() confirmed/corrected this item -- never silently
        # hidden as a plain camera read.
        recognition_source = s.get("recognition_source", "vision_auto")
        try:
            r = ops.check_out(
                conn, item_code, qty_scanned=qty, actor=actor,
                location_code=location_code, recognition_source=recognition_source,
                confidence=confidence,
            )
            gemini_flag = "  (Gemini-assisted)" if recognition_source.endswith("gemini_assist") else ""
            if r.requires_review:
                print(f"  {item_label(item_code):32s} x{qty:<3} conf={confidence:.2f}  "
                      f"-> HELD for review (low confidence) — stock unchanged, "
                      f"clear it in option 5{gemini_flag}")
            else:
                flag = ("" if confidence >= CONFIDENCE_THRESHOLD else "  (low confidence)") + gemini_flag
                print(f"  {item_label(item_code):32s} x{qty:<3} conf={confidence:.2f}  "
                      f"-> available={r.available_quantity_after}{flag}")
        except ops.LensaRakError as e:
            print(f"  {item_label(item_code):32s} x{qty:<3} conf={confidence:.2f}  -> ERROR: {e}")


def apply_bulk_return(conn, scans, actor, location_code):
    if not scans:
        print("  Nothing detected.")
        return

    summary = ops.bulk_return(conn, scans, actor=actor, location_code=location_code,
                               confidence_threshold=CONFIDENCE_THRESHOLD)
    print(f"  applied      : {[(item_label(r.item_code), r.resolved_qty) for r in summary.applied]}")
    print(f"  needs_review : {[(item_label(r.item_code), r.reason) for r in summary.needs_review]}")
    if summary.errors:
        print(f"  errors       : {[(item_label(r.item_code), r.reason) for r in summary.errors]}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def run_single_shot(conn, model, source, mode, actor, location_code):
    frame = source.read()
    if frame is None:
        print("No frame captured.")
        return
    detections = detect_items(model, frame)
    scans = group_detections(detections)  # no live window here, so no interactive quantity prompt —
                                           # qty defaults to what the camera saw (1 per box)
    scans = apply_gemini_assist(frame, scans)  # optional -- no-op unless a Gemini key is set
    print(f"Detected {len(detections)} object(s) across {len(scans)} item type(s).")
    if mode == "check_out":
        apply_check_out(conn, scans, actor, location_code)
    else:
        apply_bulk_return(conn, scans, actor, location_code)
    conn.commit()


WINDOW_NAME = "LensaRak - Vision"


def run_live(conn, model, source, mode, actor, location_code):
    """
    Opens a preview window. SPACE captures the current frame, then walks
    through a quantity check per detected item before committing — ENTER
    accepts the auto-detected count (usually 1), U lets the worker type the
    real count (e.g. a bagged item the camera can't see inside), and it
    auto-confirms after a few seconds if nobody responds. Q quits. This is
    the mode to use for the actual demo video.
    """
    print("Live preview — press SPACE to scan, Q to quit. "
          "(Window is resizable — drag a corner to make it bigger.)")
    # WINDOW_NORMAL (instead of imshow's default WINDOW_AUTOSIZE) makes the
    # window resizable and lets us start it bigger than the raw frame size —
    # this is what was making the window look tiny.
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 1152, 648)

    while True:
        frame = source.read()
        if frame is None:
            print("Camera/stream ended.")
            break

        preview_detections = detect_items(model, frame)
        annotated = draw_detections(frame, preview_detections)
        cv2.imshow(WINDOW_NAME, annotated)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord(" "):
            grouped = group_detections(preview_detections)
            grouped = apply_gemini_assist(frame, grouped)  # optional -- no-op unless a Gemini key is set;
                                                            # uses the raw frame (not annotated) for a clean crop
            print(f"\nDetected {len(preview_detections)} object(s) across {len(grouped)} item type(s).")
            if not grouped:
                print("  Nothing detected.")
                continue
            # Camera pauses on this frame (annotated, boxes already drawn) while
            # the worker confirms/edits quantity per item — see confirm_quantities().
            confirmed = confirm_quantities(annotated, grouped)
            if mode == "check_out":
                apply_check_out(conn, confirmed, actor, location_code)
            else:
                apply_bulk_return(conn, confirmed, actor, location_code)
            conn.commit()

    cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description="LensaRak Layer 2 — vision to ledger")
    parser.add_argument("--source", choices=["webcam", "phone", "folder"], default="webcam")
    parser.add_argument("--arg", default="0", help="webcam index / phone stream URL / folder path")
    parser.add_argument("--phone-rotate", type=int, choices=[0, 90, 180, 270], default=0,
                         help="phone source only -- rotate the incoming frame clockwise by this many "
                              "degrees if the preview looks sideways (try 90 first, then 270)")
    parser.add_argument("--mode", choices=["check_out", "bulk_return"], default="check_out")
    parser.add_argument("--actor", default="Aiman")
    parser.add_argument("--location", default="STORE 1")
    parser.add_argument("--live", action="store_true",
                         help="open a live preview window instead of a single-shot scan")
    parser.add_argument("--gemini-key", default="",
                         help="optional -- Gemini API key for the low-confidence second-opinion "
                              "feature (get one free at https://aistudio.google.com/apikey). "
                              "Can also be set via the GEMINI_API_KEY environment variable instead. "
                              "Leave unset to run exactly as before, with the feature off.")
    args = parser.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"Database not found at {DB_PATH} — run lensarak_clean_data.py first.")

    if args.gemini_key:
        global GEMINI_API_KEY
        GEMINI_API_KEY = args.gemini_key

    model = load_model()
    source = make_source(args.source, args.arg, phone_rotate=args.phone_rotate)
    conn = sqlite3.connect(DB_PATH)
    ops.ensure_schema(conn)
    global ITEM_NAMES
    ITEM_NAMES = load_item_names(conn)

    try:
        gemini_status = "ON" if GEMINI_API_KEY else "off"
        print(f"Mode: {args.mode}  |  Source: {args.source}:{args.arg}  |  Live: {args.live}  "
              f"|  Gemini second opinion: {gemini_status}")
        if args.live:
            run_live(conn, model, source, args.mode, args.actor, args.location)
        else:
            run_single_shot(conn, model, source, args.mode, args.actor, args.location)
    finally:
        source.release()
        conn.close()


if __name__ == "__main__":
    main()
