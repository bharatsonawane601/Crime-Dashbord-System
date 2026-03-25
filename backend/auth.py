from __future__ import annotations
"""
═══════════════════════════════════════════════════════════════
  Authentication Module — Users, Passwords, JWT
  Zone 1 Crime Intelligence System
═══════════════════════════════════════════════════════════════
"""
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import jwt
from passlib.hash import bcrypt

# ─── JWT Secret Management ──────────────────────────────────
# Fresh secret every server start → invalidates old sessions
JWT_SECRET = secrets.token_hex(64)
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 8
print("  [Auth] New session key generated (old sessions invalidated)")


# ─── Database Reference ─────────────────────────────────────
_conn: sqlite3.Connection | None = None


def init_auth_db(conn: sqlite3.Connection):
    """Initialize auth tables using the shared DB connection."""
    global _conn
    _conn = conn

    # Check if users table exists
    table_info = conn.execute("PRAGMA table_info(users)").fetchall()

    if len(table_info) > 0:
        # Table exists — check if admin role is supported
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES ('__test__', '__test__', 'admin')"
            )
            conn.execute("DELETE FROM users WHERE username = '__test__'")
            conn.commit()
        except sqlite3.IntegrityError:
            # Need to migrate table to support admin role
            print("  [Auth] Migrating users table to support admin role...")
            existing = conn.execute("SELECT * FROM users").fetchall()
            conn.execute("DROP TABLE users")
            _create_users_table(conn)
            for u in existing:
                role = "admin" if u["username"] == "admin1" else u["role"]
                conn.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                    (u["username"], u["password_hash"], role),
                )
            conn.commit()
            print("  [Auth] Migration complete")
            return
    else:
        _create_users_table(conn)

    # Seed default users if table is empty
    count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if count == 0:
        _seed_default_users(conn)

    print("  [Auth] Authentication database initialized")


def _create_users_table(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('viewer', 'editor', 'admin')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)


def _seed_default_users(conn: sqlite3.Connection):
    users = [
        ("admin1", bcrypt.hash("admin1@123"), "admin"),
        ("admin2", bcrypt.hash("admin2@123"), "editor"),
    ]
    conn.executemany(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", users
    )
    conn.commit()
    print("  [Auth] Seeded default users: admin1 (admin), admin2 (editor)")


# ─── Authentication ─────────────────────────────────────────

def authenticate_user(username: str, password: str) -> dict | None:
    row = _conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        return None
    if not bcrypt.verify(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def get_user_by_username(username: str) -> dict | None:
    row = _conn.execute(
        "SELECT id, username, role, created_at FROM users WHERE username = ?", (username,)
    ).fetchone()
    return dict(row) if row else None


# ─── User Management (Admin Only) ───────────────────────────

def get_all_users() -> list[dict]:
    rows = _conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def create_user(username: str, password: str, role: str) -> dict:
    if not username or not password or not role:
        raise ValueError("Username, password, and role are required")
    if role not in ("viewer", "editor"):
        raise ValueError("Role must be viewer or editor")
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")

    existing = _conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        raise ValueError("Username already exists")

    hashed = bcrypt.hash(password)
    cur = _conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, hashed, role),
    )
    _conn.commit()
    return {"id": cur.lastrowid, "username": username, "role": role}


def update_user_role(user_id: int, new_role: str) -> dict:
    if new_role not in ("viewer", "editor"):
        raise ValueError("Role must be viewer or editor")
    user = _conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise ValueError("User not found")
    if user["role"] == "admin":
        raise ValueError("Cannot change admin role")

    _conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    _conn.commit()
    return {"id": user_id, "username": user["username"], "role": new_role}


def delete_user(user_id: int) -> dict:
    user = _conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise ValueError("User not found")
    if user["role"] == "admin":
        raise ValueError("Cannot delete admin user")

    _conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    _conn.commit()
    return {"success": True}


# ─── JWT Helpers ────────────────────────────────────────────

def generate_token(user: dict) -> str:
    payload = {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
