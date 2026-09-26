"""
scheduler.py -- Turns a chosen offering + participant count + a booking
window into a SUGGESTED resource quantity list for reservations.py to check
against real inventory. The suggestion is never auto-committed -- the
booking form always shows it to a human to confirm/adjust before reserving,
because the organiser catalogue's per-activity bill-of-materials sheets use
inconsistent quantity formats (some give qty per set, some per 40 sets, some
none at all -- see engine/loader.py's ACT-XXX parsing notes), so there's no
reliable machine-computed exact quantity per participant to trust blindly.

Deliberately separate from engine/constraints.py's handle_capacity_overflow():
that function decides *whether to recommend* a rotation/multi-station plan
during the Programme Consultant's scoring stage; this module runs *after* a
specific offering has been picked and a real date/time is being booked.
"""

from __future__ import annotations

import math

from .constraints import match_offering_inventory
from .loader import parse_number


def compute_rotations(participant_count, max_participants) -> int:
    p = parse_number(participant_count)
    m = parse_number(max_participants)
    if not p or not m or m <= 0:
        return 1
    return max(1, math.ceil(p / m))


def suggested_quantities_for_booking(offering_id: str, activity_items: dict, inventory: list[dict],
                                      participants_this_slot: float | None) -> dict[str, int]:
    """Returns {item_code: suggested_quantity} for ONE time slot of this
    offering (one rotation, or one station). Suggestion = 1 unit per
    participant in this slot for every matched bill-of-materials item,
    minimum 1 -- a reasonable starting default for hands-on kits, always
    shown to the operator to adjust before the reservation is confirmed."""
    matched = match_offering_inventory(offering_id, activity_items, inventory)
    qty = max(1, int(math.ceil(participants_this_slot))) if participants_this_slot else 1
    return {it["item_code"]: qty for it in matched}
