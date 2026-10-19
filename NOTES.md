# Write Guard (RVG) — Notes

**What it does**: background watcher (port 8511) that polls every Flask
tool's CHANGE_LOG/BACKLOG/HANDOVER files (and the shared common/ files) for
the silent-revert pattern — content that should only ever grow going
backwards instead. Logs regressions; can optionally auto-restore from the
last known-good baseline once trusted (Phase 2, off by default).

**Status**: built, not yet run on kumar-pc. See RVG_HANDOVER.md for full
current-state detail and open issues.

**Key decisions**: see RVG_HANDOVER.md.

**Open issues**: see RVG_HANDOVER.md.

**How to run**: `start.bat` (creates venv, installs Flask, starts on
http://localhost:8511).
