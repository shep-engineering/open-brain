# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
