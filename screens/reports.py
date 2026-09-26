"""
reports.py -- Reports / Audit Trail: readiness & utilisation snapshot,
missing/damaged assets, pending sync queue, and the full accountability log
(who did what, when -- every checkout/return/booking is attributed).
"""

from __future__ import annotations

import streamlit as st

from engine import database, reservations
from screens._common import empty_state, page_header


def render(conn):
    page_header("Reports / Audit Trail", "Readiness, utilisation, and the full audit trail.")

    items = database.load_inventory(conn)
    total_units = sum((i["total_quantity"] or 0) for i in items)
    available_units = sum((i["available_quantity"] or 0) for i in items)
    utilisation_pct = round(100 * (1 - available_units / total_units), 1) if total_units else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total units", int(total_units))
    c2.metric("Available units", int(available_units))
    c3.metric("Utilisation rate", f"{utilisation_pct}%")
    c4.metric("Pending sync", database.pending_sync_count(conn))

    st.divider()
    tab_low, tab_conflicts, tab_audit = st.tabs(["Low Stock / Issues", "Booking Conflicts", "Audit Trail"])

    with tab_low:
        low = [i for i in items if (i["available_quantity"] or 0) <= 2]
        if not low:
            empty_state("No low-stock items.", icon="✅")
        else:
            st.dataframe(
                [{"Item": i["item_name"], "Code": i["item_code"], "Available": i["available_quantity"],
                  "Total": i["total_quantity"], "Category": i["category"]} for i in low],
                width="stretch", hide_index=True,
            )
        issues = conn.execute(
            "SELECT * FROM sync_queue WHERE entity_type = 'issue_report' ORDER BY created_at DESC"
        ).fetchall()
        if issues:
            st.markdown("**Recent issue reports (damaged/lost)**")
            for i in issues[:10]:
                st.caption(f"{i['created_at']} -- {i['entity_id']}: {i['payload']}")

    with tab_conflicts:
        conflicts = reservations.upcoming_conflicts(conn)
        if not conflicts:
            empty_state("No booking conflicts detected.", icon="✅")
        else:
            for c in conflicts:
                st.error(f"{c['item_code']}: #{c['r1_id']} ({c['s1']}->{c['e1']}) vs "
                         f"#{c['r2_id']} ({c['s2']}->{c['e2']}) -- {c['combined_qty']}/{c['total_quantity']}")

    with tab_audit:
        rows = conn.execute(
            "SELECT t.txn_timestamp, t.txn_type, t.item_code, i.item_name, t.qty, t.actor, "
            "t.recognition_source, t.requires_review, t.reviewed "
            "FROM transactions t JOIN items i ON i.item_code = t.item_code "
            "ORDER BY t.txn_timestamp DESC LIMIT 300"
        ).fetchall()
        if not rows:
            empty_state("No transaction records yet.")
        else:
            st.dataframe(
                [{"Time": r["txn_timestamp"], "Type": r["txn_type"], "Item": r["item_name"], "Quantity": r["qty"],
                  "By": r["actor"], "Source": r["recognition_source"],
                  "Status": "Held" if r["requires_review"] and not r["reviewed"] else "Complete"}
                 for r in rows],
                width="stretch", hide_index=True,
            )
