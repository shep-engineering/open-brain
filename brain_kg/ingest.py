"""brain_kg ingest — read the brain READ-ONLY, project into the KG DB.

Safety (PLAN-gate #1):
  - Brain is read via brain_ro_conn(): default_transaction_read_only=on, so no
    write can reach open_brain_v2 even on a coding slip.
  - Single connection, short reads (we fetch, then close before building edges).
  - A mid-ingest health probe times a trivial query on a SEPARATE brain
    connection to prove the brain stayed responsive during ingest (row-count
    parity alone does not prove the absence of lock/resource contention).

Non-destructive: nothing here writes to the brain. The KG is a derived,
disposable projection.
"""
from __future__ import annotations

import json
import time
from typing import Any

import psycopg2

from . import store
from .schema import apply_schema


BRAIN_TABLES = ("facts", "rules", "incidents", "tasks", "memory_index")


def _brain_rowcounts(conn) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for t in BRAIN_TABLES:
            cur.execute(f"SELECT count(*) FROM {t}")  # noqa: S608 - fixed table list
            out[t] = cur.fetchone()[0]
    return out


def _health_probe() -> dict[str, Any]:
    """Time a trivial query on a fresh read-only brain connection."""
    t0 = time.perf_counter()
    ok = False
    err = None
    try:
        c = store.brain_ro_conn()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            ok = True
        finally:
            c.close()
    except psycopg2.Error as e:  # pragma: no cover - infra failure path
        err = str(e)
    return {"ok": ok, "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "error": err}


def _body_for(cur, kind: str, memory_id: int) -> str:
    """Fetch the body text from the typed table (the thing memory_index lacks)."""
    if kind == "task":
        cur.execute("SELECT content FROM tasks WHERE id = %s", (memory_id,))
    elif kind == "rule":
        cur.execute("SELECT body FROM rules WHERE id = %s", (memory_id,))
    elif kind == "fact":
        cur.execute("SELECT body FROM facts WHERE id = %s", (memory_id,))
    elif kind == "incident":
        cur.execute("SELECT body FROM incidents WHERE id = %s", (memory_id,))
    else:
        return ""
    row = cur.fetchone()
    return (row[0] if row and row[0] else "") or ""


def ingest_nodes() -> dict[str, Any]:
    """Copy brain memory_index rows (+ bodies + embeddings) into kg_nodes.

    Returns a report dict. Does NOT build edges (that is build_edges.py).
    """
    # Ensure the KG DB schema exists.
    kg = store.kg_conn()
    apply_schema(kg)

    rowcounts_before = None
    node_count = 0
    brain = store.brain_ro_conn()
    try:
        rowcounts_before = _brain_rowcounts(brain)
        # Pull index rows (embedding lives here) for active memories only.
        with brain.cursor() as cur:
            cur.execute(
                """
                SELECT kind, memory_id, project, headline, active,
                       embedding::text AS emb
                FROM memory_index
                WHERE embedding IS NOT NULL
                ORDER BY kind, memory_id
                """
            )
            index_rows = cur.fetchall()

        # Fetch bodies + upsert. Use a second cursor for body lookups.
        with brain.cursor() as bodycur:
            for kind, memory_id, project, headline, active, emb in index_rows:
                body = _body_for(bodycur, kind, memory_id)
                store.upsert_node(
                    kg, kind=kind, memory_id=memory_id, project=project or "",
                    headline=headline or "", body=body, embedding_literal=emb,
                    active=bool(active), source="ingest",
                )
                node_count += 1
        kg.commit()

        # Mid/post-ingest health probe on a fresh brain connection.
        health = _health_probe()
        rowcounts_after = _brain_rowcounts(brain)
    finally:
        brain.close()

    unchanged = rowcounts_before == rowcounts_after
    report = {
        "nodes_ingested": node_count,
        "brain_rowcounts_before": rowcounts_before,
        "brain_rowcounts_after": rowcounts_after,
        "brain_unchanged": unchanged,
        "brain_health_probe": health,
    }
    # Record the run.
    with kg.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kg_ingest_runs (finished_at, nodes_ingested, brain_rowcounts,
                                        brain_health_probe, notes)
            VALUES (NOW(), %s, %s, %s, %s)
            """,
            (node_count, json.dumps({"before": rowcounts_before, "after": rowcounts_after}),
             json.dumps(health), "ingest_nodes"),
        )
    kg.commit()
    kg.close()
    return report


if __name__ == "__main__":  # pragma: no cover
    import pprint
    pprint.pprint(ingest_nodes())
