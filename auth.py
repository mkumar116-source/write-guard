"""Write Guard (RVG) — single-admin session auth.

Added 2026-11-07 for SECA finding F71 ("no auth"). Write Guard binds to
127.0.0.1 by default, but had literally no login anywhere in the code — a
real gap if app.host is ever changed. Single admin password (not a users
table — this is a one-person tool), hash stored in the settings table
under 'auth.password_hash'. If no password is set yet, the app routes to
/setup instead of /login (first-run bootstrap). Failed-login lockout
mirrors the same shape already used by SECA and netmonitor (5 fails = 5
min lockout), via the login_failures table (one row, id=1).
"""
from datetime import datetime, timedelta
from functools import wraps

from flask import session, redirect, url_for, request
from werkzeug.security import generate_password_hash, check_password_hash

import db

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 5


def is_set_up():
    return bool(db.get_setting("auth.password_hash"))


def set_password(password):
    db.set_setting("auth.password_hash", generate_password_hash(password))


def verify_password(password):
    stored = db.get_setting("auth.password_hash")
    return bool(stored) and check_password_hash(stored, password or "")


def locked_until():
    """Returns the lockout end (datetime) if login is locked right now, else None."""
    rows = db.query("SELECT locked_until FROM login_failures WHERE id = 1")
    if rows and rows[0]["locked_until"]:
        until = datetime.fromisoformat(rows[0]["locked_until"])
        if until > datetime.now():
            return until
    return None


def record_failed_login():
    """Counts a failed attempt; returns the lockout end (datetime) if this one triggered a lock."""
    rows = db.query("SELECT failed_count FROM login_failures WHERE id = 1")
    count = (rows[0]["failed_count"] if rows else 0) + 1
    until = None
    if count >= MAX_FAILED_LOGINS:
        until = datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)
        count = 0
    db.execute(
        "INSERT INTO login_failures (id, failed_count, locked_until) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET failed_count=excluded.failed_count, locked_until=excluded.locked_until",
        (count, until.isoformat(timespec="seconds") if until else None),
    )
    return until


def clear_failed_logins():
    db.execute("DELETE FROM login_failures WHERE id = 1")


def login_required(view):
    """Decorator for individual routes — kept for clarity/documentation, but
    the real enforcement is the before_request allowlist in app.py
    (fail-closed: anything not explicitly on the open list requires login,
    so a newly-added route is protected by default)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped
