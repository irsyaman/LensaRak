# LensaRak

Camera-based inventory system for Petrosains' storeroom — built for the **AI Innovators Challenge 2026** (Stage 1), Team LensaRak, Universiti Kuala Lumpur British Malaysian Institute (UniKL BMI).

LensaRak uses YOLOv8 object detection to recognise storeroom items live from a webcam or a phone camera, and automatically updates a stock ledger — while routing anything it isn't confident about to a human review queue instead of guessing wrong.

## What's in this repo

| File | Purpose |
|---|---|
| `LensaRak.bat` | Menu-driven launcher — check-out, bulk return, review, history, export |
| `lensarak_ops.py` | Core ledger logic — check-out/return, confidence gate, human review workflow |
| `lensarak_vision.py` | Camera-agnostic YOLOv8 detection layer (webcam / phone / test-photo folder) |
| `lensarak_review.py` | Console for a human to clear items flagged for review |
| `lensarak_history.py` | Read-only transaction history viewer |
| `lensarak_export.py` | Exports the full ledger + current stock to CSV/Excel |
| `lensarak_clean_data.py` | One-off script that builds the SQLite database from the storeroom catalogue |
| `lensarak_train.py` | YOLOv8 training script for the item-recognition model |

## Key design decisions

- **Confidence gate**: any detection below 0.75 confidence does NOT update stock — it's held for a human to confirm, correct, or reject, with the camera's original read always kept alongside the human's correction.
- **Camera-agnostic**: the same detection pipeline runs on a laptop webcam or a phone acting as an IP camera — no code changes needed to switch.
- **Full audit trail**: every transaction records who scanned it, when, the camera's confidence, and whether — and how — a human reviewed it.

## Not included in this repo

The trained model weights, the storeroom item catalogue/dataset, and the SQLite database are excluded — the catalogue data belongs to Petrosains and is confidential to the AI Innovators Challenge.

---
AI Innovators Challenge 2026 · Team LensaRak
