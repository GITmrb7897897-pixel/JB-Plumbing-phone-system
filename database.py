"""
Tiny SQLite wrapper for JB Plumbing's call log and settings.
No ORM on purpose — this is a one-table app and should stay easy
to read and back up (it's just a single .db file).
"""
import sqlite3
import csv
import io
from contextlib import contextmanager

DB_PATH = "jb_plumbing_calls.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_sid TEXT,
                name TEXT,
                phone TEXT,
                address TEXT,
                issue TEXT,
                preferred_time TEXT,
                next_step TEXT,
                voicemail_url TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('josh_available', 'false')"
        )


def insert_call(**kwargs):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO calls
                (call_sid, name, phone, address, issue, preferred_time,
                 next_step, voicemail_url, created_at)
            VALUES
                (:call_sid, :name, :phone, :address, :issue, :preferred_time,
                 :next_step, :voicemail_url, :created_at)
            """,
            kwargs,
        )


def get_all_calls():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM calls ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_setting(key):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def export_calls_csv():
    calls = get_all_calls()
    output = io.StringIO()
    if calls:
        writer = csv.DictWriter(output, fieldnames=calls[0].keys())
        writer.writeheader()
        writer.writerows(calls)
    return output.getvalue()
