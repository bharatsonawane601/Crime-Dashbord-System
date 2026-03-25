"""
═══════════════════════════════════════════════════════════════
  Excel ↔ SQLite Synchronization Module
  Zone 1 Crime Intelligence System
═══════════════════════════════════════════════════════════════
"""
import os
import time
import threading

import pandas as pd

from database import get_db, clear_all, get_all_records, get_record_count

# Track sync state to prevent watcher loops
_is_syncing = False
_sync_lock = threading.Lock()


def get_is_syncing() -> bool:
    return _is_syncing


# ─── Load Excel into SQLite ──────────────────────────────────

def load_excel_into_db(excel_path: str) -> dict:
    global _is_syncing

    if not os.path.isfile(excel_path):
        print(f"  [Sync] Excel file not found: {excel_path}")
        return {"success": False, "error": "File not found"}

    with _sync_lock:
        _is_syncing = True

    try:
        df = pd.read_excel(excel_path, engine="openpyxl")

        # Normalize column names (case-insensitive matching)
        col_map = {}
        for col in df.columns:
            lower = col.strip().lower()
            if lower == "year":
                col_map[col] = "year"
            elif lower == "month":
                col_map[col] = "month"
            elif lower in ("police station",):
                col_map[col] = "police_station"
            elif lower in ("crime type",):
                col_map[col] = "crime_type"
            elif lower in ("under investigation",):
                col_map[col] = "under_investigation"
            elif lower == "closed":
                col_map[col] = "closed"

        df = df.rename(columns=col_map)

        # Fill NaN with defaults
        df["year"] = df["year"].fillna(0).astype(int)
        df["month"] = df["month"].fillna(0).astype(int)
        df["police_station"] = df["police_station"].fillna("")
        df["crime_type"] = df["crime_type"].fillna("")
        df["under_investigation"] = df["under_investigation"].fillna(0).astype(int)
        df["closed"] = df["closed"].fillna(0).astype(int)

        # Clear and bulk insert
        clear_all()
        conn = get_db()
        rows = df[["year", "month", "police_station", "crime_type", "under_investigation", "closed"]].values.tolist()
        conn.executemany(
            """INSERT INTO crime_records (year, month, police_station, crime_type, under_investigation, closed)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

        count = get_record_count()
        print(f"  [Sync] Loaded {count} records from Excel into SQLite")
        return {"success": True, "count": count}

    except Exception as e:
        print(f"  [Sync] Error loading Excel: {e}")
        return {"success": False, "error": str(e)}
    finally:
        with _sync_lock:
            _is_syncing = False


# ─── Write SQLite data back to Excel ─────────────────────────

def write_back_to_excel(excel_path: str) -> dict:
    global _is_syncing

    if _is_syncing:
        return {"success": False, "error": "Sync in progress"}

    with _sync_lock:
        _is_syncing = True

    try:
        records = get_all_records()
        data = [
            {
                "Year": r["year"],
                "Month": r["month"],
                "Police Station": r["police_station"],
                "Crime Type": r["crime_type"],
                "Under Investigation": r["under_investigation"],
                "Closed": r["closed"],
            }
            for r in records
        ]

        df = pd.DataFrame(data)
        df.to_excel(excel_path, index=False, engine="openpyxl", sheet_name="Crime Data")

        print(f"  [Sync] Wrote {len(records)} records back to Excel")
        return {"success": True, "count": len(records)}

    except Exception as e:
        print(f"  [Sync] Error writing to Excel: {e}")
        return {"success": False, "error": str(e)}
    finally:
        # Delay to let watchdog debounce
        def _reset():
            time.sleep(2)
            with _sync_lock:
                global _is_syncing
                _is_syncing = False

        threading.Thread(target=_reset, daemon=True).start()


# ─── Export to a fresh Excel file ────────────────────────────

def export_to_excel(output_path: str) -> dict:
    return write_back_to_excel(output_path)
