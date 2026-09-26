"""
transactions.py -- Transactions / Accountability: manual check-out/return
(no camera), full transaction history, and the review queue for anything a
vision scan held back (low confidence, consumable returned, over-return).
"""

from __future__ import annotations

import streamlit as st

import lensarak_ops as ops  # the same root-level module lensarak_vision.py uses
from screens._common import empty_state, page_header, require_permission


def render(conn):
    page_header("Transactions", "Manual check-out/return records, full history, and the review queue.")

    tab_manual, tab_history, tab_review = st.tabs(["Manual Entry", "Full History", "Review Queue"])

    with tab_manual:
        if not require_permission("checkout_return"):
            pass
        else:
            items = conn.execute("SELECT item_code, item_name FROM items ORDER BY item_name").fetchall()
            options = {f"{i['item_name']} ({i['item_code']})": i["item_code"] for i in items}
            c1, c2, c3 = st.columns(3)
            mode = c1.selectbox("Type", ["check_out", "return"], key="txn_mode")
            picked = c2.selectbox("Item", list(options.keys()), key="txn_item")
            qty = c3.number_input("Quantity", min_value=1, value=1, key="txn_qty")
            location = st.text_input("Location", value="CHILLAX", key="txn_location")
            if st.button("Submit", type="primary", key="txn_submit"):
                item_code = options[picked]
                actor = st.session_state.get("username")
                try:
                    if mode == "check_out":
                        r = ops.check_out(conn, item_code, qty_scanned=qty, actor=actor, location_code=location)
                        st.success(f"Check-out successful -- available after: {r.available_quantity_after}")
                    else:
                        summary = ops.bulk_return(conn, [{"item_code": item_code, "qty": qty}],
                                                   actor=actor, location_code=location)
                        if summary.applied:
                            st.success("Return applied successfully.")
                        elif summary.needs_review:
                            st.warning(f"Held for review: {summary.needs_review[0].reason}")
                        elif summary.errors:
                            st.error(summary.errors[0].reason)
                except ops.LensaRakError as e:
                    st.error(str(e))

    with tab_history:
        rows = conn.execute(
            "SELECT t.*, i.item_name FROM transactions t JOIN items i ON i.item_code = t.item_code "
            "ORDER BY t.txn_timestamp DESC LIMIT 200"
        ).fetchall()
        if not rows:
            empty_state("No transactions yet.")
        else:
            st.dataframe(
                [{"Time": r["txn_timestamp"], "Item": r["item_name"], "Type": r["txn_type"], "Quantity": r["qty"],
                  "By": r["actor"], "Location": r["location_code"], "Source": r["recognition_source"],
                  "Confidence": r["confidence"], "Needs Review": bool(r["requires_review"])}
                 for r in rows],
                width="stretch", hide_index=True,
            )

    with tab_review:
        pending = conn.execute(
            "SELECT t.*, i.item_name FROM transactions t JOIN items i ON i.item_code = t.item_code "
            "WHERE t.requires_review = 1 AND t.reviewed = 0 ORDER BY t.txn_timestamp"
        ).fetchall()
        if not pending:
            empty_state("No transactions awaiting review.", icon="✅")
        else:
            if not require_permission("edit_inventory_master", "Only Admin/Store Manager can confirm reviews."):
                pass
            else:
                for p in pending:
                    with st.container(border=True):
                        st.markdown(f"**{p['item_name']}** ({p['item_code']}) x{p['qty']} -- {p['txn_type']}")
                        st.caption(f"Reason: {p['review_reason']} | conf={p['confidence']}")
                        rc1, rc2 = st.columns(2)
                        if rc1.button("✅ Approve", key=f"approve_{p['txn_id']}"):
                            ops.confirm_review(conn, p["txn_id"], approve=True, reviewer=st.session_state.get("username"))
                            st.rerun()
                        if rc2.button("❌ Reject", key=f"reject_{p['txn_id']}"):
                            ops.confirm_review(conn, p["txn_id"], approve=False, reviewer=st.session_state.get("username"))
                            st.rerun()
