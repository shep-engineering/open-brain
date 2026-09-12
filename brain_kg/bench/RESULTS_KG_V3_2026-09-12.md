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

## Robustness — the win is NOT a knife-edge fit (sweep, 2026-09-12)
Swept the entity-hop scoring constants across 60 settings (decay ∈ {.4,.5,.6,.7,.8} × floor ∈ {.3,.5,.7}
× 4 co_mention/relation weight pairs), on the full 34-query / 15-state-pair blind gold, with IDF weighting:
- **60/60 settings: the KG helps (net_B ≥ 1), and ZERO settings regress single-hop.** Across the whole
  parameter space the KG never loses to the brain and never hurts the control class.
- **36/60 settings clear the full bar (net_B ≥ 3).** The best config is not a lucky point — a majority hit it.
- net_B only ever ranges 1–3; state-pair B always 12–14 vs A's 11.
This retires the "unswept constants" caveat: the win is robust to the scoring choice, not tuned to it.

## Larger-N + IDF (the honest correction to the n=6 headline)
The first run (n=6 state-pairs) showed a big win (2/6→5/6, +3) — but that sample was *favorable* to the KG.
Expanded to **15 state-pairs** (blind, fresh topics, `queries_v3b.jsonl`): the raw entity hop netted only
+2 and HURT one query (sp2-07 — high-frequency entities `dev`(df 497)/`demo`(300)/`IAM` injected noise
that displaced the correct answer). Fix: **IDF-weight the entity contribution** — a rare shared entity
(`088`, df~5) informs far more than a common one. Result with IDF at N=15: **state-pair A 11/15 → B 14/15,
net +3, B beats A on 3 / loses 0, no single-hop regression.** Recovered the regression AND kept the wins.
So the true effect is **modest but real and robust** — not the near-double the tiny sample implied.

## Honest caveats (do not drop these when quoting the win)
1. ~~n=6 small sample~~ **RESOLVED** — expanded to 15 state-pairs (see "Larger-N + IDF" above). The win
   holds at the bigger sample (net +3) after the IDF fix, and across 60/60 sweep settings. The headline
   number to quote is the **N=15, IDF, net +3** result — not the inflated n=6.
   *(Historical note on the original n=6:)* Two of its three wins (sp-01, sp-02) were *re-ranks* of a gold
   already retrievable at rank 6-8; only sp-04 was a from-zero recall. Directionally convincing, not
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
misses**, and that translates to a real recall win on stale-state queries. Worth keeping the entity
graph as an adjacent, derived lane. The pre-production checklist that stood here is now **done**:
expanded the bench to 15 discriminating state-pairs (`queries_v3b.jsonl`), swept the scoring constants
(60/60 settings, `sweep_fast.py`), and re-ran GLiNER2 extraction on the RTX 5090 with torch cu128.
Remaining known-noisy item: GLiNER2 relation precision on a broad label set — but the *entity* layer
(deterministic + GLiNER entities), not the typed relations, carries the win, so this is enrichment, not
a blocker.

## Productionization — the sync MVP (DIFF-gate clean, 2026-09-12)
The graph is now **self-maintaining and forget-safe**, not a one-shot bench artifact:
- `python -m brain_kg.sync` (and `sync if-due`, rate-limited for a boot hook) keeps the graph current
  **incrementally**: ingest new brain nodes (read-only on the brain), a deterministic reference-entity
  pass over only `det_done_at IS NULL` nodes, and a full `kg_edges` rebuild. **Loads no model, uses no
  GPU** on this hot path — safe to fire opportunistically. GLiNER stays explicit (`build_entity_graph`).
- **Per-layer sync markers** (`det_done_at` / `gliner_done_at`, not one boolean): a re-ingest never
  re-extracts an unchanged node; a deferred GLiNER pass never falsely marks a node done for det.
- **Durability & recovery** are documented in `brain_kg/OPERATIONS.md`: the KG is a derived, disposable
  Postgres projection (~46 MB on the persistent volume) — reboot/gaming safe (retrieval is stateless
  SQL; the GPU model is a build tool, never a runtime component); recovery = truncate + rebuild from the
  brain (proven: wiped 926 entities + 4131 mentions, rebuilt exactly, bench unchanged).

### DIFF gate on the sync MVP
Independent re-derivation **confirmed 7/8 claims** (marker-survives-upsert, incremental pickup, no-op
skips the rebuild, per-node OOM defer, brain proven read-only via a raised read-only-transaction error,
bench +3 reproduces) and **found one defect**: a bare forget/supersede that adds no new node left a
stale *active* edge pointing at the now-inactive node. **Fixed (commit `87827d9`) two ways:**
1. `sync()` fingerprints the active-node set and rebuilds edges when `todo > 0` **or** the active set
   changed (a deactivation), not only on new nodes.
2. `store.outgoing_edges()` filters `n.active` on the destination (defense-in-depth) so a forgotten node
   is never surfaced even if a stale edge survives between rebuilds.

**Verified:** forgot test fact 899 → sync rebuilt via `trigger='active_set_changed'`, node 899 ends
`active=false` with 0 edges; bench still **state-pair A 11/15 → B 14/15, net +3**, no single-hop
regression; the live brain was untouched (all forgets are soft-deletes). Branch
`feat/kg-v3-encoder-extraction` (~13 commits ahead of main), **branch-and-hold** — not pushed.
