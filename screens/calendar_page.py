"""
calendar_page.py -- Reservations & Calendar: view scheduled events, their
resource reservations, and any detected conflicts. (Per spec's own prototype
recommendation: a structured list view is enough to prove scheduling +
conflict detection for the competition demo -- a full month-grid calendar
and external sync are P2/optional, see engine/reservations.py docstring.)
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from engine import loader, quotation
from engine import reservations as res
from screens._common import badge, empty_state, page_header, require_permission


def render(conn):
    page_header("Reservations & Calendar", "All scheduled events, reserved resources, and detected conflicts.")

    _, offerings_by_id, _, _, _ = loader.load_catalogue()

    conflicts = res.upcoming_conflicts(conn)
    if conflicts:
        st.error(f"🔴 {len(conflicts)} booking overlap(s) detected -- this SHOULD NOT happen "
                 f"(the system checks before booking); if you see this, someone may have "
                 f"booked another way (a direct database edit).")
        for c in conflicts:
            st.markdown(f"- **{c['item_code']}**: reservation #{c['r1_id']} ({c['s1']}->{c['e1']}) "
                        f"overlaps with #{c['r2_id']} ({c['s2']}->{c['e2']}) -- combined {c['combined_qty']} "
                        f"exceeds stock of {c['total_quantity']}")
        st.divider()

    tab_upcoming, tab_all = st.tabs(["Upcoming", "All (including cancelled)"])

    with tab_upcoming:
        events = res.list_events(conn, from_dt=datetime.now().strftime("%Y-%m-%dT%H:%M"), status="confirmed")
        _render_events(conn, events, offerings_by_id, allow_cancel=True, tab_key="up")

    with tab_all:
        events = res.list_events(conn, status=None)
        _render_events(conn, events, offerings_by_id, allow_cancel=False, tab_key="all")


def _render_events(conn, events, offerings_by_id, allow_cancel: bool, tab_key: str):
    if not events:
        empty_state("No events. Create your first booking via 'Plan Programme'.")
        return

    by_date: dict[str, list] = {}
    for ev in events:
        d = ev["start_datetime"][:10]
        by_date.setdefault(d, []).append(ev)

    for d in sorted(by_date):
        st.markdown(f"##### {d}")
        for ev in by_date[d]:
            reservations_for_event = conn.execute(
                "SELECT r.*, i.item_name FROM reservations r JOIN items i ON i.item_code = r.item_code "
                "WHERE r.event_id = ? AND r.status = 'active'", (ev["event_id"],)
            ).fetchall()
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 1, 1])
                c1.markdown(f"**{ev['title']}** ({ev.get('offering_id') or '-'})")
                c1.caption(f"{ev['start_datetime'][11:]} -> {ev['end_datetime'][11:]} | "
                           f"{ev.get('venue') or '-'} | {ev.get('participants') or 0} participant(s)")
                c2.markdown(badge("AVAILABLE") if ev["status"] == "confirmed" else "⚫ CANCELLED")
                if allow_cancel and ev["status"] == "confirmed":
                    if c3.button("Cancel", key=f"cancel_{ev['event_id']}"):
                        if require_permission("create_booking", "Your role is not permitted to cancel bookings."):
                            res.cancel_event(conn, ev["event_id"])
                            st.rerun()
                if reservations_for_event:
                    st.caption("Reserved resources: " + ", ".join(
                        f"{r['item_name']} x{int(r['quantity_reserved'])}" for r in reservations_for_event))
                if ev["status"] == "confirmed":
                    offering = offerings_by_id.get(ev.get("offering_id"))
                    xlsx_bytes = quotation.build_quotation_xlsx(conn, ev["event_id"], offering)
                    if xlsx_bytes:
                        st.download_button(
                            "🧾 Booking summary / quotation draft (.xlsx)",
                            data=xlsx_bytes,
                            file_name=f"LensaRak_quotation_event{ev['event_id']}.xlsx",
                            key=f"quote_dl_cal_{tab_key}_{ev['event_id']}",
                        )
