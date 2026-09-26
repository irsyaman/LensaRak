# LensaRak — Demo Video Script (4-Person Team)

**Target length**: ≤5:00, ONE continuous take, unedited. Must show: the bulk-return scan, at least one failure case with recovery.

---

## Roles

| Role | Who does what |
|---|---|
| **R1 — Presenter / Narrator** | Talks to camera throughout, introduces the team and the problem, narrates what's happening on-screen |
| **R2 — Storeroom Operator** | Physically places/returns items on the table, performs the "deliberate low-confidence" moment |
| **R3 — System Operator** | Sits at the laptop, runs `LensaRak.bat`, presses SPACE/keys, runs the review tool |
| **R4 — Camera Operator** | Holds the recording phone, frames the shot so BOTH the laptop screen and the item table are visible throughout — this is the single continuous recording |

**Setup note**: R4's phone is the *recording* camera (for the video). A **separate** phone or the laptop's webcam is the *system's* input camera (what LensaRak actually uses to detect items) — don't mix these two up. Position the laptop so its screen and the item table are both in R4's frame at once, or have R4 pan smoothly between them.

---

## Pre-Roll Checklist (before hitting record)

- [ ] **Check `best.pt` is the right model before anything else.** The one in the LensaRak folder right now may be an OLDER run, not the latest merged-dataset training. Check `runs\detect\train-4\results.csv` for the newest completed epoch, then promote it:
  `copy "runs\detect\train-4\weights\best.pt" "best.pt"`
  Safe to do any time — checkpoints save every epoch, training doesn't need to finish first.
- [ ] Run a silent test pass (no recording) — confirm detection works, no errors
- [ ] Items arranged: a mix of easy-to-detect items + ONE item deliberately set up to fail (odd angle / partly hidden / a class the model is weak on)
- [ ] `lensarak_review.py` queue is empty before starting (run it once, clear it, so anything flagged during the take is from THIS take only)
- [ ] Laptop charged / plugged in
- [ ] Good lighting, tidy background
- [ ] All 4 people know their cue and roughly what they say — do ONE dry run without recording first

---

## Full Script

### [0:00–0:20] Team & Project Intro

**R1 (to camera):**
> "Hi, we're Team LensaRak, from UniKL BMI. This is our submission for the AI Innovators Challenge — a camera-based inventory system for Petrosains' storeroom."

*(R2, R3, R4 each say their name in ~2 seconds — quick, not scripted individually, just first name + a nod)*

---

### [0:20–0:50] The Problem

**R1 (to camera, gesturing to the table):**
> "Petrosains' storeroom has over a hundred catalogued items used across daily programmes. When a session ends, staff do a *bulk return* — dumping mixed items back all at once, while the next activation is already loading in. That moment is logged manually today, under time pressure — exactly where miscounts happen. LensaRak automates that moment."

---

### [0:50–1:10] Launching the System

**R3 (at laptop):** double-clicks `LensaRak.bat`, selects option **3 — Bulk return, webcam, live**

**R1 (narrating):**
> "Our system is camera-agnostic — it runs on a laptop webcam or a phone acting as an IP camera, whichever a store already has. Here we're using the webcam. Let's start a bulk return."

---

### [1:10–2:40] Live Bulk-Return Scan (core demo)

**R2:** places a mix of items on the table in front of the camera (2–4 items, including one bagged/packaged item if possible)

**R3:** presses **SPACE** to scan

**R1 (narrating over the detection):**
> "The system uses YOLOv8 to detect each item and read its confidence score. Anything above 0.75 gets applied to the ledger automatically. Watch the confidence numbers on screen."

**R3:** confirms quantities as prompted (ENTER to accept, or U to type a real count for a bagged item)

**R1 (if an OCR-read label appears):**
> "This bag has a printed quantity — the system reads it automatically with OCR, so no one has to count by hand."

---

### [2:40–3:40] Deliberate Failure Case

**R2:** places or angles ONE item so the camera struggles to identify it confidently (partial occlusion, odd angle, or a class the model hasn't seen much)

**R3:** presses **SPACE** again

**R1 (narrating, pointing at the low confidence number):**
> "Here the confidence is low. Instead of guessing, the system does NOT update stock — it flags this item for human review. This is deliberate: LensaRak is built to ask a human when it isn't sure, rather than silently getting it wrong."

**R3:** presses **Q** to close the live window

---

### [3:40–4:30] Recovery — Human Review

**R3 (at laptop):** runs the review tool (`lensarak_review.py` or option 5 in `LensaRak.bat`)

**R1 (narrating):**
> "This is our review console. It shows exactly what the camera saw and why it was flagged. A reviewer can confirm it's correct, fix the quantity, correct the item entirely, or reject it — and the system keeps a full record of both the camera's original read and the human's correction, so nothing is ever silently overwritten."

**R3:** demonstrates one option live (e.g. presses **C** to correct the item, or **ENTER** to approve)

---

### [4:30–5:00] Closing

**R1 (to camera):**
> "So LensaRak automates what it can confidently handle, and hands off to a human exactly when it can't. That's the core design of our system. Thank you."

*(all 4 face camera briefly, end recording)*

---

## Notes

- Keep pacing brisk — rehearse once before the real take so nobody's reading word-for-word on camera; use the lines above as a guide, not something to recite stiffly.
- If a line runs long and you're close to 5:00, cut from the closing statement first, not from the failure-case/recovery section — that part is a **hard requirement**.
- R1 can hold a printed or phone copy of this script off-camera as a cue sheet.
