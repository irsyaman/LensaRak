"""
auth.py -- Username/password authentication backed by SQLite, stdlib-only
password hashing (PBKDF2-HMAC-SHA256 + per-user random salt -- no bcrypt/
passlib dependency, since installing extra packages has already been a
recurring headache on some of the team's laptops this competition).

Roles: Admin, Store Manager, Planner (a.k.a. Facilitator), Viewer.
Permission matrix below matches the spec exactly -- "limited" (used only for
Planner + checkout/return) means allowed but flagged/logged more closely by
the caller, not a hard block.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone

ROLES = ["Admin", "Store Manager", "Planner", "Viewer"]

# action -> {role: True | False | "limited"}
PERMISSIONS = {
    "view_inventory":        {"Admin": True, "Store Manager": True,  "Planner": True,      "Viewer": True},
    "checkout_return":       {"Admin": True, "Store Manager": True,  "Planner": "limited",  "Viewer": False},
    "create_booking":        {"Admin": True, "Store Manager": True,  "Planner": True,       "Viewer": False},
    "edit_inventory_master": {"Admin": True, "Store Manager": True,  "Planner": False,      "Viewer": False},
    "manage_users":          {"Admin": True, "Store Manager": False, "Planner": False,      "Viewer": False},
}

PBKDF2_ITERATIONS = 200_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def can(role: str, action: str):
    """Returns True, False, or "limited" for (role, action)."""
    return PERMISSIONS.get(action, {}).get(role, False)


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    digest, _ = hash_password(password, password_salt)
    return secrets.compare_digest(digest, password_hash)


def user_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def ensure_default_admin(conn: sqlite3.Connection) -> bool:
    """First run only: if there are literally no users yet, create a single
    default Admin account so the app isn't locked out of itself. Returns True
    if it just created one (caller should show a one-time warning to change
    the password immediately)."""
    if user_count(conn) > 0:
        return False
    create_user(conn, "admin", "lensarak123", "Admin", display_name="Default Admin")
    return True


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str,
                 display_name: str | None = None) -> int:
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role}")
    existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        raise ValueError(f"Username '{username}' already exists")
    pwd_hash, salt = hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, password_salt, role, display_name, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, pwd_hash, salt, role, display_name or username, _now()),
    )
    conn.commit()
    return cur.lastrowid


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        return None
    if not verify_password(password, row["password_hash"], row["password_salt"]):
        return None
    conn.execute("UPDATE users SET last_login = ? WHERE user_id = ?", (_now(), row["user_id"]))
    conn.commit()
    return dict(row)


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT user_id, username, role, display_name, created_at, last_login FROM users ORDER BY username"
    ).fetchall()
    return [dict(r) for r in rows]


def set_role(conn: sqlite3.Connection, user_id: int, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role}")
    conn.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
    conn.commit()


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()


def change_password(conn: sqlite3.Connection, user_id: int, new_password: str) -> None:
    pwd_hash, salt = hash_password(new_password)
    conn.execute("UPDATE users SET password_hash = ?, password_salt = ? WHERE user_id = ?",
                 (pwd_hash, salt, user_id))
    conn.commit()
