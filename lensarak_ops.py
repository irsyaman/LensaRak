"""
LensaRak — Fasa 2 support: check_out() and bulk_return()
AI Innovators Challenge 2026 (Petrosains)

Operates on lensarak_inventory.db (built by lensarak_clean_data.py).
On first use against that file, ensure_schema() adds three columns the
original schema didn't have yet (requires_review, reviewed, review_reason) —
safe to run against the already-delivered .db, it only ALTERs if missing.

Four scenarios this module is built to survive:

  1. Reusable vs. consumable are debited differently.
     consumable      -> available_quantity AND total_quantity both drop (it's gone).
     reusable_asset   -> only available_quantity drops (it's out on loan, still
                          counted in total_quantity until it's lost/damaged/disposed).

  2. Packaging normalisation. A scan of "2 packs" of an item sold 40-units-per-pack
     is converted to 80 base units *before* touching any balance — see
     resolve_quantity().

  3. Offline-safe idempotency. Every transaction carries a client_txn_id (a UUID
     generated at the moment it happens, on-device, with no server round trip).
     If the same client_txn_id is inserted twice — e.g. a Store 3 device retries
     a sync after losing connection mid-upload — the second insert is detected
     and treated as a no-op, not a double-deduction.

  4. Vision confidence gate. Both bulk_return() and check_out() take an optional
     confidence score per item. Anything below CONFIDENCE_THRESHOLD is recorded
     but held as requires_review=1 — its effect on stock is NOT applied until a
     human calls confirm_review() (e.g. via lensarak_review.py). Nothing gets
     silently trusted just because a camera said so, in either direction.

Run this file directly for a self-contained demo against a real copy of the
database (it makes its own throwaway copy, so it never touches your original):

    python3 lensarak_ops.py
"""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


CONFIDENCE_THRESHOLD = 0.75  # below this, a vision-recognised item needs a human to confirm it


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class LensaRakError(Exception):
    pass


class ItemNotFoundError(LensaRakError):
    pass


class InsufficientStockError(LensaRakError):
    pass


class AmbiguousUnitError(LensaRakError):
    pass


# --------------------------------------------------------------------------- #
# Schema migration — safe to call against the already-shipped .db
# --------------------------------------------------------------------------- #

def ensure_schema(conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row  # every function below relies on dict-style row access
    cols = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
    if "requires_review" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN requires_review INTEGER NOT NULL DEFAULT 0")
    if "reviewed" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0")
    if "review_reason" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN review_reason TEXT")
    conn.commit()

    _widen_recognition_source_check(conn)


def _widen_recognition_source_check(conn: sqlite3.Connection):
    """
    The database as originally shipped hard-codes recognition_source to
    CHECK (... IN ('vision_auto','vision_confirmed','manual')) -- too narrow for
    the newer 'vision_auto+gemini_assist' (Gemini second-opinion) and
    'manual_correction' (a human fixing a wrong detection) sources this module
    now writes. SQLite has no ALTER TABLE for an existing CHECK constraint, so
    this rebuilds the table in place: same columns, same rows, wider constraint.
    Safe to call every time -- it's a no-op once the constraint is already wide.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='transactions'"
    ).fetchone()
    if row is None or row[0] is None:
        return
    sql = row[0]
    if "gemini_assist" in sql and "manual_correction" in sql:
        return  # already widened -- nothing to do

    cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
    col_list = ", ".join(cols)

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE transactions RENAME TO transactions_old")
    conn.execute(
        """CREATE TABLE transactions (
            txn_id              INTEGER PRIMARY KEY AUTOINCREMENT,
            client_txn_id       TEXT NOT NULL UNIQUE,
            item_code           TEXT NOT NULL REFERENCES items(item_code),
            txn_type            TEXT NOT NULL CHECK (txn_type IN
                                  ('check_out','check_in','return','adjustment','damage','lost','disposed')),
            qty                 REAL NOT NULL,
            location_code       TEXT REFERENCES locations(location_code),
            actor               TEXT,
            recognition_source  TEXT DEFAULT 'manual'
                                  CHECK (recognition_source IN
                                    ('vision_auto', 'vision_confirmed', 'manual',
                                     'vision_auto+gemini_assist', 'manual_correction')),
            confidence          REAL,
            txn_timestamp       TEXT NOT NULL,
            synced              INTEGER NOT NULL DEFAULT 0,
            synced_at           TEXT,
            requires_review     INTEGER NOT NULL DEFAULT 0,
            reviewed            INTEGER NOT NULL DEFAULT 0,
            review_reason       TEXT
        )"""
    )
    conn.execute(f"INSERT INTO transactions ({col_list}) SELECT {col_list} FROM transactions_old")
    conn.execute("DROP TABLE transactions_old")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


# --------------------------------------------------------------------------- #
# Packaging normalisation
# --------------------------------------------------------------------------- #

def resolve_quantity(item: sqlite3.Row, qty_scanned: float, scanned_unit: Optional[str]) -> float:
    """Convert whatever unit was scanned into the item's base unit count."""
    base_unit = (item["unit"] or "").strip().lower()

    if not scanned_unit or scanned_unit.strip().lower() == base_unit:
        return float(qty_scanned)

    scanned_unit = scanned_unit.strip().lower()
    pack_container = (item["pack_container"] or "").strip().lower()

    if pack_container and scanned_unit == pack_container:
        if item["pack_is_weight_based"]:
            raise AmbiguousUnitError(
                f"{item['item_code']} is packaged by weight ({item['pack_qty']} {item['pack_unit_word']} "
                f"per {item['pack_container']}) — can't convert a pack count to a unit count automatically. "
                f"Route this to manual entry."
            )
        if not item["pack_qty"]:
            raise AmbiguousUnitError(
                f"{item['item_code']} has no known pack size for '{scanned_unit}' — can't convert."
            )
        return float(qty_scanned) * float(item["pack_qty"])

    raise AmbiguousUnitError(
        f"Don't know how to convert {qty_scanned} '{scanned_unit}' for {item['item_code']} "
        f"(base unit is '{item['unit']}', pack unit is '{item['pack_container']}')."
    )


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _get_item(conn: sqlite3.Connection, item_code: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM items WHERE item_code = ?", (item_code,)).fetchone()
    if row is None:
        raise ItemNotFoundError(f"No such item: {item_code}")
    return row


def _existing_txn(conn: sqlite3.Connection, client_txn_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM transactions WHERE client_txn_id = ?", (client_txn_id,)
    ).fetchone()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _insert_txn(conn, client_txn_id, item_code, txn_type, qty, location_code, actor,
                 recognition_source, confidence, requires_review, review_reason):
    conn.execute(
        """INSERT INTO transactions
           (client_txn_id, item_code, txn_type, qty, location_code, actor,
            recognition_source, confidence, txn_timestamp, synced,
            requires_review, reviewed, review_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?)""",
        (client_txn_id, item_code, txn_type, qty, location_code, actor,
         recognition_source, confidence, _now(), int(requires_review), review_reason),
    )
    return conn.execute(
        "SELECT * FROM transactions WHERE client_txn_id = ?", (client_txn_id,)
    ).fetchone()


# --------------------------------------------------------------------------- #
# check_out()
# --------------------------------------------------------------------------- #

@dataclass
class CheckOutResult:
    item_code: str
    resolved_qty: float
    asset_class: str
    duplicate: bool
    txn_id: int
    available_quantity_after: float
    total_quantity_after: float
    requires_review: bool = False  # True -> stock was NOT touched; held for confirm_review()


def check_out(conn: sqlite3.Connection, item_code: str, qty_scanned: float,
               scanned_unit: Optional[str] = None, actor: Optional[str] = None,
               location_code: Optional[str] = None, client_txn_id: Optional[str] = None,
               recognition_source: str = "manual", confidence: Optional[float] = None,
               confidence_threshold: float = CONFIDENCE_THRESHOLD) -> CheckOutResult:
    """
    Check an item out. Consumables are debited from both available and total
    (they're gone); reusable assets are debited from available only (they're
    on loan and must come back through bulk_return()).

    Same confidence gate as bulk_return(): if a vision confidence is given and
    it's below confidence_threshold, the transaction is recorded but held
    (requires_review=1) and stock is NOT touched yet — a human must clear it
    via confirm_review() (e.g. through lensarak_review.py) before it takes
    effect. A manual/no-confidence check_out (confidence=None) is unaffected
    and applies immediately, exactly as before.
    """
    conn.row_factory = sqlite3.Row
    client_txn_id = client_txn_id or str(uuid.uuid4())

    existing = _existing_txn(conn, client_txn_id)
    if existing is not None:
        # Same offline transaction replayed during sync — don't deduct twice.
        item = _get_item(conn, item_code)
        return CheckOutResult(item_code, existing["qty"], item["asset_class"], True,
                               existing["txn_id"], item["available_quantity"], item["total_quantity"],
                               requires_review=bool(existing["requires_review"]) and not bool(existing["reviewed"]))

    item = _get_item(conn, item_code)
    resolved_qty = resolve_quantity(item, qty_scanned, scanned_unit)

    if item["available_quantity"] < resolved_qty:
        raise InsufficientStockError(
            f"{item_code}: requested {resolved_qty} but only {item['available_quantity']} available"
        )

    if confidence is not None and confidence < confidence_threshold:
        review_reason = f"low vision confidence ({confidence:.2f} < {confidence_threshold})"
        txn = _insert_txn(conn, client_txn_id, item_code, "check_out", resolved_qty, location_code,
                           actor, recognition_source, confidence, requires_review=True,
                           review_reason=review_reason)
        conn.commit()
        return CheckOutResult(item_code, resolved_qty, item["asset_class"], False,
                               txn["txn_id"], item["available_quantity"], item["total_quantity"],
                               requires_review=True)

    txn = _insert_txn(conn, client_txn_id, item_code, "check_out", resolved_qty, location_code,
                       actor, recognition_source, confidence, requires_review=False, review_reason=None)

    new_available = item["available_quantity"] - resolved_qty
    if item["asset_class"] == "consumable":
        new_total = item["total_quantity"] - resolved_qty
        conn.execute(
            "UPDATE items SET available_quantity = ?, total_quantity = ? WHERE item_code = ?",
            (new_available, new_total, item_code),
        )
    else:  # reusable_asset
        new_total = item["total_quantity"]
        new_status = "checked_out" if new_available <= 0 else item["status"]
        conn.execute(
            "UPDATE items SET available_quantity = ?, status = ? WHERE item_code = ?",
            (new_available, new_status, item_code),
        )

    conn.commit()
    return CheckOutResult(item_code, resolved_qty, item["asset_class"], False,
                           txn["txn_id"], new_available, new_total, requires_review=False)


# --------------------------------------------------------------------------- #
# bulk_return()
# --------------------------------------------------------------------------- #

@dataclass
class ScanResult:
    item_code: str
    status: str  # 'applied' | 'needs_review' | 'error' | 'duplicate'
    resolved_qty: Optional[float] = None
    txn_id: Optional[int] = None
    reason: Optional[str] = None


@dataclass
class BulkReturnSummary:
    applied: list = field(default_factory=list)
    needs_review: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)


def bulk_return(conn: sqlite3.Connection, scans: list[dict], actor: Optional[str] = None,
                 location_code: Optional[str] = None,
                 confidence_threshold: float = CONFIDENCE_THRESHOLD) -> BulkReturnSummary:
    """
    Process a batch of items dumped on the return table in one go.

    Each scan is a dict:
        {item_code, qty (default 1), unit (optional), confidence (optional 0-1),
         client_txn_id (optional)}

    - Below-threshold confidence -> transaction is recorded but requires_review=1
      and stock is NOT updated yet. Call confirm_review() once a human checks it.
    - A consumable appearing in a return batch is an anomaly (consumables don't
      come back) -> flagged for review, not applied.
    - A return that would push available_quantity past total_quantity is flagged
      (probable duplicate scan or miscount), not silently capped.
    """
    conn.row_factory = sqlite3.Row
    summary = BulkReturnSummary()

    for scan in scans:
        item_code = scan["item_code"]
        qty_scanned = scan.get("qty", 1)
        unit = scan.get("unit")
        confidence = scan.get("confidence")
        client_txn_id = scan.get("client_txn_id") or str(uuid.uuid4())
        # A scan can name its own recognition_source (e.g. "vision_auto+gemini_assist"
        # when a Gemini second opinion confirmed a low-confidence YOLO read) --
        # falls back to the original auto-derived value when it doesn't, so every
        # existing caller behaves exactly as before.
        recognition_source = scan.get("recognition_source") or (
            "vision_auto" if confidence is not None else "manual"
        )

        existing = _existing_txn(conn, client_txn_id)
        if existing is not None:
            summary.duplicates.append(ScanResult(item_code, "duplicate", txn_id=existing["txn_id"]))
            continue

        try:
            item = _get_item(conn, item_code)
            resolved_qty = resolve_quantity(item, qty_scanned, unit)
        except LensaRakError as e:
            summary.errors.append(ScanResult(item_code, "error", reason=str(e)))
            continue

        requires_review = False
        review_reason = None

        if item["asset_class"] == "consumable":
            requires_review = True
            review_reason = "consumable scanned in a return batch — consumables aren't returned, check the item"
        elif confidence is not None and confidence < confidence_threshold:
            requires_review = True
            review_reason = f"low vision confidence ({confidence:.2f} < {confidence_threshold})"
        elif item["available_quantity"] + resolved_qty > item["total_quantity"]:
            requires_review = True
            review_reason = "returned quantity would exceed total stock — possible duplicate scan or miscount"

        txn = _insert_txn(conn, client_txn_id, item_code, "return", resolved_qty, location_code,
                           actor, recognition_source, confidence, requires_review, review_reason)

        if requires_review:
            summary.needs_review.append(ScanResult(item_code, "needs_review", resolved_qty,
                                                     txn["txn_id"], review_reason))
            continue  # stock NOT updated — waits for confirm_review()

        new_available = min(item["available_quantity"] + resolved_qty, item["total_quantity"])
        conn.execute(
            "UPDATE items SET available_quantity = ?, status = 'available' WHERE item_code = ?",
            (new_available, item_code),
        )
        summary.applied.append(ScanResult(item_code, "applied", resolved_qty, txn["txn_id"]))

    conn.commit()
    return summary


def confirm_review(conn: sqlite3.Connection, txn_id: int, approve: bool, reviewer: str,
                    corrected_item_code: Optional[str] = None,
                    corrected_qty: Optional[float] = None) -> Optional[int]:
    """
    A human clears a held transaction (a return OR a check_out -- works either
    direction). Only on approval does stock actually move.

    Plain approve (corrected_item_code/corrected_qty both left None, or equal to
    what's already recorded) applies the original item_code/qty, exactly as before.

    A CORRECTION (either argument differs from what's recorded) never edits the
    original transaction's own item_code/qty in place -- that would quietly erase
    what the camera actually thought it saw. Instead the original stays on record,
    marked reviewed with a note of what it was corrected to, and a brand-new
    "manual_correction" transaction is inserted for the real item/qty -- THAT one
    is what updates stock. So the audit trail always shows both the camera's
    original (wrong) read and the human's correction, never one silently
    replacing the other.

    Returns the new correction transaction's txn_id if one was created, else None.
    """
    conn.row_factory = sqlite3.Row
    txn = conn.execute("SELECT * FROM transactions WHERE txn_id = ?", (txn_id,)).fetchone()
    if txn is None:
        raise LensaRakError(f"No such transaction: {txn_id}")
    if not txn["requires_review"]:
        raise LensaRakError(f"Transaction {txn_id} was never pending review")
    if txn["reviewed"]:
        raise LensaRakError(f"Transaction {txn_id} was already reviewed")

    is_correction = approve and (
        (corrected_item_code is not None and corrected_item_code != txn["item_code"]) or
        (corrected_qty is not None and corrected_qty != txn["qty"])
    )
    new_txn_id = None
    txn_type = txn["txn_type"]  # 'return' -> adds stock back; 'check_out' -> debits stock

    def _apply_return(item_code: str, qty: float) -> None:
        item = _get_item(conn, item_code)
        new_available = min(item["available_quantity"] + qty, item["total_quantity"])
        conn.execute("UPDATE items SET available_quantity = ?, status = 'available' WHERE item_code = ?",
                     (new_available, item_code))

    def _apply_check_out(item_code: str, qty: float) -> None:
        item = _get_item(conn, item_code)
        if item["available_quantity"] < qty:
            raise InsufficientStockError(
                f"{item_code}: requested {qty} but only {item['available_quantity']} available "
                f"(stock may have changed since this was flagged)"
            )
        new_available = item["available_quantity"] - qty
        if item["asset_class"] == "consumable":
            new_total = item["total_quantity"] - qty
            conn.execute("UPDATE items SET available_quantity = ?, total_quantity = ? WHERE item_code = ?",
                         (new_available, new_total, item_code))
        else:
            new_status = "checked_out" if new_available <= 0 else item["status"]
            conn.execute("UPDATE items SET available_quantity = ?, status = ? WHERE item_code = ?",
                         (new_available, new_status, item_code))

    if approve and not is_correction:
        # Plain approve -- exactly the original behaviour, now for either direction.
        if txn_type == "return":
            _apply_return(txn["item_code"], txn["qty"])
        elif txn_type == "check_out":
            _apply_check_out(txn["item_code"], txn["qty"])
        conn.execute(
            "UPDATE transactions SET reviewed = 1, review_reason = review_reason || ? WHERE txn_id = ?",
            (f" | reviewed by {reviewer}: approved", txn_id),
        )

    elif is_correction:
        final_item_code = corrected_item_code or txn["item_code"]
        final_qty = corrected_qty if corrected_qty is not None else txn["qty"]
        _get_item(conn, final_item_code)  # raises ItemNotFoundError on a bad typed code

        if txn_type == "check_out":
            _apply_check_out(final_item_code, final_qty)
        else:
            _apply_return(final_item_code, final_qty)

        note = (f"correction of txn_id {txn_id} (camera originally read "
                f"{txn['item_code']} x{txn['qty']}) -- confirmed by {reviewer} as "
                f"{final_item_code} x{final_qty}")
        new_txn = _insert_txn(conn, str(uuid.uuid4()), final_item_code, txn_type, final_qty,
                               txn["location_code"], reviewer, "manual_correction", None,
                               requires_review=False, review_reason=note)
        new_txn_id = new_txn["txn_id"]

        conn.execute(
            "UPDATE transactions SET reviewed = 1, review_reason = review_reason || ? WHERE txn_id = ?",
            (f" | reviewed by {reviewer}: corrected to {final_item_code} x{final_qty} "
             f"(see txn_id {new_txn_id})", txn_id),
        )

    else:
        # Reject -- no stock effect, exactly as before.
        conn.execute(
            "UPDATE transactions SET reviewed = 1, review_reason = review_reason || ? WHERE txn_id = ?",
            (f" | reviewed by {reviewer}: rejected", txn_id),
        )

    conn.commit()
    return new_txn_id


# --------------------------------------------------------------------------- #
# Self-test demo
# --------------------------------------------------------------------------- #

def _demo():
    src = Path(__file__).parent / "lensarak_inventory.db"
    demo_path = Path(__file__).parent / "_demo_lensarak_inventory.db"
    shutil.copy(src, demo_path)

    conn = sqlite3.connect(demo_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    def show(item_code):
        i = _get_item(conn, item_code)
        print(f"    {item_code} {i['item_name']:22s} available={i['available_quantity']:>5}  "
              f"total={i['total_quantity']:>5}  status={i['status']}")

    print("=== Scenario 1+2: reusable check-out (Arduino Uno) ===")
    show("E001")
    r = check_out(conn, "E001", 5, actor="aiman", location_code="CHILLAX")
    print(f"  checked out {r.resolved_qty} units -> available={r.available_quantity_after}, "
          f"total unchanged={r.total_quantity_after}")
    show("E001")

    print("\n=== Scenario 1+2: consumable check-out with pack normalisation (Jumper Wire M-M, 2 packs) ===")
    show("E026")
    r = check_out(conn, "E026", 2, scanned_unit="pack", actor="aiman", location_code="CHILLAX")
    print(f"  scanned '2 pack' -> resolved to {r.resolved_qty} base units "
          f"(40/pack) -> available={r.available_quantity_after}, total={r.total_quantity_after} (both dropped)")
    show("E026")

    print("\n=== Scenario 3: offline idempotency — replay the same client_txn_id ===")
    fixed_id = str(uuid.uuid4())
    r1 = check_out(conn, "L001", 3, client_txn_id=fixed_id, actor="aiman", location_code="STORE 1")
    r2 = check_out(conn, "L001", 3, client_txn_id=fixed_id, actor="aiman", location_code="STORE 1")
    print(f"  first call:  duplicate={r1.duplicate}, available_after={r1.available_quantity_after}")
    print(f"  replay call: duplicate={r2.duplicate}, available_after={r2.available_quantity_after}"
          f"  <- unchanged, stock NOT double-deducted")

    print("\n=== Scenario 4: bulk return with mixed vision confidence ===")
    # E001 (5 out from above) comes back confidently; L001 (3 out) comes back blurry;
    # E026 (consumable) is wrongly scanned in the return pile.
    scans = [
        {"item_code": "E001", "qty": 5, "confidence": 0.94},
        {"item_code": "L001", "qty": 3, "confidence": 0.41},
        {"item_code": "E026", "qty": 1, "confidence": 0.88},
    ]
    summary = bulk_return(conn, scans, actor="aiman", location_code="CHILLAX")
    print(f"  applied      : {[ (r.item_code, r.resolved_qty) for r in summary.applied ]}")
    print(f"  needs_review : {[ (r.item_code, r.reason) for r in summary.needs_review ]}")
    show("E001")
    show("L001")

    print("\n  -> human reviews the L001 return and approves it:")
    review_txn = summary.needs_review[0].txn_id
    confirm_review(conn, review_txn, approve=True, reviewer="aiman")
    show("L001")

    conn.close()
    demo_path.unlink()  # clean up the throwaway copy


if __name__ == "__main__":
    _demo()
