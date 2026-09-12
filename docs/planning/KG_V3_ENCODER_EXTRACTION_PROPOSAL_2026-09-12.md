# Brain v3 candidate: a knowledge graph built on real encoder extraction — PROPOSAL
*2026-09-12. Decision doc, NOT a commitment to build. Branch-and-hold. Grounded in the head-to-head
finding from the 2026-09-11 homework (docs/BRAIN_DIAGNOSIS_AND_KG_EXPERIMENT_2026-09-11.md).*

## The finding that motivates this
The overnight KG experiment (brain_kg/) built edges from surface signals — supersede chains, k-NN
cosine neighbors, token overlap — and LOST to the plain brain on the queries a KG is supposed to win
(multi-hop / evolving-state). Measured root cause: two facts about the SAME evolving thing (e.g.
"deploy state 9/08" vs "deploy state 9/10") have **mutual cosine 0.47–0.71** — too low to link. A
cosine-derived graph inherits cosine's blindness. The graph is only as good as the edges you can
extract, and surface-text similarity does not see meaning-level relationships.

## What "real encoder extraction" is
An ENCODER model (GLiNER-class) reads text and emits typed **(entity, relation, entity)** triples in a
single forward pass. It classifies spans that are present; it does NOT generate, so it cannot
hallucinate a relation that isn't there (the "deterministic, no decoder hallucination" property).

Example:
```
"The 088 migration deploys email-intelligence for ARC-774 but can't apply to cloud — dba credential gap."
  -> (088-migration) -[implements]-> (ARC-774)
  -> (088-migration) -[deploys]->    (email-intelligence)
  -> (088-migration) -[blocked-by]-> (dba-credential-gap)

"dba credential chain complete, 088 applied to demo."
  -> (088-migration)     -[applied-to]-> (demo)
  -> (dba-credential-gap) -[resolved]
```
The two facts share ENTITIES (088-migration, dba-credential-gap) even though the sentences barely
resemble each other — so the graph links them where cosine (0.47) could not. That shared-entity link
is the multi-hop capability the whole KG thesis rests on.

## Why it's cheap/safe (addresses the reasons the idea was shelved)
- **Encoder, not decoder / LLM.** BERT-family span classifier, ~few hundred MB, CPU inference in tens
  of ms. Not the 35B metadata LLM whose eviction/thrash cost killed earlier auto-classification.
- **Off the hot path.** Extract at WRITE time (or a batch pass); search stays a fast graph walk. No
  model call per query.
- **Constrained vocabulary = deterministic.** GLiNER takes the label set as input. We supply a fixed
  domain relation set, so output is bounded and auditable, not open-ended generation.

## What building it looks like (concrete, ordered)
1. **`brain_kg/extract.py`** — wire a GLiNER-class model behind the already-stubbed
   `extract(text) -> [(head, relation, tail)]` interface. Define a FIXED relation vocabulary
   (implements, blocks, resolves, supersedes, deploys-to, owns, part-of, ...). This is the input side.
2. **Batch-extract the corpus** into the existing `kg_nodes`/`kg_edges` tables — real typed edges
   replacing the cosine-neighbor guesses.
3. **Entity resolution (the hard part, where KG projects live or die):** "088" / "the 088 migration" /
   "migration 088" must resolve to ONE node. GLiNER gives spans; linking needs a normalization pass
   (alias table + fuzzy/embedding match + a human-review queue for ambiguous merges). Under-invest here
   and the graph fragments; over-merge and it corrupts. Budget real time for this.
4. **Re-run the EXISTING head-to-head bench** (brain_kg/bench/, unchanged queries + gold) — the honest
   pass/fail gate. If entity-linked edges now beat the brain on the multi-hop/state-pair class (where
   cosine tied or lost), the KG is proven worth it. If not, we learned it cheaply, again, with numbers.
5. **Only then** consider making the KG a first-class retrieval path (graph_recall surfaced to agents),
   never authoritative over the typed stores — a derived, rebuildable projection.

## Cost / risk honesty
- Real project: days, not an overnight unit. Adds a model dependency (small, but a dependency).
- Entity resolution is an ongoing maintenance surface, not a one-time build.
- Worth it ONLY if genuinely multi-hop queries ("what rule came from the incident about X, and is it
  still current?") happen often enough to justify the pipeline. If most recall is single-topic lookup,
  the four v0.28.0 fixes already solve the felt pain (immortal stale facts) at zero model cost.

## Recommendation
Ship v0.28.0 first (done, free, addresses the actual pain). Treat the KG as a SEPARATELY-SCOPED v3
decision, gated on: (a) evidence of real multi-hop query demand, and (b) the existing bench proving an
entity-extracted graph beats the brain. The bench I built is the guardrail that keeps this an
evidence-based decision, not a vibe.

## Status
PROPOSAL ONLY. Nothing built for this beyond the extract.py stub already on the branch. Awaiting Shep's
decision on whether v3 is worth starting.
