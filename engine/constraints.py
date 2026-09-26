"""
constraints.py -- Deterministic hard-constraint checks (Constraint_Rules
RULE-001..012) and live inventory feasibility / capacity-overflow planning.

Nothing in this file ever calls an LLM. This is the "never hallucinate"
layer: an offering either passes these checks against real data or it
doesn't -- an AI never gets a vote on feasibility, only on how to describe
what already passed.
"""

from __future__ import annotations

import math

from .loader import parse_min_age, parse_number, token_variants, tokens

COST_RANK = {"low": 1, "medium": 2, "high": 3}


# ---------------------------------------------------------------------------
# Inventory matching -- which real items does an offering actually need?
# ---------------------------------------------------------------------------

def match_offering_inventory(offering_id: str, activity_items: dict, inventory: list[dict]) -> list[dict]:
    texts = activity_items.get(offering_id, [])
    if not texts:
        return []
    blob_tokens = token_variants(tokens(" ".join(texts)))
    matched = []
    for item in inventory:
        name_tokens = tokens(item["item_name"])
        if not name_tokens:
            continue
        overlap = name_tokens & blob_tokens
        if len(overlap) == len(name_tokens):
            matched.append(item)
        elif len(name_tokens) >= 3 and len(overlap) >= len(name_tokens) - 1:
            matched.append(item)
    return matched


def check_inventory_feasibility(offering_id: str, activity_items: dict, inventory: list[dict],
                                 participant_count: float | None) -> dict:
    """Returns {"status": "Ready"|"Limited"|"Unavailable"|"Unknown", ...}.
    "Unknown" means the offering's materials genuinely aren't tracked in the
    general inventory (a specialty kit, say) -- that's an honest answer, not
    a bug, and is never silently upgraded to "Ready"."""
    matched = match_offering_inventory(offering_id, activity_items, inventory)
    if not matched:
        return {
            "status": "Unknown",
            "matched_items": [],
            "note": "No inventory item was automatically matched from this activity's bill-of-materials list "
                    "-- please check stock manually.",
        }
    status = "Ready"
    details = []
    for it in matched:
        avail = it["available_quantity"] or 0
        if avail <= 0:
            status = "Unavailable"
        elif participant_count and avail < participant_count and status != "Unavailable":
            status = "Limited"
        details.append({
            "item_code": it["item_code"], "item_name": it["item_name"],
            "available_quantity": avail, "total_quantity": it["total_quantity"],
        })
    return {"status": status, "matched_items": details, "note": ""}


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

def apply_hard_constraints(request: dict, offerings: list[dict]):
    """Splits offerings into (feasible, excluded). Feasible offerings keep a
    "_flags" list of soft warnings (age unconfirmed, budget slightly over,
    etc.) that don't disqualify them but should still be shown. Excluded
    offerings keep a "_exclude_reasons" list explaining why."""
    feasible, excluded = [], []
    req_budget_rank = COST_RANK.get(str(request.get("budget") or "").strip().lower())

    for offering in offerings:
        exclude_reasons = []
        flags = []

        min_age = parse_min_age(offering.get("Recommended_Age"))
        if request.get("age") is not None and min_age is not None and request["age"] < min_age:
            exclude_reasons.append(
                f"RULE-001 Age: offering requires {offering.get('Recommended_Age')}, "
                f"request states age {request['age']}")
        elif request.get("age") is None and min_age is not None:
            flags.append(
                f"RULE-001 Age NOT YET confirmed: this offering requires a minimum age of "
                f"{offering.get('Recommended_Age')} -- confirm participant age before this becomes a final recommendation")

        if str(offering.get("Electricity_Required") or "").strip().lower() == "yes" \
                and str(request.get("electricity") or "").strip().lower() == "no":
            exclude_reasons.append("RULE-003 Electricity: offering requires electricity, venue states no electricity")

        if str(request.get("venue") or "").strip().lower() == "outdoor" \
                and str(offering.get("Indoor_Outdoor") or "").strip().lower() == "indoor":
            flags.append("RULE-006 Venue: this offering is verified for indoor use only -- confirm "
                          "shelter/surface requirements before using it outdoors")

        setup = parse_number(offering.get("Setup_Time_Min")) or 0
        std_dur = parse_number(offering.get("Standard_Duration_Min")) or 0
        req_dur = parse_number(request.get("duration_min"))
        if req_dur is not None and std_dur and req_dur < (setup + std_dur) * 0.6:
            flags.append(
                f"RULE-005 Duration: requested duration ({int(req_dur)} min) may not be enough -- this offering "
                f"usually needs ~{int(setup + std_dur)} min including setup")

        off_cost_rank = COST_RANK.get(str(offering.get("Cost_Band") or "").strip().lower())
        if req_budget_rank and off_cost_rank:
            if off_cost_rank - req_budget_rank >= 2:
                exclude_reasons.append(
                    f"RULE-008 Budget: offering cost band '{offering.get('Cost_Band')}' far exceeds the requested budget")
            elif off_cost_rank > req_budget_rank:
                flags.append(f"RULE-008 Budget: cost band '{offering.get('Cost_Band')}' is higher than the "
                              f"requested budget -- please confirm")

        max_p = parse_number(offering.get("Max_Participants"))
        req_p = parse_number(request.get("participant_count"))
        if req_p and max_p and req_p > max_p:
            rotations = math.ceil(req_p / max_p)
            flags.append(f"RULE-002 Capacity: max {int(max_p)} participants/session -- needs {rotations} "
                         f"separate rotation(s)/session(s) for {int(req_p)} participants")

        if str(request.get("accessibility") or "").strip() and str(request.get("accessibility")).lower() not in ("no", "none", ""):
            flags.append(f"RULE-011 Accessibility: {offering.get('Accessibility_Notes') or 'no specific notes recorded'}")

        if exclude_reasons:
            excluded.append({**offering, "_exclude_reasons": exclude_reasons})
        else:
            feasible.append({**offering, "_flags": flags})

    return feasible, excluded


# ---------------------------------------------------------------------------
# Capacity overflow -- batch sessions vs multi-station rotation
# ---------------------------------------------------------------------------

def handle_capacity_overflow(request: dict, ranked: list[dict], activity_items: dict,
                              inventory: list[dict]) -> dict | None:
    """ranked is matcher.recommend_programmes()'s scored list (best-first).
    Returns None when the request isn't a meaningful overflow case."""
    req_p = parse_number(request.get("participant_count"))
    if not req_p or not ranked:
        return None
    top = ranked[0]
    max_p = parse_number(top["offering"].get("Max_Participants"))
    if not max_p or req_p <= max_p * 1.5:
        return None  # not a meaningful overflow -- the ordinary rotation flag above is enough

    # Option A -- batch / turn-based sessions, same offering repeated
    rotations = math.ceil(req_p / max_p)
    option_a = {
        "label": "Staggered Sessions (Batch)",
        "description": f"Split {int(req_p)} participants into {rotations} separate sessions "
                        f"(~{math.ceil(req_p / rotations)} participants/session) using the same offering "
                        f"'{top['offering'].get('Activity_Title')}', run session by session "
                        f"(morning/afternoon or across several days).",
        "rotations": rotations,
        "offering_id": top["offering"].get("Offering_ID"),
    }

    # Option B -- multi-station rotation using distinct verified offerings
    # from different STEM domains, run in parallel. Proposed Enhancement:
    # this rotation *arrangement* is our idea -- every station activity
    # itself is still a real, verified offering.
    seen_domains = set()
    stations = []
    for cand in ranked:
        off = cand["offering"]
        domain_key = str(off.get("STEM_Domain") or "Other").split(";")[0].strip()
        if domain_key in seen_domains:
            continue
        seen_domains.add(domain_key)
        stations.append(off)
        if len(stations) == 4:
            break

    option_b = None
    if len(stations) >= 2:
        n_stations = len(stations)
        per_station = math.ceil(req_p / n_stations)
        station_plan = []
        for off in stations:
            feas = check_inventory_feasibility(off.get("Offering_ID"), activity_items, inventory, per_station)
            station_plan.append({
                "offering_id": off.get("Offering_ID"),
                "title": off.get("Activity_Title"),
                "domain": off.get("STEM_Domain"),
                "participants_this_station": per_station,
                "resource_readiness": feas["status"],
            })
        option_b = {
            "label": "Proposed Enhancement",
            "name": "Station Rotation Model (Multi-Station Rotation)",
            "description": f"Set up {n_stations} concurrent stations, ~{per_station} participants/station, each "
                            f"station using a verified offering from a different domain so that {int(req_p)} "
                            f"participants can be served in ONE time slot (rather than repeatedly).",
            "stations": station_plan,
        }

    return {"option_a": option_a, "option_b": option_b}
