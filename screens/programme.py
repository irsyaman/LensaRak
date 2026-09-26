"""
programme.py -- Plan Programme: the main AI Programme Consultant workflow.

Two phases, driven by session_state:
  1. Guided request form -> constraint filtering + scoring (engine.matcher) ->
     AI storyline (Gemini, optional) -> verified result shown with SDG
     alignment, capacity/rotations, equipment readiness.
  2. Pick a recommended offering -> choose a real date/time -> check it
     against actual reservations (engine.reservations) -> reserve resources
     + create the calendar event.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, time, timedelta

import streamlit as st

from engine import constraints, database, loader, matcher, quotation, scheduler, sdg, verifier
from screens._common import badge, empty_state, page_header, require_permission

UNKNOWN = "Not sure / not yet known"
CUSTOM = "✏️ Other (type your own)"


def _load_gemini_key() -> str:
    """Reads the Gemini key from either an OS environment variable (local
    laptop, set once via `setx GEMINI_API_KEY ...`) or Streamlit Cloud's
    Secrets manager (st.secrets) -- whichever is present. Never hardcoded,
    never logged, never shown back in the UI."""
    val = os.environ.get("GEMINI_API_KEY", "")
    if val:
        return val
    try:
        return st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        return ""  # no secrets.toml locally -- fine, just means no key yet


GEMINI_API_KEY_ENV = _load_gemini_key()
GEMINI_MODEL = "gemini-3.8-flash"  # gemini-2.5-flash was retired for new API keys (Google's own 404 said so)

SYSTEM_POLICY = (
    "You are the LensaRak AI Programme Operations Consultant for Petrosains. "
    "Recommend only from the supplied verified offerings list. Never invent an "
    "offering, duration, capacity, pricing, inventory quantity, safety condition "
    "or availability -- use only the values given to you. If required information "
    "is missing, say so or state the explicit assumption already given to you; "
    "never silently assume. Any idea you generate that is not one of the supplied "
    "verified offerings must be labelled 'Proposed Enhancement' and never presented "
    "as an existing Petrosains offering. Return structured JSON only, matching "
    "exactly the schema described in the prompt -- no prose outside the JSON."
)

REQUEST_SCHEMA_FIELDS = [
    "theme", "objective", "audience", "age", "participant_count", "duration_min",
    "budget", "venue", "electricity", "internet", "water", "accessibility", "preferred_format",
]
CRITICAL_FIELDS = ["age", "participant_count", "duration_min", "venue", "budget"]
FIELD_PROMPTS = {
    "theme": "Programme theme/title", "objective": "Stakeholder objective", "audience": "Audience type",
    "age": "Average participant age (number)", "participant_count": "Number of participants (number)",
    "duration_min": "Available duration (minutes)", "budget": "Budget (Low/Medium/High)",
    "venue": "Venue (Indoor/Outdoor)", "electricity": "Electricity available? (Yes/No)",
    "internet": "Internet available? (Yes/No)", "water": "Water available? (Yes/No)",
    "accessibility": "Special accessibility needs?", "preferred_format": "Preferred format",
}

SCENARIOS = {
    "a": dict(raw_text="Renewable energy programme for 30 primary school students, 90 minutes.",
              theme="Renewable Energy", objective="Sustainability awareness", audience="Primary",
              age=10, participant_count=30, duration_min=90, budget="Medium", venue="Indoor", electricity="Yes"),
    "b": dict(raw_text="Robotics programme.", theme="Robotics", objective=None, audience=None, age=None,
              participant_count=None, duration_min=None, budget=None, venue=None, electricity=None),
    "c": dict(raw_text="Mini-drone activity for 500 preschool students, 30 minutes, very low budget.",
              theme="Aviation", objective="Engineering exposure", audience="Early Years", age=4,
              participant_count=500, duration_min=30, budget="Low", venue="Indoor", electricity="Yes"),
}


# ---------------------------------------------------------------------------
# Gemini helpers (optional -- degrade to templated fallback with no key)
# ---------------------------------------------------------------------------

def _get_gemini_client():
    """Returns (client, error) -- error is None on success, or a short honest
    reason it didn't work (never silently mislabelled as "no key" when the
    real cause was something else, e.g. the SDK package missing)."""
    key = st.session_state.get("gemini_key", "").strip() or GEMINI_API_KEY_ENV
    if not key:
        return None, "no Gemini API key is set (GEMINI_API_KEY env var / Streamlit secret)"
    try:
        from google import genai
    except Exception as e:
        return None, f"the 'google-genai' package isn't installed/importable ({e})"
    try:
        return genai.Client(api_key=key), None
    except Exception as e:
        return None, f"client setup failed: {e}"


def compose_programme_llm(client, request, ranked, capacity_plan, missing_information, assumptions):
    context = {
        "request": {k: v for k, v in request.items() if k != "raw_text"},
        "raw_request_text": request.get("raw_text"),
        "missing_information": missing_information, "assumptions": assumptions,
        "verified_candidates": [
            {"offering_id": c["offering"].get("Offering_ID"), "title": c["offering"].get("Activity_Title"),
             "short_description": c["offering"].get("Short_Description"), "match_score": c["score"]["total"],
             "score_breakdown": c["score"]["breakdown"], "flags": c["offering"].get("_flags", []),
             "resource_readiness": c["resource"]["status"]}
            for c in ranked[:5]
        ],
        "capacity_plan": capacity_plan,
    }
    prompt = (
        f"{SYSTEM_POLICY}\n\nContext (verified data -- do not add offerings not listed here):\n"
        f"{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
        "Return JSON with exactly these top-level keys: "
        "programme (object: title, theme, objective, storyline), "
        "recommended_offerings (array of {offering_id, reason}), "
        "participant_journey (array of strings), "
        "proposed_enhancements (array of {name, description}), "
        "risks (array of strings), alternative_options (array of strings)."
    )
    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=[prompt])
        text = re.sub(r"^```(json)?|```$", "", (response.text or "").strip(), flags=re.MULTILINE).strip()
        return json.loads(text), None
    except Exception as e:
        return None, f"Gemini call failed: {e}"


def compose_programme_fallback(request, ranked, capacity_plan, error_reason: str | None = None):
    if not ranked:
        return {"programme": {"title": "No suitable offering found", "theme": request.get("theme"),
                                "objective": request.get("objective"), "storyline": ""},
                "recommended_offerings": [], "participant_journey": [], "proposed_enhancements": [],
                "risks": ["No verified offering passed the constraint filtering."], "alternative_options": []}
    top = ranked[0]["offering"]
    theme_raw = request.get("theme") or "STEM"
    theme = theme_raw if len(theme_raw) <= 40 else theme_raw[:37].rsplit(" ", 1)[0] + "..."
    storyline = (f"A {theme}-themed programme begins with a concept introduction, followed by the main "
                 f"hands-on activity '{top.get('Activity_Title')}', and closes with a reflection session.")
    journey = ["Introduction & ice-breaker (5-10 min)",
               f"Main activity: {top.get('Activity_Title')} -- {top.get('Short_Description')}",
               "Reflection & sharing session (10 min)"]
    if capacity_plan:
        journey.append("Operational note: see the rotation/station plan below.")
    return {
        "programme": {"title": f"{theme} Programme", "theme": theme, "objective": request.get("objective"), "storyline": storyline},
        "recommended_offerings": [{"offering_id": c["offering"].get("Offering_ID"),
                                     "reason": f"Match score {c['score']['total']}/100."} for c in ranked[:3]],
        "participant_journey": journey, "proposed_enhancements": [],
        "risks": [f"This storyline is a basic template -- {error_reason or 'no Gemini API key set'}."],
        "alternative_options": [f"{c['offering'].get('Activity_Title')} ({c['offering'].get('Offering_ID')})" for c in ranked[1:4]],
    }


def missing_critical_fields(request: dict) -> list[str]:
    return [f for f in CRITICAL_FIELDS if request.get(f) in (None, "")]


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

def select_or_custom(label, options, key, help=None):
    choices = [UNKNOWN] + list(options) + [CUSTOM]
    picked = st.selectbox(label, choices, key=f"{key}_sel", help=help)
    if picked == CUSTOM:
        return st.text_input(f"{label} -- type your own", key=f"{key}_custom") or None
    if picked == UNKNOWN:
        return None
    return picked


def prefill_choice(key, value, options):
    if value in (None, ""):
        st.session_state[f"{key}_sel"] = UNKNOWN
    elif value in options:
        st.session_state[f"{key}_sel"] = value
    else:
        st.session_state[f"{key}_sel"] = CUSTOM
        st.session_state[f"{key}_custom"] = value


# ---------------------------------------------------------------------------
# Booking sub-flow (step 2)
# ---------------------------------------------------------------------------

def _split_themes(offering) -> set[str]:
    return {t.strip().lower() for t in str(offering.get("Suitable_Themes") or "").split(";") if t.strip()}


def _render_conflict_suggestions(conn, res, offering, scored, qtys, duration_min, start_s, participants,
                                  activity_items, inventory):
    """Two clashing events, same day: rather than just blocking, actively
    suggest a way forward -- the next time this exact set of items is fully
    free, and/or other offerings from the same theme(s) that ARE free at the
    time originally requested."""
    st.markdown("**Suggestions**")
    any_suggestion = False

    next_slot = res.find_next_fully_available_slot(conn, qtys, duration_min, start_s)
    if next_slot:
        any_suggestion = True
        slot_dt = datetime.strptime(next_slot, "%Y-%m-%dT%H:%M")
        if st.button(f"🔁 Use next available time instead: {slot_dt.strftime('%d %b %Y, %H:%M')}", key="book_jump_slot"):
            st.session_state["book_jump_pending"] = {"date": slot_dt.date(), "time": slot_dt.time()}
            st.rerun()
    else:
        st.caption(f"No slot in the next 14 days has every required item free at once for this offering.")

    if scored:
        my_themes = _split_themes(offering)
        my_oid = offering.get("Offering_ID")
        end_s = (datetime.strptime(start_s, "%Y-%m-%dT%H:%M") + timedelta(minutes=duration_min)).strftime("%Y-%m-%dT%H:%M")
        shown = 0
        for c in scored:
            if shown >= 3:
                break
            alt = c["offering"]
            alt_oid = alt.get("Offering_ID")
            if alt_oid == my_oid:
                continue
            if my_themes and not (my_themes & _split_themes(alt)):
                continue
            alt_qtys = scheduler.suggested_quantities_for_booking(alt_oid, activity_items, inventory, participants)
            if not alt_qtys:
                continue
            alt_feas = res.check_booking_feasibility(conn, alt_qtys, start_s, end_s)
            if any(v["status"] == "CONFLICT" for v in alt_feas.values()):
                continue
            any_suggestion = True
            shown += 1
            if st.button(f"🔀 Try \"{alt.get('Activity_Title')}\" instead -- same theme, free at this time",
                         key=f"book_alt_{alt_oid}"):
                st.session_state["booking_target_offering_id"] = alt_oid
                st.session_state["last_booked_event_id"] = None
                st.rerun()

    if not any_suggestion:
        st.caption("No same-theme alternative is free at this time either -- try a different date, "
                   "or adjust quantities/venue above.")


def _render_booking_panel(conn, offering, activity_items, inventory, request, scored=None):
    st.markdown(f"### 📅 Book: {offering.get('Activity_Title')}")
    default_participants = int(request.get("participant_count") or 30)
    default_duration = int(request.get("duration_min") or 60)

    # Set by the "Use this time instead" button below, from a PREVIOUS run --
    # applied here, before the date/time widgets are created, so they pick it
    # up as their current value this run.
    jump = st.session_state.pop("book_jump_pending", None)
    if jump:
        st.session_state["book_date"] = jump["date"]
        st.session_state["book_start"] = jump["time"]

    bc1, bc2, bc3, bc4 = st.columns(4)
    ev_date = bc1.date_input("Event date", value=date.today() + timedelta(days=1), key="book_date")
    ev_start = bc2.time_input("Start time", value=time(9, 0), key="book_start")
    ev_participants = bc3.number_input("Number of participants", min_value=1, value=default_participants, key="book_participants")
    ev_venue = bc4.text_input("Venue", value=request.get("venue") or "", key="book_venue")

    max_p = loader.parse_number(offering.get("Max_Participants"))
    rotations = scheduler.compute_rotations(ev_participants, max_p) if max_p else 1
    participants_this_slot = min(ev_participants, max_p) if max_p else ev_participants

    start_dt = datetime.combine(ev_date, ev_start)
    end_dt = start_dt + timedelta(minutes=default_duration)
    st.caption(f"Slot: {start_dt.strftime('%Y-%m-%d %H:%M')} -> {end_dt.strftime('%H:%M')}"
               + (f"  |  {rotations} rotation(s) needed (max {int(max_p)} participants/session)" if rotations > 1 else ""))

    qtys = scheduler.suggested_quantities_for_booking(
        offering.get("Offering_ID"), activity_items, inventory, participants_this_slot)

    from engine import reservations as res
    start_s = start_dt.strftime("%Y-%m-%dT%H:%M")
    end_s = end_dt.strftime("%Y-%m-%dT%H:%M")

    if not qtys:
        st.info("No inventory items could be automatically matched for this offering -- "
                "you can still book the event below (without a resource reservation), or check stock manually.")
        if not require_permission("create_booking", "Your role is not permitted to create bookings."):
            return
        if st.button("✅ Schedule event (no resource reservation)", type="primary", key="book_confirm_no_items"):
            event_id = res.create_event(
                conn, offering.get("Activity_Title"), offering.get("Offering_ID"),
                st.session_state.get("user_id"), start_s, end_s, ev_venue, int(ev_participants),
                notes=request.get("raw_text"),
            )
            st.success(f"Event #{event_id} scheduled (no resources reserved). See it in 'Reservations & Calendar'.")
            st.session_state["booking_target_offering_id"] = None
            st.balloons()
        return

    st.markdown("**Suggested quantities (adjustable before confirming):**")
    edited_qtys = {}
    for item_code, qty in qtys.items():
        item = conn.execute("SELECT item_name FROM items WHERE item_code = ?", (item_code,)).fetchone()
        label = f"{item['item_name']} ({item_code})" if item else item_code
        edited_qtys[item_code] = st.number_input(label, min_value=0, value=int(qty), key=f"book_qty_{item_code}")
    qtys = {k: v for k, v in edited_qtys.items() if v > 0}

    if not qtys:
        st.warning("All quantities set to 0 -- adjust at least one item above, or use a request with matchable items.")
        return

    # Feasibility is computed fresh on every render (a cheap read-only query) --
    # no separate "Check conflicts" click required, so there's no hidden step
    # between picking an offering and being able to press the confirm button.
    feas = res.check_booking_feasibility(conn, qtys, start_s, end_s)
    any_conflict = False
    for item_code, info in feas.items():
        item = conn.execute("SELECT item_name FROM items WHERE item_code = ?", (item_code,)).fetchone()
        label = item["item_name"] if item else item_code
        if info["status"] == "CONFLICT":
            any_conflict = True
            extra = f" -- next available: {info['next_available_at']}" if info["next_available_at"] else ""
            st.error(f"{badge('CONFLICT')} {label}: need {info['requested']}, have {info['available']}{extra}")
        else:
            st.success(f"{badge('AVAILABLE')} {label}: {info['available']} available (need {info['requested']})")

    if any_conflict:
        _render_conflict_suggestions(conn, res, offering, scored, qtys, default_duration, start_s, ev_participants,
                                      activity_items, inventory)

    if not require_permission("create_booking", "Your role is not permitted to create bookings."):
        return
    if any_conflict:
        st.caption("Reserve button is disabled while any item above shows a CONFLICT -- adjust the date/time, "
                   "quantities, or venue above and the check re-runs automatically, or use a suggestion above.")
    if st.button("✅ Reserve resources & schedule event", type="primary", disabled=any_conflict, key="book_confirm"):
        event_id = res.create_event(
            conn, offering.get("Activity_Title"), offering.get("Offering_ID"),
            st.session_state.get("user_id"), start_s, end_s, ev_venue, int(ev_participants),
            notes=request.get("raw_text"),
        )
        outcome = res.reserve_resources(conn, event_id, qtys, start_s, end_s, st.session_state.get("user_id"))
        st.success(f"Event #{event_id} scheduled and resources reserved. See it in 'Reservations & Calendar'.")
        st.session_state["booking_target_offering_id"] = None
        st.session_state["last_booked_event_id"] = event_id
        st.balloons()

    # Shown once an event from THIS render session was just booked (also
    # available any time later from Reservations & Calendar, since the
    # booking itself doesn't disappear).
    last_event_id = st.session_state.get("last_booked_event_id")
    if last_event_id:
        xlsx_bytes = quotation.build_quotation_xlsx(conn, last_event_id, offering)
        if xlsx_bytes:
            st.download_button(
                "🧾 Download booking summary / quotation draft (.xlsx)",
                data=xlsx_bytes,
                file_name=f"LensaRak_quotation_event{last_event_id}.xlsx",
                key=f"quote_dl_{last_event_id}",
            )
            st.caption("No real prices exist in the catalogue (only a Low/Medium/High cost band) -- "
                       "this file leaves the price cells blank for you to fill in with your real rate card; "
                       "the Grand Total adds itself up once you do.")


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(conn):
    page_header("Plan Programme", "AI Programme Consultant -- recommendations backed by real data, not guesswork.")

    offerings, offerings_by_id, constraint_rules, theme_mapping, activity_items = loader.load_catalogue()
    inventory = database.load_inventory(conn)
    THEMES, OBJECTIVES, AUDIENCES, FORMATS = loader.extract_choice_lists(offerings, theme_mapping)

    with st.expander("⚡ Quick demo scripts"):
        demo_labels = {"a": "A -- Normal request", "b": "B -- Missing info", "c": "C -- Impossible request"}
        cols = st.columns(3)
        for col, (skey, label) in zip(cols, demo_labels.items()):
            if col.button(label, width="stretch", key=f"demo_{skey}"):
                preset = SCENARIOS[skey]
                prefill_choice("theme", preset["theme"], THEMES)
                prefill_choice("objective", preset["objective"], OBJECTIVES)
                prefill_choice("audience", preset["audience"], AUDIENCES)
                st.session_state["age"] = int(preset["age"]) if preset["age"] else 0
                st.session_state["participant_count"] = int(preset["participant_count"]) if preset["participant_count"] else 0
                st.session_state["duration_min"] = int(preset["duration_min"]) if preset["duration_min"] else 0
                st.session_state["budget_sel"] = preset["budget"] or UNKNOWN
                st.session_state["venue_sel"] = preset["venue"] or UNKNOWN
                st.session_state["electricity_sel"] = preset["electricity"] or UNKNOWN
                st.rerun()

    with st.form("quick_request_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            theme = select_or_custom("Programme theme", THEMES, "theme")
        with c2:
            objective = select_or_custom("Stakeholder objective", OBJECTIVES, "objective")
        with c3:
            audience = select_or_custom("Audience type", AUDIENCES, "audience")

        c4, c5, c6 = st.columns(3)
        with c4:
            age = st.number_input("Average participant age", min_value=0, max_value=100, step=1, key="age")
        with c5:
            participant_count = st.number_input("Number of participants", min_value=0, step=1, key="participant_count")
        with c6:
            duration_min = st.number_input("Duration (minutes)", min_value=0, step=5, key="duration_min")

        c7, c8 = st.columns(2)
        with c7:
            budget = st.selectbox("Budget", [UNKNOWN, "Low", "Medium", "High"], key="budget_sel")
        with c8:
            venue = st.selectbox("Venue", [UNKNOWN, "Indoor", "Outdoor"], key="venue_sel")

        with st.expander("Additional information (optional)"):
            d1, d2, d3 = st.columns(3)
            with d1:
                electricity = st.selectbox("Electricity available?", [UNKNOWN, "Yes", "No"], key="electricity_sel")
            with d2:
                internet = st.selectbox("Internet available?", [UNKNOWN, "Yes", "No"], key="internet_sel")
            with d3:
                water = st.selectbox("Water available?", [UNKNOWN, "Yes", "No"], key="water_sel")
            preferred_format = select_or_custom("Preferred format", FORMATS, "format")
            accessibility = st.text_input("Special accessibility needs", key="accessibility")

        submitted = st.form_submit_button("🔍 Get Recommendations", width="stretch", type="primary")

    if submitted:
        def _v(x):
            return None if x == UNKNOWN else x
        parts = []
        if theme: parts.append(f"theme {theme}")
        if objective: parts.append(f"objective {objective}")
        if audience: parts.append(f"audience {audience}")
        if age: parts.append(f"age ~{int(age)}")
        if participant_count: parts.append(f"{int(participant_count)} participants")
        if duration_min: parts.append(f"{int(duration_min)} minutes")
        summary = (", ".join(parts).capitalize()) if parts else "Programme request (no details given)"

        request = {
            "raw_text": summary, "theme": theme, "objective": objective, "audience": audience,
            "age": float(age) if age else None,
            "participant_count": float(participant_count) if participant_count else None,
            "duration_min": float(duration_min) if duration_min else None,
            "budget": _v(budget), "venue": _v(venue), "electricity": _v(electricity),
            "internet": _v(internet), "water": _v(water), "accessibility": accessibility or None,
            "preferred_format": preferred_format,
        }
        missing_fields = missing_critical_fields(request)
        assumptions = [f"{FIELD_PROMPTS[f]}: selected 'Not sure' -- assumed 'unknown'" for f in missing_fields]

        feasible, excluded = constraints.apply_hard_constraints(request, offerings)
        scored, _, resource_by_id, flags_by_id = matcher.recommend_programmes(
            request, offerings, theme_mapping, activity_items, inventory)
        capacity_plan = constraints.handle_capacity_overflow(request, scored, activity_items, inventory)

        gemini_client, client_error = _get_gemini_client()
        llm_result, llm_error = (
            compose_programme_llm(gemini_client, request, scored, capacity_plan, missing_fields, assumptions)
            if gemini_client else (None, client_error)
        )
        programme = llm_result or compose_programme_fallback(request, scored, capacity_plan, llm_error)
        verified = verifier.verify_output(programme, offerings_by_id, resource_by_id, flags_by_id)

        st.session_state["prog_result"] = {
            "request": request, "missing": missing_fields, "assumptions": assumptions,
            "excluded": excluded, "scored": scored, "capacity_plan": capacity_plan, "verified": verified,
        }
        st.session_state["booking_target_offering_id"] = None
        st.session_state["book_feasibility"] = None

    result = st.session_state.get("prog_result")
    if not result:
        empty_state("Fill in the form above, or try one of the demo scripts to see how the system works.")
        return

    request, missing, assumptions = result["request"], result["missing"], result["assumptions"]
    excluded, scored, capacity_plan, verified = result["excluded"], result["scored"], result["capacity_plan"], result["verified"]

    st.divider()
    st.caption(f"1. Request: {request.get('raw_text')}")

    if missing:
        st.warning("**2. Missing information / assumptions:**\n\n" + "\n".join(f"- {FIELD_PROMPTS.get(f, f)}" for f in missing))
    if assumptions:
        st.info("\n".join(f"- {a}" for a in assumptions))

    prog = verified["programme"]
    st.subheader(f"3. {prog.get('title') or 'Programme Recommendation'}")
    if prog.get("storyline"):
        st.write(prog["storyline"])

    # Tie-break question if top 2 scores are close
    if len(scored) >= 2 and abs(scored[0]["score"]["total"] - scored[1]["score"]["total"]) <= 5 \
            and scored[0]["offering"].get("Offering_Type") != scored[1]["offering"].get("Offering_Type"):
        st.markdown("**Two offerings scored almost the same -- what's your preference?**")
        pref = st.radio("Choose", [scored[0]["offering"].get("Offering_Type"), scored[1]["offering"].get("Offering_Type")],
                         horizontal=True, key="tiebreak_pref")
        if pref == scored[1]["offering"].get("Offering_Type"):
            scored[0], scored[1] = scored[1], scored[0]

    if excluded:
        with st.expander(f"❌ {len(excluded)} offering(s) excluded (hard constraints)"):
            for e in excluded:
                st.markdown(f"**{e.get('Activity_Title')}** ({e.get('Offering_ID')})")
                for r in e["_exclude_reasons"]:
                    st.markdown(f"- {r}")

    st.markdown("#### 4-7. Recommended Offerings (match, SDG, capacity, stock readiness)")
    if not verified["recommended_offerings"]:
        st.error("No verified offering passed the constraint filtering for this request.")
    for r in verified["recommended_offerings"]:
        with st.container(border=True):
            top1, top2 = st.columns([4, 1])
            top1.markdown(f"**{r['title']}** ({r['offering_id']})")
            top1.caption(r.get("reason", ""))
            top2.markdown(badge(r["resource_readiness"]))

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Duration (min)", r["duration_min"])
            m2.metric("Max participants", r["max_participants"])
            m3.metric("Cost band", r["cost_band"])
            m4.metric("Age", r["recommended_age"])
            for f in r.get("flags") or []:
                st.warning(f, icon="⚠️")

            sdg_rows = sdg.get_sdg_for_offering(conn, r["offering_id"])
            if sdg_rows:
                any_unreviewed = any(not sdg.is_reviewed(row) for row in sdg_rows)
                st.markdown("**UN SDG Alignment**" + ("  ⚠️ *draft, pending organiser review*" if any_unreviewed else ""))
                for row in sdg_rows:
                    label, meaning = sdg.score_label(row["effectiveness_score"])
                    flag = "" if sdg.is_reviewed(row) else " *(unverified draft)*"
                    st.caption(f"SDG {row['sdg_number']}: {row['sdg_name']} -- {row['effectiveness_score']}/5 {label} "
                               f"({row.get('rationale') or meaning}){flag}")
            else:
                st.caption("SDG mapping not yet verified for this offering.")

            # Permission is enforced inside _render_booking_panel before any write happens;
            # the button itself is shown to everyone so a Viewer can at least see the flow.
            if st.button(f"📌 Select & Book -- {r['title']}", key=f"pick_{r['offering_id']}"):
                st.session_state["booking_target_offering_id"] = r["offering_id"]
                st.session_state["last_booked_event_id"] = None

    if capacity_plan:
        st.markdown("#### Capacity Plan (large group)")
        a = capacity_plan["option_a"]
        st.info(f"**Option A -- {a['label']}**: {a['description']}")
        if capacity_plan.get("option_b"):
            b = capacity_plan["option_b"]
            st.success(f"**Option B -- [{b['label']}] {b['name']}**: {b['description']}")

    if verified["proposed_enhancements"]:
        st.markdown("#### Proposed Enhancement (AI idea -- not an official offering)")
        for e in verified["proposed_enhancements"]:
            st.success(f"**[{e['label']}] {e.get('name')}** -- {e.get('description', '')}")

    if verified["alternative_options"]:
        st.markdown("#### Alternatives")
        for a in verified["alternative_options"]:
            st.markdown(f"- {a}")

    if verified["risks"]:
        st.markdown("#### Risks / Notes")
        for r in verified["risks"]:
            st.warning(r)

    target_oid = st.session_state.get("booking_target_offering_id")
    if target_oid and target_oid in offerings_by_id:
        st.divider()
        _render_booking_panel(conn, offerings_by_id[target_oid], activity_items, inventory, request, scored)
