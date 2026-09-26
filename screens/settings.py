"""
settings.py -- User profile, sync status, data uploads (offline->online
bridge -- see engine/database.py docstring), role permissions reference,
user management (Admin only), and logout.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from engine import auth, database, sdg
from screens._common import empty_state, page_header, require_permission


def render(conn):
    page_header("Settings", "Profile, sync status, and user administration.")

    tab_profile, tab_sync, tab_perms, tab_users = st.tabs(
        ["Profile", "Data & Sync", "Role Permissions", "User Management"])

    with tab_profile:
        st.write(f"**Username:** {st.session_state.get('username')}")
        st.write(f"**Display name:** {st.session_state.get('display_name')}")
        st.write(f"**Role:** {st.session_state.get('role')}")
        st.divider()
        st.markdown("**Change password**")
        new_pw = st.text_input("New password", type="password", key="settings_new_pw")
        if st.button("Update password", key="settings_pw_submit"):
            if len(new_pw) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                auth.change_password(conn, st.session_state["user_id"], new_pw)
                st.success("Password updated.")
        st.divider()
        if st.button("🚪 Log out", key="settings_logout"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

    with tab_sync:
        st.write(f"**Inventory data last updated:** {database.db_freshness()}")
        st.write(f"**Actions awaiting sync:** {database.pending_sync_count(conn)}")

        pending = conn.execute(
            "SELECT * FROM sync_queue WHERE synced = 0 ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        if pending:
            st.dataframe(
                [{"Time": p["created_at"], "Type": p["entity_type"], "Reference": p["entity_id"]} for p in pending],
                width="stretch", hide_index=True,
            )
        else:
            empty_state("No actions awaiting sync.", icon="✅")

        st.divider()
        st.markdown("**Upgrade inventory database** (brought from an offline store)")
        st.caption("Use this when you bring the latest `lensarak_inventory.db` file from the store via "
                   "USB/WhatsApp -- upload it directly here without touching folders/terminal.")
        if require_permission("edit_inventory_master", "Only Admin/Store Manager can upgrade the database."):
            db_file = st.file_uploader("File lensarak_inventory.db", type=["db"], key="settings_db_upload")
            if db_file and st.button("Replace database", key="settings_db_replace"):
                database.DEFAULT_DB_PATH.write_bytes(db_file.getvalue())
                st.success("Database replaced. Please refresh the page to use the latest data.")

        st.divider()
        st.markdown("**Import SDG Mapping** (human-curated file only -- not AI-generated)")
        sdg.write_template()
        with open(sdg.DEFAULT_SDG_XLSX_PATH, "rb") as f:
            st.download_button("⬇️ Download SDG mapping template (blank)", f,
                                file_name="programme_sdg_mapping_template.xlsx", key="sdg_template_dl")
        if require_permission("edit_inventory_master", "Only Admin/Store Manager can import SDG mapping."):
            sdg_file = st.file_uploader("File programme_sdg_mapping.xlsx (filled in)", type=["xlsx"], key="settings_sdg_upload")
            if sdg_file and st.button("Import SDG mapping", key="settings_sdg_import"):
                sdg.DEFAULT_SDG_XLSX_PATH.write_bytes(sdg_file.getvalue())
                n = sdg.import_sdg_mapping_from_xlsx(conn)
                rows = conn.execute("SELECT * FROM programme_sdg_mapping").fetchall()
                unreviewed = sum(1 for row in rows if not sdg.is_reviewed(dict(row)))
                if unreviewed:
                    st.warning(f"{n} row(s) imported -- {unreviewed} of them are still marked as a draft/pending "
                               f"organiser review (not yet human-verified). They'll show a ⚠️ flag wherever SDG "
                               f"alignment is displayed until Verified_By/Verification_Status is updated in the file.")
                else:
                    st.success(f"{n} SDG mapping row(s) imported, all marked as reviewed.")

    with tab_perms:
        st.markdown("Permissions reference by role (Yes / Limited / No):")
        rows = []
        for action, roles in auth.PERMISSIONS.items():
            rows.append({"Action": action, **{r: ("Yes" if v is True else ("Limited" if v == "limited" else "No"))
                                                  for r, v in roles.items()}})
        st.dataframe(rows, width="stretch", hide_index=True)

    with tab_users:
        if not require_permission("manage_users", "Only Admin can manage users."):
            pass
        else:
            users = auth.list_users(conn)
            st.dataframe(
                [{"Username": u["username"], "Name": u["display_name"], "Role": u["role"],
                  "Last login": u["last_login"] or "-"} for u in users],
                width="stretch", hide_index=True,
            )
            st.markdown("**Add new user**")
            c1, c2, c3, c4 = st.columns(4)
            new_user = c1.text_input("Username", key="newuser_username")
            new_pass = c2.text_input("Password", type="password", key="newuser_password")
            new_role = c3.selectbox("Role", auth.ROLES, key="newuser_role")
            new_name = c4.text_input("Display name", key="newuser_display")
            if st.button("Create user", key="newuser_submit"):
                try:
                    auth.create_user(conn, new_user, new_pass, new_role, new_name or new_user)
                    st.success(f"User '{new_user}' created.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
