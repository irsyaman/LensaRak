"""
matcher.py -- Weighted scoring (0-100, transparent breakdown) and the single
entry point app.py calls: recommend_programmes().

Scoring weights (out of 100): Theme 25, Objective 20, Audience 15, Format
10, Duration 10, Budget 10, Resource readiness 10.
"""

from __future__ import annotations

from .constraints import COST_RANK, apply_hard_constraints, check_inventory_feasibility
from .loader import parse_min_age, parse_number, token_overlap_ratio


def score_offering(request: dict, offering: dict, theme_mapping: list[dict], resource_status: str) -> dict:
    theme_score = token_overlap_ratio(request.get("theme"), offering.get("Suitable_Themes")) * 25
    for m in theme_mapping:
        if offering.get("Offering_ID") in (m.get("Primary_Offering_ID"), m.get("Secondary_Offering_ID")):
            bonus = token_overlap_ratio(request.get("theme"), m.get("Theme")) * 25
            theme_score = max(theme_score, bonus)

    objective_score = token_overlap_ratio(request.get("objective"), offering.get("Suitable_Objectives")) * 20
    for m in theme_mapping:
        if offering.get("Offering_ID") in (m.get("Primary_Offering_ID"), m.get("Secondary_Offering_ID")):
            bonus = token_overlap_ratio(request.get("objective"), m.get("Stakeholder_Objective")) * 20
            objective_score = max(objective_score, bonus)

    audience = str(request.get("audience") or "").strip().lower()
    audience_types = str(offering.get("Audience_Types") or "").lower()
    if audience and audience in audience_types:
        audience_score = 15
    elif audience and token_overlap_ratio(audience, audience_types) > 0:
        audience_score = 8
    else:
        audience_score = 5  # neutral -- not stated / not conclusive

    preferred_format = str(request.get("preferred_format") or "").strip().lower()
    offering_format = f"{offering.get('Offering_Type', '')} {offering.get('Delivery_Mode', '')}".lower()
    if preferred_format and preferred_format in offering_format:
        format_score = 10
    elif preferred_format:
        format_score = 3
    else:
        format_score = 5

    req_dur = parse_number(request.get("duration_min"))
    std_dur = parse_number(offering.get("Standard_Duration_Min"))
    if req_dur and std_dur:
        diff_ratio = abs(req_dur - std_dur) / std_dur
        duration_score = max(0.0, 10 * (1 - diff_ratio))
    else:
        duration_score = 5

    req_budget_rank = COST_RANK.get(str(request.get("budget") or "").strip().lower())
    off_cost_rank = COST_RANK.get(str(offering.get("Cost_Band") or "").strip().lower())
    if req_budget_rank and off_cost_rank:
        budget_score = 10 if req_budget_rank == off_cost_rank else (5 if abs(req_budget_rank - off_cost_rank) == 1 else 0)
    else:
        budget_score = 5

    resource_score = {"Ready": 10, "Limited": 5, "Unavailable": 0, "Unknown": 5}.get(resource_status, 5)

    # Don't let an age-gated, high-safety activity confidently rank as THE
    # top answer when participant age hasn't actually been confirmed yet --
    # it still appears (nothing is hidden), just not with false confidence
    # ahead of safer, less age-critical options.
    age_penalty = 0.0
    min_age = parse_min_age(offering.get("Recommended_Age"))
    if request.get("age") is None and min_age is not None and str(offering.get("Safety_Level", "")).strip().lower() == "high":
        age_penalty = -15.0

    breakdown = {
        "theme_alignment": round(theme_score, 1),
        "objective_alignment": round(objective_score, 1),
        "audience_alignment": round(audience_score, 1),
        "format_fit": round(format_score, 1),
        "duration_fit": round(duration_score, 1),
        "budget_fit": round(budget_score, 1),
        "resource_readiness": round(resource_score, 1),
    }
    if age_penalty:
        breakdown["age_unconfirmed_penalty"] = age_penalty
    total = round(sum(breakdown.values()), 1)
    return {"total": total, "breakdown": breakdown}


def recommend_programmes(request: dict, offerings: list[dict], theme_mapping: list[dict],
                          activity_items: dict, inventory: list[dict]):
    """Runs hard constraints + live inventory checks + scoring together.

    Returns (scored, excluded, resource_by_id, flags_by_id):
      scored          -- [{"offering": ..., "score": {...}, "resource": {...}}, ...]
                          sorted best-first
      excluded        -- offerings dropped by a hard constraint, each with "_exclude_reasons"
      resource_by_id  -- Offering_ID -> resource-feasibility dict
      flags_by_id     -- Offering_ID -> list of soft-warning strings
    """
    feasible, excluded = apply_hard_constraints(request, offerings)

    scored = []
    for offering in feasible:
        resource = check_inventory_feasibility(
            offering.get("Offering_ID"), activity_items, inventory, request.get("participant_count"))
        score = score_offering(request, offering, theme_mapping, resource["status"])
        scored.append({"offering": offering, "score": score, "resource": resource})
    scored.sort(key=lambda c: c["score"]["total"], reverse=True)

    resource_by_id = {c["offering"].get("Offering_ID"): c["resource"] for c in scored}
    flags_by_id = {c["offering"].get("Offering_ID"): c["offering"].get("_flags", []) for c in scored}
    return scored, excluded, resource_by_id, flags_by_id
