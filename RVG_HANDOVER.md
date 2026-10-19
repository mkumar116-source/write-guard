# RVG — HANDOVER

Jira project: **RVG** (mkumar116.atlassian.net). This file is the current-state
summary — status, key decisions, open issues, next step.

## What it is

Standalone Flask tool (port 8511, `D:\Flask Tools\write-guard`) that watches
every other tool's `<KEY>_CHANGE_LOG.md` / `<KEY>_BACKLOG.md` / `<KEY>_HANDOVER.md`
(and the shared `common\COMMON_REQUESTS.md` / `PROJECT_SETUP_TEMPLATE.md`) for
the silent-revert pattern seen repeatedly on 2026-10-10 (PORTAL_CHANGE_LOG.md,
COMMON_REQUESTS.md). These files are append-only in normal use, so content
going backwards — smaller, or no longer starting with what it used to — is a
safe regression signal. A background poll loop (default 15s) keeps a baseline
per file and flags/logs any regression.

## Status

- **Phase 1 (built, not yet tested)**: monitor-only. Detects regressions,
  logs them to this tool's own `revert_incidents` table — never writes back
  to the watched file, never adds a row to the affected tool's own CHANGE_LOG.
- **Phase 2 (built, OFF by default)**: `guard.restore_enabled=1` also
  restores the file from the stored baseline when a regression is caught.
  Leave off until Phase 1 has held clean over a real stretch of normal use.
- Escape hatch: per-path pause (`revert_pauses` table, dashboard button) —
  pausing a path makes the guard adopt whatever it finds as the new baseline
  instead of flagging it, for deliberate shrinks like archiving.
- Not yet run on kumar-pc. Needs: `python -m venv venv`, requirements install,
  then `start.bat` (or run `app.py` directly) to confirm it starts on 8511
  and actually discovers the expected files.

## Key decisions

- **Separate standalone tool, not folded into Auto Backup** (2026-10-10,
  Kumar's call) — originally proposed as a feature inside Auto Backup
  (which already discovers every tool folder via `git_sync.discover()`),
  but built as its own tool instead. Port 8511, Jira key RVG, GitHub repo
  will be named **RVG** (not `write-guard`) — a deliberate one-off exception
  to the usual repo-name-matches-folder-slug convention (§2), per Kumar.
- Discovery is self-contained (`guard.discover_files()` walks the root
  folder itself) rather than importing Auto Backup's `git_sync.discover()`,
  since this is now a separate codebase/repo.
- Regression rule: current content must `startswith()` the stored baseline
  and be >= its length to count as safe growth; anything else (shorter, or
  diverged) is a regression. Only a *new* distinct bad size gets logged (not
  every poll of an unresolved one), to avoid incident-log spam.
- No alerting (email/Telegram) in this first build — logged to backlog.

## Open issues

- **Not yet tested on kumar-pc at all** — needs a first run to confirm
  venv/start.bat work and that `discover_files()` actually finds the
  expected files across all current tool folders.
- **GitHub repo**: Kumar will create the empty repo (named RVG) and run the
  first `git init`/`add`/`commit`/`push` himself, same as every other tool.
- **Nested folder discovery unconfirmed** — see RVG_BACKLOG.md.
- Port 8511 needs adding to the port list in
  `common\PROJECT_SETUP_TEMPLATE.md` §11 and `/topics/home-lab.md`.

## Next step

Kumar runs `start.bat` on his PC, confirms the Dashboard loads at
http://localhost:8511 and that the tracked-files list on Settings looks
right, then we watch Phase 1's incident log during normal use before
considering Phase 2.
