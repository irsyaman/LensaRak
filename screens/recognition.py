"""
recognition.py -- Inventory Recognition: camera/photo-based check-out or
return, using Task 1's real trained YOLO model + confidence gate (see
engine/vision.py, which wraps the root lensarak_vision.py unchanged).
Snapshot-based (st.camera_input), not a live video loop -- see
engine/vision.py docstring for why.
"""

from __future__ import annotations

import streamlit as st

import lensarak_ops as ops  # the same root-level module lensarak_vision.py uses
from engine import vision
from screens._common import empty_state, page_header, require_permission


@st.cache_resource(show_spinner="Loading recognition model (YOLO)...")
def _load_model():
    return vision.load_model()


def render(conn):
    page_header("Inventory Recognition", "Scan items with the camera for fast check-out or return.")

    if not require_permission("checkout_return"):
        return

    if vision.TEST_MODE:
        st.warning("⚠️ No `best.pt` (trained model) in this folder yet -- using the generic model "
                   "(standard COCO classes), NOT your actual items. Place `best.pt` at the project root "
                   "for real item recognition.", icon="⚠️")

    mode = st.radio("Mode", ["Check-out", "Return"], horizontal=True, key="vision_mode")
    location = st.text_input("Location", value="CHILLAX", key="vision_location")

    photo = st.camera_input("Take a photo of the item")
    if photo is None:
        empty_state("Take a photo to start recognition. No camera? Use the file uploader below.")
        photo = st.file_uploader("...or upload a photo", type=["jpg", "jpeg", "png"])
        if photo is None:
            return

    try:
        model = _load_model()
    except Exception as e:
        st.error(f"Model could not be loaded: {e}. Make sure `ultralytics` is installed "
                 f"(`pip install ultralytics`).")
        return

    frame = vision.decode_image(photo.getvalue())
    detections_raw = vision.detect(model, frame)
    grouped = vision.group(detections_raw)

    annotated = vision.annotate(frame, detections_raw)
    st.image(annotated, channels="BGR", caption="Detections (green = confident, orange = low confidence)")

    if not grouped:
        empty_state("No items detected in this photo. Try a different angle/lighting.")
        return

    item_names = {r["item_code"]: r["item_name"] for r in conn.execute("SELECT item_code, item_name FROM items")}

    st.markdown("#### Confirm quantities before submitting")
    confirmed = []
    for scan in grouped:
        code = scan["item_code"]
        label = item_names.get(code, code)
        low_conf = scan["confidence"] < vision.CONFIDENCE_THRESHOLD
        c1, c2, c3 = st.columns([3, 1, 1])
        c1.markdown(f"**{label}** ({code})" + ("  🟠 low confidence" if low_conf else "  🟢"))
        qty = c2.number_input("Quantity", min_value=0, value=int(scan["qty"]), key=f"vision_qty_{code}", label_visibility="collapsed")
        c3.caption(f"conf={scan['confidence']:.2f}")
        if qty > 0:
            confirmed.append({"item_code": code, "qty": qty, "confidence": scan["confidence"],
                               "unit": None, "recognition_source": "vision_auto"})

    actor = st.session_state.get("username")
    if st.button("✅ Submit to ledger", type="primary", key="vision_submit"):
        applied, held, errors = [], [], []
        if mode == "Check-out":
            for s in confirmed:
                try:
                    r = ops.check_out(conn, s["item_code"], qty_scanned=s["qty"], actor=actor,
                                       location_code=location, recognition_source=s["recognition_source"],
                                       confidence=s["confidence"])
                    (held if r.requires_review else applied).append((s["item_code"], s["qty"]))
                except ops.LensaRakError as e:
                    errors.append((s["item_code"], str(e)))
        else:
            summary = ops.bulk_return(conn, confirmed, actor=actor, location_code=location,
                                       confidence_threshold=vision.CONFIDENCE_THRESHOLD)
            applied = [(r.item_code, r.resolved_qty) for r in summary.applied]
            held = [(r.item_code, r.reason) for r in summary.needs_review]
            errors = [(r.item_code, r.reason) for r in summary.errors]

        if applied:
            st.success("Applied: " + ", ".join(f"{item_names.get(c, c)} x{q}" for c, q in applied))
        if held:
            st.warning("Held for review (low confidence) -- see Transactions: " +
                       ", ".join(f"{item_names.get(c, c)}" for c, _ in held))
        if errors:
            st.error("Error: " + ", ".join(f"{item_names.get(c, c)}: {msg}" for c, msg in errors))
        if not (applied or held or errors):
            st.info("No items with quantity > 0 to submit.")
