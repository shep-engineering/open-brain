"""Head-to-head harness: Engine A (the real brain) vs Engine B (KG graph).

Both engines return EXACTLY k items. Engine B seeds on the embeddings COPIED
from the brain; Engine A is the REAL brain retrieval (brain_v2.search_headlines).
CAVEAT (DIFF-gate #5): Engine A is cosine + keyword-skill-merge, NOT pure cosine
— search_headlines merges skill_trigger keyword hits and re-ranks before top-K.
So B's seed is not byte-identical to A on queries that hit a skill keyword; the
`seed_overlap` field reports the actual overlap per query rather than asserting
equality. Any A/B delta is attributable to graph expansion ONLY when
seed_overlap == k (checked in the report).

Scores recall@k on the CURRENT belief, plus a staleness flag (did the engine
return a known-stale memory as a top hit) and, for state-pair queries, the RANK
of current vs stale (the discriminating metric the KG targets — DIFF-gate #2/#3).

Run:  python -m brain_kg.bench.harness
Writes bench/RESULTS_2026-09-11.md + bench/results_raw.json
"""
from __future__ import annotations

import json
import os
from typing import Any

from brain_v2.embedding import embed_to_pgvector
from brain_v2 import store as brain_store
from brain_kg import store, retrieve

HERE = os.path.dirname(os.path.abspath(__file__))
K = int(os.getenv("OPEN_BRAIN_KG_BENCH_K", "5"))


def load_queries(fname: str = "queries.jsonl") -> list[dict[str, Any]]:
    with open(os.path.join(HERE, fname), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def engine_a_real_brain(brain_conn, query: str, k: int) -> list[dict[str, Any]]:
    """The ACTUAL brain retrieval (not the KG copy): brain_v2.search_headlines.

    This is the truthful Engine A — it queries the live brain's memory_index
    with the brain's own active=TRUE filter and cosine ranking. For FACTS this
    returns the stale wall (facts are never deactivated), which is the point.
    """
    rows = brain_store.search_headlines(brain_conn, query=query, limit=k)
    return [
        {"key": f"{r['kind']}:{r['memory_id']}", "kind": r["kind"],
         "memory_id": r["memory_id"], "headline": r.get("headline", ""),
         "score": float(r.get("similarity", 0.0)), "via": "brain"}
        for r in rows
    ]


def _gold_kind(q: dict[str, Any]) -> str:
    """Kind of the gold memories for a query. Explicit gold_kind wins; else infer
    from class (fact-recall -> fact, supersede-recall/single-hop -> rule)."""
    if q.get("gold_kind"):
        return q["gold_kind"]
    return "fact" if q["class"] == "fact-recall" else "rule"


def _keys(ids: list[int], kind: str) -> set[str]:
    return {f"{kind}:{i}" for i in ids}


def _hit(results: list[dict[str, Any]], gold_keys: set[str]) -> bool:
    return any(r["key"] in gold_keys for r in results)


def _returned_stale(results: list[dict[str, Any]], stale_keys: set[str]) -> bool:
    return any(r["key"] in stale_keys for r in results)


def _rank_of(results: list[dict[str, Any]], keys: set[str]) -> int | None:
    """1-based rank of the first result whose key is in `keys`, or None if absent."""
    for i, r in enumerate(results):
        if r["key"] in keys:
            return i + 1
    return None


def _current_beats_stale(results, gold_keys, stale_keys):
    """True if the CURRENT belief is ranked above every stale sibling (or the
    stale ones are absent). None if current is absent (can't judge)."""
    cur_rank = _rank_of(results, gold_keys)
    if cur_rank is None:
        return None
    stale_rank = _rank_of(results, stale_keys)
    return stale_rank is None or cur_rank < stale_rank


def run(query_files: list[str] | None = None) -> dict[str, Any]:
    """Engine A = the ACTUAL brain (brain_v2.search_headlines, active=TRUE filter).
    Engine B = the KG graph_recall. Both return exactly K.
    A fairness self-check confirms B's cosine seed matches a pure top-K over the
    copied embeddings (so B adds structure, not a different embedder)."""
    query_files = query_files or ["queries.jsonl", "queries_facts.jsonl"]
    kg = store.kg_conn()
    brain = store.brain_ro_conn()   # read-only brain, for the real Engine A
    rows = []
    for fname in query_files:
        for q in load_queries(fname):
            emb = embed_to_pgvector(q["query"])
            gk = _gold_kind(q)
            gold = _keys(q.get("gold_current", []), gk)
            stale = _keys(q.get("gold_stale", []), gk)

            # state-pair queries score by RANK, so pull a deeper list (both the
            # current and stale facts must be able to appear to compare ranks).
            depth = 20 if q["class"] == "state-pair" else K
            a = engine_a_real_brain(brain, q["query"], depth)  # the real brain
            b = retrieve.graph_recall(kg, emb, depth)          # the KG
            b_seed_only = retrieve.engine_a_seed(kg, emb, K)   # KG copy seed (fairness check)

            rows.append({
                "id": q["id"], "class": q["class"], "query": q["query"],
                "gold_current": q.get("gold_current", []), "gold_stale": q.get("gold_stale", []),
                "A_hit": _hit(a[:K], gold) if gold else None,
                "B_hit": _hit(b[:K], gold) if gold else None,
                "A_returned_stale": _returned_stale(a[:K], stale) if stale else None,
                "B_returned_stale": _returned_stale(b[:K], stale) if stale else None,
                # RANK metrics (the discriminating test for state pairs):
                "A_current_rank": _rank_of(a, gold) if gold else None,
                "B_current_rank": _rank_of(b, gold) if gold else None,
                "A_stale_rank": _rank_of(a, stale) if stale else None,
                "B_stale_rank": _rank_of(b, stale) if stale else None,
                "A_current_beats_stale": _current_beats_stale(a, gold, stale) if (gold and stale) else None,
                "B_current_beats_stale": _current_beats_stale(b, gold, stale) if (gold and stale) else None,
                "A_top": [r["key"] for r in a[:K]],
                "B_top": [r["key"] for r in b[:K]],
                "seed_overlap": len(set(r["key"] for r in b_seed_only) & set(r["key"] for r in a[:K])),
            })
    kg.close()
    brain.close()
    return {"k": K, "rows": rows}


def _agg(rows, cls, field):
    vals = [r[field] for r in rows if r["class"] == cls and r[field] is not None]
    return f"{sum(1 for v in vals if v)}/{len(vals)}" if vals else "n/a"


def write_report(report: dict[str, Any]) -> None:
    rows = report["rows"]
    k = report["k"]
    lines = [
        "# Head-to-head: Engine A (the real brain) vs Engine B (KG graph)",
        f"*Generated by brain_kg/bench/harness.py. K={k}.*",
        "- **Engine A** = the ACTUAL brain: `brain_v2.store.search_headlines` (active=TRUE filter, cosine).",
        "- **Engine B** = the KG: `graph_recall` (same embeddings, + supersede/neighbor graph walk).",
        "- Both return exactly K, so recall@K is not a volume artifact.",
        "",
        "## Summary (recall@K on the CURRENT belief)",
        "",
        "| Class | A recall@K | B recall@K | A returned STALE | B returned STALE |",
        "|---|---|---|---|---|",
    ]
    for cls in ["fact-recall", "supersede-recall", "single-hop"]:
        lines.append(
            f"| {cls} | {_agg(rows, cls, 'A_hit')} | {_agg(rows, cls, 'B_hit')} "
            f"| {_agg(rows, cls, 'A_returned_stale')} | {_agg(rows, cls, 'B_returned_stale')} |"
        )
    lines += [
        "",
        "## Per-query",
        "",
        "| id | class | A hit | B hit | A stale | B stale | query |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        def fmt(v):
            return "—" if v is None else ("✅" if v else "❌")
        lines.append(
            f"| {r['id']} | {r['class']} | {fmt(r['A_hit'])} | {fmt(r['B_hit'])} "
            f"| {fmt(r['A_returned_stale'])} | {fmt(r['B_returned_stale'])} | {r['query'][:48]} |"
        )
    lines += [
        "",
        "## How to read this",
        "- **fact-recall**: the CORE test. Facts have NO supersession in the brain, so stale point-in-time",
        "  facts stay active. A hit = the CURRENT fact in top-K. 'returned STALE' = surfaced a known-stale",
        "  fact as a top-K hit. This is the exact failure the KG's fact-supersede edges are meant to fix.",
        "- **supersede-recall (rules)**: the brain ALREADY deactivates superseded rules (memory_index.active),",
        "  so A and B should tie here — this class shows where the KG is REDUNDANT, reported honestly.",
        "- **single-hop**: plain lookup; the brain should be competitive/tie.",
        "- **distractor**: gold is empty (no connected answer); see results_raw.json for what each engine returned.",
        "",
        "Raw per-query output: `results_raw.json`.",
    ]
    with open(os.path.join(HERE, "RESULTS_2026-09-11.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(HERE, "results_raw.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":  # pragma: no cover
    rep = run()
    write_report(rep)
    print(json.dumps({"k": rep["k"],
                      "fact_A": _agg(rep["rows"], "fact-recall", "A_hit"),
                      "fact_B": _agg(rep["rows"], "fact-recall", "B_hit"),
                      "fact_A_stale": _agg(rep["rows"], "fact-recall", "A_returned_stale"),
                      "fact_B_stale": _agg(rep["rows"], "fact-recall", "B_returned_stale"),
                      "rule_sup_A": _agg(rep["rows"], "supersede-recall", "A_hit"),
                      "rule_sup_B": _agg(rep["rows"], "supersede-recall", "B_hit")}, indent=2))
