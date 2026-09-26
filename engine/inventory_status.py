"""
inventory_status.py -- Equipment Availability page queries: combines the
real, physical stock ledger (items table, kept current by Task 1's
checkout/return flow) with future bookings (reservations table, this
enhancement) to answer "can I get N of this, and if not now, when?".
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from . import reservations as res


def search_items(conn: sqlite3.Connection, query: str = "", category: str | None = None) -> list[dict]:
    sql = "SELECT * FROM items WHERE 1=1"
    params: list = []
    if query:
        sql += " AND (item_name LIKE ? OR item_code LIKE ?)"
        like = f"%{query}%"
        params += [like, like]
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY category, item_name"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_categories(conn: sqlite3.Connection) -> list[str]:
    return sorted(r[0] for r in conn.execute("SELECT DISTINCT category FROM items") if r[0])


def item_detail(conn: sqlite3.Connection, item_code: str) -> dict | None:
    row = conn.execute("SELECT * FROM items WHERE item_code = ?", (item_code,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    checked_out = 0
    if item["asset_class"] == "reusable_asset":
        checked_out = max(0, (item["total_quantity"] or 0) - (item["available_quantity"] or 0))

    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M")
    reserved_now = res.reserved_quantity_in_window(conn, item_code, now_iso, now_iso)

    item.update({
        "checked_out_now": checked_out,
        "reserved_now": reserved_now,
        "available_now": max(0.0, (item["available_quantity"] or 0) - reserved_now),
    })
    return item


def transaction_history(conn: sqlite3.Connection, item_code: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM transactions WHERE item_code = ? ORDER BY txn_timestamp DESC LIMIT ?",
        (item_code, limit),
    ).fetchall()
    return [dict(r) for r in rows]
