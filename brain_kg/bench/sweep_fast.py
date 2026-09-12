"""FAST scoring-constant sweep — embeds each query ONCE and reuses across all
settings (the slow sweep re-embedded 34 queries x 60 settings = 2040 ollama calls
and hung). Here: embed 34 queries once, then for each (decay, floor, cw, rw) run
graph_recall directly on the cached embeddings. ~60 settings in seconds, not hours.

Answers the DIFF-gate robustness question: does the KG win hold across a RANGE of
scoring constants (real signal) or only at the hand-picked point (fragile fit)?

Run:  python -m brain_kg.bench.sweep_fast
"""
from __future__ import annotations

import json
import os

from brain_v2.embedding import embed_to_pgvector
from brain_kg import store
from brain_kg.bench import harness

DECAYS = [0.4, 0.5, 0.6, 0.7, 0.8]
FLOORS = [0.3, 0.5, 0.7]
WEIGHTS = [(1.0, 0.5), (1.0, 0.0), (1.0, 1.0), (0.7, 0.3)]
K = 5


def _keys(ids, kind):
    return {f"{kind}:{i}" for i in ids}


def _gold_kind(q):
    return q.get("gold_kind") or ("fact" if q["class"] == "fact-recall" else "rule")


def sweep():
    # load queries + embed each ONCE
    queries = harness.load_queries("queries_v3.jsonl") + harness.load_queries("queries_v3b.jsonl")
    kg = store.kg_conn()
    brain = store.brain_ro_conn()
    cache = []  # (q, emb, gold_keys, stale_keys, A_hits)
    for q in queries:
        emb = embed_to_pgvector(q["query"])
        gk = _gold_kind(q)
        gold = _keys(q.get("gold_current", []), gk)
        a = harness.engine_a_real_brain(brain, q["query"], K)
        a_hit = any(r["key"] in gold for r in a[:K]) if gold else None
        cache.append((q, emb, gold, a_hit))
    brain.close()

    results = []
    for decay in DECAYS:
        for floor in FLOORS:
            for cw, rw in WEIGHTS:
                os.environ["OPEN_BRAIN_KG_ENT_SUPPORT_DECAY"] = str(decay)
                os.environ["OPEN_BRAIN_KG_ENT_FLOOR"] = str(floor)
                os.environ["OPEN_BRAIN_KG_ENT_COMENTION_W"] = str(cw)
                os.environ["OPEN_BRAIN_KG_ENT_RELATION_W"] = str(rw)
                import importlib, brain_kg.config as cfg, brain_kg.retrieve as retr
                importlib.reload(cfg); importlib.reload(retr)
                sp_b = sp_a = sh_b = sh_a = 0
                bw = aw = 0
                for q, emb, gold, a_hit in cache:
                    b = retr.graph_recall(kg, emb, K)
                    b_hit = any(r["key"] in gold for r in b[:K]) if gold else None
                    if q["class"] == "state-pair":
                        sp_a += bool(a_hit); sp_b += bool(b_hit)
                    if q["class"] == "single-hop":
                        sh_a += bool(a_hit); sh_b += bool(b_hit)
                    if q["class"] in ("state-pair", "multi-hop"):
                        if b_hit and not a_hit: bw += 1
                        if a_hit and not b_hit: aw += 1
                net = bw - aw
                r = {"decay": decay, "floor": floor, "cw": cw, "rw": rw,
                     "state_pair_B": sp_b, "state_pair_A": sp_a,
                     "single_hop_B": sh_b, "single_hop_A": sh_a, "net_B": net}
                results.append(r)
                print(f"decay={decay} floor={floor} cw={cw} rw={rw} -> "
                      f"state-pair B={sp_b}/{sp_a}A net_B={net} single-hop B={sh_b}")
    kg.close()
    with open(os.path.join(os.path.dirname(__file__), "sweep_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    wins = [r for r in results if r["net_B"] >= 3 and r["single_hop_B"] >= r["single_hop_A"]]
    holds = [r for r in results if r["net_B"] >= 1 and r["single_hop_B"] >= r["single_hop_A"]]
    print(f"\n=== {len(wins)}/{len(results)} settings meet net_B>=3; "
          f"{len(holds)}/{len(results)} hold net_B>=1, no single-hop regression ===")
    print("net_B range:", sorted(set(r["net_B"] for r in results)))
    return results


if __name__ == "__main__":  # pragma: no cover
    sweep()
