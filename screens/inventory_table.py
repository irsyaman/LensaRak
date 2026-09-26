"""
inventory_table.py -- Inventory: a plain filterable table of every item
(distinct from Equipment Availability, which is the per-item search + book
a future date/time flow). This page is for a fast full-table overview,
filtering and CSV export.
"""

from __future__ import annotations

import streamlit as st

from engine import inventory_status
from screens._common import empty_state, page_header


def render(conn):
    page_header("Inventory", "Full table of all items -- filter by category, status or location.")

    categories = ["All"] + inventory_status.list_categories(conn)
    locations = ["All"] + sorted({r["storage_location"] for r in conn.execute("SELECT DISTINCT storage_location FROM items").fetchall() if r["storage_location"]})
    asset_classes = ["All", "consumable", "reusable_asset"]

    c1, c2, c3, c4 = st.columns(4)
    category = c1.selectbox("Category", categories, key="inv_table_category")
    location = c2.selectbox("Location", locations, key="inv_table_location")
    asset_class = c3.selectbox("Asset type", asset_classes, key="inv_table_assetclass")
    query = c4.text_input("Search name/code", key="inv_table_query")

    sql = "SELECT * FROM items WHERE 1=1"
    params: list = []
    if category != "All":
        sql += " AND category = ?"; params.append(category)
    if location != "All":
        sql += " AND storage_location = ?"; params.append(location)
    if asset_class != "All":
        sql += " AND asset_class = ?"; params.append(asset_class)
    if query:
        sql += " AND (item_name LIKE ? OR item_code LIKE ?)"; params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY category, item_name"

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    st.caption(f"{len(rows)} item(s)")
    if not rows:
        empty_state("No items match the filter.")
        return

    table = [{"Code": r["item_code"], "Item": r["item_name"], "Category": r["category"],
              "Type": r["asset_class"], "Available": r["available_quantity"], "Total": r["total_quantity"],
              "Unit": r["unit"], "Status": r["status"], "Location": r["storage_location"],
              "Specific Location": r["specific_location"]} for r in rows]
    st.dataframe(table, width="stretch", hide_index=True)

    csv = "\n".join([",".join(table[0].keys())] + [",".join(str(v) for v in row.values()) for row in table])
    st.download_button("⬇️ Download as CSV", csv, file_name="lensarak_inventory_export.csv", key="inv_table_csv")
