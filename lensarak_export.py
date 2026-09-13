#!/usr/bin/env python3
"""
LensaRak -- export the full ledger + current stock to CSV files, for when
the cmd window is too cramped to read the whole picture at once.

Opens straight in Excel / Google Sheets by double-clicking the file -- no
extra install, no database viewer needed. Uses only the Python standard
library (csv + sqlite3), so it always works even offline.

USAGE
-----
    python lensarak_export.py

Writes two files next to this script (overwritten fresh every run, so it's
always a snapshot of "right now"):
    lensarak_ledger_export.csv   -- every transaction ever logged, in full
    lensarak_stock_export.csv    -- current stock level for every item

TIP: if you want to poke around the database itself (not just a snapshot),
the free "DB Browser for SQLite" app (sqlitebrowser.org) can open
lensarak_inventory.db directly and let you browse/sort/filter every table
live -- handy for spot-checking, but this export is the quicker option for
"just show me everything clearly" during the demo/judging.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import lensarak_ops as ops

HERE = Path(__file__).parent
DB_PATH = HERE / "lensarak_inventory.db"
LEDGER_OUT = HERE / "lensarak_ledger_export.csv"
STOCK_OUT = HERE / "lensarak_stock_export.csv"


def export_ledger(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT t.txn_id, t.txn_timestamp, t.txn_type, t.item_code, i.item_name,
               t.qty, t.recognition_source, t.confidence, t.actor, t.location_code,
               t.requires_review, t.reviewed, t.review_reason, t.client_txn_id
        FROM transactions t
        LEFT JOIN items i ON i.item_code = t.item_code
        ORDER BY t.txn_timestamp
        """
    ).fetchall()
    # utf-8-sig so Excel on Windows doesn't mangle non-ASCII characters (e.g. "16x2")
    with open(LEDGER_OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "txn_id", "timestamp", "type", "item_code", "item_name", "qty",
            "recognition_source", "confidence", "actor", "location",
            "requires_review", "reviewed", "review_reason", "client_txn_id",
        ])
        for r in rows:
            r = dict(r)
            if r["confidence"] is not None:
                r["confidence"] = round(r["confidence"], 2)
            w.writerow([
                r["txn_id"], r["txn_timestamp"], r["txn_type"], r["item_code"], r["item_name"],
                r["qty"], r["recognition_source"], r["confidence"], r["actor"], r["location_code"],
                r["requires_review"], r["reviewed"], r["review_reason"], r["client_txn_id"],
            ])
    return len(rows)


def export_stock(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT item_code, item_name, category, asset_class, unit,
               available_quantity, total_quantity, status,
               storage_location, specific_location,
               pack_qty, pack_unit_word, pack_container
        FROM items
        ORDER BY item_code
        """
    ).fetchall()
    with open(STOCK_OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "item_code", "item_name", "category", "asset_class", "unit",
            "available_quantity", "total_quantity", "status",
            "storage_location", "specific_location",
            "pack_qty", "pack_unit_word", "pack_container",
        ])
        w.writerows(rows)
    return len(rows)


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ops.ensure_schema(conn)

    n_txn = export_ledger(conn)
    n_items = export_stock(conn)
    conn.close()

    print(f"Exported {n_txn} transaction(s)      -> {LEDGER_OUT.name}")
    print(f"Exported {n_items} item(s) (current stock) -> {STOCK_OUT.name}")
    print("\nDouble-click either .csv file in the LensaRak folder to open it in Excel.")
    print("Re-run this anytime for a fresh snapshot -- it overwrites the same two files.")


if __name__ == "__main__":
    main()
