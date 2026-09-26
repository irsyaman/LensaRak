"""
verifier.py -- Anti-hallucination pass for AI-generated programme storylines.

Whatever an LLM proposes for "recommended_offerings" gets every fact
(title, duration, capacity, cost band, safety level, age) overwritten from
the real catalogue, and any offering_id that doesn't actually exist in the
catalogue is dropped and logged as a risk rather than silently shown. The
LLM never gets the final word on a fact that's already known.
"""

from __future__ import annotations


def verify_output(programme: dict, offerings_by_id: dict, resource_by_id: dict, flags_by_id: dict) -> dict:
    verified_recs = []
    risks = list(programme.get("risks") or [])
    for rec in programme.get("recommended_offerings") or []:
        oid = rec.get("offering_id")
        truth = offerings_by_id.get(oid)
        if not truth:
            risks.append(f"AI tried to recommend offering_id '{oid}' which does not exist in the catalogue -- discarded.")
            continue
        verified_recs.append({
            "offering_id": oid,
            "title": truth.get("Activity_Title"),
            "reason": rec.get("reason", ""),
            "duration_min": truth.get("Standard_Duration_Min"),
            "max_participants": truth.get("Max_Participants"),
            "cost_band": truth.get("Cost_Band"),
            "safety_level": truth.get("Safety_Level"),
            "recommended_age": truth.get("Recommended_Age"),
            "resource_readiness": resource_by_id.get(oid, {}).get("status", "Unknown"),
            "flags": flags_by_id.get(oid, []),
        })
    enhancements = []
    for e in programme.get("proposed_enhancements") or []:
        if isinstance(e, str):
            e = {"name": e, "description": ""}
        e["label"] = "Proposed Enhancement"
        enhancements.append(e)

    return {
        "programme": programme.get("programme", {}),
        "recommended_offerings": verified_recs,
        "participant_journey": programme.get("participant_journey") or [],
        "proposed_enhancements": enhancements,
        "risks": risks,
        "alternative_options": programme.get("alternative_options") or [],
    }
