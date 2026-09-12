"""brain_kg retrieval — graph_recall, plus a faithful Engine-A shim.

Fairness (PLAN-gate #4):
  - Engine A (brain) and Engine B (KG) seed on the SAME embeddings and cosine.
  - Engine B is reported BOTH ways: seed_only (must match A) and seed+graph.
  - Both engines return EXACTLY k items, so recall@k cannot be won by volume.
"""
from __future__ import annotations

from typing import Any

from . import store
from .config import KG_DEFAULT_HOPS, KG_HOP_DECAY, KG_SEED_K


def _key(kind: str, memory_id: int) -> str:
    return f"{kind}:{memory_id}"


def engine_a_seed(conn, query_embedding_literal: str, k: int) -> list[dict[str, Any]]:
    """Engine A analogue: pure top-k cosine over the (copied) embeddings.

    This is the SAME ranking the brain's search_headlines produces because it
    uses the same embeddings + the same cosine operator. Returned so the harness
    can assert seed_only(B) == A and so A and B are scored identically.
    """
    rows = store.seed_nodes(conn, query_embedding_literal, k)
    return [
        {"key": _key(r["kind"], r["memory_id"]), "kind": r["kind"],
         "memory_id": r["memory_id"], "headline": r["headline"],
         "score": float(r["sim"]), "active": r["active"], "via": "seed"}
        for r in rows
    ]


def graph_recall(
    conn, query_embedding_literal: str, k: int,
    seed_k: int = KG_SEED_K, hops: int = KG_DEFAULT_HOPS,
) -> list[dict[str, Any]]:
    """Seed on cosine, expand `hops` over active edges, return top-k by blended
    score. Supersede edges let a stale seed reach its current corrector; neighbor
    edges reach connected nodes outside the flat top-k."""
    seeds = store.seed_nodes(conn, query_embedding_literal, seed_k)
    # scored[key] = (best_score, record)
    scored: dict[str, dict[str, Any]] = {}

    def consider(kind, memory_id, headline, score, active, via, path):
        key = _key(kind, memory_id)
        prev = scored.get(key)
        if prev is None or score > prev["score"]:
            scored[key] = {"key": key, "kind": kind, "memory_id": memory_id,
                           "headline": headline, "score": float(score),
                           "active": active, "via": via, "path": path}

    frontier_ids: list[int] = []
    seed_sim_by_id: dict[int, float] = {}
    for r in seeds:
        consider(r["kind"], r["memory_id"], r["headline"], r["sim"], r["active"],
                 "seed", [r["memory_id"]])
        frontier_ids.append(r["id"])
        seed_sim_by_id[r["id"]] = float(r["sim"])

    # Expand hops.
    current = list(frontier_ids)
    hop_seed_sim = dict(seed_sim_by_id)
    for hop in range(1, hops + 1):
        if not current:
            break
        edges = store.outgoing_edges(conn, current)
        next_ids: list[int] = []
        next_seed_sim: dict[int, float] = {}
        for e in edges:
            base = hop_seed_sim.get(e["from_id"], 0.0)
            # Boost superseded->corrector strongly (the correction hop is the point).
            rel_boost = 1.0 if e["relation"] == "supersedes" else float(e["weight"])
            score = base * rel_boost * (KG_HOP_DECAY ** hop)
            consider(e["kind"], e["memory_id"], e["headline"], score, e["active"],
                     f"{e['relation']}@hop{hop}", None)
            next_ids.append(e["id"])
            # propagate the best inherited similarity for a possible next hop
            if score > next_seed_sim.get(e["id"], 0.0):
                next_seed_sim[e["id"]] = score
        current = next_ids
        hop_seed_sim = next_seed_sim

    ranked = sorted(scored.values(), key=lambda d: d["score"], reverse=True)
    return ranked[:k]
