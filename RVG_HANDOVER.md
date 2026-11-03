# RVG — HANDOVER

Jira project: **RVG** (mkumar116.atlassian.net). This file is the current-state
summary — status, key decisions, open issues, next step.

## What it is

Standalone Flask tool (port 8511, `D:\Flask Tools\write-guard`) that watches
every other tool's `<KEY>_CHANGE_LOG.md` / `<KEY>_BACKLOG.md` / `<KEY>_HANDOVER.md`
(and shared `common\` files: `COMMON_REQUESTS.md`, `PROJECT_SETUP_TEMPLATE.md`,
`COMMON_WRITE_LOG.md`) for the silent-revert pattern first seen on 2026-10-10
(PORTAL_CHANGE_LOG.md, COMMON_REQUESTS.md). These files are append-only in
normal use, so content going backwards — smaller than the last known-good
size — is a safe regression signal. A background poll loop (default 15s)
keeps a baseline per file and flags/logs any regression.

## Status

- **Live and running** on kumar-pc since 2026-10-11. Currently **Phase 1 —
  monitor-only**: detects regressions, logs them to `revert_incidents`,
  never writes back to the watched file on its own.
- **Phase 2 (built, off by default)**: `guard.restore_enabled=1` — not
  silent auto-restore. A regression queues as `pending_approval` on the
  dashboard with a diff snippet; Kumar clicks Approve (restores from
  baseline) or Reject (adopts the current content as the new baseline).
  Still off — Phase 1 hasn't yet run clean long enough to flip it.
- Escape hatch: per-path pause (`revert_pauses` table, dashboard button,
  duration presets 10 min/30 min/1 hr/4 hr/24 hr) — pausing a path makes the
  guard adopt whatever it finds as the new baseline instead of flagging it,
  for deliberate shrinks like archiving.
- **Smart Alerting Hub integration** (2026-10-17): regressions POST to the
  hub (`send_hub_alert`); the poll loop also watchdogs the hub's own
  `/health` and logs a dashboard incident if the hub goes down.
- **Write-attribution cross-check** (RVG-14, 2026-10-24): every regression
  is matched against `common\COMMON_WRITE_LOG.md` for a row on the same file
  within a few minutes of detection — `attributed` (with the logged
  chat/note) or the stronger `unattributed` signal if nothing explains it.
  Dashboard shows this per incident; unattributed also flagged in the hub
  alert message.
- **Baseline recovery** (RVG-15, 2026-11-02): the baseline used to be a
  one-way high-water mark that never lowered, so one legitimate shrink (an
  archive rotation, say) got re-flagged as "still regressed" on every poll
  forever — 35 of 59 total incidents were repeat noise from just 3 files
  this way. Now resets on `attributed`, and separately auto-adopts a shrunk
  size that's held stable for `baseline.stability_minutes` (default 3 min)
  even without attribution, covering files like `COMMON_WRITE_LOG.md` that
  rarely get a matching log row about writing to themselves.

## Key decisions

- **Separate standalone tool, not folded into Auto Backup** (2026-10-10,
  Kumar's call) — originally proposed as an Auto Backup feature, built as
  its own tool instead. GitHub repo name is **RVG** (not `write-guard`), a
  deliberate one-off exception to the repo-matches-folder-slug convention.
- Discovery is self-contained (`guard.discover_files()` walks the root
  folder itself), not Auto Backup's `git_sync.discover()`.
- Regression rule: shrink-only (`found_size < baseline_size`) — not a
  `startswith()`-the-baseline check, since BACKLOG.md/HANDOVER.md get
  legitimate in-place edits (checkbox flips, mid-file inserts), not pure
  appends. Only a *new* distinct bad size gets logged (not every poll of an
  unresolved one), to avoid incident-log spam.
- No separate alerting channel of its own — routed through the Smart
  Alerting Hub instead (RVG-1).
- Baseline-lowering (RVG-15) is deliberately Phase-1-only (`status='logged'`)
  — it never short-circuits a `pending_approval` incident Kumar still needs
  to Approve/Reject, so Phase 2's approval gate stays meaningful if it's
  ever turned on.

## Open issues

- **RVG-7**: Retry queue for hub alerts — a failed POST to the hub is
  currently dropped, not queued/retried. Deliberate v1 tradeoff since Write
  Guard's own dashboard/incident log is the backstop either way.
- Already-stuck baselines from before RVG-15 existed (e.g.
  `NETMON_CHANGE_LOG.md`, frozen since 2026-10-21) won't self-heal until a
  new attribution or stability-window trigger fires — a one-time manual
  reset of those was offered but not yet done, pending Kumar's go-ahead.

## Next step

Keep Phase 1 running and watch the incident log for a clean stretch before
considering Phase 2 (`restore_enabled=1`) — the RVG-15 fix should make that
log much quieter going forward. RVG-7 is the one open backlog item.
