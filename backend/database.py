from __future__ import annotations
"""
═══════════════════════════════════════════════════════════════
  Database Module — SQLite with WAL mode
  Zone 1 Crime Intelligence System
═══════════════════════════════════════════════════════════════
"""
import sqlite3
import os
import math
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DB_DIR, "crime_data.db")


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with WAL mode and row_factory."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ─── Shared connection for the app ──────────────────────────
_conn: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = get_connection()
    return _conn


def init_db():
    """Create tables and indexes if they don't exist."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS crime_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            police_station TEXT NOT NULL,
            crime_type TEXT NOT NULL,
            under_investigation INTEGER DEFAULT 0,
            closed INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_station ON crime_records(police_station);
        CREATE INDEX IF NOT EXISTS idx_year ON crime_records(year);
        CREATE INDEX IF NOT EXISTS idx_crime_type ON crime_records(crime_type);
    """)
    conn.commit()
    print("  [DB] SQLite database initialized")


# ─── CRUD Operations ─────────────────────────────────────────

def get_all_records() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM crime_records ORDER BY year DESC, month DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_records_paginated(page: int = 1, limit: int = 50, filters: dict | None = None) -> dict:
    filters = filters or {}
    conn = get_db()
    where_parts = []
    params = []

    if filters.get("year"):
        where_parts.append("year = ?")
        params.append(int(filters["year"]))
    if filters.get("month"):
        where_parts.append("month = ?")
        params.append(int(filters["month"]))
    if filters.get("station"):
        where_parts.append("police_station = ?")
        params.append(filters["station"])
    if filters.get("crimeType"):
        where_parts.append("crime_type = ?")
        params.append(filters["crimeType"])
    if filters.get("search"):
        where_parts.append("(police_station LIKE ? OR crime_type LIKE ?)")
        params.extend([f"%{filters['search']}%", f"%{filters['search']}%"])

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    offset = (page - 1) * limit

    rows = conn.execute(
        f"SELECT * FROM crime_records {where_clause} "
        f"ORDER BY year DESC, month DESC, id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    total = conn.execute(
        f"SELECT COUNT(*) as total FROM crime_records {where_clause}", params
    ).fetchone()["total"]

    return {
        "records": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": math.ceil(total / limit) if limit else 0,
    }


def get_record_by_id(record_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM crime_records WHERE id = ?", (record_id,)).fetchone()
    return dict(row) if row else None


def add_record(record: dict) -> dict:
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO crime_records (year, month, police_station, crime_type, under_investigation, closed)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            int(record.get("year", 0)),
            int(record.get("month", 0)),
            record.get("police_station") or record.get("policeStation", ""),
            record.get("crime_type") or record.get("crimeType", ""),
            int(record.get("under_investigation") or record.get("underInvestigation", 0)),
            int(record.get("closed", 0)),
        ),
    )
    conn.commit()
    return {"id": cur.lastrowid, **record}


def update_record(record_id: int, updates: dict) -> dict | None:
    conn = get_db()
    fields = []
    params = []

    field_map = {
        "year": "year",
        "month": "month",
        "police_station": "police_station",
        "crime_type": "crime_type",
        "under_investigation": "under_investigation",
        "closed": "closed",
    }
    for key, col in field_map.items():
        if key in updates:
            fields.append(f"{col} = ?")
            params.append(int(updates[key]) if key in ("year", "month", "under_investigation", "closed") else updates[key])

    if not fields:
        return get_record_by_id(record_id)

    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(record_id)
    conn.execute(f"UPDATE crime_records SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    return get_record_by_id(record_id)


def delete_record(record_id: int) -> int:
    conn = get_db()
    cur = conn.execute("DELETE FROM crime_records WHERE id = ?", (record_id,))
    conn.commit()
    return cur.rowcount


def clear_all():
    conn = get_db()
    conn.execute("DELETE FROM crime_records")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='crime_records'")
    conn.commit()


# ─── Summary & Filters (matches /api/data format) ────────────

def get_data_summary() -> dict:
    conn = get_db()
    rows = conn.execute(
        """SELECT year, month,
                  police_station AS policeStation,
                  crime_type AS crimeType,
                  under_investigation AS underInvestigation,
                  closed
           FROM crime_records ORDER BY year, month"""
    ).fetchall()

    records = [dict(r) for r in rows]
    total_inv = sum(r["underInvestigation"] for r in records)
    total_closed = sum(r["closed"] for r in records)
    total = total_inv + total_closed
    closure_rate = round((total_closed / total) * 100, 1) if total > 0 else 0

    years = sorted(set(r["year"] for r in records))
    months = sorted(set(r["month"] for r in records))
    stations = sorted(set(r["policeStation"] for r in records))
    crime_types = sorted(set(r["crimeType"] for r in records))

    return {
        "records": records,
        "summary": {
            "totalCrimes": len(records),
            "totalUnderInvestigation": total_inv,
            "totalClosed": total_closed,
            "closureRate": closure_rate,
        },
        "filters": {
            "years": years,
            "months": months,
            "stations": stations,
            "crimeTypes": crime_types,
        },
        "lastModified": datetime.utcnow().isoformat() + "Z",
    }


def get_record_count() -> int:
    conn = get_db()
    return conn.execute("SELECT COUNT(*) as count FROM crime_records").fetchone()["count"]
