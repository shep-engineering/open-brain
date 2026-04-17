"""Open Brain v2 schema.

Four atomic memory types per Windsurf synthesis §4.1:
    RULE      — behavioral constraint. Immutable body. Supersede-only.
    FACT      — project/domain fact. Access-based decay (Ebbinghaus).
    INCIDENT  — episodic record. Soft archive after 90 days no-access.
    TASK      — cross-session obligation. Lifecycle state only.

Active session state (the "WORKING CONTEXT" block from §4.1) is
ephemeral — regenerated at boot from task args. It is NOT stored.

The v2 schema intentionally does NOT have a single `memories` table.
Each type gets its own table because retrieval policies differ per
type, and a unified table encourages the merged-wall pathology v1
exhibits.

A shared `memory_index` table holds the embedding + headline projection
that boot/search use — it lets one query rank headlines across all
types without materializing bodies.
"""
from __future__ import annotations

from .config import EMBEDDING_DIMS

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

-- ── RULE ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rules (
    id             SERIAL      PRIMARY KEY,
    headline       TEXT        NOT NULL,
    body           TEXT        NOT NULL,
    severity       TEXT        NOT NULL CHECK (severity IN ('BLOCKER', 'PATTERN', 'DEPRECATED')),
    project        TEXT        NOT NULL DEFAULT '',
    source         TEXT        NOT NULL DEFAULT '',
    supersedes     INTEGER     REFERENCES rules(id) ON DELETE SET NULL,
    superseded_by  INTEGER     REFERENCES rules(id) ON DELETE SET NULL,
    supersede_reason TEXT,
    linked_incident_ids INTEGER[] NOT NULL DEFAULT ARRAY[]::INTEGER[],
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT headline_word_cap CHECK (array_length(regexp_split_to_array(trim(headline), '\\s+'), 1) <= 15)
);
-- Migrate CHECK constraint: remove 'CONTEXT' severity (never used, removed from write gate)
DO $$ BEGIN
    ALTER TABLE rules DROP CONSTRAINT IF EXISTS rules_severity_check;
    ALTER TABLE rules ADD CONSTRAINT rules_severity_check
        CHECK (severity IN ('BLOCKER', 'PATTERN', 'DEPRECATED'));
EXCEPTION WHEN check_violation THEN
    RAISE WARNING 'brain_v2 schema: rules_severity_check constraint could not be applied '
                  '— existing rows may have severity=CONTEXT. Run: '
                  'UPDATE rules SET severity = ''PATTERN'' WHERE severity = ''CONTEXT''; '
                  'then re-apply the schema.';
END $$;
DROP INDEX IF EXISTS rules_severity_project_idx;  -- superseded by the partial index below
DROP INDEX IF EXISTS rules_active_idx;
CREATE INDEX IF NOT EXISTS rules_project_severity_idx
    ON rules (project, severity) WHERE superseded_by IS NULL;

-- ── FACT ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS facts (
    id             SERIAL      PRIMARY KEY,
    headline       TEXT        NOT NULL,
    body           TEXT        NOT NULL,
    project        TEXT        NOT NULL DEFAULT '',
    tags           TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    ttl            TIMESTAMPTZ,
    confidence     REAL        NOT NULL DEFAULT 1.0,
    access_count   INTEGER     NOT NULL DEFAULT 0,
    last_accessed  TIMESTAMPTZ,
    source         TEXT        NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS facts_project_idx ON facts (project);
CREATE INDEX IF NOT EXISTS facts_ttl_idx ON facts (ttl) WHERE ttl IS NOT NULL;

-- ── INCIDENT ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS incidents (
    id             SERIAL      PRIMARY KEY,
    headline       TEXT        NOT NULL,
    body           TEXT        NOT NULL,
    project        TEXT        NOT NULL DEFAULT '',
    root_cause     TEXT,
    resolution     TEXT,
    linked_rule_ids INTEGER[]  NOT NULL DEFAULT ARRAY[]::INTEGER[],
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed  TIMESTAMPTZ,
    archived       BOOLEAN     NOT NULL DEFAULT FALSE,
    source         TEXT        NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS incidents_project_idx ON incidents (project);
CREATE INDEX IF NOT EXISTS incidents_active_idx ON incidents (occurred_at DESC) WHERE archived = FALSE;

-- ── TASK ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tasks (
    id             SERIAL      PRIMARY KEY,
    content        TEXT        NOT NULL,
    project        TEXT        NOT NULL DEFAULT '',
    priority       TEXT        NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high')),
    status         TEXT        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'blocked', 'done', 'stale')),
    due_condition  TEXT,
    created_session TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS tasks_status_project_idx ON tasks (status, project);

-- ── MEMORY INDEX ─────────────────────────────────────────────────────
-- Single embedding index across all types so boot/search can rank
-- headlines without materializing bodies from four tables.
CREATE TABLE IF NOT EXISTS memory_index (
    kind             TEXT        NOT NULL CHECK (kind IN ('rule', 'fact', 'incident', 'task')),
    memory_id        INTEGER     NOT NULL,
    project          TEXT        NOT NULL DEFAULT '',
    headline         TEXT        NOT NULL,
    severity         TEXT,            -- NULL for non-rule
    embedding        VECTOR({EMBEDDING_DIMS}),
    pinned            BOOLEAN     NOT NULL DEFAULT FALSE,
    active           BOOLEAN     NOT NULL DEFAULT TRUE,    -- FALSE when rule superseded, incident archived, task done/stale, forgotten, or decayed
    forgotten_at     TIMESTAMPTZ,    -- NULL = not forgotten
    forgotten_reason TEXT,
    forgotten_by     TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (kind, memory_id)
);
-- Ensure columns exist on upgrades from earlier v2 schemas (pre-forget)
ALTER TABLE memory_index ADD COLUMN IF NOT EXISTS forgotten_at TIMESTAMPTZ;
ALTER TABLE memory_index ADD COLUMN IF NOT EXISTS forgotten_reason TEXT;
ALTER TABLE memory_index ADD COLUMN IF NOT EXISTS forgotten_by TEXT;
-- v1 tool parity columns (v0.20.0): annotations + ratings
ALTER TABLE memory_index ADD COLUMN IF NOT EXISTS annotation TEXT;
ALTER TABLE memory_index ADD COLUMN IF NOT EXISTS upvotes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory_index ADD COLUMN IF NOT EXISTS downvotes INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS memory_index_embedding_hnsw
    ON memory_index USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS memory_index_active_idx ON memory_index (active, project);
CREATE INDEX IF NOT EXISTS memory_index_severity_idx ON memory_index (severity) WHERE severity IS NOT NULL;

-- ── ACTION ITEMS ─────────────────────────────────────────────────
-- Separate from tasks. Action items are obligations surfaced at boot
-- that BLOCK writes until acknowledged. Modeled on v1's per-source
-- pending_action_items pattern.
CREATE TABLE IF NOT EXISTS action_items (
    id             SERIAL      PRIMARY KEY,
    source_kind    TEXT        NOT NULL,   -- 'rule', 'fact', 'incident', 'task'
    source_id      INTEGER     NOT NULL,   -- id of the memory that produced this item
    text           TEXT        NOT NULL,   -- the action item text
    project        TEXT        NOT NULL DEFAULT '',
    status         TEXT        NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'will_execute', 'already_done', 'not_relevant')),
    ack_reason     TEXT,
    ack_source     TEXT,                   -- which agent acked it
    ack_at         TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS action_items_pending_idx
    ON action_items (project, status) WHERE status = 'pending';

-- ── ACTIVE SESSIONS ──────────────────────────────────────────────
-- Per-source session registry. Liveness model: process lifecycle is
-- authoritative (NO timer-based TTL — v1's v0.14.0 lesson). Rows are
-- ended via explicit end_session call on clean exit, or superseded by
-- a new row with the same (source, cwd, pid) tuple on reboot.
CREATE TABLE IF NOT EXISTS active_sessions (
    id             SERIAL      PRIMARY KEY,
    source         TEXT        NOT NULL,
    project        TEXT        NOT NULL DEFAULT '',
    cwd            TEXT        NOT NULL DEFAULT '',
    pid            INTEGER,
    host           TEXT        NOT NULL DEFAULT '',
    current_task   TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    heartbeat_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at       TIMESTAMPTZ,
    status         TEXT        NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'ended')),
    metadata       JSONB
);
CREATE INDEX IF NOT EXISTS active_sessions_project_status_idx
    ON active_sessions (project, status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS active_sessions_dedup_idx
    ON active_sessions (source, cwd, pid) WHERE status = 'active';

-- ── HANDOFFS ─────────────────────────────────────────────────────
-- Session-to-session continuity notes. A clean end_session may write
-- a handoff; boot_session_v2 auto-populates its handoff field from the
-- most recent one for the project.
CREATE TABLE IF NOT EXISTS handoffs (
    id             SERIAL      PRIMARY KEY,
    session_id     INTEGER     REFERENCES active_sessions(id) ON DELETE SET NULL,
    source         TEXT        NOT NULL,
    project        TEXT        NOT NULL DEFAULT '',
    content        TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS handoffs_project_recency_idx
    ON handoffs (project, created_at DESC);

-- ── MAINTENANCE RUNS ─────────────────────────────────────────────
-- Rate-limit ledger for run_maintenance_v2. A boot-time hook can
-- fire the tool on every boot; the tool checks this table and
-- short-circuits if the last run is within the rate-limit window.
-- One row per actual run (skipped calls do NOT insert a row).
CREATE TABLE IF NOT EXISTS maintenance_runs (
    id             SERIAL      PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    report         JSONB,
    source         TEXT        NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS maintenance_runs_recency_idx
    ON maintenance_runs (started_at DESC);

-- ── AUDIT LOG ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS v2_audit (
    audit_id       SERIAL      PRIMARY KEY,
    operation      TEXT        NOT NULL,
    kind           TEXT        NOT NULL,
    memory_id      INTEGER     NOT NULL,
    snapshot       JSONB,
    source         TEXT,
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS v2_audit_ts_idx ON v2_audit (changed_at DESC);

-- ── TOOL EVENTS ─────────────────────────────────────────────────────
-- Every tool call (reads AND writes) with timing. v2_audit covers
-- write mutations only; tool_events covers the full picture for
-- observability: which tools are called, how often, how fast, errors.
CREATE TABLE IF NOT EXISTS tool_events (
    id          BIGSERIAL    PRIMARY KEY,
    session_id  INTEGER      REFERENCES active_sessions(id) ON DELETE SET NULL,
    tool_name   TEXT         NOT NULL,
    project     TEXT         NOT NULL DEFAULT '',
    source      TEXT         NOT NULL DEFAULT '',
    duration_ms INTEGER,
    success     BOOLEAN      NOT NULL DEFAULT TRUE,
    error_msg   TEXT,
    occurred_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS tool_events_session_time_idx
    ON tool_events (session_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS tool_events_tool_time_idx
    ON tool_events (tool_name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS tool_events_project_time_idx
    ON tool_events (project, occurred_at DESC)
    WHERE project != '';
"""


def apply_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()
