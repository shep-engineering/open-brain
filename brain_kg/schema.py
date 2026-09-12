"""brain_kg schema — a minimal belief-time knowledge graph.

Lives in the KG's OWN database (open_brain_kg), never in the brain's.
Bi-temporal fact-time and fuzzy entity-resolution were CUT for the overnight
build (PLAN-gate #5); this is the minimal graph that proves the re-scoped
thesis: supersede-edge + semantic-neighbor traversal beats flat cosine on
stale-recall and connected-recall.

Correction primitive = `active` boolean (belief-time). Setting a node/edge
inactive means "no longer believed" while keeping the row for audit — exactly
the facts-supersession the brain lacks.
"""
from __future__ import annotations

from .config import EMBEDDING_DIMS

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

-- ── NODES ────────────────────────────────────────────────────────────
-- One node per brain memory. (kind, memory_id) is the exact dedup key —
-- no fuzzy canonicalization (documented limitation).
CREATE TABLE IF NOT EXISTS kg_nodes (
    id          SERIAL      PRIMARY KEY,
    kind        TEXT        NOT NULL,          -- rule|fact|incident|task (mirrors the brain)
    memory_id   INTEGER     NOT NULL,          -- id in the brain's typed table
    project     TEXT        NOT NULL DEFAULT '',
    headline    TEXT        NOT NULL,
    body        TEXT        NOT NULL DEFAULT '',
    embedding   VECTOR({EMBEDDING_DIMS}),      -- COPIED from the brain (not recomputed)
    active      BOOLEAN     NOT NULL DEFAULT TRUE,   -- FALSE = superseded/forgotten (belief-time)
    source      TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT kg_nodes_kind_mem_unique UNIQUE (kind, memory_id)
);
CREATE INDEX IF NOT EXISTS kg_nodes_active_idx ON kg_nodes (active, project);

-- ── EDGES ────────────────────────────────────────────────────────────
-- relation in (supersedes, neighbor, same_project). provenance_memory_id is
-- the brain memory that asserted the edge (auditable path).
CREATE TABLE IF NOT EXISTS kg_edges (
    id                  SERIAL      PRIMARY KEY,
    src_id              INTEGER     NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    dst_id              INTEGER     NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    relation            TEXT        NOT NULL CHECK (relation IN ('supersedes', 'neighbor', 'same_project')),
    weight              REAL        NOT NULL DEFAULT 1.0,
    provenance_memory_id INTEGER,
    active              BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT kg_edges_unique UNIQUE (src_id, dst_id, relation)
);
CREATE INDEX IF NOT EXISTS kg_edges_src_idx ON kg_edges (src_id) WHERE active;
CREATE INDEX IF NOT EXISTS kg_edges_dst_idx ON kg_edges (dst_id) WHERE active;
CREATE INDEX IF NOT EXISTS kg_edges_relation_idx ON kg_edges (relation) WHERE active;

-- Relax the relation CHECK to admit GLiNER2 typed relations (KG v3). The old
-- CHECK (supersedes/neighbor/same_project) rejected typed edges on first insert.
-- We drop the hard CHECK entirely: the typed vocabulary is defined + bounded in
-- brain_kg/extract.py (RELATION_LABELS), so the DB stays permissive and the app
-- controls the set. Existing cosine-graph relations still work unchanged.
DO $$ BEGIN
    ALTER TABLE kg_edges DROP CONSTRAINT IF EXISTS kg_edges_relation_check;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

-- ── ENTITIES (KG v3) ─────────────────────────────────────────────────
-- Canonical entities extracted by GLiNER2, with their OWN id space (kg_nodes is
-- memory-keyed UNIQUE(kind,memory_id) and cannot hold entities). canonical_key
-- is the deterministic-ER normalized form (lowercase, punct/space stripped) so
-- "088"/"the 088 migration"/"migration 088" collapse to one row.
CREATE TABLE IF NOT EXISTS kg_entities (
    id             SERIAL      PRIMARY KEY,
    canonical_key  TEXT        NOT NULL UNIQUE,   -- normalized dedup key
    label          TEXT,                          -- GLiNER2 entity label (migration/ticket/...)
    display_name   TEXT        NOT NULL,          -- a representative surface form
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS kg_entities_label_idx ON kg_entities (label);

-- Entity mentions: which memory (fact/rule/incident node) an entity appeared in.
-- This is the entity<->memory bridge that lets graph_recall walk entity -> fact.
CREATE TABLE IF NOT EXISTS kg_entity_mentions (
    id             SERIAL      PRIMARY KEY,
    entity_id      INTEGER     NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    node_id        INTEGER     NOT NULL REFERENCES kg_nodes(id)    ON DELETE CASCADE,
    surface        TEXT,                          -- the raw span as it appeared
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT kg_entity_mentions_unique UNIQUE (entity_id, node_id)
);
CREATE INDEX IF NOT EXISTS kg_entity_mentions_entity_idx ON kg_entity_mentions (entity_id);
CREATE INDEX IF NOT EXISTS kg_entity_mentions_node_idx   ON kg_entity_mentions (node_id);

-- Typed entity->entity edges (the GLiNER2 relations). Separate table from the
-- memory-node kg_edges so the cosine graph (fallback lane) stays untouched.
CREATE TABLE IF NOT EXISTS kg_entity_edges (
    id                   SERIAL      PRIMARY KEY,
    src_entity_id        INTEGER     NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    dst_entity_id        INTEGER     NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    relation             TEXT        NOT NULL,     -- bounded by extract.RELATION_LABELS
    weight               REAL        NOT NULL DEFAULT 1.0,
    provenance_memory_id INTEGER,                  -- brain memory whose body asserted it
    active               BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT kg_entity_edges_unique UNIQUE (src_entity_id, dst_entity_id, relation, provenance_memory_id)
);
CREATE INDEX IF NOT EXISTS kg_entity_edges_src_idx ON kg_entity_edges (src_entity_id) WHERE active;
CREATE INDEX IF NOT EXISTS kg_entity_edges_dst_idx ON kg_entity_edges (dst_entity_id) WHERE active;

-- ── INGEST RUNS ──────────────────────────────────────────────────────
-- One row per ingest, with the mid-ingest brain-health probe result
-- (PLAN-gate #1: prove the brain stayed responsive, not just row-count parity).
CREATE TABLE IF NOT EXISTS kg_ingest_runs (
    id                  SERIAL      PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    nodes_ingested      INTEGER,
    edges_built         INTEGER,
    brain_rowcounts     JSONB,       -- facts/rules/incidents/tasks/memory_index before and after
    brain_health_probe  JSONB,       -- mid-ingest health_v2 latency/ok
    notes               TEXT
);
"""


def apply_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()
