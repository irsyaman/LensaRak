#!/usr/bin/env python3
"""
app.py -- LensaRak entry point: login gate + persistent left sidebar,
dispatching to the render(conn) function in each screens/*.py module.

Run with:
    streamlit run app.py

First run creates a default Admin account (username: admin,
password: lensarak123) -- change the password immediately in Settings.
"""

from __future__ import annotations

import streamlit as st

from engine import auth, database
from screens import _common, availability, calendar_page, catalogue, home, inventory_table, programme, recognition, reports, settings, transactions

st.set_page_config(page_title="LensaRak", page_icon="🧭", layout="wide")

PAGE_RENDERERS = {
    "Home": home.render,
    "Plan Programme": programme.render,
    "Equipment Availability": availability.render,
    "Inventory Recognition": recognition.render,
    "Reservations & Calendar": calendar_page.render,
    "Inventory": inventory_table.render,
    "Transactions": transactions.render,
    "Programme Catalogue": catalogue.render,
    "Reports / Audit Trail": reports.render,
    "Settings": settings.render,
}

PAGE_ICONS = {
    "Home": "🏠", "Plan Programme": "📋", "Equipment Availability": "🔎",
    "Inventory Recognition": "📷", "Reservations & Calendar": "📅", "Inventory": "📦",
    "Transactions": "🧾", "Programme Catalogue": "📚", "Reports / Audit Trail": "📊", "Settings": "⚙️",
}


@st.cache_resource
def _get_conn():
    conn = database.get_conn()
    database.ensure_app_schema(conn)
    return conn


def _login_screen(conn):
    st.title("🧭 LensaRak")
    st.caption("AI Programme & Inventory Operations Platform -- log in to continue.")

    just_created = auth.ensure_default_admin(conn)
    if just_created:
        st.warning("Default Admin account created: **admin** / **lensarak123** -- change this password "
                   "immediately after your first login (Settings > Profile).", icon="🔑")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary", width="stretch")

    if submitted:
        user = auth.authenticate(conn, username, password)
        if user is None:
            st.error("Incorrect username or password.")
        else:
            st.session_state["authenticated"] = True
            st.session_state["user_id"] = user["user_id"]
            st.session_state["username"] = user["username"]
            st.session_state["display_name"] = user["display_name"] or user["username"]
            st.session_state["role"] = user["role"]
            st.session_state["nav"] = "Home"
            st.rerun()


def _sidebar():
    with st.sidebar:
        st.title("🧭 LensaRak")
        st.caption(_common.current_user_label())
        st.divider()
        for route in _common.ROUTES:
            icon = PAGE_ICONS.get(route, "")
            is_active = st.session_state.get("nav") == route
            if st.button(f"{icon}  {route}", key=f"nav_{route}", width="stretch",
                         type="primary" if is_active else "secondary"):
                st.session_state["nav"] = route
                st.rerun()
        st.divider()
        if st.button("🚪 Log out", width="stretch", key="sidebar_logout"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()


def main():
    conn = _get_conn()
    st.session_state.setdefault("authenticated", False)

    if not st.session_state["authenticated"]:
        _login_screen(conn)
        return

    st.session_state.setdefault("nav", "Home")
    _sidebar()

    renderer = PAGE_RENDERERS.get(st.session_state["nav"], home.render)
    renderer(conn)


main()
