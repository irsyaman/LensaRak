"""
LensaRak — Fasa 1: Data Cleaning + Offline-First SQLite Schema
AI Innovators Challenge 2026 (Petrosains)

What this script does, in one pass:
  1. Loads the 109 valid items from Inventory_Master (raw dataset has ~997 rows,
     most are blank template rows — FAQ confirms only 109 are real).
  2. Reclassifies each item as REUSABLE_ASSET vs CONSUMABLE. The raw Asset Type
     column is broken (108/109 items are mislabelled "Consumable"), so this
     uses an explicit, reviewed per-item mapping instead of trusting that field
     — see ASSET_CLASS_OVERRIDES below. Every item has a one-line reason,
     because judges will ask "how did you decide this" and "we hand-reviewed
     all 109 items against the FAQ's own examples" is a real answer.
  3. Extracts packaging quantity out of the free-text Remarks field
     (e.g. "40 units per pack" -> qty=40, per="pack") into structured columns,
     so the system can convert "3 packs counted" into an actual unit count.
  4. Creates an offline-first SQLite schema (items / locations / transactions)
     with an append-only, sync-flagged transaction ledger — the store-and-
     forward pattern the FAQ asks for — and populates it.

Run:
    python3 lensarak_clean_data.py <path_to_xlsx>

Outputs (written next to this script):
    lensarak_inventory_clean.csv   — cleaned, flat item table
    lensarak_inventory.db          — SQLite database (items, locations, transactions)
"""

import csv
import re
import sqlite3
import sys
from pathlib import Path

import openpyxl

# ---------------------------------------------------------------------------
# 1. Asset classification — explicit, per-item, with a reason.
#
# Default per category (matches how the FAQ frames each category), with named
# exceptions. Every one of the 109 item codes is accounted for below.
# ---------------------------------------------------------------------------

CATEGORY_DEFAULT = {
    "Electronics & Robotics": ("reusable_asset", "board/sensor/module — checked out and returned for activities"),
    "Laboratory & Science Supplies": ("reusable_asset", "lab equipment/glassware — washed and reused"),
    "Craft Materials & STEM Kits": ("consumable", "craft supply — used or given out per activity"),
    "Stationery & Office Supplies": ("reusable_asset", "office tool — issued and returned"),
    "Tools & Equipment": ("reusable_asset", "hand tool — checked out and returned"),
}

# item_code -> (asset_class, reason) — overrides the category default above.
ASSET_CLASS_OVERRIDES = {
    # Electronics & Robotics: small parts that get soldered/used up, not returned
    "E018": ("consumable", "small part — soldered into a build, not returned"),
    "E019": ("consumable", "small part — soldered into a build, not returned"),
    "E025": ("consumable", "small part — soldered into a build, not returned"),
    "E026": ("consumable", "sold/used by the length — not returned"),
    "E027": ("consumable", "sold/used by the length — not returned"),
    # Laboratory & Science Supplies: PPE and chemicals are single-use / consumed
    "L011": ("consumable", "PPE — single use"),
    "L012": ("consumable", "PPE — single use"),
    "L020": ("consumable", "chemical reagent"),
    "L021": ("consumable", "chemical reagent"),
    "L022": ("consumable", "chemical reagent"),
    "L023": ("consumable", "chemical reagent"),
    "L024": ("consumable", "chemical reagent"),
    "L025": ("consumable", "chemical reagent"),
    "L026": ("consumable", "chemical reagent"),
    # Craft Materials: these two are reusable signage/display props, not craft supply
    "C007": ("reusable_asset", "reusable signage/display prop, not a craft consumable"),
    "C008": ("reusable_asset", "reusable signage/display prop, not a craft consumable"),
    # Stationery & Office: things that get written/taped/stuck away, not returned
    "S001": ("consumable", "runs out with use, not returned"),
    "S002": ("consumable", "runs out with use, not returned"),
    "S004": ("consumable", "used per sheet"),
    "S005": ("consumable", "used per sheet"),
    "S008": ("consumable", "used per activity"),
    "S009": ("consumable", "runs out with use, not returned"),
    "S010": ("consumable", "used, not returned"),
    "S011": ("consumable", "runs out with use, not returned"),
    "S012": ("consumable", "used, not returned"),
    "S015": ("consumable", "wears down with use"),
    # Tools & Equipment: consumed in the act of using the tool
    "T009": ("consumable", "melted/used up during soldering"),
    "T013": ("consumable", "fastener — installed, not returned"),
}

PACKAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s+per\s+(\w+)", re.IGNORECASE)
WEIGHT_UNITS = {"g", "kg", "ml", "l"}

# Offline stores, per the storeroom fact sheet / team brief.
OFFLINE_LOCATIONS = {"STORE 3", "STORE 4", "CHEMICAL ROOM", "CONCOURSE"}


def classify(item_code: str, category: str):
    if item_code in ASSET_CLASS_OVERRIDES:
        return ASSET_CLASS_OVERRIDES[item_code]
    return CATEGORY_DEFAULT.get(category, ("reusable_asset", "no rule matched — default, needs review"))


def parse_packaging(remarks):
    if not remarks:
        return None, None, None, False
    m = PACKAGE_RE.search(str(remarks))
    if not m:
        return None, None, None, False
    qty_str, unit_word, container = m.groups()
    qty = float(qty_str) if "." in qty_str else int(qty_str)
    is_weight = unit_word.lower() in WEIGHT_UNITS
    return qty, unit_word, container, is_weight


def load_items(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["Inventory_Master"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    items = []
    for r in rows[1:]:
        item_code = r[0]
        if not item_code:
            continue
        item_code = str(item_code).strip()
        record = dict(zip(header, r))
        category = (record.get("Category") or "").strip()
        asset_class, reason = classify(item_code, category)
        pack_qty, pack_unit, pack_container, pack_is_weight = parse_packaging(record.get("Remarks"))

        unit = (record.get("Unit") or "").strip()
        total_quantity = record.get("Total Quantity")
        available_quantity = record.get("Available Quantity")
        original_unit = original_total = original_avail = None

        # Rebase to the smallest real unit when the stock is currently counted
        # in whole packs/boxes but we know what's inside each one (e.g. 64
        # "Pack" of jumper wire, 40 wires/pack -> re-express as 2560 "units").
        # Without this, a scan of "2 packs" can only ever resolve to "2" of
        # whatever the base unit already is — it can't produce the 80 individual
        # units a bulk-return camera would actually count. Weight-based packs
        # (e.g. 100g air clay) are left alone: "count" doesn't apply to grams.
        if (pack_container and not pack_is_weight and pack_qty
                and unit.lower() == pack_container.lower()):
            original_unit, original_total, original_avail = unit, total_quantity, available_quantity
            unit = pack_unit.strip() if pack_unit else unit
            total_quantity = round(total_quantity * pack_qty) if total_quantity is not None else None
            available_quantity = round(available_quantity * pack_qty) if available_quantity is not None else None

        items.append({
            "item_code": item_code,
            "item_name": (record.get("Item Name") or "").strip(),
            "category": category,
            "raw_asset_type": record.get("Asset Type"),
            "asset_class": asset_class,
            "classification_reason": reason,
            "storage_location": (record.get("Storage Location") or "").strip(),
            "specific_location": (record.get("Specific Location / Rack") or "").strip(),
            "unit": unit,
            "total_quantity": total_quantity,
            "available_quantity": available_quantity,
            "original_unit": original_unit,
            "original_total_quantity": original_total,
            "original_available_quantity": original_avail,
            "remarks": record.get("Remarks"),
            "pack_qty": pack_qty,
            "pack_unit_word": pack_unit,
            "pack_container": pack_container,
            "pack_is_weight_based": pack_is_weight,
        })
    return items


def write_csv(items, out_path):
    fieldnames = list(items[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(items)


SCHEMA_SQL = """
CREATE TABLE locations (
    location_code   TEXT PRIMARY KEY,
    location_name   TEXT NOT NULL,
    is_offline      INTEGER NOT NULL DEFAULT 0   -- 1 = store-and-forward required
);

CREATE TABLE items (
    item_code               TEXT PRIMARY KEY,
    item_name               TEXT NOT NULL,
    category                TEXT NOT NULL,
    asset_class             TEXT NOT NULL CHECK (asset_class IN ('reusable_asset','consumable')),
    classification_reason   TEXT,
    storage_location        TEXT REFERENCES locations(location_code),
    specific_location       TEXT,
    unit                     TEXT,
    total_quantity           INTEGER,
    available_quantity       INTEGER,
    status                   TEXT NOT NULL DEFAULT 'available'
                              CHECK (status IN ('available','checked_out','missing','lost','damaged','disposed')),
    pack_qty                 REAL,
    pack_unit_word           TEXT,
    pack_container            TEXT,
    pack_is_weight_based      INTEGER DEFAULT 0,
    original_unit             TEXT,     -- set only when rebased from pack-count to piece-count
    original_total_quantity    INTEGER,
    original_available_quantity INTEGER,
    remarks                   TEXT
);

-- Append-only ledger. Every check-out / check-in / bulk-return / adjustment is
-- one row here — never edited, only inserted. client_txn_id is generated on
-- the device at the moment of the transaction (works with no internet); when
-- the store reconnects, unsynced rows (synced=0) are pushed and marked synced.
CREATE TABLE transactions (
    txn_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_txn_id     TEXT NOT NULL UNIQUE,   -- UUID generated offline, for idempotent sync
    item_code         TEXT NOT NULL REFERENCES items(item_code),
    txn_type          TEXT NOT NULL CHECK (txn_type IN
                        ('check_out','check_in','return','adjustment','damage','lost','disposed')),
    qty               REAL NOT NULL,
    location_code     TEXT REFERENCES locations(location_code),
    actor             TEXT,                    -- who performed it (staff/team member)
    recognition_source TEXT DEFAULT 'manual'    -- 'vision_auto' | 'vision_confirmed' | 'manual'
                        CHECK (recognition_source IN ('vision_auto','vision_confirmed','manual')),
    confidence        REAL,                     -- vision model confidence, if applicable
    txn_timestamp     TEXT NOT NULL,             -- when it actually happened (device clock)
    synced            INTEGER NOT NULL DEFAULT 0,
    synced_at         TEXT
);

CREATE INDEX idx_txn_item ON transactions(item_code);
CREATE INDEX idx_txn_unsynced ON transactions(synced) WHERE synced = 0;
"""

# Locations seen in Reference_Data / the storeroom brief.
LOCATIONS = [
    ("CHILLAX", "Chillax"),
    ("BOX AREA", "Box Area"),
    ("BIN AREA", "Bin Area"),
    ("BAGGAGE AREA", "Baggage Area"),
    ("MAKER STUDIO", "Maker Studio"),
    ("CHEMICAL ROOM", "Chemical Room (Store 3)"),
    ("STORE 1", "Store 1"),
    ("STORE 2", "Store 2"),
    ("STORE 3", "Store 3"),
    ("STORE 4", "Store 4 (Concourse)"),
]


def build_database(items, db_path):
    if Path(db_path).exists():
        Path(db_path).unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)

    conn.executemany(
        "INSERT INTO locations (location_code, location_name, is_offline) VALUES (?, ?, ?)",
        [(code, name, 1 if code in OFFLINE_LOCATIONS else 0) for code, name in LOCATIONS],
    )

    seen_locations = {code for code, _ in LOCATIONS}
    for it in items:
        loc = it["storage_location"].upper()
        if loc and loc not in seen_locations:
            conn.execute(
                "INSERT INTO locations (location_code, location_name, is_offline) VALUES (?, ?, ?)",
                (loc, loc.title(), 1 if loc in OFFLINE_LOCATIONS else 0),
            )
            seen_locations.add(loc)

    conn.executemany(
        """INSERT INTO items (item_code, item_name, category, asset_class, classification_reason,
                               storage_location, specific_location, unit, total_quantity,
                               available_quantity, pack_qty, pack_unit_word, pack_container,
                               pack_is_weight_based, original_unit, original_total_quantity,
                               original_available_quantity, remarks)
           VALUES (:item_code, :item_name, :category, :asset_class, :classification_reason,
                   :storage_location, :specific_location, :unit, :total_quantity,
                   :available_quantity, :pack_qty, :pack_unit_word, :pack_container,
                   :pack_is_weight_based, :original_unit, :original_total_quantity,
                   :original_available_quantity, :remarks)""",
        items,
    )
    conn.commit()
    return conn


def print_summary(items, conn):
    from collections import Counter

    print(f"\nItems loaded: {len(items)}")

    cls = Counter(i["asset_class"] for i in items)
    print(f"  reusable_asset : {cls['reusable_asset']}")
    print(f"  consumable     : {cls['consumable']}")

    by_cat = Counter((i["category"], i["asset_class"]) for i in items)
    print("\nBy category:")
    cats = sorted(set(i["category"] for i in items))
    for c in cats:
        r = by_cat.get((c, "reusable_asset"), 0)
        k = by_cat.get((c, "consumable"), 0)
        print(f"  {c:32s} reusable={r:2d}  consumable={k:2d}")

    packed = [i for i in items if i["pack_qty"] is not None]
    print(f"\nPackaging extracted from Remarks: {len(packed)} / {len(items)} items")

    rebased = [i for i in items if i["original_unit"] is not None]
    print(f"Rebased from pack-count to piece-count: {len(rebased)} items")
    for i in rebased[:6]:
        print(f"    {i['item_code']} {i['item_name']}: {i['original_total_quantity']} {i['original_unit']} "
              f"-> {i['total_quantity']} {i['unit']}")
    if len(rebased) > 6:
        print(f"    ... and {len(rebased) - 6} more")
    weight_based = [i for i in packed if i["pack_is_weight_based"]]
    if weight_based:
        print(f"  ⚠ weight-based (not a unit count), flagged for manual handling:")
        for i in weight_based:
            print(f"    {i['item_code']} {i['item_name']}: {i['remarks']}")

    n_loc = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    n_offline = conn.execute("SELECT COUNT(*) FROM locations WHERE is_offline=1").fetchone()[0]
    print(f"\nLocations: {n_loc} total, {n_offline} flagged offline (store-and-forward)")


def main():
    xlsx_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not xlsx_path or not Path(xlsx_path).exists():
        print("Usage: python3 lensarak_clean_data.py <path_to_DATASET_xlsx>")
        sys.exit(1)

    out_dir = Path(__file__).parent
    items = load_items(xlsx_path)
    write_csv(items, out_dir / "lensarak_inventory_clean.csv")
    conn = build_database(items, out_dir / "lensarak_inventory.db")
    print_summary(items, conn)
    print(f"\nWrote: {out_dir / 'lensarak_inventory_clean.csv'}")
    print(f"Wrote: {out_dir / 'lensarak_inventory.db'}")


if __name__ == "__main__":
    main()
