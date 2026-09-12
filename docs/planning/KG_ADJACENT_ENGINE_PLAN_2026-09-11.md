# Adjacent Knowledge-Graph Memory Engine — Plan + Build + Head-to-Head Harness
*2026-09-11 overnight. Branch: `feat/kg-adjacent-memory-engine`. Non-destructive: `brain_v2/` is not touched.*

## The assignment (Shep, verbatim intent)
1. Troubleshoot my brain (open-brain-v2). Done — diagnosis below, empirically confirmed against the live DB.
2. Research. Work via branches. Do NOT destroy what we built. Use the agnostic-validator loop.
3. Build an **adjacent** engine alongside the brain (a knowledge-graph memory engine).
4. **Test the two against each other** so Shep can see the comparison in the morning.

## Part 1 — Diagnosis (empirically confirmed, live `open_brain_v2` 2026-09-11)
The brain has a **vector store** (pgvector embeddings) + a **relational store** (four typed tables)
but **NO knowledge graph**. Concretely, verified by querying the live DB:

| Claim | Evidence (live query) |
|---|---|
| Facts are immortal + un-correctable | `facts` columns = `id,headline,body,project,tags,ttl,confidence,access_count,last_accessed,source,created_at` — **no `superseded_by`, no `active`, no `decayed_at`.** 871 facts, 861 "active." |
| Correction exists, but only for rules | `rules` has `supersedes,superseded_by,supersede_reason` — the primitive was built for one type only. |
| No graph exists | `information_schema.tables` matching node/edge/relation/triple/graph = **empty.** Only `linked_incident_ids INTEGER[]` — an ad-hoc array pointer, not a queryable typed edge. |
| Recall is headline-only | `memory_index` (the search table) carries `headline` but **not `body`** — retrieval returns headlines, forcing re-derivation of detail. |
| Retrieval surfaces noise, not connected facts | LIVE: `search_v2("user preferences formatting rules")` returned a timetrack CLI, Archen financials, API shapes — **zero prefs.** KG-topic query surfaced junk facts headlined `1,2,3,4` above real content. Pure embedding-cosine ranking has no notion of *which facts connect to which.* |

**The irony that proves it:** when I tried to correct fact #871 tonight, the write gate told me to
"route to supersede(old_id=871)" — but **facts have no supersede path.** The store recommended an
operation its own schema cannot perform.

### Why a knowledge graph fixes exactly these failures
Per Shep's text (and the Zep/Cognee/A-MEM literature the prior synthesis cited but never built):
- **Multi-hop reasoning without burning tokens.** "What did I learn about X that connects to the
  incident that produced rule Y?" is a 2-hop graph walk, not a wall of embeddings to re-read.
- **Cheap real-time edge/node updates.** A new relation is one edge insert. Correcting a belief is
  invalidating one edge (temporal validity), not rewriting a body.
- **Inspectable/auditable reasoning paths.** The path that produced an answer is a concrete subgraph.
- **Cheap deterministic extraction with a small ENCODER model (GLiNER-class).** Entities + relations
  in a single forward pass, no decoder, no hallucination. Extraction is the input side of the graph.

## Part 2 — What I am NOT doing (guardrails)
- NOT modifying `brain_v2/` (schema, store, server, tests all untouched).
- NOT cutting over. NOT touching the live `open_brain_v2` data. The KG engine gets its **own DB**
  (`open_brain_kg`) so a bug cannot corrupt the brain.
- NOT auto-resolving contradictions (synthesis §9: flag, never auto-resolve). Edge invalidation is
  explicit + audited.
- NOT proposing to raise the boot cap (Lost-in-the-Middle is real).

## Part 3 — The adjacent engine (`brain_kg/`)
A temporal knowledge graph over the SAME memory corpus, as a peer package. Design principles carried
forward from what already works (do not reinvent): atomic typed nodes, supersession-as-correction,
headline-scannable, hard-capped boot, audit on every write.

### RE-SCOPE (2026-09-11, folding PLAN-gate blocking findings #2/#3/#5)
The PLAN-gate reviewer re-derived the edge inventory from the live corpus and proved the original
Tier-A thesis is **unsupported by this data**: 0 rule→incident links, 1 incident→rule link, 17/871
tagged facts, prose (not noun-phrase) headlines. A "rule that came from the incident about X" 2-hop
graph has a population of ONE. Building it would yield a near-star graph that proves nothing. So the
thesis is re-scoped to **exactly the failure I lived tonight, which the data DOES support**:

> **Re-scoped thesis:** When a query matches a *retired/superseded* memory, flat vector-cosine
> (the brain) surfaces the STALE version; a graph that carries the **supersede edge** walks one hop
> to the **current** belief. Plus: k-NN **semantic-neighbor edges** over the copied embeddings turn
> 2358 isolated nodes into a traversable graph, so a seed can reach a *connected* answer the flat
> top-K missed. These are the two edge classes the corpus actually has in quantity (130 supersede
> chains; dense embedding neighborhoods).

### 3.1 Data model — minimal belief-time graph (bi-temporal CUT for tonight)
Two tables in the KG's OWN DB. Dropped per reviewer #5: `valid_from` fact-time axis, fuzzy
`canonical_key` entity-resolution. Kept: belief-time correction + provenance.

```
kg_nodes(id, kind, memory_id, project, headline, body, embedding VECTOR(4096),
         active BOOLEAN, source, created_at)       -- active=FALSE = no longer believed (superseded/forgotten)
kg_edges(id, src_id, dst_id, relation, weight, provenance_memory_id,
         active BOOLEAN, created_at)               -- relation ∈ {supersedes, neighbor, same_project}
```
- **Correction = `active=FALSE`**, not deletion — still auditable. This is the facts-supersession the
  brain lacks, generalized: any node/edge can be invalidated without losing the record.
- **`provenance_memory_id`** ties every edge to the memory that asserted it (auditable path).
- Node dedup is **exact `(kind, memory_id)`** only (documented limitation: no fuzzy entity resolution).

### 3.2 Edge construction (input side) — deterministic, from signals the corpus HAS
`brain_kg/build_edges.py`, no model call, three real edge classes only:
- **`supersedes`** (130 typed edges): straight from `rules.superseded_by` — corrector→retired. The
  retired node gets `active=FALSE`. This is the correction backbone.
- **`neighbor`** (the multi-hop payload): for each node, its top-N cosine neighbors over the COPIED
  embeddings (same vectors the brain ranks on). This is what makes the graph traversable; it uses the
  brain's own embeddings so it introduces no new signal the brain didn't already have — only new
  *structure* over that signal.
- **`same_project`** (grouping): light edges among nodes sharing a non-empty project, capped per node
  so dense projects (archen-ai-layer=706) don't create a hairball.
- **Tier B (real body extraction) is CUT tonight** (reviewer #5). One-line docstring marks the
  `extract(text)->[(h,rel,t)]` seam for later; no stub code, no model in the hot path.

### 3.3 Retrieval (output side) — the thing being tested, fairness locked
`brain_kg/retrieve.py`: `graph_recall(query, k, hops=1)`:
1. **Seed — IDENTICAL to Engine A** (reviewer #4): same embeddings, same cosine, same top-K as
   `brain_v2.store.search_headlines`. Enforced in code; the harness asserts seed-only(B) == A.
2. **Expand:** walk `hops` `active` edges from each seed (supersede edge → current belief;
   neighbor/same_project → connected node).
3. **Rank** the reached set by seed-similarity × edge-weight × `KG_HOP_DECAY^hop`.
4. **Return exactly K** (reviewer #4: answer-set size matched to A, so recall@K is not a volume win)
   + the traversal path (auditable "why these").

### 3.4 Non-destructive build-from-brain (safety hardened, reviewer #1)
`brain_kg/ingest.py` reads `open_brain_v2` **read-only** and projects into `open_brain_kg`:
- Connects with `default_transaction_read_only=on` (and, if a `brain_kg_reader` SELECT-only role can
  be created, uses it); **capped at 1 connection**; each read in a short transaction (no long snapshot).
- **Mid-ingest live-brain health probe** (`health_v2`) — proves the brain stayed responsive, not just
  that row counts matched after. Row-count equality alone does NOT prove no lock/resource contention.
- Copies embeddings (not recompute) → identical seed + zero Ollama load.
The brain is the source; the KG is a derived, disposable projection (drop = lose nothing).

## Part 4 — The head-to-head harness (`brain_kg/bench/`), fair by construction
`bench/harness.py` runs the SAME query set against BOTH engines, **both returning exactly K**:
- **Engine A = the brain:** `brain_v2.store.search_headlines` (current flat-cosine retrieval).
- **Engine B = the KG:** `graph_recall` (same seed, + graph expansion). Reports **seed-only** (must
  equal A — a self-check that the embedding copy is faithful) AND **seed+graph**.
- **Query set** (`bench/queries.jsonl`) — frozen + committed before engines run, AND made fair per
  reviewer #3:
  - **`gold` is derived from memory CONTENT, blind to the edge set.** Gold = "which memory actually
    answers this," judged from bodies — NOT "which memory the graph connects." The diff-gate reviewer
    re-derives ≥3 gold answers from bodies alone to confirm no gold row merely restates an edge.
  - **`supersede-recall` class** (the honest thesis): query phrased in terms of a RETIRED memory; gold
    = the CURRENT (non-superseded) belief. Flat cosine tends to return the stale one; the graph should
    hop to current.
  - **`connected` class:** gold is a memory that is a semantic neighbor but outside flat top-K.
  - **`single-hop` class:** plain lookup — the brain should tie or win; if B loses here the table says so.
  - **`distractor/negative` class** (reviewer #3): gold = "no connected answer" — a graph that always
    returns something connected is penalized for false positives.
- **Metrics:** recall@K, MRR, and for the supersede class a **staleness flag** (did the engine return a
  DEPRECATED/superseded memory as a top hit?). Gold in `bench/gold.jsonl`, frozen.
- **Output:** `bench/RESULTS_2026-09-11.md` — side-by-side table + raw per-query JSON. Honest: the
  realistic expected outcome is a NARROW, specific win on the supersede-recall + connected classes,
  parity on single-hop, and B must not beat A by volume (K matched).

## Part 5 — The validator loop (both gates RAN; both caught real defects)
- **PLAN gate (done):** adversarial reviewer re-derived the diagnosis + re-scoped the thesis (Tier A
  couldn't build a multi-hop graph on this corpus). Verdict PROCEED-WITH-CHANGES, folded. Ledgered.
- **EXECUTE:** built `brain_kg/` on the branch, own DB `open_brain_kg`, read-only ingest. Tests 4/4 pass.
- **DIFF gate (done):** adversarial reviewer re-ran the bench + re-derived gold from bodies. Verdict
  **BLOCKED** — the first benchmark was too broken to conclude anything. Findings + fixes:
  1. Inverted gold (fact 868 = an abandoned engine, labeled "current") → gold re-derived from CONTENT.
  2. Unwinnable gold (fact 578 is `active=FALSE` in the brain, unreachable) → dropped.
  3. Fact-recall couldn't isolate the KG — every "stale" fact tested was ALREADY brain-inactive → added
     a **state-pair class** scored by RANK on facts that are ACTIVE in the brain (814/832/848, 805/821).
  4. Implicit cosine>0.82 heuristic false-demoted ~8 valid bio/resume facts → REMOVED; replaced with a
     narrow state-marker + entity-overlap signal.
  5. Tests skipped (0 coverage) → own `conftest.py`, now **4/4 pass**.
  6. Fairness docstring overstated ("seed==A") → corrected to report `seed_overlap` honestly.
  All findings ledgered. Both gates ledgered at `F:\agentic-governance\validation-log\PLAN_GATE_REJECTIONS.md`.

## Part 5.1 — KEY NEGATIVE FINDING (honest, and it matters)
A cosine-derived KG inherits cosine's blindness. The real stale-state pathology is successive deploy-state
snapshots (848 vs 814 vs 832; 821 vs 805) — the SAME evolving topic but with **LOW mutual cosine
(0.47–0.71)**. Embeddings alone cannot link them, so neither the brain's flat cosine nor a cosine-neighbor
graph co-ranks them. The supersede signal must come from what cosine CANNOT see: shared **entity** tokens
(ticket ids, migration numbers, named subsystems), timestamps, explicit "corrects fact N". This is the
encoder/relation-extraction the KG literature (Zep temporal graph) and Shep's own diagram (GLiNER-class
extraction) call for — and it is the deterministic edge signal `build_edges._build_entity_overlap_edges`
now adds. The measured outcome of that is the actual homework result.

## Part 6 — Success criteria (what "marked improvement" means, measurably)
1. A real graph exists over the real corpus (node/edge counts > 0, edges carry provenance).
2. The bench runs both engines on a frozen query set and produces a committed side-by-side result.
3. On the multi-hop class, Engine B beats Engine A on recall@K by a margin the results table quantifies
   — OR, if it doesn't, the table shows exactly where and why (honest negative is still a finding).
4. `brain_v2/` and `open_brain_v2` are byte-for-byte unchanged (proven: `git diff` clean on brain_v2,
   and a row-count probe of the live brain before/after is identical).
```
