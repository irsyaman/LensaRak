"""
catalogue.py -- Programme Catalogue: browse the organiser's verified
offerings, their constraints, and (if curated) their SDG alignment.
"""

from __future__ import annotations

import streamlit as st

from engine import constraints, database, loader, sdg
from screens._common import badge, empty_state, page_header


def render(conn):
    page_header("Programme Catalogue", "All organiser-verified offerings, with current stock status.")

    offerings, offerings_by_id, constraint_rules, theme_mapping, activity_items = loader.load_catalogue()
    inventory = database.load_inventory(conn)

    query = st.text_input("Search activity", key="catalogue_query")
    filtered = [o for o in offerings if not query or query.lower() in str(o.get("Activity_Title", "")).lower()]

    st.caption(f"{len(filtered)} / {len(offerings)} offering(s)")
    for o in filtered:
        oid = o.get("Offering_ID")
        feas = constraints.check_inventory_feasibility(oid, activity_items, inventory, None)
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{o.get('Activity_Title')}** ({oid})")
            c1.caption(o.get("Short_Description") or "")
            c2.markdown(badge(feas["status"]))

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Duration (min)", o.get("Standard_Duration_Min") or "-")
            m2.metric("Max participants", o.get("Max_Participants") or "-")
            m3.metric("Cost band", o.get("Cost_Band") or "-")
            m4.metric("Age", o.get("Recommended_Age") or "-")

            with st.expander("Details & constraints"):
                st.write(f"Suitable themes: {o.get('Suitable_Themes') or '-'}")
                st.write(f"Suitable objectives: {o.get('Suitable_Objectives') or '-'}")
                st.write(f"Audience: {o.get('Audience_Types') or '-'}")
                st.write(f"Electricity required: {o.get('Electricity_Required') or '-'} | "
                         f"Indoor/Outdoor: {o.get('Indoor_Outdoor') or '-'} | Safety: {o.get('Safety_Level') or '-'}")

                sdg_rows = sdg.get_sdg_for_offering(conn, oid)
                any_unreviewed = any(not sdg.is_reviewed(row) for row in sdg_rows) if sdg_rows else False
                st.markdown("**UN SDG Alignment**" + ("  ⚠️ *draft, pending organiser review*" if any_unreviewed else ""))
                if sdg_rows:
                    for row in sdg_rows:
                        label, meaning = sdg.score_label(row["effectiveness_score"])
                        flag = "" if sdg.is_reviewed(row) else " *(unverified draft)*"
                        st.caption(f"SDG {row['sdg_number']}: {row['sdg_name']} -- {row['effectiveness_score']}/5 "
                                   f"{label} ({row.get('rationale') or meaning}){flag}")
                else:
                    st.caption("SDG mapping not yet verified for this offering.")

                if feas["matched_items"]:
                    st.markdown("**Related inventory items**")
                    st.dataframe(
                        [{"Item": m["item_name"], "Available": m["available_quantity"], "Total": m["total_quantity"]}
                         for m in feas["matched_items"]],
                        width="stretch", hide_index=True,
                    )
                else:
                    st.caption(feas.get("note") or "No matching inventory detected.")

    if not filtered:
        empty_state("No offering matches your search.")
