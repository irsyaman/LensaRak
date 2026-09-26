"""
reservations.py -- Internal event calendar + resource booking, with conflict
detection so two events can never silently double-book the same equipment.

Time handling: every start/end is stored as an ISO-8601 string
("YYYY-MM-DDTHH:MM"), always UTC-naive local time (the store/office's own
clock) -- fine for a single-site tool where every device already agrees on
local time, and keeps every comparison a plain string/datetime compare with
no timezone-conversion edge cases to get wrong under time pressure.

"available for this window" is intentionally conservative: it's the lower of
(a) total_quantity minus whatever's already reserved by OTHER active
reservations that overlap the requested window, and (b) the item's current
available_quantity right now (so a booking can't promise stock that's
already checked out and simply hasn't been returned yet). Either number can
be the binding constraint depending on how far in the future the booking is.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import database

DATETIME_FMT = "%Y-%m-%dT%H:%M"


def _parse(dt_str: str) -> datetime:
    return datetime.strptime(dt_str[:16], DATETIME_FMT)


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return _parse(a_start) < _parse(b_end) and _parse(a_end) > _parse(b_start)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def create_event(conn: sqlite3.Connection, title: str, offering_id: str | None,
                  organiser_user_id: int | None, start_datetime: str, end_datetime: str,
                  venue: str | None, participants: int | None, notes: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO events (title, offering_id, organiser_user_id, start_datetime, end_datetime, "
        "venue, participants, status, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)",
        (title, offering_id, organiser_user_id, start_datetime, end_datetime, venue, participants,
         notes, database.now_iso()),
    )
    conn.commit()
    event_id = cur.lastrowid
    database.queue_for_sync(conn, "event", event_id, {
        "title": title, "offering_id": offering_id, "start_datetime": start_datetime,
        "end_datetime": end_datetime, "venue": venue, "participants": participants,
    })
    return event_id


def list_events(conn: sqlite3.Connection, from_dt: str | None = None, status: str | None = "confirmed") -> list[dict]:
    q = "SELECT * FROM events WHERE 1=1"
    params: list = []
    if from_dt:
        q += " AND end_datetime >= ?"
        params.append(from_dt)
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " ORDER BY start_datetime ASC"
    return [dict(r) for r in conn.execute(q, params).fetchall()]


def cancel_event(conn: sqlite3.Connection, event_id: int) -> None:
    conn.execute("UPDATE events SET status = 'cancelled' WHERE event_id = ?", (event_id,))
    conn.execute("UPDATE reservations SET status = 'released' WHERE event_id = ? AND status = 'active'",
                 (event_id,))
    conn.commit()
    database.queue_for_sync(conn, "event_cancel", event_id, {"event_id": event_id})


# ---------------------------------------------------------------------------
# Reservations / conflict detection
# ---------------------------------------------------------------------------

def _active_reservations_for_item(conn: sqlite3.Connection, item_code: str,
                                   exclude_reservation_id: int | None = None) -> list[dict]:
    q = "SELECT * FROM reservations WHERE item_code = ? AND status = 'active'"
    params: list = [item_code]
    if exclude_reservation_id:
        q += " AND reservation_id != ?"
        params.append(exclude_reservation_id)
    return [dict(r) for r in conn.execute(q, params).fetchall()]


def reserved_quantity_in_window(conn: sqlite3.Connection, item_code: str, start_dt: str, end_dt: str,
                                 exclude_reservation_id: int | None = None) -> float:
    total = 0.0
    for r in _active_reservations_for_item(conn, item_code, exclude_reservation_id):
        if _overlaps(start_dt, end_dt, r["start_datetime"], r["end_datetime"]):
            total += r["quantity_reserved"]
    return total


def available_for_window(conn: sqlite3.Connection, item_code: str, start_dt: str, end_dt: str) -> dict:
    """Returns {"available": n, "total_quantity": n, "current_physical_available": n,
    "already_reserved_in_window": n}."""
    item = conn.execute("SELECT * FROM items WHERE item_code = ?", (item_code,)).fetchone()
    if item is None:
        return {"available": 0, "total_quantity": 0, "current_physical_available": 0, "already_reserved_in_window": 0}
    already_reserved = reserved_quantity_in_window(conn, item_code, start_dt, end_dt)
    by_future_capacity = (item["total_quantity"] or 0) - already_reserved
    available = max(0.0, min(by_future_capacity, item["available_quantity"] or 0))
    return {
        "available": available,
        "total_quantity": item["total_quantity"] or 0,
        "current_physical_available": item["available_quantity"] or 0,
        "already_reserved_in_window": already_reserved,
    }


def next_available_at(conn: sqlite3.Connection, item_code: str, needed_qty: float, from_dt: str) -> str | None:
    """Heuristic, not a full scheduler: looks at active reservations for this
    item ending at/after from_dt, sorted earliest-first, and returns the
    first end_datetime at which enough is very likely free again. Good
    enough to point an organiser toward a workable next slot -- always
    re-check available_for_window() against the actual chosen time before
    confirming."""
    future = [r for r in _active_reservations_for_item(conn, item_code)
              if _parse(r["end_datetime"]) >= _parse(from_dt)]
    future.sort(key=lambda r: r["end_datetime"])
    for r in future:
        check = available_for_window(conn, item_code, r["end_datetime"], r["end_datetime"])
        if check["available"] >= needed_qty:
            return r["end_datetime"]
    return None


def check_booking_feasibility(conn: sqlite3.Connection, required_quantities: dict[str, float],
                               start_dt: str, end_dt: str) -> dict[str, dict]:
    """Dry-run check (no writes) -- for showing a conflict/availability
    summary in the booking form BEFORE the user confirms. Returns
    {item_code: {"status": "AVAILABLE"|"CONFLICT", "available": n, "requested": n,
    "next_available_at": str|None}}."""
    result = {}
    for item_code, qty in required_quantities.items():
        info = available_for_window(conn, item_code, start_dt, end_dt)
        ok = info["available"] >= qty
        result[item_code] = {
            "status": "AVAILABLE" if ok else "CONFLICT",
            "available": info["available"],
            "requested": qty,
            "next_available_at": None if ok else next_available_at(conn, item_code, qty, end_dt),
        }
    return result


def reserve_resources(conn: sqlite3.Connection, event_id: int, required_quantities: dict[str, float],
                       start_dt: str, end_dt: str, created_by: int | None) -> dict[str, dict]:
    """Actually writes reservation rows -- only call this after the operator
    has confirmed the feasibility check. Still re-checks each item at write
    time (in case something changed in between) and skips reserving any item
    that's gone infeasible, reporting it back as CONFLICT rather than
    silently over-booking."""
    outcome = {}
    for item_code, qty in required_quantities.items():
        info = available_for_window(conn, item_code, start_dt, end_dt)
        if info["available"] < qty:
            outcome[item_code] = {"status": "CONFLICT", "available": info["available"], "requested": qty}
            continue
        conn.execute(
            "INSERT INTO reservations (event_id, item_code, quantity_reserved, start_datetime, end_datetime, "
            "status, created_by, created_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
            (event_id, item_code, qty, start_dt, end_dt, created_by, database.now_iso()),
        )
        outcome[item_code] = {"status": "RESERVED", "available": info["available"], "requested": qty}
    conn.commit()
    database.queue_for_sync(conn, "reservation_batch", event_id, {"event_id": event_id, "items": outcome})
    return outcome


def find_next_fully_available_slot(conn: sqlite3.Connection, required_quantities: dict[str, float],
                                    duration_min: float, from_dt: str, max_days_ahead: int = 14) -> str | None:
    """When two events of the same type clash on the same day, this answers
    "when COULD this actually be booked instead?" -- unlike next_available_at
    (which looks at one item), this only returns a slot where EVERY required
    item is simultaneously free for the full duration. Heuristic, like
    next_available_at: checks each item's own next-free time plus the same
    time on the following days (up to max_days_ahead), and returns the
    earliest candidate that clears every item. Returns None if nothing in
    that window works -- the caller should say so plainly, not imply a slot
    that isn't actually confirmed."""
    if not required_quantities:
        return None
    candidates: set[str] = set()
    for item_code, qty in required_quantities.items():
        na = next_available_at(conn, item_code, qty, from_dt)
        if na:
            candidates.add(na)
    base = _parse(from_dt)
    for d in range(1, max_days_ahead + 1):
        candidates.add((base + timedelta(days=d)).strftime(DATETIME_FMT))
    for cand_start in sorted(candidates):
        if _parse(cand_start) <= base:
            continue
        cand_end = (_parse(cand_start) + timedelta(minutes=duration_min)).strftime(DATETIME_FMT)
        if all(available_for_window(conn, ic, cand_start, cand_end)["available"] >= q
               for ic, q in required_quantities.items()):
            return cand_start
    return None


def upcoming_conflicts(conn: sqlite3.Connection) -> list[dict]:
    """Scans all active reservations for any accidental double-booking (used
    by the Reports/alerts panel) -- should normally be empty, since
    reserve_resources() checks before writing, but this catches anything
    inserted another way (a manual DB edit, a future import) too."""
    rows = conn.execute(
        "SELECT r1.item_code, r1.reservation_id AS r1_id, r2.reservation_id AS r2_id, "
        "r1.start_datetime AS s1, r1.end_datetime AS e1, r2.start_datetime AS s2, r2.end_datetime AS e2, "
        "r1.quantity_reserved + r2.quantity_reserved AS combined_qty, i.total_quantity "
        "FROM reservations r1 JOIN reservations r2 "
        "  ON r1.item_code = r2.item_code AND r1.reservation_id < r2.reservation_id "
        "  AND r1.status = 'active' AND r2.status = 'active' "
        "JOIN items i ON i.item_code = r1.item_code"
    ).fetchall()
    conflicts = []
    for r in rows:
        if _overlaps(r["s1"], r["e1"], r["s2"], r["e2"]) and r["combined_qty"] > r["total_quantity"]:
            conflicts.append(dict(r))
    return conflicts
