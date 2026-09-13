#!/usr/bin/env python3
"""
LensaRak — offline review console for anything the vision pipeline flagged to
needs_review (low camera confidence, a consumable seen in a return pile, a
return that would overshoot total stock, etc).

This is a plain desk/keyboard tool -- no camera involved. Run it whenever
there's a pending review queue to clear (e.g. end of shift):

    python lensarak_review.py

For every pending transaction it shows what the camera saw and why it was
flagged, then asks:

    [ENTER]  camera got it right — approve exactly as recorded
    [U]      item_code is right, quantity is wrong — type the real quantity
    [C]      item_code is wrong — type the real item_code, then confirm/edit qty
    [N]      reject — don't apply anything, leave stock untouched

A [C] correction never silently overwrites what the camera thought it saw —
the original detection stays on record, marked reviewed and noted as
corrected, and the actual stock update happens on a brand-new
manual_correction transaction that references it (see confirm_review() in
lensarak_ops.py). Nothing is ever forced through blind.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import lensarak_ops as ops

DB_PATH = Path(__file__).parent / "lensarak_inventory.db"


def load_item_names(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT item_code, item_name FROM items").fetchall()
    return {row["item_code"]: row["item_name"] for row in rows}


def item_label(item_code: str, names: dict[str, str]) -> str:
    name = names.get(item_code)
    return f"{item_code} {name}" if name else item_code


def ask_quantity(default_qty: float) -> float:
    """[ENTER] keeps the default qty. [U] switches to typing a real one."""
    while True:
        raw = input(f"    [ENTER] keep qty {default_qty}   [U] change quantity  > ").strip()
        if raw == "":
            return default_qty
        if raw.lower() == "u":
            typed = input("    New quantity: ").strip()
            try:
                qty = float(typed)
            except ValueError:
                print("    Not a number, try again.")
                continue
            if qty <= 0:
                print("    Quantity must be positive.")
                continue
            return qty
        print("    Press ENTER, or type U.")


def ask_item_code(names: dict[str, str]) -> str:
    """Loops until a valid item_code (or its exact name, case-insensitive) is typed."""
    by_name = {name.lower(): code for code, name in names.items()}
    while True:
        typed = input("    Correct item_code (or type 'list' to see the catalogue): ").strip()
        if typed.lower() == "list":
            for code, name in sorted(names.items()):
                print(f"      {code}  {name}")
            continue
        if typed in names:
            return typed
        if typed.lower() in by_name:
            return by_name[typed.lower()]
        print(f"    '{typed}' isn't in the catalogue — check spelling or type 'list'.")


def review_one(conn: sqlite3.Connection, txn: sqlite3.Row, names: dict[str, str], reviewer: str) -> None:
    conf_str = f"conf={txn['confidence']:.2f}" if txn["confidence"] is not None else "conf=n/a"
    print(f"\n  txn_id {txn['txn_id']}  |  {txn['txn_type']}  |  "
          f"{item_label(txn['item_code'], names)}  x{txn['qty']}  |  {conf_str}")
    print(f"    flagged: {txn['review_reason']}")

    while True:
        choice = input(
            "    [ENTER] correct as-is   [U] fix quantity   [C] fix item   [N] reject  > "
        ).strip().lower()

        if choice == "":
            ops.confirm_review(conn, txn["txn_id"], approve=True, reviewer=reviewer)
            print("    -> approved as recorded.")
            return

        if choice == "u":
            new_qty = ask_quantity(txn["qty"])
            ops.confirm_review(conn, txn["txn_id"], approve=True, reviewer=reviewer,
                                corrected_qty=new_qty)
            print(f"    -> corrected quantity to {new_qty}.")
            return

        if choice == "c":
            new_code = ask_item_code(names)
            new_qty = ask_quantity(txn["qty"])  # same ENTER=default / U=type pattern as elsewhere
            ops.confirm_review(conn, txn["txn_id"], approve=True, reviewer=reviewer,
                                corrected_item_code=new_code, corrected_qty=new_qty)
            print(f"    -> corrected to {item_label(new_code, names)} x{new_qty}.")
            return

        if choice == "n":
            ops.confirm_review(conn, txn["txn_id"], approve=False, reviewer=reviewer)
            print("    -> rejected, stock unchanged.")
            return

        print("    Press ENTER, or type U, C, or N.")


def main():
    if not DB_PATH.exists():
        sys.exit(f"Database not found at {DB_PATH}")

    reviewer = input("Reviewer name: ").strip() or "reviewer"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ops.ensure_schema(conn)
    names = load_item_names(conn)

    pending = conn.execute(
        "SELECT * FROM transactions WHERE requires_review = 1 AND reviewed = 0 ORDER BY txn_timestamp"
    ).fetchall()

    if not pending:
        print("Nothing pending review.")
        conn.close()
        return

    print(f"{len(pending)} transaction(s) pending review.")
    for txn in pending:
        review_one(conn, txn, names, reviewer)

    print("\nDone.")
    conn.close()


if __name__ == "__main__":
    main()
