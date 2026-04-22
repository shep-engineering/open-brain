# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.23.2] + [brain_v2 2.1.2] - 2026-04-21

### MCP client identification metadata on active_sessions

Populates the previously-unused `metadata` JSONB column with operator-
facing identity info so a human (or automation) inspecting the session
registry can tell at a glance which app opened each row. Triggered by
V2 prod incident 2026-04-20: a row with `host=''` `source='claude'`
left us unable to tell if it was Claude Code, Cursor, a dashboard, or
a stray process without side-channel investigation.

Captured at register time (no schema change — column already existed):

- `client.{name, version}` from the MCP `initialize` handshake's
  `clientInfo` (read via FastMCP's `Context` type — NOT the raw
  `RequestContext`, which doesn't auto-inject). Double-guarded
  None checks.
- `parent.{pid, name, cmdline_head}` from `psutil.Process(getpid).parent()`.
  `cmdline_head` is the first 200 chars with common credential
  patterns redacted (`--token=`, `--api-key=`, `Bearer `, `sk-*`,
  `ghp_*`, etc.) as defense-in-depth.
- `recorded_at` — ISO UTC timestamp.

**Changes:**

- `session_liveness.py` — new `capture_identity_metadata(client_info)`
  + `_scrub_cmdline_secrets(s)` helpers + `_SECRET_PATTERNS` regex list.
- `server.py::boot_session` — optional `context: Context = None`
  parameter (FastMCP auto-injects). Extracts clientInfo, calls helper,
  passes metadata to `db_register_session`.
- `brain_v2/server.py::boot_session_v2` — same treatment, plus
  explicit `store.end_session` call on the `source='auto'` row
  before the real boot row lands (it doesn't get superseded
  automatically — supersede matches `source+cwd+pid` and 'auto' !=
  'claude').
- `brain_v2/server.py::_auto_register_session` — captures parent-only
  metadata (no clientInfo available before the client connects).
- `brain_v2/boot.py::build` — threads `metadata` param through to
  `register_session`.
- `brain_v2/store.py::list_active_sessions` — SELECT now includes
  `metadata`.
- V1 `list_active_sessions` / `_serialize_session` already surface
  metadata via pass-through; no V1 list changes needed.

**Defense-in-depth host fallback (separate commit on same branch):**

Both `db_register_session` (V1) and `register_session` (V2) now
default empty/None host to `socket.gethostname()` internally. The
boot tools already defaulted at the tool layer; this catches any
direct register_session caller that bypasses the tool (tests, scripts,
future code paths). No empty-host rows land in the registry going
forward.

Deviation from plan §5.7: the reviewed plan called for
`raise ValueError("host required")` on empty host. That would be a
breaking API change (17+ V2 tests pass no host), forcing a minor
bump instead of a patch. Chose fallback over rejection — achieves the
same data outcome (no empty-host rows) while keeping the patch
classification valid.

Behavior change on `test_register_empty_host_stays_empty_string`: now
`test_register_empty_host_defaults_to_this_machine`. Stored host
becomes `socket.gethostname().lower()` instead of `''`.

**Tests:** 7 new in `tests/test_session_liveness.py` (minimal,
with_client_info, empty_client_info, parent_exited, cmdline_truncated,
secret_scrubbed, scrub_noop). 3 new in `tests/test_session_registry.py`
(identity_metadata with context, without context, empty-host
fallback). 5 new in `brain_v2/tests/test_session_registry.py::
TestIdentityMetadata` (register_stores, list_returns,
auto_parent_only, auto_row_ended_by_explicit_end, boot_threads_metadata).
1 existing V2 test renamed + updated for new host-fallback behavior.

Patch bumps. No public API surface change — `metadata` field is
additive on the response.

Plan (Windsurf-reviewed): `docs/planning/v23-2-mcp-client-identification.md`.

## [0.23.1] + [brain_v2 2.1.1] - 2026-04-21

### PID reuse false-positive fix

v0.23.0's probe used pid-only existence checks (`psutil.pid_exists`),
which can't distinguish the original registering process from a new
unrelated process that the OS later assigned the same pid. Observed in
production on 2026-04-20: V1 row #13 registered with pid 41712 (a
Claude MCP server that had since exited); Windows reassigned 41712 to
`ChatGPT.exe`; the probe happily reported "alive" and kept bumping
heartbeat_at on a row that represented a dead session.

Fixes the identity check to match (pid, create_time) tuple:

- `scripts/migrate_v8_active_sessions_pid_create_time.py` (new) —
  adds `pid_create_time DOUBLE PRECISION` (nullable) to V1
  `active_sessions`. Idempotent.
- `brain_v2/schema.py` — adds the same column inline in the
  `CREATE TABLE active_sessions` + an idempotent `ALTER TABLE ...
  ADD COLUMN IF NOT EXISTS` for existing V2 DBs.
- `session_liveness.py` — new `get_pid_create_time(pid) -> float | None`
  wrapping `psutil.Process(pid).create_time()`, and
  `verify_pid_identity(pid, stored_create_time) -> bool`. Tolerance
  1s (absorbs psutil sub-ms jitter; far below any realistic pid-reuse
  window). Legacy rows with `NULL` stored_create_time fall back to the
  old `is_pid_alive` check for back-compat.
- `session_liveness.probe_and_mark_ended` + `heartbeat_agent.probe_once`
  both switched from `is_pid_alive` to `verify_pid_identity`.
- `server.py::db_register_session` and `brain_v2/store.py::register_session`
  now capture `pid_create_time` at insert via `session_liveness.get_pid_create_time`.
- `server.py::db_list_active_sessions` and `brain_v2/store.py::list_active_sessions`
  now return `pid_create_time` in their row dicts so the probe has it.
- `scripts/heartbeat_agent.py::_fetch_local_active` — SELECT includes
  `pid_create_time`.

Behavior change on `AccessDenied` during probe: `verify_pid_identity`
returns False (treats as dead), where the old `is_pid_alive` would
have returned True. Same-user processes don't hit this; it's a
defensive choice for the unusual case.

Tests: 7 new in `tests/test_session_liveness.py` (`get_pid_create_time`
happy+edge, `verify_pid_identity` match/mismatch/tolerance/null-fallback/
bad-input, probe reaps impostor, probe preserves legacy NULL row).
4 new in `brain_v2/tests/test_session_registry.py::TestPidReuse`
(register stamps create_time, dead-pid → NULL, boot reaps impostor,
boot preserves legacy NULL). No regressions.

Patch bump on both brains — no public API surface change, just
tighter liveness semantics on the existing probe.

Out of scope: backfilling create_time on pre-existing rows (can't —
we don't know when those processes started). Legacy rows stay NULL
and retain pid-only fallback until they transition out of active
some other way.

## [0.23.0] + [brain_v2 2.1.0] - 2026-04-20

### Session-registry trust fixes across both brains

Closes the "heartbeat agent silently down → registry goes stale → boot
surfaces dead sessions as live siblings" failure mode on V1 and V2.
Evidence at session start on the-workstation: 3 "active" V1 rows (2 pids confirmed
dead via `tasklist`, heartbeat_at frozen 2 days), plus 3 "active" V2 rows
with `heartbeat_at == started_at` on every one — V2 had never bumped
heartbeat_at because it had no external probe code at all.

**V1 additions (v0.23.0):**
- `scripts/migrate_v7_active_sessions_ended_at.py` — idempotent ALTER TABLE
  adding `ended_at TIMESTAMPTZ` (nullable, NULL for legacy rows). Closes
  the only real schema divergence between V1 and V2 `active_sessions`.
  Enables uniform write SQL across both brains.
- `session_liveness.py` module (repo root) — shared by V1 and V2.
  `is_pid_alive`, `normalize_host`, `compute_staleness(rows)`,
  `probe_and_mark_ended(conn, rows, my_host, cap=20)`. Constants
  `STALENESS_WARN_SECONDS` (env `OPEN_BRAIN_REGISTRY_STALENESS_WARN`,
  default 600) and `NULL_PID_TTL_MINUTES` (env `OPEN_BRAIN_SESSION_TTL_MINUTES`,
  default 1440 — 24 hours).
- `server.py::boot_session` — opportunistic same-host pid probe on every
  boot (cap 20 rows, try/except wrapped), plus `registry_staleness_seconds`
  / `registry_trustworthy` / `self_healed_ended_ids` on the OTHER ACTIVE
  SESSIONS section. Warning banner when untrustworthy.
- `server.py::list_active_sessions` — emits `registry_staleness_seconds`
  / `registry_trustworthy` at top level.
- `server.py::db_register_session` — normalizes host to lowercase on
  insert (mirrors V2). Fixes latent case-sensitive match bug.
- `scripts/heartbeat_agent.py` — multi-URL probe. Accepts
  `OPEN_BRAIN_PROBE_URLS` env (comma-separated); default derives the V2
  URL from V1 by swapping `:5432/openbrain` → `:5433/open_brain_v2`.
  Per-URL isolation: one DB down doesn't stop probing of the other.
  `_fetch_local_active`, `_mark_ended`, `_bump_heartbeat`,
  `_sweep_null_pid_stale`, `probe_once` all accept a `url` argument
  (defaulting to `DATABASE_URL` for test back-compat).
- `probe_all_urls(host_filter, urls)` — main loop helper.
- Null-pid TTL sweep in `probe_once` — gated strictly on `pid IS NULL`
  so probeable rows keep the `memory #4929/#3719` probe-based semantics.
  Uses `make_interval(mins => %s)` (not `%s || ' minutes'::interval`
  which silently fails on integer binding).
- `scripts/windows/install-heartbeat-agent.ps1` +
  `uninstall-heartbeat-agent.ps1` + `run-heartbeat-agent.cmd` —
  registers `OpenBrainHeartbeatAgent` scheduled task at logon with
  unlimited runtime + 60s restart on failure. Logs to
  `%LOCALAPPDATA%\open-brain\heartbeat-agent.log`.
- `scripts/windows/open-brain-on.cmd` step 5/5 — prefers
  `schtasks /run OpenBrainHeartbeatAgent` if installed, inline `start /B`
  fallback otherwise.
- Tests: 16 new in `tests/test_session_liveness.py` (normalize_host,
  is_pid_alive, compute_staleness, probe_and_mark_ended). 5 new in
  `tests/test_heartbeat_agent.py` (case-insensitive host match, boot
  self-heal, staleness warning, null-pid under/over TTL). 1 existing
  test updated for new lowercase-host contract.

**V2 additions (brain_v2 2.1.0):**
- `brain_v2/store.py::register_session` — normalizes host via
  `session_liveness.normalize_host`. Empty string stays empty (schema
  column is NOT NULL DEFAULT ''). Mixed-case lowercased.
- `brain_v2/boot.py::build` — opportunistic same-host pid probe on
  siblings (inner try/except so a probe failure doesn't kill the whole
  boot response — Windsurf flagged the outer try/except in
  `boot_session_v2` is too broad). Filters dead rows out of returned
  `other_active_sessions`. Computes staleness via shared helper.
- `BootPayload` — new fields: `registry_staleness_seconds`,
  `registry_trustworthy`, `self_healed_ended_ids`.
- `brain_v2/server.py::boot_session_v2` — defaults host to
  `socket.gethostname()` when caller omits (mirrors V1's
  `server.py:1835`). Prevents empty-host rows like V2 production id #4.
- `brain_v2/server.py::list_active_sessions_v2` — emits
  `registry_staleness_seconds` / `registry_trustworthy` at top level.
- Tests: V2 `test_session_registry.py` extended with 9 new tests across
  3 classes (`TestHostNormalization`, `TestBootOpportunisticProbe`,
  `TestRegistryStaleness`).
- `brain_v2/tests/conftest.py::_ensure_schema` — now takes
  `safety_guard_v2` as an explicit parameter so the ordering dependency
  is unambiguous (Windsurf's minor fix).

**Key design decision:** Option A — one probe process, shared module,
one V1 column migration. Alternative (Option B) would duplicate
`heartbeat_agent.py` as `heartbeat_agent_v2.py` — rejected because
the four "duplicate-logic" failure modes (drift, semantic divergence,
double install, test duplication) are historically well-documented in
open-brain's own memory (#4978, #3719, #5117). Coupling the shared
probe to V2's schema is provisional; if V2 later adopts an event-based
or in-process lifecycle-integrated liveness model, split by copying the
file and flipping the URL env var. Noted in
`scripts/heartbeat_agent.py`'s module docstring.

**One debugging lesson captured:** V1's `active_sessions` had no
`ended_at` column during initial Track 2C work. My sweep SQL
`SET status='ended', ended_at=NOW()` failed silently because `probe_once`
wrapped the sweep in a bare try/except with `verbose=False`. Identified
by running the sweep directly outside the test harness; fixed by the
v7 migration + uniform SQL. Lesson: silent catch-all except blocks hide
the bugs that hurt most.

**Design doc:** `docs/planning/v14-registry-trust-extend-to-v2.md` —
Windsurf-reviewed plan capturing the schema comparison, options
considered, risks, and branch hygiene.

## [2.0.0] - 2026-04-17

### brain_v2 — major revision, production-ready

Ground-up memory architecture redesign. Runs alongside v1 as a separate
MCP server (`open-brain-v2`) on its own Postgres database (`open_brain_v2`,
port 5433). v1 remains untouched and authoritative until explicit cutover.

**Architecture:**
- Four atomic memory types (RULE / FACT / INCIDENT / TASK) replacing
  the single `memories` table. Each type has its own table, retrieval
  policy, and lifecycle.
- Shared `memory_index` table with pgvector HNSW embedding index for
  cross-type headline search without materializing bodies.
- Headline-only boot payload (5 BLOCKER cap, 5 PATTERN cap, 2000 token
  cap). Bodies fetched on demand via `recall_v2`.
- Immutable RULE bodies — supersede-only modification path.
- Five-step write gate: type + severity + headline + atomicity + duplicate
  detection. Merge is an invalid operation for RULE type.
- In-session temporal cache with LRU eviction (cap 100 sessions).
- Session registry with handoff protocol for cross-session continuity.
- Action-item gate: pending items BLOCK writes until acknowledged.

**Observability (ported from v1 + new):**
- `brain_v2/observability.py` — JSONL rotating log (5MB x 5), in-memory
  ring buffer (500 entries), per-tool call counts / error counts / avg+p99
  latency, Windows desktop toast alerts on errors.
- `tool_events` table — persistent telemetry for every MCP tool call
  (reads AND writes) with session_id, duration_ms, success/error.
  Pre-boot events buffered to JSONL on disk, flushed with real session_id
  on `boot_session_v2`. `ON CONFLICT (event_id) DO NOTHING` for crash
  recovery. `session_id` is NOT NULL — guaranteed.
- `metrics_v2` MCP tool — per-tool call counts, error rates, avg/p99 ms.
- `recent_errors_v2` MCP tool — last N errors from ring buffer.
- `health_v2` MCP tool — DB connectivity, Ollama reachability (socket-level
  connect test, not urlopen — respects 5s timeout on Windows), table row
  counts, server uptime, tool list.
- psycopg2 auto-instrumentation via OpenTelemetry (silent fail if not installed).
- Configurable slow-call threshold (`OPEN_BRAIN_V2_SLOW_CALL_MS`, default 10s).

**Maintenance:**
- Ebbinghaus fact decay (configurable halflife + threshold). Hard TTL support.
- Incident archive (90-day default, configurable).
- Rate-limited maintenance hook (`run_maintenance_if_due_v2`).
- Prune with v1 safeguards (30-day floor, 50-row cap, dry_run default).

**Audit findings addressed (3 rounds, 26 total fixes):**
- Transaction atomicity (autocommit removed, conn.commit at boundaries).
- Connection reuse (21s DNS overhead eliminated).
- HNSW index preservation (CTE rewrite reverted to direct ORDER BY).
- Schema constraint alignment (CONTEXT severity removed, migration blocks).
- Missing commits, silent exception swallowing, dead code, redundant
  indexes, unbounded queries, wrong file paths, duplicate imports.

**Config:** All constants env-overridable. `BOOT_TASK_COUNT_CAP`,
`SLOW_CALL_THRESHOLD_MS`, `OLLAMA_EMBED_TIMEOUT` added.

**Tests:** 192 tests against real Postgres + real Ollama, ~3 minutes.
Verbose output + short tracebacks by default (`pyproject.toml addopts`).

**Version:** `2.0.0` per semver — this is a major revision, not an
incremental update to v1.

## [0.22.0] - 2026-04-16

### Added — brain_v2 parallel operation: agent configs + monitoring

v2 is now wired for parallel operation alongside v1 across all agents.

**Status monitoring (per telemetry guardrail):**
- `health_v2()` MCP tool — returns DB connectivity, Ollama reachability,
  per-table row counts, server uptime, tool-call counter, tool list.
  First thing to call when diagnosing issues.
- Structured dual-output logging: every tool call logged with entry/exit
  + duration to both `logs/brain_v2.log` (persistent file) and stderr
  (for MCP client visibility). Auto-instrumented via monkey-patched
  `mcp.tool()` decorator — zero per-function changes, applies to all
  36 tools uniformly.
- Startup log line: `"server.py loading — pid=<pid> log_file=<path>"`.

**Agent configs registered (v2 alongside v1):**
- Claude Code: already registered (via `claude mcp add`). Both v1 + v2
  show `✓ Connected` in `claude mcp list`.
- Windsurf (codeium): `~/.codeium/windsurf/mcp_config.json` — added.
- Windsurf (alt): `~/.windsurf/mcp_config.json` — added.
- Cursor: `~/.cursor/mcp.json` — added.
- VS Code MCP config was empty; left alone (no v1 entry to pair with).

**v2 startup scripts (do NOT touch v1):**
- `scripts/windows/brain-v2-up.cmd` — starts `open-brain-v2-db` container
  on port 5433, waits for health check, reports status.
- `scripts/brain-v2-up.sh` — same for Linux/WSL/macOS.

**v1 stays authoritative.** v2 runs alongside for agents to exercise.
No cutover. Phase 4 (task leases) and v1 canonicalization deferred
per Dave's instruction.

**Tool count:** 35 → **36**. Tests: 192 existing still pass (not re-run
this session since no store/schema changes; only server wiring + logging).

## [0.21.0] - 2026-04-16

### Added — brain_v2 v1 parity complete: forget_many + unsupersede + prune + brain_startup_reminder

Closes the FINAL v1 tool-parity gap. v2 now has functional equivalents
for every v1 tool except `load_skill` (explicitly deferred — requires
skill_trigger schema, tracked as a separate feature).

**4 new MCP tools:**
- `forget_many_v2(kinds, memory_ids, reason, source)` — batch
  soft-delete via parallel lists. Partial success allowed; returns
  per-item outcome (forgotten / already_forgotten / not_found).
- `unsupersede_v2(old_id, source)` — reverse a rule supersession.
  Restores severity, reactivates memory_index row, clears
  superseded_by. Corrector rule stays active (caller can forget it
  for full undo). Mirrors v1's unsupersede exactly.
- `prune_v2(days, min_access, dry_run)` — HARD delete of stale
  memories with v1 safeguards: 30-day floor, 50-row cap, dry_run
  default True, pinned never pruned, rules never pruned. Targets
  facts (by access_count), archived incidents, and done/stale tasks.
  Every deletion audited via v2_audit BEFORE the row is removed.
- `brain_startup_reminder_v2()` — mandatory session-start system
  message, adjusted for v2 tool names. Same structure as v1.

**Prune safeguards (mirrors v1 exactly):**
- `OPEN_BRAIN_V2_PRUNE_MIN_DAYS` (default 30) — hard floor
- `OPEN_BRAIN_V2_PRUNE_MAX_DELETE` (default 50) — hard cap per call
- Rules are NEVER pruned (immutable in v2 design)
- Pinned memories are NEVER pruned

**Bug fix:** `store.prune()` now returns `{dry_run: False, deleted: 0}`
when dry_run=False but no candidates match (previously returned a
dry_run-shaped response, causing KeyError on `result["deleted"]`).

**Tests (24 new in `test_parity_final.py`):**
- TestForgetMany (5): batch, partial-not-found, idempotent, empty,
  mixed-kinds
- TestUnsupersede (4): reverses-chain, corrector-remains-active,
  not-superseded-raises, missing-raises
- TestBrainStartupReminder (3): structured message, mentions
  boot_session_v2, mentions action_item ack
- TestPruneSafeguards (12): below-min-raises, dry-run-default,
  hard-delete-execution, pinned-never-pruned, rules-never-pruned,
  access-count-filter, archived-incidents-eligible, non-archived-
  not-pruned, done-tasks-eligible, open-tasks-not-pruned, hard-cap-
  per-call (monkeypatched), dry-run-reports-would-total

**Full regression:** 192/192 passing against real Postgres + real
Ollama (~61 min full-suite runtime, no mocks).

**Tool count:** 31 → **35**. Total v2 test count: 168 → **192**.

**v1 tool parity is now COMPLETE** (except load_skill, deferred).

## [0.20.0] - 2026-04-16

### Added — brain_v2 v1 tool parity: annotate / rate / pin / unpin / scratch / brain_checkpoint

Closes the last major v1-to-v2 tool-parity gap. Agents moving from v1
to v2 now have equivalent day-to-day affordances.

**8 new MCP tools, each modeled exactly on the v1 equivalent:**

| v1 tool | v2 equivalent | Notes |
|---|---|---|
| `annotate` | `annotate_v2(kind, memory_id, note, clear)` | Set/clear/read modes |
| `rate` | `rate_v2(kind, memory_id, direction)` | 'up' or 'down' |
| `pin` | `pin_v2(kind, memory_id)` | Project-scoped only (global refused) |
| `unpin` | `unpin_v2(kind, memory_id)` | No-op success on never-pinned |
| `scratch_set` | `scratch_set_v2(key, value)` | In-process dict; ephemeral |
| `scratch_get` | `scratch_get_v2(key)` | Returns {key, value, found} |
| `scratch_list` | `scratch_list_v2()` | Returns {count, entries} |
| `brain_checkpoint` | `brain_checkpoint_v2(action, source, context, project)` | Per-source cooldown |

**Behavioral parity with v1:**
- `pin_v2` enforces the "no pinning global memories" rule (returns
  an error if the memory has an empty project).
- `brain_checkpoint_v2` uses an in-process cooldown keyed by
  `(source, action)`. Default 5 min (env `OPEN_BRAIN_V2_CHECKPOINT_COOLDOWN`).
- Scratchpad state is in-process only, intentionally not persisted
  (mirrors v1's `_scratch` dict).

**Schema additions to `memory_index`** (via `ALTER TABLE … ADD COLUMN
IF NOT EXISTS`, backward-compatible):
- `annotation TEXT`
- `upvotes INTEGER NOT NULL DEFAULT 0`
- `downvotes INTEGER NOT NULL DEFAULT 0`

**Tests (25 new in `test_parity.py`):**
- TestAnnotate (3): set, clear, missing-raises
- TestRate (5): upvote, downvote, accumulation, invalid direction,
  missing-raises
- TestPinUnpin (6): project-scoped success, global-rejected, unpin,
  unpin-noop, pin-fact, missing-raises
- TestScratchpad (4): set+get, get-missing, list, overwrite
- TestBrainCheckpoint (5): source-required, action-required,
  surfaces-blocker-rules, cooldown-skips-repeat, different-actions-
  not-skipped
- TestIntegration (2): annotation persists across connections, vote
  counts persist across connections

**Full regression:** 168/168 passing against real Postgres + real
Ollama (no mocks). Full-suite runtime: ~53 minutes.

**Tool count:** 23 → 31. Total v2 test count: 143 → **168**.

## [0.19.0] - 2026-04-16

### Added — brain_v2 operational completeness: forget + stats + list_recent

Closes the "v1 tool parity for day-to-day ops" gap. Before this
release, v2 had no mechanism to remove a bad memory, inspect the
corpus, or review recent activity — all three are table stakes for a
memory system meant to run alongside v1.

**3 new MCP tools:**
- `forget_v2(kind, memory_id, reason, source)` — soft-delete. Sets
  `memory_index.active=FALSE` + records `forgotten_at`, `forgotten_reason`,
  `forgotten_by`. Body preserved for audit. Idempotent.
- `stats_v2(project)` — corpus stats: per-kind totals + active/forgotten
  breakdown, rule severity counts, task status breakdown, incident
  archive counts, pending action items, active sessions.
- `list_recent_v2(limit, days, kind, project, include_forgotten)` —
  recent memory_index rows (headline-only). Filters on kind, project,
  age in days. Hard cap 200.

**recall_v2 enhancement:** now surfaces a forgotten banner when
recalling a forgotten memory. Body still returned (audit semantics)
but with `forgotten: {forgotten_at, forgotten_reason, forgotten_by,
banner}` metadata so agents know not to trust it as current truth.
Mirrors v1's superseded-memory banner pattern.

**Schema additions to memory_index:**
- `forgotten_at TIMESTAMPTZ` (NULL = not forgotten)
- `forgotten_reason TEXT`
- `forgotten_by TEXT`

Added via `ALTER TABLE … ADD COLUMN IF NOT EXISTS` for backward
compatibility with pre-0.19 v2 databases.

**Tests (20 new):**
- `TestForget`: deactivation, idempotency, missing-id error, excluded
  from search, recall still returns body, kind validation
- `TestStats`: empty DB, per-kind counts, severity breakdown, forgotten
  separation, task status, project filter
- `TestListRecent`: newest-first ordering, limit, kind filter,
  forgotten exclusion/inclusion, days filter, hard cap, headline-only

All passing against real Postgres.

**Tool count:** 20 → 23. Total v2 test count: 143.

## [0.18.0] - 2026-04-16

### Added — brain_v2 maintenance scheduling decision + rate-limited hook path

Closes the P2 follow-up "scheduling decision" listed in v0.17.0.

**Decision (see `brain_v2/MAINTENANCE_SCHEDULING.md`):** MCP-hook,
rate-limited in code — not external cron. Rationale: services aren't
always up on Dave's workstation (Open Brain OFF shortcut), cron would
race service availability, MCP hook aligns with v2's process-lifecycle
liveness model, maintenance is pure SQL (no infra-cost risk), and we
don't want to add another daemon.

**Implementation:**
- New `maintenance_runs` table (id, started_at, finished_at, report
  JSONB, source). One row per actual run; skipped calls do NOT insert.
- `MaintenanceReport` dataclass extended with `skipped`, `skipped_reason`,
  `last_run_at` fields for the skip path.
- `run_all(conn, source)` now records start + finish in maintenance_runs.
- `run_if_due(conn, hours=24.0, source)` — short-circuits if the last
  successful run was within `hours`. Returns a skipped report instead.
  Safe to fire on every boot via a PostToolUse hook.
- `run_maintenance_if_due_v2(hours=24.0)` MCP tool.

**Optional Claude Code hook** (user configures in `~/.claude/settings.json`):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "mcp__open-brain-v2__boot_session_v2",
        "hooks": [{"type": "mcp", "tool": "mcp__open-brain-v2__run_maintenance_if_due_v2"}]
      }
    ]
  }
}
```

With the 24h default, runs at most once per day per MCP-client host.

**Tests:** 6 new tests in `TestRunIfDue` class (no-prior / within-window
/ after-window / skipped-no-record / custom-window / counts-on-real-run).
All passing. Full regression: 117 original + 6 new = 123 tests.

**Docs updated:**
- `brain_v2/MAINTENANCE_SCHEDULING.md` (new) — full rationale + hook config
- `docs/planning/brain-v2-gap-analysis.md` — all P0–P3 closed, known
  limitations documented (§7): heuristic classification caveat,
  scheduling trade-off, cutover remains out of scope.

**Tool count:** 19 → 20.

## [0.17.0] - 2026-04-16

### Added — brain_v2 P2 gaps closed: fact decay + incident archive

Closes Gaps 5 and 6 from `docs/planning/brain-v2-gap-analysis.md`.
Completes Windsurf synthesis §4.6 (decay by type).

**New module `brain_v2/maintenance.py`:**
- `decay_facts(conn, halflife_days, threshold)` — Ebbinghaus decay.
  Score = `2^(-Δdays / halflife)` where Δdays is days since last access
  (falls back to created_at if never accessed). Facts below threshold
  are deactivated in `memory_index`; previously-deactivated facts that
  recovered above threshold are reactivated. Hard-TTL facts whose ttl
  is past are expired separately and never reactivate.
- `archive_incidents(conn, archive_days)` — soft-archive after N days
  of no access. Flips `archived=TRUE` on the incident row AND deactivates
  the corresponding `memory_index` entry. Already-archived incidents
  are skipped (idempotent).
- `run_all(conn)` — runs both jobs, returns a unified `MaintenanceReport`.

**3 new MCP tools:**
- `run_maintenance_v2()` — unified trigger.
- `decay_facts_v2()` — run only the fact-decay job.
- `archive_incidents_v2()` — run only the incident-archive job.

All three return affected id lists so callers can audit outcomes.

**New config:**
- `OPEN_BRAIN_V2_FACT_DECAY_THRESHOLD` (default 0.1) — score below
  which a fact is deactivated. At halflife=7 and threshold=0.1,
  deactivation happens at ~23 days no-access.

**Testing:** 17 new tests in `test_maintenance.py` covering fresh /
old / reactivate / custom-halflife / custom-threshold / past-TTL /
future-TTL / TTL-exclusive / recent-incident / old-incident / already-
archived / custom-archive-days / empty-DB / idempotency / rules-not-
affected / tasks-not-affected. All passing against real Postgres.

**Decay does NOT delete.** It deactivates the `memory_index` row only.
Bodies preserved for `recall()` and audit. Un-decay happens automatically
on the next maintenance run after a deactivated fact is recalled.

**Tool count:** 16 → 19. Total v2 test count: 117.

**All gap-analysis P0-P2 items are now closed.** Remaining: P3
(cosmetic cleanup) and the maintenance scheduling decision (MCP
trigger only vs. external cron) — deferred until usage signals it.

## [0.16.0] - 2026-04-16

### Added — brain_v2 P1 gaps closed: session registry + handoff protocol

Closes Gaps 3 and 4 from `docs/planning/brain-v2-gap-analysis.md`. v2 now
has sibling-session awareness and continuity across reboots.

**New schema tables:**
- `active_sessions` — per-source session registry (source, project, cwd,
  pid, host, current_task, started_at, heartbeat_at, ended_at, status,
  metadata). Process lifecycle is authoritative; NO timer-based TTL (v1
  v0.14.0 lesson). New row with same (source, cwd, pid) ends the prior
  active row (supersede on reboot).
- `handoffs` — session-to-session continuity notes, 2000-char hard cap,
  optionally linked to the writing session.

**boot_session_v2 changes:**
- Accepts optional `cwd` / `pid` / `host` args.
- Registers a new `active_sessions` row on every boot.
- Returns `session_id`, `other_active_sessions`, `handoff_source` in
  the payload.
- Auto-populates `handoff` from the latest handoff for the project
  (excluding the caller's own session). Explicit handoff arg wins.

**4 new MCP tools:**
- `list_active_sessions_v2(project, exclude_self)` — surface siblings.
- `update_active_task_v2(task, session_id)` — bump current_task + heartbeat.
- `end_session_v2(handoff, session_id, source)` — clean exit; can write
  a handoff in the same call.
- `write_handoff_v2(content, source, project, session_id)` — mid-session
  checkpoint.

**Testing:** 27 new tests in `test_session_registry.py` covering register /
end / list / update_task / handoff write+read / project filter / session
exclusion / supersede-on-reboot / auto-populated handoff / explicit
handoff override / register=False dry-run. All passing against real
Postgres. Regression run on prior suites: 27/27 still pass.

**Also fixed:** Gap 7 (dead `params` variable in store.search_headlines)
was already cleaned up in v0.15.1 but the commit rolled it into this
release.

**Tool count:** 12 → 16 (new: list_active_sessions_v2,
update_active_task_v2, end_session_v2, write_handoff_v2).

**Still open (tracked for future work):**
- Gap 5: Fact decay job (schema-only, no runtime)
- Gap 6: Incident 90-day archive job (schema-only)

## [0.15.0] - 2026-04-15

### Added — Open Brain v2 bifurcation (Phase 1 scaffold)

Parallel v2 memory architecture per `docs/planning/windsurf-memory-architecture-synthesis.md`
(best-of-breed synthesis) + `docs/planning/infra-cost-addendum.md`
(Ollama-runtime falsifiable check).

Code lives in a new package `brain_v2/` on branch `feat/brain-v2-bifurcation`.
V1 (`server.py`, `openbrain` DB on port 5432) is untouched. V2 runs alongside:

- **New Postgres container** `open-brain-v2-db` on port **5433**, DB `open_brain_v2`
  (`docker-compose.v2.yml`). Full physical isolation from v1's container.
- **New MCP server** `open-brain-v2` with tool namespace `mcp__open-brain-v2__*`.
  Registration snippet at `brain_v2/mcp_registration_snippet.json`.
- **Pre-v2 backup** of the live brain at `backups/brain-pre-v2-20260415.sql`
  (28 MB) per guardrail #827.

**Phase 1 contracts (Windsurf §4.3 + §4.4):**

- Four atomic memory types: `RULE`, `FACT`, `INCIDENT`, `TASK` — each in its
  own table with type-specific retrieval policies. No unified `memories` table.
- Shared `memory_index` holds the embedding + headline projection so boot
  and search can rank headlines without materializing bodies.
- Write gate (`brain_v2/write_gate.py`): (1) type declared,
  (2) atomicity (≤400 words, no stacked `GUARDRAIL 20xx-` markers),
  (3) headline ≤15 words, (4) cosine >0.75 duplicate detection against
  same-kind active entries, (5) supersede-only for RULE bodies.
- `remember_rule` refuses to merge — returns `DuplicateHit` so the caller
  routes to `supersede_rule_v2`. RULE bodies are immutable after creation.
- Boot payload: headline-only, 5 BLOCKER cap, 5 PATTERN task-relevance cap,
  2K token total cap, truncate TASKs → PATTERNs → BLOCKERs if over.
  WORKING CONTEXT regenerated from the `task` arg at every boot; not stored.
- In-session temporal cache for recency boost + link-traversal boost on recall.
- Audit log `v2_audit` for every INSERT / SUPERSEDE / UPDATE.

**Falsifiable infra check** (`brain_v2/infra_check.py`) per infra-cost-
addendum §4: verifies no `METADATA_LLM_MODEL` reload line appears in
`ollama.log` between a `boot_session` and a `remember_rule` call. Current
run: PASS (token estimate 12, no Qwen eviction, nomic-embed only).

**Test coverage** (`brain_v2/tests/`, 39 tests, all passing against real
Postgres + real Ollama):

- `test_write_gate.py` — 18 tests, all 5 gate steps
- `test_boot_payload.py` — 11 tests, cap enforcement + truncation order +
  headline-only + project scoping + superseded-rule exclusion
- `test_recall_search_cache.py` — 10 tests, body fetch + access-count bump +
  headline-only search + temporal cache + task lifecycle

**Out of scope for this commit** (future phases per Windsurf §6):
Phase 3 decay beyond session cache, Phase 4 parallel-session coordination,
Phase 5 compaction, canonicalization of v1's merged blockers into v2 atomic
form, and `~/.claude/settings.json` MCP registration (permission-blocked
during this session; snippet provided in `brain_v2/mcp_registration_snippet.json`
for manual paste).

## [0.14.0] - 2026-04-15

### Changed — Session registry: replaced TTL with signoff + external heartbeat agent

v0.13.0 shipped a 5-minute heartbeat TTL. That was a timer-based
expiry mechanism, which memory #3719 and now memory #4929 explicitly
flag as wrong: a session doing long non-brain work (Edit, Bash,
WebFetch) stopped bumping its implicit heartbeat and vanished from
the registry. Shep: "TIMEOUTS DO NOT WORK. Use a signoff instead.
Consider a heartbeat agent."

v0.14.0 replaces the model entirely:

1. **Explicit signoff** — server.py now registers `atexit` +
   SIGTERM/SIGINT handlers that call `db_end_session` for every
   cached `session_id` on clean shutdown (MCP stdio close,
   Ctrl+C, `kill`). Covers the common path.

2. **External heartbeat agent** — new `scripts/heartbeat_agent.py`
   runs as a separate process, periodically (`OPEN_BRAIN_HEARTBEAT_INTERVAL`,
   default 60s) queries `active_sessions WHERE status='active'`,
   and for each row pid-probes via `psutil.pid_exists`. Rows whose
   process is gone → marked `status='ended'`. Also bumps
   `heartbeat_at` on confirmed-alive rows so observers can see
   when liveness was last verified. Catches SIGKILL / power-loss
   cases that atexit can't. One agent per host; filters by
   `active_sessions.host` column.

3. **Supersede on reboot** — if a new `boot_session` arrives from
   the same `(source, cwd, pid)` tuple as an existing active row,
   the prior row is marked `ended` before the new one inserts.
   Handles client reconnect / restart cleanly.

4. **Default pid / host in `boot_session`** — if the caller omits
   them, server defaults to `os.getpid()` and
   `socket.gethostname()`. The owning server.py process IS the
   session lifetime authority, so probing its pid is the correct
   signal. Agents don't have to plumb these through.

Gone:
- `db_sweep_dead_sessions` — time-based sweep; deleted.
- `_heartbeat_source` / implicit heartbeat in `_record_search` —
  the mechanism that silently failed sessions doing long non-brain
  work; deleted.
- `OPEN_BRAIN_SESSION_TTL_MINUTES` env var — unused; removed.

New:
- `OPEN_BRAIN_HEARTBEAT_INTERVAL` env var (default 60s) — agent
  probe cadence.
- `db_supersede_previous_session(source, cwd, pid)` db helper.
- `_signoff_all_sessions()` + `_install_session_signoff_hooks()`
  in server.py.
- `scripts/heartbeat_agent.py` standalone daemon — run with
  `--once` for a single probe pass, `--interval N` to override,
  `--host H` to filter, `-v` for verbose logs.
- 5 new tests in `tests/test_heartbeat_agent.py` covering probe
  alive / dead / other-host / null-pid / heartbeat-bump.

Tests reflect the new model: `tests/test_session_registry.py`
dropped TTL / implicit-heartbeat tests, added supersede + signoff
tests (12 tests total). Full regression green.

**Migration note:** schema is unchanged (v6 migration from v0.13.0
still current). No data migration required. First run of v0.14.0
against an existing DB will leave stale v0.13.0 rows as-is; the
heartbeat agent cleans them on its first cycle.

**Deploy:** launch `python scripts/heartbeat_agent.py` from your
Open Brain startup (dashboard.py, `open-brain-on.cmd`, or systemd).
Without the agent running, sessions from crashed/SIGKILLed clients
stay `active` in the registry forever — cosmetic noise, not a
correctness issue.

**Required dependency:** `psutil` (v7.2.2 or later). Install via
`pip install psutil`. This is listed in `requirements.txt` and needed
by `scripts/heartbeat_agent.py` for `pid_exists()` checks. Missing
psutil causes import failure and 500 errors on startup.

---

### Added — Action-item compliance gate

Memories carry `action_items` in their metadata. Before v0.14.0 those
were advisory — the booting session could read them and choose to
ignore them, which is exactly the failure mode that produced the
2026-04-14 Netflix SRE/DDoS-vs-CI/CD miss (memory #3719). The brain
surfaced the action_item "Update flashcard app for correct role
(SRE/Edge/DDoS EM)"; a sibling session built a CI/CD flashcard app
anyway.

v0.13.0 fixed the *visibility* gap (sibling sessions). v0.14.0 closes
the *compliance* gap: write-set tools are now **BLOCKED** until the
booting session explicitly engages with each surfaced action_item.

**Behavior**

On `boot_session`, the server scans memories surfaced in RECENT
HISTORY (last 7 days) and KNOWN ISSUES & CORRECTIONS for
`metadata.action_items`. Each item is pushed onto a per-source
pending list, deduped by text, capped at
`OPEN_BRAIN_ACTION_ITEM_GATE_MAX` (default 10). The response adds a
new `pending_action_items` field and an `ACTION ITEMS PENDING`
context section.

Pinned guardrails are skipped intentionally — they're rules, not
pending tasks. Their action_items are usually "how to apply"
instructions that surface on every boot already.

**Blocked write set** (when `pending_action_items` non-empty):
`remember`, `capture_context`, `supersede`. Reads (`search`,
`recall`, `list_recent`, `list_active_sessions`) stay open so the
session can investigate before acknowledging.

**New tool — `acknowledge_action_item(source, memory_id, text,
decision, reason="")`**. `decision` ∈ `{will_execute, already_done,
not_relevant}`. `reason` is required for `already_done` and
`not_relevant` — explain *why* you're dismissing. Idempotent: acking
an item not in the pending list returns success with `removed: 0`.

**Audit log** at `logs/action_item_acks.jsonl` (append-only JSONL)
records every ack: timestamp, source, memory_id, text, decision,
reason. Not rotated by the server; no dashboard in v0.14.0.

**No schema change.** Pure in-memory state (`_pending_action_items:
dict[source, list[...]]`). Re-ack required on reboot — matches
session-registry TTL semantics.

**Backwards compatibility:** boots that surface no action_items are
unchanged (no ACTION ITEMS PENDING section, no blocking, no new
response fields populated).

**Tool count:** 25 → 26.

**Tests:** 14 in `tests/test_action_item_gate.py`. Full regression
green (belief + pinned + skills + session_registry + action_item_gate).

**Lineage:** follow-up to v0.13.0. Memory #3719 escalated the rule
that action_items are BLOCKING — this release enforces it
architecturally rather than behaviorally. Does NOT cover non-brain
tools (Edit, Bash, WebFetch); that's Phase 4 hook-installer scope.

**Design doc:** `docs/planning/ACTION_ITEM_GATE_DESIGN.md`.

---

## [0.13.0] - 2026-04-14

### Added — Session registry (parallel-session visibility)

Open Brain now tracks **live MCP-client sessions** so a booting session
can see what other sessions are currently working on. Closes the
parallel-session blind spot that caused the 2026-04-14 Netflix prep
mix-up: a sibling Claude session was running interview-prep work while
this one was somewhere else; neither knew, and the brain had no
mechanism to surface either to the other.

**Why:** memory #3719 flagged the gap explicitly after the incident.
Before this release, `boot_session` returned static memories only.
Nothing in the schema represented "which agents are live right now,
working on what." A guardrail memory can't fix that — the sibling is
architecturally blind.

**New table** (`scripts/migrate_v6_session_registry.py` — idempotent,
reversible):
  * `active_sessions(id, source, project, cwd, pid, host, current_task,
    started_at, heartbeat_at, status, metadata)`
  * 3 indexes: `(status, heartbeat_at)` for TTL sweeps,
    `(project, status)` for cross-session lookups,
    `(source, cwd, status)` for dedup + implicit heartbeat.

**TTL rule:** rows with `status='active'` AND `heartbeat_at < now() -
OPEN_BRAIN_SESSION_TTL_MINUTES` (default 5 min) are swept to
`status='ended'` inline on `boot_session` / `list_active_sessions`.
5 minutes matches the Anthropic prompt-cache TTL — a session that
hasn't pinged in that window has lost its warm cache anyway.

**Modified tool — `boot_session`.**
- New optional args: `task`, `cwd`, `pid`, `host`.
- Registers a new `active_sessions` row on every call.
- Sweeps dead rows first.
- Adds an **OTHER ACTIVE SESSIONS** section to the returned context
  listing all sibling live sessions in the same project.
- Returns `session_id` in the response for use by `update_active_task` /
  `end_session`.

**New tool — `update_active_task(source, task, session_id=0)`.**
Updates `current_task` on the caller's session and bumps heartbeat.
Call when the user pivots or a task completes.

**New tool — `list_active_sessions(source, project="",
exclude_self=True)`.** Read-only snapshot. Sweeps dead rows first.

**New tool — `end_session(source, session_id=0)`.** Clean-shutdown
path. Optional (TTL handles crashes) but reduces noise.

**Implicit heartbeat:** every MCP tool call from a booted source
refreshes `heartbeat_at` via the existing `_record_search` hook — no
extra round-trips for the caller. Failures are silent.

**Agent-side contract (load-bearing):** the booting agent MUST
surface OTHER_ACTIVE_SESSIONS to the user when any listed session is
in the same project or a related cwd, before starting overlapping
work. Same treatment as action_items on memories.

**Tool count:** 22 → 25.

**Lineage:** elevated from "next-session candidate" to PRIORITY-1
after a repeat of the parallel-session failure hit 2026-04-14
(sibling told user "I have no knowledge of what you're doing" when
the user had explicitly said a sibling Claude session existed).
Separate from the action_item-compliance failure (same incident, same
day) that memory #3719 owns — Session Registry fixes the visibility
gap; action_item compliance is a behavioral failure a registry can't
patch.

---

## [0.12.0] - 2026-04-14

### Added — Skills layer (conditional-load guardrails)

Pinned memories no longer have to load at every session boot. Memories
can now carry a `skill_trigger` JSONB payload that makes them load
*only* when a keyword matches a query, or when explicitly requested via
`load_skill(name)`. Always-on rules (workflow rules, "never commit to
main") can opt back in via `always_on: true`.

**Why:** `boot_session` returned all 26 pinned guardrails for
open-brain on every call — ~15–20 KB of instructions injected
regardless of the task. Per the HumanLayer/Chroma harness-engineering
research, heavy always-on prompt steering competes with the agent's
"instruction budget" and causes forgetting under pressure. The skills
layer shrinks the boot payload to just the always-on set and surfaces
the rest on demand.

**New tool — `load_skill(name, source, project="")`.** Explicit lookup
of a skill by its unique `skill_trigger.name`. Active-only by default.
Respects `skill_trigger.projects` scope (empty = global, populated =
scoped).

**Modified tools:**
- `remember(..., skill_trigger=None)` — optional tag turning the new
  memory into a skill.
- `search` — a new layer bumps skill-triggered memories to the top of
  the result set when any keyword substring-matches the query,
  flagged via `via_skill_trigger: "<name>"`. Capped by
  `OPEN_BRAIN_SKILL_TRIGGER_MAX` (default 5).
- `boot_session` (via `db_get_pinned`) — now excludes skill-tagged
  memories unless `skill_trigger.always_on` is true. Also filters
  superseded memories (belief-revision follow-through fix).

**Schema additions** (`scripts/migrate_v5_skills_layer.py` —
idempotent, reversible):
  * `skill_trigger JSONB DEFAULT NULL` on `memories`
  * `idx_memories_skill_trigger` — partial GIN index on rows where
    `skill_trigger IS NOT NULL`

**Shape:**
```json
{
  "name": "ollama-shutdown-graceful",
  "keywords": ["ollama", "shutdown", "graceful"],
  "projects": [],
  "always_on": false
}
```

**Backwards compatibility:** Existing memories (`skill_trigger = NULL`)
behave exactly as before. No data migration required for pre-existing
pinned guardrails; opt-in via `supersede` with a new `skill_trigger`
on the corrector.

**Test coverage:** 13 tests in `tests/test_skills_layer.py` — schema
sanity, round-trip, boot filter (3 scenarios), search auto-match (3
scenarios), `load_skill` (4 scenarios), belief-revision interaction.

**Tool count:** 21 → 22.

**Lineage:** Phase 1 of the 6-phase Brain Harness Plan. Unblocks
Phase 4 (hook installer) to read `skill_trigger` and fire skills on
tool-name triggers.

---

## [0.11.0] - 2026-04-14

### Added — Belief revision (memory supersession)

Open Brain now treats memories as **revisable beliefs** rather than
immutable facts. Two new MCP tools — `supersede` and `unsupersede` —
let an agent (or user) mark an existing memory as corrected by a
newer one. The old memory is preserved (audit trail intact) but
filtered out of default `search` / `recall` / `list_recent` results
so agents only see current truth.

**Why:** memory `#3663` had asserted two contradicting facts about
whether the Open Brain ON script starts the MCP server. Future agents
recalling `#3663` got mixed signals and acted on whichever fragment
matched their query surface. Same class of failure — at coarser
granularity — produced the 2026-04-14 roadmap-drift incident
(memory `#1126`). The fix is structural: explicit supersession via
the schema, not memory-and-discipline.

**Schema additions** (`scripts/migrate_v4_belief_revision.py` —
idempotent, reversible):
  * `superseded_by_id INTEGER REFERENCES memories(id) ON DELETE SET NULL`
  * `superseded_at TIMESTAMPTZ`
  * `superseded_reason TEXT` (required on every `supersede` call —
    no silent overwrites)
  * `idx_memories_active` partial index on `id WHERE superseded_by_id IS NULL`
  * `idx_memories_superseded_by` partial index on `superseded_by_id`

**New MCP tools:**
  * `supersede(old_memory_id, new_content, reason, source, type_override?,
    project?, inherit_pinned?)` — creates the new memory through the
    full pipeline (embedding, metadata, secrets-filter, dedup) and
    writes its ID to `old.superseded_by_id`. Refuses to chain — if
    you try to supersede an already-superseded memory, the error
    points you at the latest in the chain.
  * `unsupersede(memory_id, source)` — clears the supersession
    metadata. The corrector memory survives; call `forget()` on it
    separately for full undo.

**Modified MCP tools (backwards-compatible — all default to current
behavior, opt-in to history via new flag):**
  * `search(..., include_superseded=False)`
  * `list_recent(..., include_superseded=False)`
  * `recall(memory_id)` — when called on a superseded memory by ID,
    now returns its content AS RECORDED plus a `banner` field +
    `superseded_by_id` / `superseded_at` / `superseded_reason`
    metadata pointing at the corrector. Audit semantics intact —
    you can still see what you used to believe.

**Dedup correctness fix:** `db_find_duplicate` and `db_find_related`
now exclude superseded memories from their similarity search.
Otherwise re-storing content similar to a corrected (now-superseded)
memory would false-match against the stale version and skip the
write — defeating the whole point.

**Pinning inheritance** is opt-in via `inherit_pinned=True` on
`supersede()`. By default, supersedeing a pinned guardrail does NOT
auto-promote the new memory to pinned status — explicit opt-in
prevents accidental promotion of a non-guardrail.

**Schema safety** (per guardrail #827):
  * Backup taken before any schema change: `backups/brain-pre-belief-revision-20260414-102607.sql` (3326 memories baseline)
  * Worked on isolated branch (`feat/brain-belief-revision`)
  * Migration verified idempotent (re-run is no-op) and reversible
    (drop columns, no orphaned data) on the test database before
    touching production
  * 18/18 belief-revision tests pass; full test suite still green
  * Production migration applied only after Shep's design review +
    explicit approval (5 product decisions: tool naming, recall
    behavior, pinning inheritance, auto-supersession, dashboard
    surfacing — see `docs/planning/BELIEF_REVISION_DESIGN.md`)

**Tool count:** 19 → 21.

## [0.10.0] - 2026-04-14

### Added — Dashboard single-instance guard

`dashboard.py` now detects an already-running instance at startup, brings
that window to the foreground, and exits 0. Covers every launch path
(desktop shortcut, `.cmd` wrapper, CLI) because the check lives inside
the Python process — not in a wrapper script the shortcut bypasses.

Implementation uses `psutil.process_iter` to find any python process
whose argv has a `Path.name == 'dashboard.py'` AND resolves to our
on-disk file. Critical Windows-specific detail: the scan must exclude
the **process ancestor chain**, not just `os.getpid()` —
`.venv\Scripts\python.exe` is a launcher that re-execs as the base
interpreter, so both processes live during startup and both carry the
same `dashboard.py` argv. Without ancestor-skip, the guard falsely
matched its own launcher parent and exited silently. Verified E2E with
a real two-click test on the desktop shortcut.

### Added — Dashboard GPU/compute-device selector (ticket #5074)

A "GPU" dropdown in the dashboard titlebar lets the user pick which
CUDA device Ollama binds to. Useful on machines with multiple GPUs
where one card needs to stay free for other workloads (gaming, other
ML jobs).

- `nvidia-smi -L` populates the dropdown with friendly names
  ("RTX 5090", "RTX 3080 Ti", "Auto (both)").
- Selection persists to `logs/dashboard-config.json` as `gpu_device`.
- `infrastructure.resolved_cuda_visible_devices()` reads the config
  and overrides `CUDA_VISIBLE_DEVICES` on every subsequent ollama
  spawn — including spawns from the launcher (`.cmd`/`.ps1`) flow.
- "Apply" button does a graceful shutdown→respawn via the v0.9.0
  pipeline (model unload → Ctrl+Break → `ensure_ollama` with new env).
- Graceful degradation: if `nvidia-smi` is unavailable or reports no
  GPUs, the selector hides itself.

### Added — Mobile-UX evaluator harness (Tier 1)

`tests/mobile/test_demo_nav.py` — Playwright + Chromium harness that
builds the docs site, serves it locally, and drives the reveal.js
demo deck through Pixel 5 device emulation in both portrait and
landscape. Asserts: page loads, chevrons render, taps advance/regress
slides, horizontal swipes navigate, swipe-up does NOT navigate (the
specific bug Shep hit), and the mobile chrome survives a portrait→
landscape rotation.

Pre-fix run reproduced 3 known bugs + 3 the diagnostic surfaced;
post-fix run is **20/20 PASS**. Adds `playwright>=1.58` to
`requirements.txt`.

This is the harness-engineering Phase-2 evaluator pattern from
`docs/planning/BRAIN_HARNESS_PLAN.md`, applied to the mobile demo
specifically. Approach scales to other UI work.

### Added — `docs/references.md`

Wired into mkdocs nav. Links the Oracle "Agent Memory" article
(reference rather than redistributing the PDF), the Anthropic
harness-engineering posts used in research, and the MCP spec.

### Fixed — Mobile reveal.js demo: chevrons broken in portrait, missing in landscape

Earlier mobile-nav attempt (commit 58d25d3) shipped without device-
level testing and broke in three specific ways:

- **Right chevron didn't work** because `Reveal.isLastSlide()` returns
  `true` on slide 0 of a 12-slide deck in this Reveal build, and my
  CSS set `pointer-events: none` on the resulting `disabled` class.
  Fixed by using `Reveal.getIndices().h` math directly and renaming
  the visual class to `at-edge` — opacity-only, never blocks taps.
- **No chevrons in landscape** because `@media (max-width: 768px)`
  misses landscape phones (often 900+px wide), AND
  `const isMobile = matchMedia(...)` was computed once at page load
  and never updated on orientationchange. Fixed by switching the
  CSS gate to `@media (pointer: coarse)` (covers both orientations
  + tablets) and dropping the JS `isMobile` constant entirely so
  CSS handles all orientation logic via live media queries.
- **Swipe-up advanced** because the slide content was taller than
  the viewport, so swipe-up was actually browser-level scrolling
  (not Reveal navigation). Fixed by `body { overflow: hidden;
  overscroll-behavior: none }` under `pointer:coarse` so vertical
  scroll can't happen.

Plus: chevron handlers now bind both `touchstart` (with
`preventDefault`+`stopPropagation` to keep Reveal's gesture handler
from also processing the same touch) AND `click` (fallback for
keyboard / non-touch). Added an always-on slide counter
("3 / 12") fixed bottom-center as a permanent affordance. Safe-area-
inset positioning so notched devices don't clip the chrome.

### Added — `docs/planning/BRAIN_HARNESS_PLAN.md`

Internal design doc reframing Open Brain through Anthropic's April-2026
harness-engineering body of work. Six proposed phases (Skills layer →
Hook installer → Evaluator-for-checkpoint + Event log → Handoff →
Audit) with effort/risk/value estimates and a recommended sequence.
Filtered from the public build via `exclude_docs`.

## [0.9.1] - 2026-04-13

### Fixed — Scrub hardcoded personal paths from tracked tree

Pre-flight remediation for shep-engineering publication. All Category A (~18)
and Category B (3) hardcoded-path leaks identified in `docs/planning/SCRUB.md`
are removed from tracked, non-planning files. Behaviorally identical for the
author's current installation; portable for anyone else cloning the repo.

- **Path relativization** — launcher scripts now resolve the repo root from
  their own location rather than hardcoding a hardcoded absolute path:
  `start.ps1`, `stop.ps1` via `$MyInvocation.MyCommand.Path`;
  `scripts/windows/open-brain-{on,off,dashboard,sse-proxy}.cmd` and
  `backup-brain.cmd` via `%~dp0..\..`;
  `scripts/windows/create-desktop-shortcuts.ps1` via `$PSScriptRoot`.
- **Docstring / comment scrubs** — `server.py`, `dashboard.py`, `AGENTS.md`,
  `scripts/ensure-stack.sh` replace hardcoded install paths with the
  `<OPEN_BRAIN_ROOT>` placeholder.
- **Cross-project leak fixes**:
  - `telemetry.py` — docstring no longer names a sibling project directory
  - `scripts/make_icon.py` — rewritten to parameterize the source PNG via
    `sys.argv[1]` (default `assets/brain-source.png`) + resolve the output
    path relative to the repo root; previously hardcoded a path in a separate unrelated tool's output directory
  - `.windsurf/workflows/scaffold-AI.md` — generic `<your-archetypes-dir>/AI/...`
- **`scripts/windows/open-brain-off.cmd` rewritten** to delegate to the v0.9.0
  `infrastructure.bring_down()` instead of its previous taskkill-heavy flow —
  now behaviorally identical to clicking "Close + Stop Open Brain" from the
  dashboard. Also fixes pre-v0.9.0 overreach: no longer kills Docker Desktop
  (respects other containers) and no longer calls the dead-code `ollama stop`
  with no arg.
- **`scripts/windows/open-brain-on.cmd` version banner** — was stuck at
  `v0.4.1 / 12 tools` (5 releases stale), now `v0.9.0 / 19 tools`.
- **`.task-markers/` added to `.gitignore`** + 6 March-2026 tracked markers
  untracked via `git rm --cached`. One of them leaked an a personal dev path
  path in its test-note field.

### Added — `scripts/deploy-docs.ps1` + `scripts/deploy-docs.sh`

Wrapper around `mkdocs gh-deploy` that forces the committer identity to
`David Sheppard <davidasheppard@outlook.com>` for the deploy commit.
Without this, `mkdocs gh-deploy` silently publishes the gh-pages commit
under whatever `git config user.email` is active locally — which would
be `degailen@gmail.com` for anyone working on `degailen/main` and result
in a committer-identity leak on the public shep-engineering repo.

Supports `--orphan` / `-Orphan` to force a clean-history orphan push
when history needs to be wiped (e.g., after a prior identity leak).

### Fixed — Committer-identity leak on `shep-engineering/open-brain` gh-pages

Prior `mkdocs gh-deploy` invocations this session (commits `1e9bd23`,
`e2b8748`, and 2 prior degailen-authored commits going back to 2026-03-25)
were published with the `degailen <degailen@gmail.com>` identity. Force-
pushed a single clean orphan commit authored as David Sheppard. The leak
exposure happened (public record at time of push) but the current remote
history is clean.

## [0.9.0] - 2026-04-13

### Added — Genuine graceful ollama shutdown (Ctrl+Break via Win32)

Replaces `taskkill /F` as the primary ollama stop mechanism with a real
OS-level graceful signal delivered through Win32's
`GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pgid)`. Ollama's Go server
handles the signal, closes in-flight requests, tears down model contexts,
and exits on its own — typical exit time **<1.5s after signal** vs. the
instant-but-dirty force-kill previously used.

**Why:** the prior implementation was functional but halfbaked for a
shippable product. SIGKILL-equivalent termination left model weights in
VRAM until process teardown, bypassed ollama's own cleanup path, and
emitted no evidence that shutdown was clean vs. forced.

**How the signal gets delivered** (documented for future spelunkers):

1. **Spawn flags changed.** `DETACHED_PROCESS` REMOVED — it forbids the
   child from having a console, making Ctrl+Break impossible to deliver.
   Replaced with `CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP` plus
   `STARTUPINFO.wShowWindow = SW_HIDE` so the child has a real but
   invisible console and is the leader of its own process group.
2. **PID persistence.** `ensure_ollama` writes the spawned PID to
   `logs/ollama.pid`. `_stop_ollama` runs in a different Python process
   (dashboard close dialog → bring_down thread) so it needs a file handoff
   to find the process-group leader.
3. **Signal delivery via helper subprocess.** In-process
   `FreeConsole`/`AttachConsole` would detach the main dashboard from
   its own console and potentially kill us from our own signal. Instead,
   `_win_send_ctrl_break` runs a small `python -c` helper that:
   `FreeConsole()` → `AttachConsole(ollama_pid)` →
   `SetConsoleCtrlHandler(NULL, TRUE)` → `GenerateConsoleCtrlEvent(1, pid)`.
4. **Target the pgid, not zero.** `GenerateConsoleCtrlEvent(CTRL_BREAK, 0)`
   broadcasts to every process sharing the console — including the
   helper, which then dies with `STATUS_CONTROL_C_EXIT` (0xC000013A).
   Passing the target PID as `dwProcessGroupId` routes the signal only
   to ollama's group. Helper exits cleanly with rc=0.
5. **Empirical proof of graceful.** Exit-within-grace-window (≤10s) is
   the evidence: if ollama ignored the signal we'd fall through to the
   `/F` fallback; we don't. Ollama's Go runtime doesn't emit a
   distinctive shutdown log line, so log-scanning for proof was dropped.

**Force-kill fallback** is still wired but only triggers if the graceful
path fails to land an exit inside 10s — verified not to trigger in
normal operation.

### Added — Process ownership model

`_stop_ollama` now distinguishes between "we spawned this ollama" and
"someone else spawned this ollama" (e.g., the Windows Ollama desktop app
has a watchdog at `ollama app.exe` that auto-respawns `ollama.exe`). The
distinction is driven by the presence of `logs/ollama.pid`:

- **Owned** (pid file exists + pid alive): unload models → graceful
  Ctrl+Break → poll exit → optional force-kill.
- **Externally managed** (no pid file, or dead pid): unload models and
  walk away. Killing would be futile (the watchdog respawns) and rude
  (other consumers of the shared ollama server get disrupted).

`ensure_ollama`'s fast path (API already responding) now explicitly
clears any stale pid file so the external-management branch fires on
the next stop.

### Added — `ollama ps`-driven model unload

Before any termination step, `_stop_ollama` calls `ollama ps`, parses
loaded models, and issues `ollama stop <model>` per model to release
VRAM cleanly. This is the documented graceful unload — correct whether
or not we own the server process.

### Fixed — `stop_all` no longer kills Docker Desktop

Prior behavior (`taskkill /IM "Docker Desktop.exe" /F`) was overreach:
users often run unrelated containers (other project DBs, redis, dev
environments) that would be collateral damage. `_stop_docker_desktop`
is removed entirely; `stop_all` now only stops `ollama` (when owned)
and the `open-brain-db` container. Docker Desktop and every other
container stay up.

### Fixed — Dead-code `ollama stop` with no args

`_stop_ollama` previously called `subprocess.run(["ollama", "stop"])`
with no argument, which errors with `accepts 1 arg(s), received 0` and
does nothing. Removed. Real graceful unload is the `ollama ps` → per-
model-`ollama stop <name>` loop above.

### Fixed — `UnicodeDecodeError` parsing `ollama ps` output

Subprocess calls reading ollama's CLI output now pass
`encoding="utf-8", errors="replace"` — `ollama ps` can emit bytes that
fail cp1252 decoding (e.g., `0x8f` in model-tag rendering).

### Fixed — Dashboard shutdown daemon-thread race

`_run_off_script` previously spawned a `daemon=True` thread to run
`bring_down()` and then immediately destroyed the main window. Window
destruction ended the Tk mainloop → Python exited → daemon thread was
killed mid-shutdown. Symptom: `startup.log` showed `stop:start` with
no follow-up `stop:info` entries, and ollama + db stayed running after
"Close + Stop". Fix: `_run_off_script` now swaps the close dialog into
a "Stopping Open Brain services…" progress panel, runs `bring_down` on
a **non-daemon** thread with a progress callback updating the label,
polls the thread via `after()`, and destroys both windows only after
the thread has exited. UI stays responsive during the 1–5s shutdown.

### Changed — `logs/ollama.pid`

New runtime artifact. Created on `ensure_ollama` when we spawn the
server; removed on successful graceful stop or force-kill. Presence
determines ownership in `_stop_ollama`.

### Empirical verification (2026-04-13)

| Path | Result |
|---|---|
| Unit tests (`tests/test_infrastructure.py`) | 18/18 pass |
| Python-API shutdown (`infrastructure._stop_ollama` direct call) | Ctrl+Break delivered, ollama exited in **0.53–1.44s** |
| Full GUI flow (desktop shortcut → splash → main UI → Close + Stop) | Graceful chain complete in 5.05s total: signal sent, ollama exited gracefully 1.44s later, db stopped, Docker Desktop untouched, dashboard closed |
| Docker Desktop process count pre/post shutdown | **unchanged** (3 → 3) |

## [0.8.0] - 2026-04-13

### Changed — Dashboard launcher rewritten in pure Python

Replaces the fragile `.cmd` launcher chain
(`dashboard.cmd → pythonw → dashboard.py → Popen(cmd /c on.cmd) →
start /B ollama-serve.cmd → ollama serve` — 5 process boundaries) with
a single Python module, `scripts/infrastructure.py`, that dashboard.py
calls directly.

**Why:** every attempted patch to the `.cmd` chain this cycle (4+) uncovered
a new failure mode: cmd quote-parsing, `%~dp0` in if/else blocks, `::`
comments inside `( ... )`, `start /B` redirection applying to START
instead of the spawned command, `start` default-`/K` keeping cmd alive,
inherited stdout pipe held by detached children, file-sharing conflicts
on shared append log, `printf '-...'` dash-as-flag errors. The chain was
fragile by construction; rebuilding in Python eliminates the entire class.

**Empirical verification (2026-04-13):**
- Launched `ollama serve` via the new detach pattern
  (`stdin=DEVNULL, stdout=<file>, stderr=STDOUT, close_fds=True,
  creationflags=DETACHED_PROCESS|CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP`).
  Parent returned in 0ms; ollama ready at t=2.7s; parent exited;
  ollama still alive and serving.
- Launched `dashboard.py` via pythonw. Splash window appeared, drove
  `infrastructure.bring_up()` in a worker thread, completed in ~4s,
  main window title flipped to `"Open Brain Dashboard"`. No hang.
- 17/17 unit tests pass (mocked subprocess).

### Added

- `scripts/infrastructure.py` — new module. Public API:
  `Infrastructure.ensure_docker/ensure_db/ensure_ollama/stop_all` +
  `bring_up(on_progress=...)` + `bring_down(on_progress=...)`.
  Different launch patterns per process type:
  - Short-lived CLI (`docker info`/`docker start`/`docker stop`/`taskkill`):
    `subprocess.run(capture_output=True, timeout=N)`.
  - Long-lived console (`ollama serve`): `subprocess.Popen` with the
    full detach flag set described above.
  - GUI (`Docker Desktop.exe`): `Popen` with stdio redirected to
    DEVNULL, no detach flags (GUI self-registers tray).
  - Readiness polling: `docker info`, `psycopg2.connect(connect_timeout=N)`,
    `urllib.request.urlopen(ollama_api, timeout=N)`, each in a bounded
    poll loop with clear timeout/failure paths.
- `tests/test_infrastructure.py` — 17 mocked tests covering fast-path,
  cold-start, timeout, launch-failure, and `stop_all` semantics for
  each component.
- Structured `Progress` dataclass for splash/log communication. Every
  bring_up/bring_down step emits `{step, status, detail, elapsed_s}`
  entries to both `logs/startup.log` and the UI callback.

### Changed — dashboard.py

- `launch_open_brain()` removed. Replaced by `infrastructure.bring_up`
  invoked from `StartupSplash` worker thread.
- `StartupSplash.__init__` no longer takes a `proc` argument. Takes
  only `master` and `on_ready`. Internal worker thread calls
  `infrastructure.bring_up(on_progress=self._on_progress)` and handles
  success/failure via `self.after(0, ...)` UI marshalling.
- `Dashboard._run_off_script` replaced: now calls
  `infrastructure.bring_down()` in a daemon thread instead of spawning
  `cmd /c open-brain-off.cmd` in a new console.
- `Dashboard._start_service` Ollama path uses
  `infrastructure.Infrastructure().ensure_ollama()` for consistent
  detach semantics with startup.

### Kept (for users who prefer CLI-first workflow)

- `scripts/windows/open-brain-on.cmd` — still works. Dashboard no
  longer invokes it. Comment explains the dashboard uses
  `infrastructure.py` directly now.
- `scripts/windows/open-brain-off.cmd` — still works for desktop
  shortcut users. Dashboard uses `infrastructure.bring_down()` instead.
- `scripts/windows/open-brain-dashboard.cmd` — unchanged; it just
  invokes `pythonw dashboard.py` with a wmic single-instance check.

### Migration

No API changes. MCP schema unchanged. The launcher internals are
purely implementation. Users who only click the desktop shortcut see
a cleaner splash (structured progress instead of streamed cmd output)
and — critically — no more splash hang.

## [0.7.0] - 2026-04-12

### BREAKING

- **`source` is now REQUIRED on `boot_session`, `search`, `remember`,
  `capture_context`, and `brain_checkpoint`.** Previously optional with a
  default of `""`. Omitting it now raises `TypeError` at the Python layer
  (MCP schema rejects the call); passing `source=""` returns a JSON error
  with `blocked_by: "source_required"` and a remediation message.

### Why

Session-compliance is tracked per-source via `_session_tracker[source]`.
Empty source falls back to a `"_global"` bucket. When agents
inconsistently passed `source` (e.g. `search()` without, then
`remember(source="claude")`), the search updated `"_global"` while the
store incremented `"claude"` — two different buckets. The compliance
counter for `"claude"` grew unbounded despite the agent having polled the
brain, producing spurious `BLOCKED: N stores since last search` errors.
Agents forgetting to plumb `source` is too consistent a failure mode to
rely on discipline alone; enforcing it at the MCP schema layer makes the
footgun impossible.

### Changed

- `server.py::_source_required_error` helper emits canonical
  `blocked_by: source_required` errors with a remediation hint.
- `remember(content, source, ...)`: `source` moved to required positional.
- `search(query, source, ...)`: `source` moved to required positional.
- `boot_session(source, project="")`: `source` moved to required positional.
- `brain_checkpoint(action, source, ...)`: `source` moved to required positional.
- `capture_context(context, source, project="")`: `source` moved to required positional.
- All five tool bodies reject empty-string `source` at top of `try` block
  with a clear remediation error.

### Improved

- Compliance error messages now tell the agent exactly how to fix the call:
  "Call search(query='...', source='{source}') to reset the per-agent
  counter."
- `docs/tools.md`: updated Core Tools table to mark `source` required with
  a BREAKING callout at the end of the section.
- Agent prompts (`prompts/claude-desktop.md`, `prompts/cursor-rules.md`,
  `prompts/windsurf-rules.md`, `prompts/generic-system-prompt.md`):
  explicit required-on-every-call language + mention of
  `blocked_by: source_required`.

### Tests

- `tests/test_session_compliance.py`: added 6 new tests covering
  required-positional enforcement (TypeError on omit) and empty-string
  rejection on all five tools. Replaced 4 obsolete tests that asserted
  "source-optional" behavior.
- `tests/test_pinned_memories.py`: updated 5 `search()` calls to pass
  `source="test"`.
- Full suite: 101/101 pass.

### Migration

- Any caller (agent, CLI script, test) that was calling these tools
  without `source` must be updated. The JSON error returned on
  empty-string calls includes the exact fix.
- MCP clients pass parameters by name (JSON), so positional-arg reorder
  does NOT affect MCP callers — only direct Python callers.

## [0.6.1] - 2026-04-08

### Fixed

- **OTel OTLP spam**: Gated gRPC trace exporter behind `OTEL_OTLP_ENABLED=1` env var (opt-in).
  Without a collector running, `BatchSpanProcessor` retried forever, flooding `server-crash.log`.
- **localhost DNS resolution**: Changed all default URLs from `localhost` to `127.0.0.1` in
  `server.py`, `telemetry.py`, and `open-brain-on.cmd`. On Windows, `localhost` resolves to
  both `::1` (IPv6) and `127.0.0.1`, causing double connection attempts and slower failures.
- **Startup race condition**: `open-brain-on.cmd` now waits for PostgreSQL to accept connections
  (up to 30s readiness loop) before launching `server.py`. Previously, the server started
  immediately after `docker start`, causing migration failures on cold boot.

### Added

- **Self-healing agent fallback**: All agent prompt files (`prompts/`, `CLAUDE.md`) now instruct
  agents to auto-start infrastructure when the brain is unavailable, retry once, then degrade
  gracefully. Agents never freeze or loop when the brain is down.

## [0.6.0] - 2026-04-03

### Added

- **Cognitive Architecture (Phases 1-5)**: Complete session boot and enforcement system:
  - `boot_session(project, source)` MCP tool: loads pinned guardrails, project architecture,
    recent session history (7 days), and known issues/corrections at session start. Stores
    context in working memory scratchpad.
  - `brain_checkpoint(action, context, project, source)` MCP tool: searches brain before
    risky actions (editing infrastructure, database, deployment, server, or config files).
    Returns relevant guardrails and memories with similarity scores. 5-minute cooldown per topic.
  - `detect-correction.sh` UserPromptSubmit hook: scans user messages for correction patterns
    (ALL CAPS, profanity, "wrong", "stop", "don't") and injects directive to save the
    correction as a pinned guardrail immediately.
  - `require-brain-boot.sh` PreToolUse hook: blocks all non-brain tools until `boot_session`
    has been called in the session.
  - `require-brain-checkpoint.sh` PreToolUse hook: BLOCKS editing risky files until
    `brain_checkpoint` has been called. Hard enforcement.
  - `require-brain-save.sh` PreToolUse hook: blocks git commit until brain has been written to.
  - `auto-boot-brain.sh` SessionStart hook: injects boot directive as additionalContext
    before the AI sees its first user message.
- **Auto-pin guardrails**: `remember()` with `type_override="guardrail"` and a project
  automatically pins the memory so it surfaces in every future `boot_session`.
- **Correction repeat detection**: `db_find_repeated_corrections()` finds guardrail memories
  with >70% cosine similarity. `stats()` returns `correction_repeat_rate`. `boot_session`
  surfaces "REPEATED CORRECTIONS (CRITICAL)" section when detected.
- **REST API endpoints**: `/boot` and `/checkpoint` for HTTP clients (ChatGPT Desktop, etc.)
- **`updated_at` column**: Auto-set by PostgreSQL trigger on every UPDATE. `list_recent()`
  and dashboard sort by `GREATEST(created_at, updated_at)` so merged memories surface as
  recent activity.
- **Dashboard timezone localization**: All UTC timestamps converted to local time for display.
  Storage remains UTC.

### Changed

- **Dashboard**: Removed all WSL calls. Windows-native process checks via `wmic`/`taskkill`.
  30-second fallback refresh. Filtered stale Ollama logs. Fixed "last restart: unknown".
  Server log shows only MCP tool calls (not DB query noise).
- **`_check_compliance()`**: Now requires boot before any store operation (not just search).
  Tracks `_booted_sources` per session.
- **All agent prompts**: Updated `windsurf-rules.md`, `cursor-rules.md`, `claude-desktop.md`,
  `generic-system-prompt.md` with boot-first mandatory instructions.
- **Prune safeguards**: Hard minimum 30 days, max 50 deletions per call. Prevents
  accidental mass deletion.

### Security

- **Audit log**: `memories_audit` table with row-level trigger captures every INSERT, UPDATE,
  DELETE with full row data. Added to `setup_db.py` for new installs.
- **Backup scripts**: Daily `pg_dump` scripts for bash and Windows cmd.

## [0.4.5] - 2026-03-30

### Added

- **Claude Code enforcement hooks**: Two hook scripts that guarantee Claude
  searches Open Brain before taking any action.
  - `hooks/brain-reminder.sh` (UserPromptSubmit) -- injects mandatory reminder
  - `hooks/require-brain-search.sh` (PreToolUse) -- blocks all tools until
    `mcp__open-brain__search` is called. Read-only tools are whitelisted.
- **Auto-install via `wire`**: Running `python server.py wire` now automatically
  copies hooks to `~/.claude/hooks/` and registers them in
  `~/.claude/settings.json`. Works on Windows, macOS, and Linux.
- Documentation for hooks in `docs/getting-started/wiring-agents.md`.
- `hooks/README.md` with manual install instructions.

## [0.4.4] - 2026-03-25

### Fixed

- **Dashboard hang on launch**: DNS resolution of `localhost` blocked indefinitely
  after customtkinter + OpenTelemetry initialization on Windows. Replaced all
  `localhost` references with `127.0.0.1` in `dashboard.py`.
- **Dashboard OTLP exporter freeze**: Full `telemetry.initialize()` started an OTLP
  gRPC exporter that retried against a missing `localhost:4317` collector, blocking
  threads. Dashboard now uses a JSONL-only span exporter (no network).
- **Observability strip showing zeros**: `fetch_obs_metrics()` was reading from
  `open-brain.jsonl` (legacy) instead of `otel-traces.jsonl` (active OTel traces).
  Now reads MCP tool call spans from OTel traces.
- **MCP status "Not responding"**: `check_mcp()` relied on WSL `pgrep` which fails
  when the server runs as a native Windows process. Now uses OTel trace recency as
  primary check (active span within 5 minutes = online).
- **OTLP exporter timeout in server**: Added `timeout=2` and `export_timeout_millis=3000`
  to the OTLP `BatchSpanProcessor` in `telemetry.py` so a missing collector never
  blocks the MCP server.

### Changed

- **Event-driven dashboard refresh**: Replaced 10-second polling with PostgreSQL
  `LISTEN/NOTIFY`. A `memories_notify` trigger fires `pg_notify('memories_changed')`
  on INSERT/UPDATE/DELETE. Dashboard refreshes data widgets instantly on change.
  Service health checks (Ollama, MCP) moved to a separate 60-second interval.
- **Windows taskbar icon**: Set `SetCurrentProcessExplicitAppUserModelID` and
  `SetProcessDpiAwareness` so the dashboard renders its own icon in the taskbar
  instead of the generic Python icon.
- **Logs excluded from git**: Added `logs/` to `.gitignore` and removed tracked
  log files. Runtime logs should never be committed.

## [0.4.3] - 2026-03-24

### Added

- **Smart UPDATE/MERGE on store**: When a new memory is semantically related but not an
  exact duplicate (similarity in `[MERGE_LOWER_THRESHOLD, DEDUP_THRESHOLD)`), the LLM
  decides whether to `ADD`, `MERGE`, `REPLACE`, or `SKIP`:
  - `MERGE` — LLM writes a single combined memory preserving all unique facts from both
  - `REPLACE` — new memory contradicts/supersedes the existing one; old is overwritten
  - `SKIP` — new memory is essentially a repeat; stored as-is to existing
  - `ADD` — new memory is distinct enough to store separately (default / fallback)
  - `action` field in `remember()` / `capture_context()` responses now includes `"merged"`
    and `"replaced"` in addition to existing `"stored"`, `"updated"`, `"skipped"`.
- **Background consolidation thread**: When `OPEN_BRAIN_CONSOLIDATION_INTERVAL > 0` and
  `METADATA_LLM_MODEL` is set, a background thread periodically scans all memories and
  merges/replaces related ones using the LLM. Disabled by default (`0`).
- `OPEN_BRAIN_MERGE_LOWER_THRESHOLD` env var (default `0.70`) — controls the lower bound
  of the smart-merge gray zone.
- `OPEN_BRAIN_CONSOLIDATION_INTERVAL` env var (default `0`, disabled) — seconds between
  background consolidation passes.
- Set `METADATA_LLM_MODEL=qwen2.5:14b` as the default metadata/merge LLM (local Ollama).

## [0.4.2] - 2026-03-24

### Added

- **Working memory scratchpad**: Three new MCP tools (`scratch_set`, `scratch_get`, `scratch_list`)
  provide an ephemeral key-value store that lives only for the current server session. Agents use
  this to track in-session context (current task, active file, reasoning state) without polluting
  long-term memory. Cleared automatically on server restart.
- **Bi-temporal modelling**: `memories` table now has two time axes:
  - `valid_time` — when the event actually happened (user-supplied via `remember(valid_time=...)`,
    defaults to `NOW()`). Backfilled to `created_at` for existing rows.
  - `transaction_time` — when Open Brain learned about it (always `NOW()` on insert, never changes).
  - `search()` gains an `as_of` parameter: pass an ISO 8601 timestamp to retrieve only memories
    whose `valid_time` is on or before that date. Ask "what did I know as of March 1st?"
- `remember()` gains a `valid_time` parameter for backdating memories to when an event occurred.
- Indexed both `valid_time` and `transaction_time` for efficient range queries.
- Both migrations are idempotent and run automatically on startup.

## [0.4.1] - 2026-03-24

### Changed

- **Uptime-based decay** replaces calendar-time decay. Decay now accumulates only
  while the server is actually running -- gaps when the server is off (overnight,
  vacations, power outages) do not count against memory freshness. A month-long
  break costs you nothing.

### Added

- `server_uptime` table (single-row counter) tracks cumulative active seconds
  across all server sessions. Auto-created on first start.
- `last_accessed_uptime` column on `memories` table. Records the uptime counter
  value at the moment a memory is accessed. Used by the decay formula instead of
  wall-clock timestamps.
- Background flush thread writes the running uptime total to the DB every
  `OPEN_BRAIN_UPTIME_FLUSH_INTERVAL` seconds (default 60). At most this many
  seconds can be lost on a hard kill / power outage.
- `OPEN_BRAIN_UPTIME_FLUSH_INTERVAL` env var documented in `.env.example`.

## [0.4.0] - 2026-03-24

### Added

- **Recency decay scoring**: search results now apply exponential time-decay based on
  `last_accessed`, so stale memories naturally fade. Configurable via `OPEN_BRAIN_DECAY_LAMBDA`
  (default `0.005`; set to `0` to disable).
- **Hybrid vector + full-text search**: combines cosine similarity with PostgreSQL `ts_rank`
  for better retrieval of exact names, dates, and project codes. Auto-migrates the `fts`
  tsvector column and GIN index on first start when enabled. Configurable via
  `OPEN_BRAIN_HYBRID_WEIGHT` (default `0.3`; set to `0` for pure vector).
- **Time-scoped search**: `search()` now accepts `since_days` and `until_days` params for
  temporal queries (e.g. "what did I decide last week?").
- **Two new memory types** from the CoALA cognitive architecture taxonomy:
  - `procedural` -- workflow rules, conventions, non-negotiables, how-to knowledge
  - `episodic` -- specific past events, session recollections, "last time X happened"
- `OPEN_BRAIN_DECAY_LAMBDA` and `OPEN_BRAIN_HYBRID_WEIGHT` documented in `.env.example`
- Updated `docs/architecture/memory-model.md` with new types and retrieval features

## [0.3.0] - 2026-03-18

### Added

- **Session compliance tracking**: server tracks when each source last called
  `search()`. If `remember()` or `capture_context()` is called without a recent
  search, a `compliance_warning` field is injected into the response. Storage is
  never blocked -- the warning is a nudge, not a gate.
- `search()` now accepts an optional `source` parameter for compliance tracking
- `COMPLIANCE_WINDOW` env var (default 300s / 5 minutes) controls staleness
- 19 tests for compliance tracking in `tests/test_session_compliance.py`

## [0.2.0] - 2026-03-18

### Added

- **Pinned memories (guardrails)**: memories can be pinned to a project so they
  always appear at the top of search results, regardless of query similarity.
  Use this for workflow rules, conventions, and guardrails that agents must see.
- New MCP tools: `pin(memory_id)` and `unpin(memory_id)`
- New memory type: `guardrail` for organizing workflow rules
- `_format_search_entry()` helper for consistent result formatting
- `db_get_pinned()` and `db_set_pinned()` database helpers
- Migration script `scripts/migrate_v3_pinned.py` (idempotent)
- 23 tests for pinned memory behavior in `tests/test_pinned_memories.py`

### Changed

- `search()` now prepends pinned memories for the queried project (pinned do
  not count against the `limit` parameter)
- `prune()` now skips pinned memories (both dry-run count and actual delete)
- `recall()` and `list_recent()` now include `pinned: true` in output
- `db_list_recent` and `db_get_by_id` queries now include the `pinned` column

### Security

- **Secrets filter** (`secrets_filter.py`): blocks API keys, tokens, private
  keys, and database passwords from being stored in the brain. Applied before
  embedding (prevents leaking to models) and inside `db_store_deduped()` as
  safety net. Two modes: reject (default) or redact.
- 30 tests for secrets filter in `tests/test_secrets_filter.py`

## [0.1.0] - 2026-03-17

### Added

- Initial release: MCP server with 11 tools
- PostgreSQL + pgvector semantic memory storage
- Ollama local embedding (nomic-embed-text, 768 dims)
- LLM-based metadata extraction (qwen2.5:32b)
- Auto-capture via `capture_context` with LLM decomposition and smart batching
- Semantic search, recall, annotate, rate, prune, forget tools
- Project scoping for multi-project memory isolation
- Deduplication via cosine similarity threshold
- Wire CLI for auto-configuring MCP clients
- Cross-platform support (Windows, Linux, macOS, WSL)
