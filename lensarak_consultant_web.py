#!/usr/bin/env python3
"""
LensaRak AI Programme Consultant -- Web Dashboard (Task 2).

A chatbox-style Streamlit front-end over the SAME engine used by the CLI
(lensarak_consultant.py) -- no recommendation/scoring/verification logic is
duplicated here, this file only asks questions and renders results.

The main input is a guided "Quick Request" form whose Theme / Objective /
Audience pickers are populated straight from the organiser's own catalogue
(Theme_Objective_Mapping + Offerings_Master) -- so a stakeholder picks from
what Petrosains actually offers instead of typing every field from scratch,
but every picker still has a "type your own" option for anything not listed.
A free-text chat box stays available underneath for anyone who'd rather just
describe the request in a sentence (parsed by Gemini, or a regex fallback).

Deliberately separate from the Task 1 vision/checkout system: it does not
import or touch lensarak_vision.py / lensarak_ops.py, and only ever reads
lensarak_inventory.db (never writes to it).

USAGE
-----
    streamlit run lensarak_consultant_web.py

Or via LensaRak.bat -> option 9.

Paste a Gemini API key in the sidebar to enable AI-generated storylines; it
works fine without one too (falls back to a simpler templated storyline).
"""

from __future__ import annotations

import streamlit as st

import lensarak_consultant as core

st.set_page_config(page_title="LensaRak AI Programme Consultant", page_icon="🧭", layout="wide")

STATUS_BADGE = {
    "Ready": "🟢 Ready", "Limited": "🟡 Limited", "Unavailable": "🔴 Unavailable", "Unknown": "⚪ Unknown",
}

UNKNOWN = "Tidak pasti / belum tahu"
CUSTOM = "✏️ Lain-lain (taip sendiri)"


# ---------------------------------------------------------------------------
# Cached data loading (catalogue + inventory) -- re-read only when the
# underlying files actually change, so a stock refresh (new .db dropped in)
# is picked up without needing a code change, just a dashboard rerun.
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Memuatkan katalog program & inventori...")
def _load_data(catalogue_mtime, db_mtime):
    offerings, offerings_by_id, constraint_rules, theme_mapping, activity_items = core.load_catalogue(
        core.DEFAULT_CATALOGUE_PATH)
    inventory = core.load_inventory(core.DEFAULT_DB_PATH) if core.DEFAULT_DB_PATH.exists() else []
    return offerings, offerings_by_id, constraint_rules, theme_mapping, activity_items, inventory


def load_data():
    cat_mtime = core.DEFAULT_CATALOGUE_PATH.stat().st_mtime if core.DEFAULT_CATALOGUE_PATH.exists() else 0
    db_mtime = core.DEFAULT_DB_PATH.stat().st_mtime if core.DEFAULT_DB_PATH.exists() else 0
    return _load_data(cat_mtime, db_mtime)


def _split_multi(val):
    return [p.strip() for p in str(val or "").split(";") if p.strip()]


def extract_choice_lists(offerings, theme_mapping):
    """Real vocabulary pulled straight from the organiser catalogue, so the
    dropdowns only ever suggest things Petrosains actually has."""
    themes = sorted({m["Theme"].strip() for m in theme_mapping if m.get("Theme")})
    objectives = sorted({m["Stakeholder_Objective"].strip() for m in theme_mapping if m.get("Stakeholder_Objective")})
    audiences = sorted({a for o in offerings for a in _split_multi(o.get("Audience_Types"))})
    formats = sorted({o["Offering_Type"].strip() for o in offerings if o.get("Offering_Type")})
    return themes, objectives, audiences, formats


def get_gemini_client():
    key = st.session_state.get("gemini_key", "").strip()
    if not key:
        return None
    core.GEMINI_API_KEY = key
    return core._get_gemini_client()


# ---------------------------------------------------------------------------
# "Pick from the catalogue, or type your own" -- shared by theme/objective/
# audience/format. Returns the picked value, the custom-typed text, or None
# when the user explicitly says they're not sure.
# ---------------------------------------------------------------------------

def select_or_custom(label, options, key, help=None):
    choices = [UNKNOWN] + list(options) + [CUSTOM]
    picked = st.selectbox(label, choices, key=f"{key}_sel", help=help)
    if picked == CUSTOM:
        return st.text_input(f"{label} -- taip sendiri", key=f"{key}_custom") or None
    if picked == UNKNOWN:
        return None
    return picked


def prefill_choice(key, value, options):
    """Sets session_state so a demo-scenario button can pre-fill a
    select_or_custom picker before the form widget is (re)created."""
    if value in (None, ""):
        st.session_state[f"{key}_sel"] = UNKNOWN
    elif value in options:
        st.session_state[f"{key}_sel"] = value
    else:
        st.session_state[f"{key}_sel"] = CUSTOM
        st.session_state[f"{key}_custom"] = value


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def render_report(request, missing_information, assumptions, excluded, verified, capacity_plan):
    if request.get("raw_text"):
        st.caption(f"Permintaan: {request['raw_text']}")

    if missing_information:
        st.warning("**Maklumat hilang -- sila jawab untuk cadangan lebih tepat:**\n\n" +
                   "\n".join(f"- {m}" for m in missing_information))
    if assumptions:
        st.info("**Andaian yang dibuat:**\n\n" + "\n".join(f"- {a}" for a in assumptions))

    prog = verified["programme"]
    st.subheader(prog.get("title") or "Cadangan Program")
    if prog.get("storyline"):
        st.write(prog["storyline"])

    if excluded:
        with st.expander(f"❌ {len(excluded)} offering ditolak (kekangan keras)"):
            for e in excluded:
                st.markdown(f"**{e.get('Activity_Title')}** ({e.get('Offering_ID')})")
                for r in e["_exclude_reasons"]:
                    st.markdown(f"- {r}")

    st.markdown("#### Offering Disyorkan")
    if not verified["recommended_offerings"]:
        st.error("Tiada offering verified lepas tapisan kekangan untuk permintaan ini.")
    for r in verified["recommended_offerings"]:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(f"**{r['title']}** ({r['offering_id']})")
                st.caption(r.get("reason", ""))
            with c2:
                st.markdown(STATUS_BADGE.get(r["resource_readiness"], r["resource_readiness"]))
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Tempoh (min)", r["duration_min"])
            m2.metric("Max peserta", r["max_participants"])
            m3.metric("Cost band", r["cost_band"])
            m4.metric("Umur", r["recommended_age"])
            for f in r.get("flags") or []:
                st.warning(f, icon="⚠️")

    if capacity_plan:
        st.markdown("#### Pelan Kapasiti (kumpulan besar)")
        a = capacity_plan["option_a"]
        st.info(f"**Pilihan A -- {a['label']}**: {a['description']}")
        if capacity_plan.get("option_b"):
            b = capacity_plan["option_b"]
            st.success(f"**Pilihan B -- [{b['label']}] {b['name']}**: {b['description']}")
            cols = st.columns(len(b["stations"]))
            for col, s in zip(cols, b["stations"]):
                with col:
                    st.markdown(f"**{s['title']}**")
                    st.caption(s["domain"])
                    st.markdown(f"{s['participants_this_station']} peserta")
                    st.markdown(STATUS_BADGE.get(s["resource_readiness"], s["resource_readiness"]))

    if verified["participant_journey"]:
        st.markdown("#### Participant Journey")
        for i, step in enumerate(verified["participant_journey"], 1):
            st.markdown(f"{i}. {step}")

    if verified["proposed_enhancements"]:
        st.markdown("#### Proposed Enhancement (idea AI -- bukan offering rasmi)")
        for e in verified["proposed_enhancements"]:
            st.success(f"**[{e['label']}] {e.get('name')}** -- {e.get('description', '')}")

    if verified["alternative_options"]:
        st.markdown("#### Alternatif")
        for a in verified["alternative_options"]:
            st.markdown(f"- {a}")

    if verified["risks"]:
        st.markdown("#### Risiko / Nota")
        for r in verified["risks"]:
            st.warning(r)


def run_pipeline(request, missing_information, assumptions):
    offerings, offerings_by_id, constraint_rules, theme_mapping, activity_items, inventory = load_data()
    gemini_client = get_gemini_client()
    excluded, scored, capacity_plan, verified = core.run(
        request, missing_information, assumptions, offerings, offerings_by_id,
        theme_mapping, activity_items, inventory, gemini_client)
    return excluded, verified, capacity_plan


def add_turn(request, missing_information, assumptions):
    excluded, verified, capacity_plan = run_pipeline(request, missing_information, assumptions)
    st.session_state["history"].append({
        "role": "assistant", "request": request, "missing_information": missing_information,
        "assumptions": assumptions, "excluded": excluded, "verified": verified, "capacity_plan": capacity_plan,
    })


# ---------------------------------------------------------------------------
# Load data + catalogue-driven choice lists (needed by both the sidebar demo
# buttons and the main form, so this happens before either is drawn)
# ---------------------------------------------------------------------------

offerings, offerings_by_id, constraint_rules, theme_mapping, activity_items, inventory = load_data()
THEMES, OBJECTIVES, AUDIENCES, FORMATS = extract_choice_lists(offerings, theme_mapping)

st.session_state.setdefault("history", [])
st.session_state.setdefault("pending_request", None)
st.session_state.setdefault("pending_missing", None)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🧭 LensaRak Consultant")
    st.caption("AI Programme Operations Consultant")

    st.text_input("Gemini API key (optional)", type="password", key="gemini_key",
                  help="Kosongkan untuk jalan dengan templat asas (tetap penuh berfungsi, "
                       "cuma storyline kurang 'kaya'). Boleh set GEMINI_API_KEY env var pun.")

    st.divider()
    st.markdown("**Kesegaran data**")
    st.caption(f"Inventory: {core.db_freshness(core.DEFAULT_DB_PATH)}")
    st.caption(f"Katalog: {core.db_freshness(core.DEFAULT_CATALOGUE_PATH)}")
    st.caption("Data inventory disalin manual dari store -- bukan sync real-time. "
               "Ganti fail lensarak_inventory.db dalam folder ini bila stok berubah.")

    st.divider()
    st.markdown("**Skrip demo pantas** (isi form terus)")
    demo_labels = {"a": "A -- Permintaan normal", "b": "B -- Info hilang", "c": "C -- Permintaan mustahil"}
    for skey, label in demo_labels.items():
        if st.button(label, use_container_width=True, key=f"demo_{skey}"):
            preset = core.SCENARIOS[skey]
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

    st.divider()
    if st.button("🗑️ Reset perbualan", use_container_width=True):
        st.session_state["history"] = []
        st.session_state.pop("pending_request", None)
        st.session_state.pop("pending_missing", None)
        st.rerun()


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

st.title("AI Programme Consultant")
st.caption("Pilih dari tema/objektif Petrosains sendiri (atau taip sendiri), sistem akan cadangkan offering "
           "verified, semak stok sebenar, dan bina pelan program yang boleh dipercayai.")

for turn in st.session_state["history"]:
    with st.chat_message(turn["role"]):
        if turn["role"] == "user":
            st.write(turn["text"])
        else:
            render_report(turn["request"], turn["missing_information"], turn["assumptions"],
                          turn["excluded"], turn["verified"], turn["capacity_plan"])

# ---- Guided Quick Request form (primary input path) ----
st.markdown("### Permintaan Baru")
with st.form("quick_request_form"):
    c1, c2, c3 = st.columns(3)
    with c1:
        theme = select_or_custom("Tema program", THEMES, "theme")
    with c2:
        objective = select_or_custom("Objektif stakeholder", OBJECTIVES, "objective")
    with c3:
        audience = select_or_custom("Jenis audiens", AUDIENCES, "audience")

    c4, c5, c6 = st.columns(3)
    with c4:
        age = st.number_input("Umur purata peserta", min_value=0, max_value=100, step=1, key="age")
    with c5:
        participant_count = st.number_input("Bilangan peserta", min_value=0, step=1, key="participant_count")
    with c6:
        duration_min = st.number_input("Tempoh (minit)", min_value=0, step=5, key="duration_min")

    c7, c8 = st.columns(2)
    with c7:
        budget = st.selectbox("Bajet", [UNKNOWN, "Low", "Medium", "High"], key="budget_sel")
    with c8:
        venue = st.selectbox("Venue", [UNKNOWN, "Indoor", "Outdoor"], key="venue_sel")

    with st.expander("Maklumat tambahan (pilihan)"):
        d1, d2, d3 = st.columns(3)
        with d1:
            electricity = st.selectbox("Elektrik tersedia?", [UNKNOWN, "Yes", "No"], key="electricity_sel")
        with d2:
            internet = st.selectbox("Internet tersedia?", [UNKNOWN, "Yes", "No"], key="internet_sel")
        with d3:
            water = st.selectbox("Air tersedia?", [UNKNOWN, "Yes", "No"], key="water_sel")
        preferred_format = select_or_custom("Format pilihan", FORMATS, "format")
        accessibility = st.text_input("Keperluan aksesibiliti khas", key="accessibility")
        notes = st.text_area("Nota tambahan (konteks lain untuk AI)", key="notes")

    submitted = st.form_submit_button("🔍 Dapatkan Cadangan", use_container_width=True, type="primary")

if submitted:
    def _v(x):
        return None if x == UNKNOWN else x

    parts = []
    if theme: parts.append(f"tema {theme}")
    if objective: parts.append(f"objektif {objective}")
    if audience: parts.append(f"audiens {audience}")
    if age: parts.append(f"umur ~{int(age)}")
    if participant_count: parts.append(f"{int(participant_count)} peserta")
    if duration_min: parts.append(f"{int(duration_min)} minit")
    if _v(budget): parts.append(f"bajet {budget}")
    if _v(venue): parts.append(f"venue {venue}")
    summary = (", ".join(parts).capitalize()) if parts else "Permintaan program (tiada butiran diberi)"
    if notes:
        summary += f". Nota tambahan: {notes}"

    request = {
        "raw_text": summary, "theme": theme, "objective": objective, "audience": audience,
        "age": float(age) if age else None,
        "participant_count": float(participant_count) if participant_count else None,
        "duration_min": float(duration_min) if duration_min else None,
        "budget": _v(budget), "venue": _v(venue), "electricity": _v(electricity),
        "internet": _v(internet), "water": _v(water), "accessibility": accessibility or None,
        "preferred_format": preferred_format,
    }
    missing_fields = core.missing_critical_fields(request)
    assumptions = [f"{core.FIELD_PROMPTS[f]}: dipilih 'Tidak pasti' -- andaian dibuat sebagai 'tidak diketahui'"
                   for f in missing_fields]
    st.session_state["history"].append({"role": "user", "text": summary})
    add_turn(request, [], assumptions)
    st.rerun()

# ---- Pending follow-up form for the free-text chat path below ----
if st.session_state["pending_request"] is not None:
    request = st.session_state["pending_request"]
    missing = st.session_state["pending_missing"]
    with st.chat_message("assistant"):
        st.warning("Beberapa maklumat kritikal belum ada. Jawab di bawah untuk cadangan lebih tepat, "
                   "atau tekan 'Guna andaian' untuk teruskan tanpanya.")
        with st.form("followup_form"):
            answers = {}
            if "Umur purata peserta (nombor)" in missing:
                answers["age"] = st.number_input("Umur purata peserta", min_value=0, max_value=100, value=0)
            if "Bilangan peserta (nombor)" in missing:
                answers["participant_count"] = st.number_input("Bilangan peserta", min_value=0, value=0)
            if "Tempoh yang ada (minit)" in missing:
                answers["duration_min"] = st.number_input("Tempoh (minit)", min_value=0, value=0)
            if "Venue (Indoor/Outdoor)" in missing:
                answers["venue"] = st.selectbox("Venue", ["", "Indoor", "Outdoor"])
            if "Bajet (Low/Medium/High)" in missing:
                answers["budget"] = st.selectbox("Bajet", ["", "Low", "Medium", "High"])
            fc1, fc2 = st.columns(2)
            f_submitted = fc1.form_submit_button("Hantar jawapan", use_container_width=True)
            f_assume = fc2.form_submit_button("Guna andaian sahaja", use_container_width=True)

        if f_submitted or f_assume:
            assumptions = []
            for field, val in answers.items():
                if f_submitted and val not in (0, ""):
                    request[field] = val
                else:
                    assumptions.append(f"{field}: tidak dinyatakan -- andaian dibuat sebagai 'tidak diketahui'")
            add_turn(request, [], assumptions)
            st.session_state["pending_request"] = None
            st.session_state["pending_missing"] = None
            st.rerun()

# ---- Alternate free-text path ----
st.caption("Atau, kalau lebih senang, terangkan terus dalam ayat -- sistem cuba faham automatik:")
raw_text = st.chat_input("Terangkan keperluan program stakeholder dalam ayat...")

if raw_text and st.session_state["pending_request"] is None:
    st.session_state["history"].append({"role": "user", "text": raw_text})
    gemini_client = get_gemini_client()
    request = core.parse_request_core(raw_text, gemini_client)
    missing = core.missing_critical_fields(request)
    if missing:
        st.session_state["pending_request"] = request
        st.session_state["pending_missing"] = missing
    else:
        add_turn(request, [], [])
    st.rerun()

if not st.session_state["history"]:
    st.info("Isi form di atas (pilih tema/objektif dari senarai Petrosains sendiri), atau cuba salah satu "
            "skrip demo di sidebar kiri.")
