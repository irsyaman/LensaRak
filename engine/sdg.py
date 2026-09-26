"""
sdg.py -- UN Sustainable Development Goal relevance for catalogue offerings.

CRITICAL RULE (per spec): the LLM never invents an SDG relationship or an
effectiveness score. This module only ever displays what a human curator
put in data/programme_sdg_mapping.xlsx (imported into the programme_sdg_mapping
SQLite table). If nothing has been curated yet for an offering, the UI must
say so plainly ("SDG mapping not yet verified") -- never guess, never fall
back to a plausible-looking default.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import openpyxl

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_SDG_XLSX_PATH = DATA_DIR / "programme_sdg_mapping.xlsx"

SDG_TEMPLATE_HEADERS = [
    "Offering_ID", "SDG_Number", "SDG_Name", "Relationship_Level",
    "Effectiveness_Score", "Rationale", "Evidence_Source", "Verified_By", "Last_Reviewed",
]

SCORE_LABELS = {
    5: ("Very Strong", "The SDG is central to the activity objective and learning outcomes."),
    4: ("Strong", "Direct and substantial contribution."),
    3: ("Moderate", "Relevant, but not the main learning outcome."),
    2: ("Supporting", "Indirect contribution or supporting context."),
    1: ("Minimal", "Weak or incidental connection."),
}


def write_template(path: Path = DEFAULT_SDG_XLSX_PATH) -> Path:
    """Creates an EMPTY template (header row only) if no file exists yet --
    never pre-fills rows, since only a human curator's real data belongs in
    this file."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "programme_sdg_mapping"
    ws.append(SDG_TEMPLATE_HEADERS)
    wb.save(path)
    return path


def import_sdg_mapping_from_xlsx(conn: sqlite3.Connection, xlsx_path: Path = DEFAULT_SDG_XLSX_PATH) -> int:
    """Replaces the programme_sdg_mapping table's contents with whatever is
    curated in the xlsx. Returns the number of rows imported (0 if the file
    doesn't exist or has no data rows -- not an error, just "nothing verified
    yet")."""
    if not xlsx_path.exists():
        return 0
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb.active
    rows = [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
    if len(rows) < 2:
        return 0
    header = [str(h).strip() if h else "" for h in rows[0]]

    conn.execute("DELETE FROM programme_sdg_mapping")
    n = 0
    for r in rows[1:]:
        d = dict(zip(header, r))
        if not d.get("Offering_ID") or not d.get("SDG_Number"):
            continue
        conn.execute(
            "INSERT INTO programme_sdg_mapping "
            "(offering_id, sdg_number, sdg_name, relationship_level, effectiveness_score, "
            " rationale, evidence_source, verified_by, last_reviewed, verification_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(d.get("Offering_ID")), int(d.get("SDG_Number")), d.get("SDG_Name"),
                d.get("Relationship_Level"), d.get("Effectiveness_Score"), d.get("Rationale"),
                d.get("Evidence_Source"), d.get("Verified_By"), str(d.get("Last_Reviewed") or ""),
                d.get("Verification_Status"),
            ),
        )
        n += 1
    conn.commit()
    return n


def is_reviewed(row: dict) -> bool:
    """True only when a real organiser/human has signed off this row.
    Anything else (blank, 'Draft - review required', an AI/assistant name in
    Verified_By, etc.) is treated as NOT yet reviewed -- the UI must disclose
    that, never present a draft mapping as settled fact."""
    status = str(row.get("verification_status") or "").strip().lower()
    verifier = str(row.get("verified_by") or "").strip().lower()
    if status and ("draft" in status or "review" in status or "pending" in status):
        return False
    if "ai" in verifier or "assist" in verifier or "draft" in verifier:
        return False
    return bool(status or verifier)


def get_sdg_for_offering(conn: sqlite3.Connection, offering_id: str) -> list[dict]:
    """Returns [] when nothing has been curated for this offering yet --
    callers must render that as "not yet verified", never synthesize a value."""
    rows = conn.execute(
        "SELECT * FROM programme_sdg_mapping WHERE offering_id = ? ORDER BY effectiveness_score DESC",
        (offering_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def score_label(score) -> tuple[str, str]:
    try:
        return SCORE_LABELS.get(int(score), ("Unverified", ""))
    except (TypeError, ValueError):
        return ("Unverified", "")
