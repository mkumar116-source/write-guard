"""Write Guard (RVG) — Flask app.

Phase 1: monitor-only file-revert watcher for every Flask tool's
CHANGE_LOG/BACKLOG/HANDOVER files plus the shared common/ files.
See guard.py for the detection logic. See auth.py for the single-admin
login added 2026-11-07 (SECA F71).
"""
import os
import secrets

from flask import Flask, render_template, request, redirect, url_for, session

import auth
import db
import guard

APP_VERSION = "0.1.0"

# Routes reachable with no login — everything else is fail-closed (anything
# not explicitly listed here requires a session), so a newly-added route is
# protected by default. /health stays open since other tools' watchdogs poll it.
OPEN_PATHS = {"/login", "/setup", "/health"}


def create_app():
    app = Flask(__name__)
    db.init_db()

    # Session secret: generated once and stored alongside everything else in
    # the settings table (same spot as auth.password_hash / hub.api_key) —
    # not hardcoded in source, not reset on every restart.
    secret = db.get_setting("auth.secret_key")
    if not secret:
        secret = secrets.token_hex(32)
        db.set_setting("auth.secret_key", secret)
    app.secret_key = secret

    @app.before_request
    def require_login():
        if request.path in OPEN_PATHS or request.path.startswith("/static/"):
            return None
        if not auth.is_set_up():
            return redirect(url_for("setup"))
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return None

    @app.after_request
    def add_security_headers(response):
        # SECA F396/F397 (2026-11-07): baseline security headers + hide the
        # Werkzeug version from the Server header, same pattern applied to
        # netmonitor. CSP is kept loose (unsafe-inline for style/script)
        # since the dashboard templates use inline style, no nonce setup.
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'"
        )
        response.headers["Server"] = "Write Guard"
        return response

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if auth.is_set_up():
            return redirect(url_for("login"))
        error = None
        if request.method == "POST":
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            if len(password) < 8:
                error = "Password must be at least 8 characters."
            elif password != confirm:
                error = "Passwords don't match."
            else:
                auth.set_password(password)
                session["logged_in"] = True
                return redirect(url_for("dashboard"))
        return render_template("setup.html", version=APP_VERSION, error=error)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not auth.is_set_up():
            return redirect(url_for("setup"))
        error = None
        if request.method == "POST":
            until = auth.locked_until()
            if until:
                error = f"Too many failed attempts — locked until {until.strftime('%H:%M:%S')}."
            elif auth.verify_password(request.form.get("password", "")):
                auth.clear_failed_logins()
                session["logged_in"] = True
                next_path = request.args.get("next") or url_for("dashboard")
                return redirect(next_path)
            else:
                locked_now = auth.record_failed_login()
                error = (
                    f"Too many failed attempts — locked for {auth.LOCKOUT_MINUTES} minutes."
                    if locked_now else "Incorrect password."
                )
        return render_template("login.html", version=APP_VERSION, error=error)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def dashboard():
        incidents = db.query("SELECT * FROM revert_incidents ORDER BY id DESC LIMIT 50")
        pending = db.query("SELECT * FROM revert_incidents WHERE status='pending_approval' ORDER BY id DESC")
        baselines = db.query("SELECT path, size, updated_at FROM file_baselines ORDER BY path")
        pauses = db.query("SELECT path, paused_until FROM revert_pauses")
        hub_queue_count = db.query("SELECT COUNT(*) AS c FROM pending_hub_alerts")[0]["c"]

        # Attribution-rate stat (2026-11-07) — simple, no new storage: % of
        # the last N real-file incidents (excludes sentinel rows like
        # "(Systemic)"/"(Smart Alerting Hub)" and pre-RVG-14 rows with no
        # attribution_status at all) that came back attributed.
        attr_rows = db.query(
            "SELECT attribution_status FROM revert_incidents "
            "WHERE path NOT IN ('(Systemic)', '(Smart Alerting Hub)') "
            "AND attribution_status IS NOT NULL ORDER BY id DESC LIMIT 50"
        )
        attribution_rate = None
        if attr_rows:
            attributed = sum(1 for r in attr_rows if r["attribution_status"] == "attributed")
            attribution_rate = {"pct": round(100 * attributed / len(attr_rows)), "n": len(attr_rows)}

        return render_template(
            "dashboard.html",
            version=APP_VERSION,
            settings=db.all_settings(),
            incidents=incidents,
            pending=pending,
            baselines=baselines,
            pauses={p["path"]: p["paused_until"] for p in pauses},
            tracked_count=len(guard.discover_files()),
            hub_queue_count=hub_queue_count,
            attribution_rate=attribution_rate,
        )

    @app.route("/scan-now", methods=["POST"])
    def scan_now():
        guard.scan_once()
        return redirect(url_for("dashboard"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            db.set_setting("guard.enabled", "1" if request.form.get("enabled") else "0")
            db.set_setting("guard.restore_enabled", "1" if request.form.get("restore_enabled") else "0")
            interval = request.form.get("poll_interval_seconds", "15").strip()
            if interval.isdigit() and int(interval) >= 5:
                db.set_setting("guard.poll_interval_seconds", interval)
            window = request.form.get("attribution_window_minutes", "2").strip()
            if window.isdigit() and int(window) >= 1:
                db.set_setting("attribution.match_window_minutes", window)
            grace = request.form.get("attribution_grace_minutes", "5").strip()
            if grace.isdigit() and int(grace) >= 1:
                db.set_setting("attribution.recheck_grace_minutes", grace)
            stability = request.form.get("baseline_stability_minutes", "3").strip()
            if stability.isdigit() and int(stability) >= 1:
                db.set_setting("baseline.stability_minutes", stability)
            return redirect(url_for("settings"))
        return render_template("settings.html", version=APP_VERSION, settings=db.all_settings(),
                                tracked=guard.discover_files())

    @app.route("/pause", methods=["POST"])
    def pause_file():
        path = request.form.get("path", "")
        minutes = int(request.form.get("minutes", "10") or 10)
        if path:
            guard.pause(path, minutes)
        return redirect(url_for("dashboard"))

    @app.route("/unpause", methods=["POST"])
    def unpause_file():
        path = request.form.get("path", "")
        if path:
            guard.unpause(path)
        return redirect(url_for("dashboard"))

    @app.route("/approve-restore", methods=["POST"])
    def approve_restore():
        incident_id = request.form.get("incident_id", type=int)
        if incident_id:
            guard.approve_restore(incident_id)
        return redirect(url_for("dashboard"))

    @app.route("/reject-restore", methods=["POST"])
    def reject_restore():
        incident_id = request.form.get("incident_id", type=int)
        if incident_id:
            guard.reject_restore(incident_id)
        return redirect(url_for("dashboard"))

    @app.route("/health")
    def health():
        return {"status": "ok", "version": APP_VERSION}

    return app


if __name__ == "__main__":
    app = create_app()
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("Write Guard")
    guard.start_scheduler()
    host = db.get_setting("app.host", "127.0.0.1")
    port = int(db.get_setting("app.port", "8511"))
    interval = db.get_setting("guard.poll_interval_seconds", "15")
    tracked_count = len(guard.discover_files())
    guard.send_hub_alert(
        "tool_started", "info",
        f"Write Guard started — watching {tracked_count} files, polling every {interval}s.",
        link=f"http://localhost:{port}",
    )
    print(f"Write Guard v{APP_VERSION} running on http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
