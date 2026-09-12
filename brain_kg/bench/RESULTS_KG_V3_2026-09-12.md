# KG v3 — encoder entity-extraction graph vs the brain (honest head-to-head)
*2026-09-12. Branch `feat/kg-v3-encoder-extraction`. DIFF-gate confirmed. Read the caveats.*

## Bottom line
A knowledge graph built from **encoder-extracted entities** (GLiNER2 + a deterministic
reference-entity pass) **beats the brain's flat vector-cosine retrieval** on stale-state recall,
clearing a bar that was fixed *before* the result was seen. **But the sample is small (n=6 on the
discriminating class) — this is a directional, correctly-measured signal, not a statistically powered
claim.**

## The numbers (Engine A = brain flat-cosine, Engine B = KG graph_recall; both return exactly K=5)
| Class | A recall@5 | B recall@5 | verdict |
|---|---|---|---|
| **state-pair** (the discriminating class) | **2/6** | **5/6** | **B wins** |
| multi-hop | 6/7 | 6/7 | tie |
| single-hop (control) | 2/3 | 2/3 | tie (no regression) |
| distractor | (both return nothing confident) | | tie |

**Net: B beats A on 3 discriminating queries, A beats B on 0 → +3, meeting the pre-committed bar (≥3, no
single-hop regression).** Reproduced identically across 3 runs including one in a fresh post-restart process.

## Why it wins (verified, not asserted)
- Both engines seed on the **same** cosine top-5 (seed_overlap = 5 = K on every winning query), so the
  entire A/B delta is the **entity-graph expansion**, not a different embedder or more items.
- The cleanest case, **sp-04** (RLS-drift state): cosine never surfaced the gold "resolved" incident
  (363) anywhere in its top-20. The entity graph reached it at rank 2 via schema-object entities the
  query names (`data_classification`, `role_capabilities`, `privileged_role_register`, `RLS`) shared
  between the query's seed and the target. That's the exact cosine-blind connection the whole
  investigation was about.

## The key lever: deterministic-entity coverage
The first bench was a **tie** (a loss vs the bar). Root cause, quantified: GLiNER2's generic label set
left **42% of gold facts with zero extracted entities** — the graph couldn't reach them. The fix was a
**deterministic reference-entity pass** (`build_entity_graph.deterministic_entities`) over the corpus's
explicit references (ticket ids, migration numbers, environments, RLS/SSM/role/table/file names). It
added ~4155 mentions, reaching **1284/2206 nodes (~58%)** — a general mechanism, not gold-specific. That
closed the gap and the state-pair result went 2/6 → 5/6.

## Honest caveats (do not drop these when quoting the win)
1. **n=6 state-pair is a small sample.** Two of the three wins (sp-01, sp-02) are *re-ranks* of a gold
   already retrievable at rank 6-8; only sp-04 is a from-zero recall. Directionally convincing, not
   powered. Don't quote "5/6" without the n=6.
2. **Scoring constants are unswept hyperparameters.** `retrieve.py`'s entity-hop scoring (`1-0.6^support`,
   co_mention 1.0 vs relation 0.5, `0.5+0.5*factor`) is principled (monotonic in shared-entity count) and
   **not** tuned to the gold — but it was never swept, so treat it as a reasonable default, not an optimum.
3. **GLiNER2 relation precision is noisy** with a broad label set (it emitted "088 supersedes cloud").
   The *entity* extraction carries the win here more than the *relations*.

## Non-destruction
The live brain (`open_brain_v2`) was never touched — all KG work wrote only `open_brain_kg`. Extraction
ran in an isolated venv (`brain_kg/.venv-kg`), never the brain runtime.

## Validation trail
- PLAN gate: caught a fabricated "extract.py stub already exists" premise; re-scoped to a real schema
  migration + deterministic-ER slice.
- Pre-committed win bar set before any result (superseded fact 880 → 885).
- Blind gold: 19 queries derived by a reviewer from content, blind to the edge set, all gold active.
- DIFF gate: confirmed the win is honest and not gold-fitted (patterns general, gold honest, delta is
  real entity expansion), with the n=6 caveat.

## Verdict + recommendation
The thesis holds: **encoder entity extraction links same-topic facts that flat cosine (0.47 similarity)
misses**, and that translates to a real recall win on stale-state queries — *on this small sample*. Worth
keeping the entity graph as an adjacent, derived lane. Before treating it as production-ready: expand the
bench (more discriminating queries), sweep the scoring constants, and re-run extraction on the GPU (now
that torch cu128 works on the 5090) with a domain-tuned GLiNER2 label set to lift relation precision.
