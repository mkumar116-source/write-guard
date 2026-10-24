"""SQLite storage for Write Guard (RVG)."""
import os
import sqlite3
from contextlib import closing

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "write_guard.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS file_baselines (
    path       TEXT PRIMARY KEY,
    content    TEXT,
    size       INTEGER,
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS revert_incidents (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    path               TEXT,
    detected_at        TEXT DEFAULT (datetime('now', 'localtime')),
    baseline_size      INTEGER,
    found_size         INTEGER,
    restored           INTEGER NOT NULL DEFAULT 0,
    detail             TEXT,
    status             TEXT NOT NULL DEFAULT 'logged',
    diff_snippet       TEXT,
    attribution_status TEXT,
    attributed_note    TEXT
);

CREATE TABLE IF NOT EXISTS revert_pauses (
    path        TEXT PRIMARY KEY,
    paused_until TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "app.host": "127.0.0.1",
    "app.port": "8511",
    "guard.enabled": "1",            # master on/off for the polling loop
    "guard.restore_enabled": "0",    # phase gate: 0 = monitor-only (log), 1 = approval-gated restore
    "guard.poll_interval_seconds": "15",
    "sources.root_folder": r"D:\Flask Tools",
    # Smart Alerting Hub integration (added 2026-10-17) — kept as settings, not
    # hardcoded in source. hub.api_key defaults to empty here deliberately: the
    # real key is seeded directly into the live (gitignored) data/write_guard.db
    # once, not committed in this file, since this repo is headed to GitHub
    # (private, but secrets don't belong in source either way).
    "hub.enabled": "1",
    "hub.base_url": "http://localhost:8512",
    "hub.api_key": "",
    # Write-attribution cross-check (added 2026-10-24, RVG-14 — after incident
    # 14 had no matching COMMON_WRITE_LOG.md row). match_window_minutes is how
    # close a log row's timestamp must be to detected_at to count as a match;
    # recheck_grace_minutes is how long an 'unattributed' incident keeps
    # getting re-checked (the log row is usually appended a little after the
    # file write itself) before it's left as unattributed for good.
    "attribution.match_window_minutes": "2",
    "attribution.recheck_grace_minutes": "5",
}


def set_setting(key, value):
    execute("INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_conn()) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            DEFAULT_SETTINGS.items(),
        )
        # Migrate existing installs (CREATE TABLE IF NOT EXISTS above won't add
        # columns to an already-existing revert_incidents table) — added
        # 2026-10-12 for RVG-2 (approval-gated restore) and RVG-5 (diff snippet).
        for ddl in (
            "ALTER TABLE revert_incidents ADD COLUMN status TEXT NOT NULL DEFAULT 'logged'",
            "ALTER TABLE revert_incidents ADD COLUMN diff_snippet TEXT",
            # RVG-14 (2026-10-24) — write-attribution cross-check columns
            "ALTER TABLE revert_incidents ADD COLUMN attribution_status TEXT",
            "ALTER TABLE revert_incidents ADD COLUMN attributed_note TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()


def get_setting(key, default=None):
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def all_settings():
    with closing(get_conn()) as conn:
        rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    return {r["key"]: r["value"] for r in rows}


def execute(sql, params=()):
    with closing(get_conn()) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def query(sql, params=()):
    with closing(get_conn()) as conn:
        return conn.execute(sql, params).fetchall()
