# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
