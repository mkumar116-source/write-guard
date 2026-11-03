"""Write Guard (RVG) — Flask app.

Phase 1: monitor-only file-revert watcher for every Flask tool's
CHANGE_LOG/BACKLOG/HANDOVER files plus the shared common/ files.
See guard.py for the detection logic.
"""
import os
import sys

from flask import Flask, render_template, request, redirect, url_for

import db
import guard

APP_VERSION = "0.1.0"


def create_app():
    app = Flask(__name__)
    db.init_db()

    @app.route("/")
    def dashboard():
        incidents = db.query("SELECT * FROM revert_incidents ORDER BY id DESC LIMIT 50")
        pending = db.query("SELECT * FROM revert_incidents WHERE status='pending_approval' ORDER BY id DESC")
        baselines = db.query("SELECT path, size, updated_at FROM file_baselines ORDER BY path")
        pauses = db.query("SELECT path, paused_until FROM revert_pauses")
        return render_template(
            "dashboard.html",
            version=APP_VERSION,
            settings=db.all_settings(),
            incidents=incidents,
            pending=pending,
            baselines=baselines,
            pauses={p["path"]: p["paused_until"] for p in pauses},
            tracked_count=len(guard.discover_files()),
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
