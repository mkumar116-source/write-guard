"""Write Guard (RVG) — core scan logic.

Watches every tool's CHANGE_LOG.md / BACKLOG.md / HANDOVER.md (classic and
<KEY>_-prefixed names) plus the shared common/ files, for the silent-revert
pattern: a file's content going backwards (smaller, or diverging from what
it used to start with) instead of only ever growing, which is the normal
shape of these append-only files.

Phase 1 (default): monitor-only. Detects a regression, logs it to
revert_incidents, does NOT touch the file on disk.
Phase 2 (guard.restore_enabled = 1): a regression is queued as
'pending_approval' instead of being restored automatically — nothing is
written to the file until Kumar clicks Approve on the dashboard (or
Reject, which leaves the file as-is and adopts its current content as the
new baseline). Changed 2026-10-12 (RVG-2) — this was originally meant to
be silent auto-restore, but Kumar asked for an approval step given this
tool's own file is still only ~2 days into being trusted.

Never writes into the watched file's own tool semantics (no CHANGE_LOG rows
added) — all guard activity is logged in this tool's own DB only.

Smart Alerting Hub integration (added 2026-10-17): a regression also POSTs
an alert to the hub (settings hub.*), and the poll loop watchdogs the hub's
own /health endpoint — see send_hub_alert() / check_hub_health() below.

Write-attribution cross-check (added 2026-10-24, RVG-14 — after incident 14
on PROJECT_SETUP_TEMPLATE.md had no explanation): every regression is
cross-checked against common/COMMON_WRITE_LOG.md (see PROJECT_SETUP_TEMPLATE
§23) for a row on the same file within a few minutes of detection. A match
means a legitimate, logged edit; no match is the stronger "unattributed"
signal. See _find_attribution() / recheck_unattributed() below.

Baseline recovery (added 2026-11-02, RVG-15 — after the ChatQA Hub
investigation found the baseline is a one-way high-water mark that never
lowers, so one legitimate shrink — an archive rotation, or anything else —
re-flags as "still regressed" on every poll forever, compounding a single
real event into dozens of incidents as the file keeps growing again below
its old peak). Two paths lower the baseline back down, both Phase-1-only
(status='logged' — never overrides a pending_approval incident awaiting
Kumar's click): (1) the moment a regression is marked attributed, baseline
resets to current content; (2) if a shrunk size holds stable for
baseline.stability_minutes with no further change, Write Guard quietly
adopts it as the new baseline even if never attributed (covers
COMMON_WRITE_LOG.md's case — chats rarely log a write to the log file
about itself, so attribution alone doesn't catch its own noise). The
original incident row is never deleted, just annotated.
"""
import os
import threading
import time
from datetime import datetime, timedelta

import requests

import db

SELF_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK = threading.Lock()

TRACKED_SUFFIXES = ("_CHANGE_LOG.md", "_BACKLOG.md", "_HANDOVER.md")
CLASSIC_NAMES = ("CHANGE_LOG.md", "BACKLOG.md")
SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "archive", "data", "static", "templates"}
FIXED_EXTRA = ["common\\COMMON_REQUESTS.md", "common\\PROJECT_SETUP_TEMPLATE.md",
               "common\\COMMON_WRITE_LOG.md"]
WRITE_LOG_RELPATH = "common\\COMMON_WRITE_LOG.md"


def discover_files():
    """All paths this guard watches, relative to the root folder's parent
    (i.e. full absolute paths), found fresh each scan so new tools/files
    are picked up automatically."""
    root = db.get_setting("sources.root_folder", r"D:\Flask Tools")
    found = []
    if os.path.isdir(root):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            # don't descend more than 2 levels below root (matches tool folder layout)
            depth = os.path.relpath(dirpath, root).count(os.sep)
            if depth >= 2:
                dirnames[:] = []
            if os.path.abspath(dirpath) == os.path.abspath(SELF_DIR):
                continue  # never watch our own tool's files
            for name in filenames:
                if name.endswith(TRACKED_SUFFIXES) or name in CLASSIC_NAMES:
                    found.append(os.path.join(dirpath, name))
    for rel in FIXED_EXTRA:
        p = os.path.join(root, rel)
        if os.path.isfile(p) and p not in found:
            found.append(p)
    return found


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _write(path, content):
    tmp = path + ".rvg_tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    os.replace(tmp, path)


def _paused(path):
    rows = db.query("SELECT paused_until FROM revert_pauses WHERE path=?", (path,))
    if not rows:
        return False
    try:
        return datetime.fromisoformat(rows[0]["paused_until"]) > datetime.now()
    except ValueError:
        return False


def pause(path, minutes):
    until = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    db.execute("INSERT INTO revert_pauses (path, paused_until) VALUES (?, ?) "
               "ON CONFLICT(path) DO UPDATE SET paused_until = excluded.paused_until", (path, until))


def unpause(path):
    db.execute("DELETE FROM revert_pauses WHERE path=?", (path,))


def _baseline(path):
    rows = db.query("SELECT content, size FROM file_baselines WHERE path=?", (path,))
    return (rows[0]["content"], rows[0]["size"]) if rows else (None, None)


def _now_ist():
    # kumar-pc's system clock is already IST; datetime.now() is local time.
    # (SQLite's own datetime('now') is UTC, which is the bug this fixes —
    # dashboard times were showing 5:30 behind the wall clock.)
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _set_baseline(path, content):
    db.execute("INSERT INTO file_baselines (path, content, size, updated_at) VALUES (?, ?, ?, ?) "
               "ON CONFLICT(path) DO UPDATE SET content=excluded.content, size=excluded.size, "
               "updated_at=excluded.updated_at", (path, content, len(content), _now_ist()))


def _last_incident(path):
    rows = db.query("SELECT * FROM revert_incidents WHERE path=? ORDER BY id DESC LIMIT 1", (path,))
    return rows[0] if rows else None


def _diff_snippet(baseline, current, max_len=80):
    """First point of divergence between the old baseline and the current
    (shrunk) content, for the incident detail — added 2026-10-12 (RVG-5) so
    an incident is diagnosable from the dashboard without a manual diff."""
    b_lines = baseline.splitlines()
    c_lines = current.splitlines()
    for i in range(min(len(b_lines), len(c_lines))):
        if b_lines[i] != c_lines[i]:
            return f"line {i + 1} differs: baseline {b_lines[i][:max_len]!r} -> found {c_lines[i][:max_len]!r}"
    if len(c_lines) < len(b_lines):
        missing = b_lines[len(c_lines)][:max_len]
        return f"truncated after line {len(c_lines)} — first missing line: {missing!r}"
    return "content differs at the byte level (no clear line boundary)"


def _log_incident(path, baseline_size, found_size, detail, restored, status, diff_snippet,
                   attribution_status=None, attributed_note=None, detected_at=None):
    detected_at = detected_at or _now_ist()
    return db.execute(
        "INSERT INTO revert_incidents (path, detected_at, baseline_size, found_size, detail, restored, "
        "status, diff_snippet, attribution_status, attributed_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (path, detected_at, baseline_size, found_size, detail, int(restored), status, diff_snippet,
         attribution_status, attributed_note))


# --- Write-attribution cross-check (added 2026-10-24, RVG-14) ---------------

def _write_log_path():
    root = db.get_setting("sources.root_folder", r"D:\Flask Tools")
    return os.path.join(root, WRITE_LOG_RELPATH)


def _normalize_path(p):
    return os.path.normcase(os.path.normpath(p.strip()))


def _parse_write_log_rows():
    """Rows from COMMON_WRITE_LOG.md's table: | Date/Time (IST) | File |
    Tool/Chat | Note |. Skips the header/separator lines and any malformed
    row rather than raising — a hand-edited log shouldn't break scanning."""
    content = _read(_write_log_path())
    if not content:
        return []
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= {"-", " "}:
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or cols[0].lower().startswith("date"):
            continue  # header row or malformed
        try:
            dt = datetime.strptime(cols[0], "%Y-%m-%d %H:%M")
        except ValueError:
            continue  # not a data row (or unexpected format) — skip, don't crash the scan
        rows.append({
            "dt": dt,
            "file": cols[1],
            "tool_chat": cols[2],
            "note": cols[3] if len(cols) > 3 else "",
        })
    return rows


def _find_attribution(path, detected_at_str, window_minutes):
    """Nearest COMMON_WRITE_LOG.md row for this same path within
    window_minutes of detected_at_str, or None if there isn't one (yet)."""
    try:
        detected_dt = datetime.strptime(detected_at_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    target = _normalize_path(path)
    best, best_delta = None, None
    for row in _parse_write_log_rows():
        if _normalize_path(row["file"]) != target:
            continue
        delta = abs((row["dt"] - detected_dt).total_seconds()) / 60
        if delta <= window_minutes and (best is None or delta < best_delta):
            best, best_delta = row, delta
    return best


def recheck_unattributed():
    """A chat usually writes the tracked file, then appends the
    COMMON_WRITE_LOG.md row a little after — so an incident logged as
    'unattributed' at detection time may still get explained once that row
    lands. Re-checks each such incident on every poll until
    attribution.recheck_grace_minutes elapses, then leaves it as the
    stronger, permanent 'unattributed' signal."""
    window_minutes = int(db.get_setting("attribution.match_window_minutes", "2") or 2)
    grace_minutes = int(db.get_setting("attribution.recheck_grace_minutes", "5") or 5)
    now = datetime.now()
    for inc in db.query("SELECT * FROM revert_incidents WHERE attribution_status='unattributed'"):
        try:
            detected_dt = datetime.strptime(inc["detected_at"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        if (now - detected_dt).total_seconds() > grace_minutes * 60:
            continue  # past grace period — stays unattributed
        match = _find_attribution(inc["path"], inc["detected_at"], window_minutes)
        if match:
            note = f"{match['tool_chat']} — {match['note']}" if match["note"] else match["tool_chat"]
            db.execute("UPDATE revert_incidents SET attribution_status='attributed', attributed_note=? "
                       "WHERE id=?", (note, inc["id"]))
            # RVG-15: same as the immediate-attribution case in check_one() —
            # now explained, so stop comparing against the stale baseline.
            # Phase-1 only. Re-read fresh rather than trusting found_size,
            # since more time has passed and the file may have moved again.
            if inc["status"] == "logged":
                current = _read(inc["path"])
                if current is not None:
                    _set_baseline(inc["path"], current)


# --- Smart Alerting Hub integration (added 2026-10-17) -----------------------

def send_hub_alert(alert_key, severity, message, link=None):
    """Best-effort POST to the Smart Alerting Hub. Never raises — a hub
    outage must not break scanning. Returns True on a 2xx response."""
    if db.get_setting("hub.enabled", "1") != "1":
        return False
    base_url = db.get_setting("hub.base_url", "http://localhost:8512")
    api_key = db.get_setting("hub.api_key", "")
    if not api_key:
        return False
    payload = {
        "alert_key": alert_key[:100],
        "severity": severity,
        "message": message[:2000],
    }
    if link:
        payload["link"] = link
    try:
        resp = requests.post(
            f"{base_url}/api/alert",
            headers={"X-Hub-Key": api_key},
            json=payload,
            timeout=5,
        )
        return resp.ok
    except requests.RequestException:
        return False  # hub unreachable — dropped, not queued (RVG-7, backlogged)


def check_hub_health():
    """Watchdog: the hub can't credibly report its own death, so Write Guard
    polls it on the same schedule as file scanning. A failure is logged as
    an incident-style row on Write Guard's own dashboard (not re-sent to the
    hub — that would be circular) so it's visible even though Write Guard
    has no separate mailer/Telegram channel of its own yet (RVG-1)."""
    if db.get_setting("hub.enabled", "1") != "1":
        return
    base_url = db.get_setting("hub.base_url", "http://localhost:8512")
    was_down = db.get_setting("hub.was_down", "0") == "1"
    try:
        resp = requests.get(f"{base_url}/health", timeout=5)
        up = resp.ok
    except requests.RequestException:
        up = False

    if not up and not was_down:
        _log_incident(
            "(Smart Alerting Hub)", 0, 0,
            f"Health check failed at {base_url}/health — hub appears down. "
            "Write Guard will keep polling and clear this once it's back.",
            False, "logged", None,
        )
        db.set_setting("hub.was_down", "1")
    elif up and was_down:
        db.set_setting("hub.was_down", "0")


def check_one(path, restore_enabled):
    """Returns 'baseline' | 'grew' | 'unchanged' | 'regression' | 'paused' | 'unreadable'."""
    current = _read(path)
    if current is None:
        return "unreadable"
    if _paused(path):
        _set_baseline(path, current)  # don't fight a deliberate change; adopt it as the new baseline
        return "paused"

    baseline, baseline_size = _baseline(path)
    if baseline is None:
        _set_baseline(path, current)
        return "baseline"

    if len(current) >= len(baseline):
        if len(current) > len(baseline):
            _set_baseline(path, current)
            return "grew"
        return "unchanged"

    # regression: the file got SMALLER than its last known-good size.
    # (Not using a "must start with the old baseline" check here — BACKLOG.md
    # and HANDOVER.md both get legitimate in-place edits, not pure appends,
    # e.g. a checkbox flipped to [x] or a line inserted mid-file, which would
    # falsely trip a prefix check even though nothing was lost. Only
    # CHANGE_LOG.md is strictly append-only by convention; shrink alone is
    # still the one safe, file-type-agnostic signal a revert actually gives.)
    last_incident = _last_incident(path)
    last_found = last_incident["found_size"] if last_incident else None
    if last_found != len(current):   # only log a NEW distinct bad state, not a repeat poll of the same one
        detail = f"baseline {baseline_size}B -> found {len(current)}B"
        snippet = _diff_snippet(baseline, current)
        if restore_enabled:
            status = "pending_approval"
            detail += " (queued for approval — nothing restored until approved on the dashboard)"
        else:
            status = "logged"
        detected_at = _now_ist()
        window_minutes = int(db.get_setting("attribution.match_window_minutes", "2") or 2)
        match = _find_attribution(path, detected_at, window_minutes)
        if match:
            attribution_status = "attributed"
            attributed_note = f"{match['tool_chat']} — {match['note']}" if match["note"] else match["tool_chat"]
        else:
            attribution_status = "unattributed"
            attributed_note = None
        _log_incident(path, baseline_size, len(current), detail, False, status, snippet,
                       attribution_status, attributed_note, detected_at)
        # RVG-15: an attributed regression is a known, explained edit — the
        # shrunk content IS the new legitimate state, so stop comparing
        # against the old baseline. Phase-1 only; never short-circuits a
        # pending_approval incident Kumar still needs to act on.
        if attribution_status == "attributed" and status == "logged":
            _set_baseline(path, current)
        alert_msg = f"{path} shrank: {baseline_size}B -> {len(current)}B. {snippet}"
        if restore_enabled:
            alert_msg += " Queued for approval."
        if attribution_status == "unattributed":
            alert_msg += " UNATTRIBUTED — no matching COMMON_WRITE_LOG.md entry."
        send_hub_alert("regression_detected", "critical", alert_msg,
                        link=f"http://localhost:{db.get_setting('app.port', '8511')}")
    elif last_incident and last_incident["status"] == "logged":
        # RVG-15: same bad state as the last logged incident (no new row —
        # that's the existing dedup above). If it's held unchanged for a
        # while with nothing new going wrong, treat it as the new normal
        # instead of re-flagging it against the stale baseline forever —
        # covers cases attribution never catches (e.g. a chat rarely logs a
        # write to COMMON_WRITE_LOG.md about itself writing COMMON_WRITE_LOG.md).
        try:
            last_dt = datetime.strptime(last_incident["detected_at"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            last_dt = None
        stability_minutes = int(db.get_setting("baseline.stability_minutes", "3") or 3)
        if last_dt and (datetime.now() - last_dt).total_seconds() >= stability_minutes * 60:
            _set_baseline(path, current)
            db.execute(
                "UPDATE revert_incidents SET detail = detail || ? WHERE id = ?",
                (f" — baseline auto-reset after holding stable {stability_minutes}+ min", last_incident["id"]),
            )
    return "regression"


def approve_restore(incident_id):
    """Write the stored baseline back to disk for a pending-approval
    incident. Returns (ok, message)."""
    rows = db.query("SELECT * FROM revert_incidents WHERE id=?", (incident_id,))
    if not rows:
        return False, "incident not found"
    inc = rows[0]
    if inc["status"] != "pending_approval":
        return False, f"incident is not pending approval (status={inc['status']})"
    baseline, baseline_size = _baseline(inc["path"])
    if baseline is None:
        return False, "no stored baseline for this path"
    try:
        _write(inc["path"], baseline)
    except OSError as e:
        return False, f"restore failed: {e}"
    db.execute("UPDATE revert_incidents SET status='restored', restored=1 WHERE id=?", (incident_id,))
    return True, "restored from baseline"


def reject_restore(incident_id):
    """Leave the file as-is and adopt its current content as the new
    baseline, so it stops being re-flagged. Returns (ok, message)."""
    rows = db.query("SELECT * FROM revert_incidents WHERE id=?", (incident_id,))
    if not rows:
        return False, "incident not found"
    inc = rows[0]
    if inc["status"] != "pending_approval":
        return False, f"incident is not pending approval (status={inc['status']})"
    current = _read(inc["path"])
    if current is not None:
        _set_baseline(inc["path"], current)
    db.execute("UPDATE revert_incidents SET status='rejected' WHERE id=?", (incident_id,))
    return True, "rejected — current content adopted as new baseline"


def scan_once():
    enabled = db.get_setting("guard.enabled", "1") == "1"
    if not enabled:
        return {"enabled": False}
    restore_enabled = db.get_setting("guard.restore_enabled", "0") == "1"
    results = {}
    for path in discover_files():
        try:
            results[path] = check_one(path, restore_enabled)
        except Exception as e:
            results[path] = f"error: {e}"
    return {"enabled": True, "results": results}


def _loop():
    time.sleep(15)  # let the app finish starting
    while True:
        try:
            if LOCK.acquire(blocking=False):
                try:
                    scan_once()
                    check_hub_health()
                    recheck_unattributed()
                finally:
                    LOCK.release()
        except Exception:
            pass  # never kill the scheduler thread; errors are per-file inside scan_once
        interval = int(db.get_setting("guard.poll_interval_seconds", "15") or 15)
        time.sleep(max(5, interval))


_started = False


def start_scheduler():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, name="write-guard-poll", daemon=True).start()
