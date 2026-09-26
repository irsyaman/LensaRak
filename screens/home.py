"""
home.py -- Home dashboard: the first thing a user sees after login. Should
answer "what can I do, and is anything wrong right now?" without needing an
instruction manual.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from engine import database, reservations
from screens._common import badge, empty_state, goto, page_header


def render(conn):
    name = st.session_state.get("display_name") or st.session_state.get("username")
    page_header(f"Welcome back, {name}", "What would you like to do today?")

    b1, b2, b3, b4 = st.columns(4)
    if b1.button("📋 Plan a Programme", width="stretch", type="primary"):
        goto("Plan Programme")
    if b2.button("🔎 Check Equipment", width="stretch"):
        goto("Equipment Availability")
    if b3.button("📷 Scan Inventory", width="stretch"):
        goto("Inventory Recognition")
    if b4.button("📅 View Calendar", width="stretch"):
        goto("Reservations & Calendar")

    st.write("")

    # --- Operational summary cards ---------------------------------------
    items = database.load_inventory(conn)
    total_available = sum((i["available_quantity"] or 0) for i in items)
    checked_out_reusable = sum(
        max(0, (i["total_quantity"] or 0) - (i["available_quantity"] or 0))
        for i in conn.execute("SELECT total_quantity, available_quantity FROM items WHERE asset_class='reusable_asset'").fetchall()
    )
    upcoming = reservations.list_events(conn, from_dt=datetime.now().strftime("%Y-%m-%dT%H:%M"))
    pending_review = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE requires_review = 1 AND reviewed = 0"
    ).fetchone()["n"]
    last_sync = database.db_freshness()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Available inventory (units)", int(total_available))
    c2.metric("Checked-out (reusable)", int(checked_out_reusable))
    c3.metric("Upcoming events", len(upcoming))
    c4.metric("Unresolved exceptions", pending_review)
    c5.metric("Last data update", last_sync.split(",")[0] if "," in last_sync else last_sync)

    st.write("")
    left, right = st.columns([2, 1])

    with left:
        st.markdown("#### Upcoming Events")
        if not upcoming:
            empty_state("No upcoming events -- create your first booking in 'Plan Programme'.")
        else:
            for ev in upcoming[:8]:
                conflicts = reservations.upcoming_conflicts(conn)
                has_conflict = any(True for _ in conflicts)  # cheap global flag; per-event detail is in Calendar page
                with st.container(border=True):
                    cc1, cc2, cc3 = st.columns([3, 1, 1])
                    cc1.markdown(f"**{ev['title']}** ({ev.get('offering_id') or '-'})")
                    cc1.caption(f"{ev['start_datetime']} -> {ev['end_datetime']} | {ev.get('venue') or '-'}")
                    cc2.metric("Participants", ev.get("participants") or 0)
                    cc3.markdown(badge("CONFLICT") if has_conflict and conflicts else badge("AVAILABLE"))

    with right:
        st.markdown("#### Alerts")
        alerts = []
        low_stock = [i for i in items if (i.get("available_quantity") or 0) <= 2 and (i.get("total_quantity") or 0) > 0]
        if low_stock:
            alerts.append(f"⚠️ {len(low_stock)} item(s) low on stock (<=2 units)")
        conflicts = reservations.upcoming_conflicts(conn)
        if conflicts:
            alerts.append(f"🔴 {len(conflicts)} possible booking overlap(s) -- check Reservations & Calendar")
        if pending_review:
            alerts.append(f"🟡 {pending_review} transaction(s) awaiting review (low confidence)")
        if not alerts:
            empty_state("No issues detected right now.", icon="✅")
        else:
            for a in alerts:
                st.warning(a)

    st.write("")
    st.markdown("#### Quick Search")
    q = st.text_input("Search item, activity, booking or user...", key="home_quick_search")
    if q:
        matches = [i for i in items if q.lower() in (i["item_name"] or "").lower() or q.lower() in (i["item_code"] or "").lower()]
        if matches:
            st.dataframe(
                [{"Item": m["item_name"], "Code": m["item_code"], "Available": m["available_quantity"],
                  "Total": m["total_quantity"], "Category": m["category"]} for m in matches[:15]],
                width="stretch", hide_index=True,
            )
        else:
            empty_state(f"No matches for '{q}'.")
