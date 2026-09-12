# Troubleshooting my brain: diagnosis + an adjacent knowledge-graph experiment
*2026-09-11 overnight. Branch `feat/kg-adjacent-memory-engine`. Non-destructive: the live brain
(`brain_v2/`, DB `open_brain_v2`) was never modified. Everything below is validated against the
live DB and through both gates of the agnostic-validation loop.*

## 1. The assignment
Shep: "troubleshoot your brain" using a memory-engine diagram (agent loop ↔ session + permanent
memory → **knowledge graph** [highlighted], vector store, relational store) and text on why KGs help
(multi-hop reasoning without burning tokens, cheap edge updates, auditable paths, cheap deterministic
entity/relation extraction with small encoder models). Then: research, work via branches, don't
destroy what exists, use the validator loop, **build an adjacent engine alongside the brain and test
them against each other.**

## 2. Diagnosis (empirically confirmed against live `open_brain_v2`)
The brain has a **vector store** (pgvector, qwen3-embedding:8b @ 4096d) + a **relational store**
(four typed tables) but **no knowledge graph.**

| Finding | Evidence (live query) |
|---|---|
| Facts are immortal + un-correctable | `facts` has no `superseded_by`/`active`/`decay` column. 871 facts, 861 active. |
| Correction exists for RULES only | `rules` has `supersedes`/`superseded_by`; 130 real supersede chains. |
| No graph table | node/edge/relation/triple/graph tables = none. Only `linked_incident_ids INTEGER[]` (populated 0×). |
| Recall is headline-only | `memory_index` (the search table) has `headline`, not `body`. |
| Retrieval surfaces noise | `search_v2("user preferences formatting rules")` returned a timetrack CLI, financials, API shapes — zero prefs. Junk facts headlined `1,2,3,4` outranked real content. |

The irony that names the bug: the write gate told me to "route to supersede(fact 871)" — **but facts
have no supersede path.** The store recommends an operation its own schema cannot perform.

## 3. What I built (the adjacent engine, `brain_kg/`)
A minimal belief-time knowledge graph over the SAME corpus, in its OWN DB (`open_brain_kg`), so a bug
can't touch the brain. Reuses the brain's OWN stored embeddings (copied, not recomputed) so the
vector-seed step is identical — the only thing measured is the graph.
- `kg_nodes(active)` + `kg_edges(relation, provenance_memory_id, active)`. Correction = `active=FALSE`
  (belief-time), never deletion — the facts-supersession the brain lacks, generalized to every type.
- Edges: `supersedes` (130 from rules; + fact-supersede from explicit "corrects fact N" and a narrow
  entity-overlap signal), `neighbor` (k-NN over copied embeddings), `same_project`.
- `graph_recall(query, k, hops)`: seed on cosine (identical to the brain), walk `active` edges (a
  stale seed hops its supersede edge to the current belief; neighbor edges reach connected nodes),
  return exactly K + the auditable path.
- Read-only ingest (session-level `default_transaction_read_only=on`, single connection, mid-ingest
  health probe). Row-count parity proven before/after every run. `brain_v2/` git-clean throughout.
- Tests: 4/4 pass (correction primitive, seed-excludes-inactive, exactly-K, neighbor-reach).

## 4. The head-to-head (both validator gates ran; both caught real defects)
- **PLAN gate** re-scoped the thesis: the corpus can't feed the multi-hop graph I first proposed
  (0 rule→incident links, 17/871 tagged facts). Folded before building.
- **DIFF gate** returned BLOCKED on my first benchmark — and was right: inverted gold, an unwinnable
  gold row, a cosine heuristic that false-demoted ~8 valid facts, and tests that didn't run. All
  fixed: gold re-derived from content, a RANK-scored `state-pair` class added on facts that are
  ACTIVE in the brain, the bad heuristic removed, tests made to run (4/4).

## 5. The honest result + the finding that matters
- For **RULES**, the brain is already fine — it deactivates superseded rules (`memory_index.active`),
  so the KG's rule-supersede is **redundant.** (A ≈ B on the rule class. Reported, not hidden.)
- The real gap is **FACTS** (no supersession → stale state snapshots accumulate, all active, all
  returned). This is the lived recall failure.
- **KEY NEGATIVE FINDING:** a cosine-derived KG inherits cosine's blindness. The stale-state pathology
  is successive deploy-state snapshots (848 vs 814 vs 832; 821 vs 805) — the SAME evolving topic but
  **low mutual cosine (0.47–0.71)**, and terse summaries (848: "demo-green is ONE owner-grant away")
  share almost no machine-extractable tokens with the detailed snapshots they supersede. Embeddings
  alone cannot link them; nor can a token-overlap heuristic reliably. **This is precisely why the KG
  literature (and Shep's diagram) uses a small ENCODER model for entity/relation extraction** — the
  deterministic signals present in THIS corpus are too sparse. The graph mechanism works (proven by
  tests and the explicit 578→577 correction edge); the INPUT side (extraction) is the real bottleneck.

## 6. What this says about fixing the brain (recommendations)
1. **Give FACTS a correction primitive** (the cheapest, highest-value fix, entirely within the current
   architecture): add `superseded_by`/`active` to facts + a `supersede_fact` path, so the write gate's
   own "route to supersede" hint becomes executable. This alone removes the immortal-stale-fact wall.
2. **Add a real extraction step** (encoder model, GLiNER-class, off the hot path / batch) to populate
   typed edges the corpus doesn't spell out — the KG only pays off with real relations, not cosine.
3. **Store body in the search projection or rank on it**, so recall stops being headline-only.
4. The KG is worth keeping as an ADJACENT, derived projection (it never needs to be authoritative) for
   multi-hop/auditable queries — but only after (2), or it just re-ranks what cosine already gives.

## 7. Non-destruction proof
- `git status --short brain_v2/` → clean every check.
- Live brain row counts identical before/after every ingest (facts=871; rules/incidents/memory_index
  drift only from other live sessions, not from the KG — separate DB).
- KG lives entirely in `open_brain_kg`; dropping it loses nothing not reconstructable from the brain.
