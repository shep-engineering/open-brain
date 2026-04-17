# brain_v2 Maintenance Scheduling — Decision

**Date:** 2026-04-16
**Status:** Decided
**Scope:** `run_maintenance_v2` / `decay_facts_v2` / `archive_incidents_v2`

---

## Decision

**MCP hook (post-boot, rate-limited in code) — not external cron.**

Primary mechanism: `run_maintenance_v2` MCP tool, called on-demand or
via a Claude Code hook configured in `~/.claude/settings.json`.

Secondary mechanism: a rate-limit built into the tool itself so the
hook can fire liberally (e.g., on every boot) without running real
work every time. The tool tracks `last_run_at` and skips the actual
decay/archive SQL if the last run was within the rate-limit window
(default 24h).

---

## Rationale

Open Brain v2 runs on a Windows developer workstation, not a
persistent server. Specifically:

1. **Services are not always up.** Dave explicitly stops Open Brain
   via the "Open Brain OFF" desktop shortcut. An external cron job
   would wake up on schedule and either (a) fail loudly when the v2
   Postgres container is down, or (b) silently skip — both worse than
   "run opportunistically when the stack is already up."

2. **MCP hook aligns with v2's liveness model.** v2 treats process
   lifecycle as authoritative (v1 v0.14.0 lesson: timer-based TTL was
   wrong). A boot-time hook fires exactly when the stack is provably
   up — no need to race scheduler against service availability.

3. **Multi-client host.** Dave runs Claude Code + Windsurf + Cursor
   concurrently. An external cron would need single-writer semantics
   across all three. A boot-time hook per MCP client, rate-limited,
   naturally distributes the work: first boot of the day wins, others
   no-op.

4. **No new process to manage.** v1 already runs a heartbeat_agent.py
   daemon (session-registry liveness). Adding a second daemon for
   maintenance means two extra processes for Dave to start/stop/debug.
   The MCP-hook path adds zero processes.

5. **Maintenance is infra-cheap.** `decay_facts` and
   `archive_incidents` are pure SQL — no embeddings, no LLM calls.
   Running them on boot adds negligible latency (tens of ms on the
   typical corpus), so the infra-cost-addendum §4 constraint (no
   metadata-LLM eviction) is not at risk.

## What was rejected

**External Windows Task Scheduler / cron job.**

Rejected for the reasons above, primarily (1) services-not-always-up
and (4) don't-add-more-daemons. A task-scheduler approach would also
require Dave to configure Windows Task Scheduler separately from the
brain_v2 repo, adding install-time friction.

**Purely manual invocation via MCP tool.**

Rejected because low-activity projects would never run maintenance.
Per guardrail #3719, "agents will skip steps under pressure" — we
should not rely on discipline for hygiene that benefits every user
silently.

## Implementation — already in place

- `run_maintenance_v2` MCP tool (unified trigger)
- `decay_facts_v2` (decay only)
- `archive_incidents_v2` (archive only)

## Implementation — added with this decision

- `maintenance_runs` table: `id`, `started_at`, `finished_at`, `report JSONB`
- `run_all(conn, if_due_hours=None)` — if `if_due_hours` is set and
  the most recent row's `started_at` is within the window, returns a
  `MaintenanceReport` with a `skipped=True` marker and zero counts.
- `run_maintenance_if_due_v2(hours=24)` MCP tool — wraps `run_all`
  with the rate-limit check. Safe to fire on every boot.

## User configuration — optional hook

Users who want automatic maintenance add this to
`~/.claude/settings.json` (or equivalent MCP-client config):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "mcp__open-brain-v2__boot_session_v2",
        "hooks": [
          {
            "type": "mcp",
            "tool": "mcp__open-brain-v2__run_maintenance_if_due_v2"
          }
        ]
      }
    ]
  }
}
```

With the default 24h rate limit, this runs at most once per day per
MCP-client host, regardless of how many boots happen.

For users who prefer not to install the hook: nothing breaks. The
manual `run_maintenance_v2` MCP tool still exists. The worst outcome
of no maintenance is gradual growth of inactive-index-but-still-in-
memory_index rows — search will still work, just less efficiently over
time.

## Revisit criteria

Reopen this decision if any of the following become true:

- v2 is deployed as a persistent multi-user service (no longer a local
  dev tool).
- Maintenance becomes expensive enough that boot-time invocation
  meaningfully impacts latency (monitor via `maintenance_runs.finished_at
  - started_at`).
- A specific failure mode surfaces where the rate-limit window is
  wrong (e.g., a fact with a 4-hour TTL doesn't expire fast enough
  under a 24h maintenance cadence).
