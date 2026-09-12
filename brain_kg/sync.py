"""brain_kg sync — keep the KG current as brain memories land (MVP: cheap, no GPU).

Per the PLAN gate re-scope: the MVP sync is the CHEAP path only —
  1. ingest new brain memories into kg_nodes (read-only on the brain),
  2. deterministic reference-entity pass over det_done_at IS NULL nodes (no model),
  3. FULL edge-rebuild (neighbor/same_project/supersede/entity-overlap — cheap SQL).
This carries most of the proven bench win with ZERO VRAM/embedder contention, so it
is safe to fire from a boot/Post hook via sync_if_due().

GLiNER2 typed-relation extraction is DELIBERATELY NOT here — firing a GPU model from
a boot hook self-defeats (the embedder is hottest at boot, so the VRAM gate would
always defer it). Run it explicitly/periodically instead:
  python -m brain_kg.build_entity_graph        # full GLiNER pass (VRAM-gated)

Non-negotiable: reads the brain read-only; all writes to open_brain_kg only; the MVP
path loads no model.
"""
from __future__ import annotations

import os
from typing import Any

from . import store, ingest, build_edges, build_entity_graph
from .schema import apply_schema

SYNC_INTERVAL_HOURS = float(os.getenv("OPEN_BRAIN_KG_SYNC_INTERVAL_HOURS", "6"))


def _unmarked_det_count(kg) -> int:
    with kg.cursor() as cur:
        cur.execute("SELECT count(*) FROM kg_nodes WHERE active AND body <> '' AND det_done_at IS NULL")
        return cur.fetchone()[0]


def sync() -> dict[str, Any]:
    """Run the cheap MVP sync. No GPU, no model. Idempotent (no-op when current)."""
    # ensure the schema (markers) exist
    kg0 = store.kg_conn()
    apply_schema(kg0)
    # fingerprint the active-node set BEFORE ingest (DIFF-gate #3 fix): a
    # forget/supersede-without-new-node DEACTIVATES a node with no new det work,
    # so we must rebuild edges when the active set CHANGED, not only when new
    # unmarked nodes appear — else stale edges to a now-inactive node linger.
    with kg0.cursor() as c:
        c.execute("SELECT count(*) FROM kg_nodes WHERE active")
        active_before = c.fetchone()[0]
    kg0.close()

    # 1. ingest new brain memories (read-only on the brain; upserts kg_nodes)
    ingest_report = ingest.ingest_nodes()

    # 2. deterministic pass over only the unmarked nodes (stamps det_done_at)
    kg = store.kg_conn()
    todo = _unmarked_det_count(kg)
    with kg.cursor() as c:
        c.execute("SELECT count(*) FROM kg_nodes WHERE active")
        active_after = c.fetchone()[0]
    kg.close()
    if todo > 0:
        det_report = build_entity_graph.build_deterministic_only(only_unmarked=True)
    else:
        det_report = {"nodes_processed": 0, "note": "no unmarked nodes — deterministic no-op"}

    # 3. full edge-rebuild when the graph changed: new unmarked nodes (todo>0) OR
    #    the active-node set shifted (a deactivation, e.g. forget/supersede). Cheap
    #    SQL/CPU, no GPU. Rebuilds supersede/neighbor/same_project + entity-overlap
    #    over the whole graph (required for correct new-state supersession).
    edges_report: dict[str, Any] = {"skipped": "nothing changed"}
    if todo > 0 or active_after != active_before:
        edges_report = _rebuild_edges()
        edges_report["trigger"] = "new_nodes" if todo > 0 else "active_set_changed"

    return {"ingest": {"nodes": ingest_report.get("nodes_ingested"),
                       "brain_unchanged": ingest_report.get("brain_unchanged")},
            "deterministic": det_report,
            "edges": edges_report,
            "gliner": "NOT run in sync (explicit: python -m brain_kg.build_entity_graph)"}


def _rebuild_edges() -> dict[str, Any]:
    """Full rebuild of the cosine/supersede edge lanes over the whole graph.
    Cheap (SQL + copied embeddings, no GPU). The entity graph (kg_entity_*) is
    additive/idempotent and is refreshed by the deterministic pass, so we rebuild
    the kg_edges lane here."""
    kg = store.kg_conn()
    with kg.cursor() as cur:
        cur.execute("TRUNCATE kg_edges RESTART IDENTITY")
    kg.commit()
    kg.close()
    brain = store.brain_ro_conn()
    kg = store.kg_conn()
    try:
        sup = build_edges.build_supersede_edges(kg, brain)
    finally:
        brain.close()
    nbr = build_edges.build_neighbor_edges(kg)
    proj = build_edges.build_same_project_edges(kg)
    fsup = build_edges.build_fact_supersede_edges(kg)
    kg.close()
    return {"supersede": sup, "neighbor": nbr, "same_project": proj, "fact_supersede": fsup}


def sync_if_due() -> dict[str, Any]:
    """Rate-limited entry for a boot/Post hook (mirrors run_maintenance_if_due_v2:
    rate-limited in code, no external cron). Short-circuits if the last sync is
    within SYNC_INTERVAL_HOURS. Cheap path only — safe to fire opportunistically."""
    kg = store.kg_conn()
    with kg.cursor() as cur:
        cur.execute(
            "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(finished_at)))/3600.0 "
            "FROM kg_ingest_runs WHERE notes = 'sync'"
        )
        row = cur.fetchone()
    kg.close()
    hours_since = row[0] if row and row[0] is not None else None
    if hours_since is not None and hours_since < SYNC_INTERVAL_HOURS:
        return {"skipped": f"last sync {hours_since:.1f}h ago < {SYNC_INTERVAL_HOURS}h interval"}
    report = sync()
    # record the run (reuse kg_ingest_runs with notes='sync')
    kg = store.kg_conn()
    with kg.cursor() as cur:
        cur.execute("INSERT INTO kg_ingest_runs (finished_at, notes) VALUES (NOW(), 'sync')")
    kg.commit()
    kg.close()
    report["recorded"] = True
    return report


if __name__ == "__main__":  # pragma: no cover
    import pprint, sys
    if len(sys.argv) > 1 and sys.argv[1] == "if-due":
        pprint.pprint(sync_if_due())
    else:
        pprint.pprint(sync())
