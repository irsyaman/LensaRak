#!/usr/bin/env python3
"""
LensaRak -- read-only transaction history viewer.

Shows everything that has ever been logged to the ledger (check-outs,
returns, corrections -- every row in the transactions table), not just the
pending needs_review queue (that's lensarak_review.py's job).

No camera, no writes -- purely for looking at what's already been recorded.

USAGE
-----
    python lensarak_history.py                  # last 25 transactions, all types
    python lensarak_history.py --all             # every transaction ever logged
    python lensarak_history.py --n 50            # last 50 instead of 25
    python lensarak_history.py --item E009        # only this item_code
    python lensarak_history.py --pending          # only requires_review=1 and not yet reviewed
    python lensarak_history.py --reviewed         # only the ones a human has already cleared
    python lensarak_history.py --corrections      # only manual_correction rows (what got fixed)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import lensarak_ops as ops

DB_PATH = Path(__file__).parent / "lensarak_inventory.db"

COLW = dict(txn_id=6, ts=19, item=28, qty=7, type=10, source=24, conf=6, status=18)


def load_item_names(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT item_code, item_name FROM items").fetchall()
    return {row["item_code"]: row["item_name"] for row in rows}


def item_label(item_code: str, names: dict[str, str]) -> str:
    name = names.get(item_code)
    label = f"{item_code} {name}" if name else item_code
    return label[: COLW["item"] - 1]


def status_of(txn: sqlite3.Row) -> str:
    if not txn["requires_review"]:
        return "-"
    if txn["reviewed"]:
        return "reviewed"
    return "PENDING REVIEW"


def print_row(cells: list[str]) -> None:
    widths = list(COLW.values())
    print("  ".join(c.ljust(w) for c, w in zip(cells, widths)))


def print_header() -> None:
    print_row(["txn_id", "timestamp", "item", "qty", "type", "source", "conf", "status"])
    print("-" * (sum(COLW.values()) + 2 * (len(COLW) - 1)))


def print_txn(txn: sqlite3.Row, names: dict[str, str]) -> None:
    conf = f"{txn['confidence']:.2f}" if txn["confidence"] is not None else "-"
    print_row([
        str(txn["txn_id"]),
        (txn["txn_timestamp"] or "")[:19],
        item_label(txn["item_code"], names),
        f"{txn['qty']:g}",
        txn["txn_type"] or "-",
        (txn["recognition_source"] or "-")[: COLW["source"] - 1],
        conf,
        status_of(txn),
    ])
    if txn["review_reason"]:
        print(f"        note: {txn['review_reason']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="LensaRak -- view logged transactions")
    parser.add_argument("--all", action="store_true", help="show everything (ignores --n)")
    parser.add_argument("--n", type=int, default=25, help="how many most-recent rows to show (default 25)")
    parser.add_argument("--item", help="only this item_code")
    parser.add_argument("--pending", action="store_true", help="only rows waiting on lensarak_review.py")
    parser.add_argument("--reviewed", action="store_true", help="only rows a human has already cleared")
    parser.add_argument("--corrections", action="store_true", help="only manual_correction rows")
    args = parser.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"Database not found at {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ops.ensure_schema(conn)

    where = []
    params: list = []
    if args.item:
        where.append("item_code = ?")
        params.append(args.item)
    if args.pending:
        where.append("requires_review = 1 AND reviewed = 0")
    if args.reviewed:
        where.append("requires_review = 1 AND reviewed = 1")
    if args.corrections:
        where.append("recognition_source = 'manual_correction'")

    sql = "SELECT * FROM transactions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY txn_timestamp DESC"
    if not args.all:
        sql += " LIMIT ?"
        params.append(args.n)

    rows = conn.execute(sql, params).fetchall()
    names = load_item_names(conn)

    if not rows:
        print("Nothing matches.")
        conn.close()
        return

    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE requires_review = 1 AND reviewed = 0"
    ).fetchone()[0]
    print(f"{total} transaction(s) logged in total  |  {pending} pending review\n")

    print_header()
    for txn in rows:
        print_txn(txn, names)

    if not args.all and len(rows) == args.n and total > args.n:
        print(f"\n(showing last {args.n} -- use --all to see everything, or --n <number> for more)")

    conn.close()


if __name__ == "__main__":
    main()
