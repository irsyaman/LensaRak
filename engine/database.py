"""
database.py -- Single shared SQLite connection + schema for the whole
LensaRak app (Task 1's real inventory/transactions tables, plus the new
tables this enhancement adds: users, events, reservations,
programme_sdg_mapping, sync_queue).

Deliberately ONE database file (lensarak_inventory.db) rather than a
separate lensarak.db -- the items/transactions tables already live there
and are the real, tested, currently-in-use inventory ledger; splitting it
across two files would just create a second sync problem on top of the one
that already exists (store <-> office). ensure_app_schema() only ever adds
tables/columns, never drops or rewrites existing inventory data.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import lensarak_ops as ops  # the same root-level module lensarak_vision.py uses -- one copy, no drift

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# LensaRak_Final keeps lensarak_inventory.db at the project root (same file
# lensarak_vision.py/lensarak_ops.py already read/write) -- not a subfolder.
DEFAULT_DB_PATH = PROJECT_ROOT / "lensarak_inventory.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_app_schema(conn: sqlite3.Connection) -> None:
    """Safe to call every time (CREATE TABLE IF NOT EXISTS / additive ALTERs
    only). Extends Task 1's existing items/transactions schema (via the
    root-level lensarak_ops.ensure_schema) and adds this enhancement's own
    tables."""
    # Task 1's own migration (requires_review/reviewed/review_reason columns,
    # widened recognition_source CHECK) -- unchanged, just re-invoked here.
    ops.ensure_schema(conn)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT NOT NULL UNIQUE,
            password_hash   TEXT NOT NULL,
            password_salt   TEXT NOT NULL,
            role            TEXT NOT NULL CHECK (role IN ('Admin','Store Manager','Planner','Viewer')),
            display_name    TEXT,
            created_at      TEXT NOT NULL,
            last_login      TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title               TEXT NOT NULL,
            offering_id         TEXT,
            organiser_user_id   INTEGER REFERENCES users(user_id),
            start_datetime      TEXT NOT NULL,
            end_datetime        TEXT NOT NULL,
            venue               TEXT,
            participants        INTEGER,
            status              TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed','cancelled')),
            notes               TEXT,
            created_at          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reservations (
            reservation_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id            INTEGER NOT NULL REFERENCES events(event_id),
            item_code           TEXT NOT NULL REFERENCES items(item_code),
            quantity_reserved   REAL NOT NULL,
            start_datetime      TEXT NOT NULL,
            end_datetime        TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','released','cancelled')),
            created_by          INTEGER REFERENCES users(user_id),
            created_at          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS programme_sdg_mapping (
            mapping_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            offering_id          TEXT NOT NULL,
            sdg_number           INTEGER NOT NULL,
            sdg_name             TEXT,
            relationship_level   TEXT,
            effectiveness_score  INTEGER CHECK (effectiveness_score BETWEEN 1 AND 5),
            rationale            TEXT,
            evidence_source      TEXT,
            verified_by          TEXT,
            last_reviewed        TEXT,
            verification_status  TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_queue (
            sync_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type   TEXT NOT NULL,
            entity_id     TEXT NOT NULL,
            payload       TEXT,
            created_at    TEXT NOT NULL,
            synced        INTEGER NOT NULL DEFAULT 0,
            synced_at     TEXT
        );
        """
    )
    # Additive column for installs where programme_sdg_mapping already existed
    # before verification_status was added (CREATE TABLE IF NOT EXISTS above
    # only applies to a brand-new table, not an existing one).
    try:
        conn.execute("ALTER TABLE programme_sdg_mapping ADD COLUMN verification_status TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists

    # An early version of this table had a too-strict CHECK on
    # relationship_level (didn't allow "Very Strong", which score_label()
    # itself uses for a 5/5 rating) -- a real curated mapping file can
    # legitimately use that label, so relax it. Only do this while the table
    # is still empty, so a real curator's already-imported rows are never
    # touched/dropped.
    row = conn.execute("SELECT COUNT(*) AS n FROM programme_sdg_mapping").fetchone()
    if row and row["n"] == 0:
        info = conn.execute("PRAGMA table_info(programme_sdg_mapping)").fetchall()
        has_old_check = any(
            c["name"] == "relationship_level" for c in info
        ) and "relationship_level   TEXT CHECK" in (
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='programme_sdg_mapping'"
            ).fetchone()["sql"] or ""
        )
        if has_old_check:
            conn.execute("DROP TABLE programme_sdg_mapping")
            conn.execute(
                """
                CREATE TABLE programme_sdg_mapping (
                    mapping_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    offering_id          TEXT NOT NULL,
                    sdg_number           INTEGER NOT NULL,
                    sdg_name             TEXT,
                    relationship_level   TEXT,
                    effectiveness_score  INTEGER CHECK (effectiveness_score BETWEEN 1 AND 5),
                    rationale            TEXT,
                    evidence_source      TEXT,
                    verified_by          TEXT,
                    last_reviewed        TEXT,
                    verification_status  TEXT
                )
                """
            )
    conn.commit()


def queue_for_sync(conn: sqlite3.Connection, entity_type: str, entity_id: str, payload: dict) -> None:
    """Logs a locally-made change so an operator can see (Reports page) how
    many actions are waiting to be synced upstream once this device is back
    online. No actual remote sync target is implemented -- this is the
    bookkeeping half of the offline-first design, matching the existing
    manual USB/WhatsApp file-transfer sync process."""
    conn.execute(
        "INSERT INTO sync_queue (entity_type, entity_id, payload, created_at, synced) VALUES (?, ?, ?, ?, 0)",
        (entity_type, str(entity_id), json.dumps(payload, ensure_ascii=False, default=str), now_iso()),
    )
    conn.commit()


def pending_sync_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM sync_queue WHERE synced = 0").fetchone()
    return row["n"] if row else 0


def db_freshness(db_path: Path = DEFAULT_DB_PATH) -> str:
    if not db_path.exists():
        return f"NO FILE -- {db_path.name} not found"
    ts = datetime.fromtimestamp(db_path.stat().st_mtime)
    return ts.strftime("%d %b %Y, %I:%M %p")


def load_inventory(conn: sqlite3.Connection) -> list[dict]:
    """Every item, in the shape engine.constraints/matcher expect."""
    rows = conn.execute(
        "SELECT item_code, item_name, category, unit, total_quantity, "
        "available_quantity, status FROM items"
    ).fetchall()
    return [dict(r) for r in rows]


def load_locations(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute("SELECT * FROM locations").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
