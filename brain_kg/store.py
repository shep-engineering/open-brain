"""brain_kg store — connections + node/edge access for the KG database.

Two connection factories, deliberately separate:
  - kg_conn():     read/write to the KG's OWN database (open_brain_kg).
  - brain_ro_conn(): READ-ONLY to the brain (open_brain_v2). Forced read-only at
                     the session level (default_transaction_read_only=on) so a
                     coding slip cannot write to the live brain (PLAN-gate #1).
"""
from __future__ import annotations

from typing import Any

import psycopg2
import psycopg2.extras

from .config import (
    KG_DATABASE_URL,
    KG_SOURCE_BRAIN_URL,
    KG_SOURCE_READONLY_OPTIONS,
)


def kg_conn() -> psycopg2.extensions.connection:
    """A read/write connection to the KG's own database."""
    return psycopg2.connect(KG_DATABASE_URL)


def brain_ro_conn() -> psycopg2.extensions.connection:
    """A READ-ONLY connection to the brain. Safety-critical (PLAN-gate #1).

    `options=-c default_transaction_read_only=on` makes every transaction on
    this session read-only at the server level — any INSERT/UPDATE/DELETE
    raises `read-only transaction`, regardless of the code path. Single
    connection by construction (callers use one and close it).
    """
    return psycopg2.connect(KG_SOURCE_BRAIN_URL, options=KG_SOURCE_READONLY_OPTIONS)


# ── KG writes ────────────────────────────────────────────────────────────
def upsert_node(
    conn, kind: str, memory_id: int, project: str, headline: str,
    body: str, embedding_literal: str | None, active: bool, source: str,
) -> int:
    """Insert (or update) one node, return its KG id. embedding_literal is a
    pgvector '[...]' string copied from the brain, or None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kg_nodes (kind, memory_id, project, headline, body, embedding, active, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (kind, memory_id) DO UPDATE SET
                project = EXCLUDED.project, headline = EXCLUDED.headline,
                body = EXCLUDED.body, embedding = EXCLUDED.embedding,
                active = EXCLUDED.active, source = EXCLUDED.source
            RETURNING id
            """,
            (kind, memory_id, project, headline, body, embedding_literal, active, source),
        )
        return cur.fetchone()[0]


def add_edge(
    conn, src_id: int, dst_id: int, relation: str, weight: float,
    provenance_memory_id: int | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kg_edges (src_id, dst_id, relation, weight, provenance_memory_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (src_id, dst_id, relation) DO UPDATE SET
                weight = EXCLUDED.weight
            """,
            (src_id, dst_id, relation, weight, provenance_memory_id),
        )


# ── KG reads (retrieval) ─────────────────────────────────────────────────
def seed_nodes(conn, query_embedding_literal: str, k: int) -> list[dict[str, Any]]:
    """Top-k ACTIVE nodes by cosine similarity to the query embedding.

    This is the vector-seed step. It uses the SAME embeddings copied from the
    brain and the SAME cosine operator, so seed(B) is comparable to Engine A's
    ranking (PLAN-gate #4).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, kind, memory_id, project, headline, body, active,
                   1 - (embedding <=> %s::vector) AS sim
            FROM kg_nodes
            WHERE embedding IS NOT NULL AND active = TRUE
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (query_embedding_literal, query_embedding_literal, k),
        )
        return list(cur.fetchall())


def outgoing_edges(conn, node_ids: list[int]) -> list[dict[str, Any]]:
    """Active edges leaving any of node_ids, with the destination node fields.

    Includes supersede edges even when the destination is inactive is NOT
    wanted here — but the supersede edge points corrector->retired, so to reach
    the CURRENT belief from a retired seed we also traverse INCOMING supersede
    edges. This function returns both directions of supersede plus outgoing
    neighbor/same_project, so a stale seed can hop to its corrector.
    """
    if not node_ids:
        return []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            -- outgoing neighbor/same_project/supersedes
            SELECT e.src_id AS from_id, e.dst_id AS to_id, e.relation, e.weight,
                   n.id, n.kind, n.memory_id, n.project, n.headline, n.body, n.active
            FROM kg_edges e JOIN kg_nodes n ON n.id = e.dst_id
            WHERE e.active AND e.src_id = ANY(%s)
            UNION ALL
            -- incoming supersedes: retired seed -> its corrector (current belief)
            SELECT e.dst_id AS from_id, e.src_id AS to_id, e.relation, e.weight,
                   n.id, n.kind, n.memory_id, n.project, n.headline, n.body, n.active
            FROM kg_edges e JOIN kg_nodes n ON n.id = e.src_id
            WHERE e.active AND e.relation = 'supersedes' AND e.dst_id = ANY(%s)
            """,
            (node_ids, node_ids),
        )
        return list(cur.fetchall())


def entity_connected_nodes(conn, node_ids: list[int]) -> list[dict[str, Any]]:
    """From seed memory-node ids, reach OTHER memory nodes via the entity graph:
    seed node -> its entities (kg_entity_mentions) -> connected entities
    (kg_entity_edges, either direction) -> memory nodes that mention those
    (kg_entity_mentions). Returns destination node fields + the relation + a
    shared/linked entity name for the audit path. This is the KG v3 payload:
    facts linked by MEANING (shared entities) that cosine never connects."""
    if not node_ids:
        return []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            WITH seed_ent AS (
                SELECT DISTINCT entity_id FROM kg_entity_mentions WHERE node_id = ANY(%s)
            ),
            linked_ent AS (
                -- entities directly connected to a seed entity (either direction)
                SELECT ee.dst_entity_id AS ent, ee.relation FROM kg_entity_edges ee
                    JOIN seed_ent s ON s.entity_id = ee.src_entity_id WHERE ee.active
                UNION ALL
                SELECT ee.src_entity_id AS ent, ee.relation FROM kg_entity_edges ee
                    JOIN seed_ent s ON s.entity_id = ee.dst_entity_id WHERE ee.active
                UNION ALL
                -- also the seed entities themselves (co-mention linking)
                SELECT entity_id AS ent, 'co_mention'::text AS relation FROM seed_ent
            )
            SELECT DISTINCT n.id, n.kind, n.memory_id, n.project, n.headline,
                   n.body, n.active, le.relation, en.display_name AS via_entity
            FROM linked_ent le
            JOIN kg_entity_mentions m ON m.entity_id = le.ent
            JOIN kg_entities en ON en.id = le.ent
            JOIN kg_nodes n ON n.id = m.node_id
            WHERE n.id <> ALL(%s) AND n.active
            """,
            (node_ids, node_ids),
        )
        return list(cur.fetchall())


def counts(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM kg_nodes")
        nodes = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM kg_edges")
        edges = cur.fetchone()[0]
        cur.execute("SELECT relation, count(*) FROM kg_edges GROUP BY relation")
        by_rel = {r: c for r, c in cur.fetchall()}
    return {"nodes": nodes, "edges": edges, **{f"edges_{k}": v for k, v in by_rel.items()}}
