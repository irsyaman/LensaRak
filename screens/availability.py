"""
availability.py -- Equipment Availability page (spec section 9).
"""

from __future__ import annotations

from datetime import date, datetime, time

import streamlit as st

from engine import inventory_status, reservations
from screens._common import badge, empty_state, page_header, require_permission


def render(conn):
    page_header("Equipment Availability", "Search for items, check real stock, and book for a future date.")

    c1, c2 = st.columns([3, 1])
    with c1:
        query = st.text_input("Search item name/code", key="avail_query")
    with c2:
        categories = ["All categories"] + inventory_status.list_categories(conn)
        category = st.selectbox("Category", categories, key="avail_category")

    items = inventory_status.search_items(conn, query=query, category=None if category == "All categories" else category)
    if not items:
        empty_state("No matching items. Try a different keyword.")
        return

    st.caption(f"{len(items)} item(s) found")
    for it in items[:30]:
        detail = inventory_status.item_detail(conn, it["item_code"])
        with st.container(border=True):
            h1, h2 = st.columns([4, 1])
            h1.markdown(f"**{detail['item_name']}** ({detail['item_code']})")
            h1.caption(f"{detail['category']} | {detail['asset_class'].replace('_', ' ')}")
            status_now = "AVAILABLE" if detail["available_now"] > 0 else "CHECKED OUT"
            h2.markdown(badge(status_now))

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total", int(detail["total_quantity"] or 0))
            m2.metric("Available now", int(detail["available_now"]))
            m3.metric("Reserved (now)", int(detail["reserved_now"]))
            m4.metric("Checked out", int(detail["checked_out_now"]))
            st.caption(f"📍 {detail.get('storage_location') or '-'} / {detail.get('specific_location') or '-'}")

            with st.expander("Check availability for a specific date/time + actions"):
                d1, d2, d3, d4 = st.columns(4)
                chk_date = d1.date_input("Date", value=date.today(), key=f"avail_date_{it['item_code']}")
                chk_start = d2.time_input("Start", value=time(9, 0), key=f"avail_start_{it['item_code']}")
                chk_end = d3.time_input("End", value=time(11, 0), key=f"avail_end_{it['item_code']}")
                chk_qty = d4.number_input("Quantity needed", min_value=1, value=1, step=1, key=f"avail_qty_{it['item_code']}")

                if st.button("Check availability", key=f"avail_check_{it['item_code']}"):
                    start_dt = f"{chk_date.isoformat()}T{chk_start.strftime('%H:%M')}"
                    end_dt = f"{chk_date.isoformat()}T{chk_end.strftime('%H:%M')}"
                    info = reservations.available_for_window(conn, it["item_code"], start_dt, end_dt)
                    if info["available"] >= chk_qty:
                        st.success(f"{badge('AVAILABLE')} -- {info['available']} unit(s) available for this slot.")
                    else:
                        next_at = reservations.next_available_at(conn, it["item_code"], chk_qty, end_dt)
                        st.error(f"{badge('CONFLICT')} -- only {info['available']} unit(s) available (need {chk_qty}).")
                        if next_at:
                            st.info(f"Next available: around {next_at}")

                st.divider()
                a1, a2, a3 = st.columns(3)
                if a1.button("📜 View History", key=f"avail_hist_{it['item_code']}"):
                    hist = inventory_status.transaction_history(conn, it["item_code"])
                    if hist:
                        st.dataframe(
                            [{"Time": h["txn_timestamp"], "Type": h["txn_type"], "Quantity": h["qty"],
                              "By": h["actor"], "Location": h["location_code"]} for h in hist],
                            width="stretch", hide_index=True,
                        )
                    else:
                        empty_state("No transaction history for this item yet.")
                if a2.button("📍 Locate", key=f"avail_locate_{it['item_code']}"):
                    st.info(f"Location: **{detail.get('storage_location') or '-'}** / {detail.get('specific_location') or '-'}")
                if a3.button("🚩 Report Issue", key=f"avail_issue_{it['item_code']}"):
                    st.session_state[f"reporting_{it['item_code']}"] = True

                if st.session_state.get(f"reporting_{it['item_code']}"):
                    if not require_permission("edit_inventory_master",
                                               "Only Admin/Store Manager can report inventory issues."):
                        pass
                    else:
                        issue = st.text_area("Describe the issue (damaged/lost/other)", key=f"issue_text_{it['item_code']}")
                        if st.button("Submit report", key=f"issue_submit_{it['item_code']}"):
                            from engine import database as db
                            db.queue_for_sync(conn, "issue_report", it["item_code"],
                                               {"item_code": it["item_code"], "issue": issue,
                                                "reported_by": st.session_state.get("username")})
                            st.success("Report recorded (in the sync queue). Notify the Store Manager directly if urgent.")
                            st.session_state[f"reporting_{it['item_code']}"] = False
