"""
_common.py -- Shared UI building blocks so every page in the app looks and
behaves the same way (spec section 11: consistent status badges, page
title + one-line description on every page, empty-state guidance).
"""

from __future__ import annotations

import streamlit as st

ROUTES = [
    "Home", "Plan Programme", "Equipment Availability", "Inventory Recognition",
    "Reservations & Calendar", "Inventory", "Transactions", "Programme Catalogue",
    "Reports / Audit Trail", "Settings",
]


def goto(route: str):
    st.session_state["nav"] = route
    st.rerun()


STATUS_BADGE = {
    "AVAILABLE": "🟢 AVAILABLE", "Ready": "🟢 Ready",
    "RESERVED": "🔵 RESERVED",
    "CHECKED OUT": "🟠 CHECKED OUT", "checked_out": "🟠 CHECKED OUT",
    "CONFLICT": "🔴 CONFLICT", "Unavailable": "🔴 Unavailable",
    "Limited": "🟡 Limited",
    "OFFLINE": "⚫ OFFLINE",
    "NEEDS INFO": "🟡 NEEDS INFO",
    "VERIFIED": "✅ VERIFIED",
    "Unknown": "⚪ Unknown",
}


def badge(status: str) -> str:
    return STATUS_BADGE.get(status, status)


def page_header(title: str, description: str = ""):
    st.title(title)
    if description:
        st.caption(description)
    st.divider()


def empty_state(message: str, icon: str = "ℹ️"):
    st.info(message, icon=icon)


def require_permission(action: str, message: str | None = None) -> bool:
    """Returns True if the current user can do `action`; otherwise shows a
    clear message and returns False so the caller can stop rendering the
    gated part of the page (never a silent no-op)."""
    from engine import auth
    role = st.session_state.get("role")
    allowed = auth.can(role, action)
    if not allowed:
        st.warning(message or f"Role '{role}' is not permitted to perform this action.", icon="🔒")
        return False
    return True


def current_user_label() -> str:
    name = st.session_state.get("display_name") or st.session_state.get("username") or "?"
    role = st.session_state.get("role") or ""
    return f"{name} ({role})"
