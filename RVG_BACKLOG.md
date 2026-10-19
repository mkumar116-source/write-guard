# RVG — BACKLOG

New ideas / stories. Write one per line starting with a dash and an empty box:
`- [ ] Short story title`
Auto Backup → Jira Sync → Create in Jira makes the story and writes the key back.

- [RVG-1] ~~Alert (email/Telegram) on first new incident~~ — **done 2026-10-17**: integrated with Smart Alerting Hub (SAH, port 8512) instead of building its own mailer — `send_hub_alert()` posts on regression (`regression_detected`) and startup (`tool_started`); also watchdogs the hub's own `/health` since the hub can't report its own death
- [RVG-2] ~~Flip `guard.restore_enabled` to 1~~ — **done 2026-10-12**, redesigned as approval-gated (not silent auto-restore): a regression queues as "pending approval" on the dashboard with a diff snippet; Kumar clicks Approve/Reject, nothing written without that click
- [RVG-3] Nested tool-folder discovery (e.g. `Lan Monitor\netmonitor`) — current discover_files() walks 2 levels deep by name pattern, not by git_sync.discover()'s repo-detection logic; confirm it actually finds NETMON's files correctly
- [RVG-4] ~~Per-path pause via the dashboard currently needs the full absolute path typed into the form~~ — **done 2026-10-12**: path selection was already one-click per row (no typing needed); added a duration-preset dropdown (10 min/30 min/1 hr/4 hr/24 hr) replacing the free-number minutes field
- [RVG-5] ~~Incident detail currently just records byte-size deltas~~ — **done 2026-10-12**: added `_diff_snippet()`, shown on the dashboard next to each incident (first divergent line, or where content was truncated)
- [RVG-6] ~~Check/log the Claude device-bridge connection's own drops/flakiness~~ — **moved to `common\COMMON_REQUESTS.md` (2026-10-12)**, not an RVG feature; it's cross-cutting across every tool chat, not something write-guard itself can build or poll for
- [RVG-7] RVG-7: Retry queue for hub alerts — if the POST to Smart Alerting Hub fails (hub down/unreachable), the alert is currently just dropped rather than queued and retried. Deliberate v1 tradeoff (2026-10-17, Kumar's call) since Write Guard's own dashboard/incident log is the backstop regardless of whether the hub POST succeeded. Would need a small local SQLite table (pending alert payload + retry count/backoff) and a retry pass in the poll loop
