# brain_kg — operations, durability & recovery

The knowledge graph is an **adjacent, derived, disposable** projection of the brain. This doc is the
answer to "what happens when the machine reboots / it corrupts / it rots."

## Durability model (why a reboot / gaming session is safe)
- The graph lives in **Postgres** (`open_brain_kg`, ~46 MB) on the **persistent Docker volume**
  (`open-brain_open_brain_v2_data`), same durability as the brain's own store. Power off → it's on disk
  → reboot brings it back. Nothing load-bearing is in RAM.
- **Retrieval is stateless SQL** (`graph_recall`): no model, no in-memory index, no warm-up. Works
  immediately after a cold boot.
- **The GPU/GLiNER2 model is a BUILD tool, not a runtime component.** It is only loaded during
  extraction. Using the graph (queries) never loads it. So gaming on the 5090 never touches the KG.

## The anti-brittleness property (why this KG ≠ a brittle system-of-record KG)
The brain (`open_brain_v2`) is the **source of truth**; the KG is regenerated from it. Therefore:
- **A corrupt/weird graph cannot poison your memories** — it is downstream, never authoritative.
- **Recovery = truncate + rebuild.** You lose nothing. (Proven 2026-09-12: wiped 926 reference entities +
  4131 mentions, rebuilt, got exactly 926 back and the benchmark passed identically.)
- Retrieval **never regresses** even with a bad graph: `graph_recall` seeds on the same cosine as the
  brain and only *adds* entity edges (sweep: 60/60 settings, zero single-hop regressions).

## Durability strategy: DO NOT back up the KG
Backing up a derived store is the brittle pattern — the backup drifts from the source. Instead:
- The **brain IS backed up** (`scripts/backup-brain.sh` dumps `open_brain_v2`).
- The **KG rebuilds from the brain** on demand. If the KG DB is ever lost/corrupt, recreate it.

## Recovery procedures

### Keeping the graph current — the SYNC job (routine, cheap, NO GPU)
```
# from F:\open-brain — ingest new memories + deterministic pass + full edge-rebuild
python -m brain_kg.sync            # run now
python -m brain_kg.sync if-due     # rate-limited (OPEN_BRAIN_KG_SYNC_INTERVAL_HOURS, default 6) — for a boot hook
```
Loads NO model, so it's safe to fire opportunistically (boot/PostToolUse hook via `sync_if_due`). It only
does the cheap layers: ingest new brain nodes, deterministic reference-entity pass over `det_done_at IS
NULL` nodes, and a full edge-rebuild (kg_edges — required, not incremental, so new state supersedes old).
Idempotent: with nothing new it does no deterministic work and skips the edge rebuild. Per-layer markers
(`det_done_at`, `gliner_done_at`) mean a re-ingest never re-extracts an unchanged node.

**GLiNER typed relations are NOT in the sync** (a boot-hook GPU load self-defeats — the embedder is
hottest at boot). Run them explicitly/rarely (see B). They add entity richness but the deterministic
layer already carries the retrieval win, so this is optional enrichment.

### A. Deterministic layer only (fast, NO GPU) — most corruption
```
# from F:\open-brain
python -m brain_kg.build_entity_graph det
```
Reads the brain corpus, re-derives the reference entities (ticket/migration/env/role tokens) + mentions.
Instant, no model, no GPU. Idempotent. (The sync above does this incrementally; this does the whole corpus.)

### B. Full rebuild incl GLiNER2 entity/relation extraction (HEAVY — GPU)
Runs GLiNER2 over every active node on the **RTX 3080 Ti** (the 5090 stays free). **Warn before running:**
it is GPU-bound on a VRAM-tight card shared with the embedder.
```
# ensure the 3080 Ti has headroom first (embedder VRAM is bursty)
python -m brain_kg.ingest                 # refresh nodes from the brain (read-only on the brain)
python -m brain_kg.build_entity_graph     # full: GLiNER2 triples + deterministic entities
```
- Device is auto-pinned to the GPU whose name contains `3080` (override `OPEN_BRAIN_KG_GPU_NAME`).
- Loading **refuses** if free VRAM < `OPEN_BRAIN_KG_MIN_FREE_VRAM_MIB` (default 1800) — it will not OOM or
  thrash the embedder; run it when the embedder is idle, or lower the threshold to override (risks OOM).
- To rebuild from scratch: `TRUNCATE kg_entity_edges, kg_entity_mentions, kg_entities RESTART IDENTITY;`
  (KG DB only — never touches the brain) then run the above.

### C. KG DB lost entirely
```
docker exec open-brain-v2-db psql -U postgres -c "CREATE DATABASE open_brain_kg;"
python -c "import psycopg2; from brain_kg.schema import apply_schema; \
  apply_schema(psycopg2.connect('postgresql://postgres:password@localhost:5433/open_brain_kg'))"
python -m brain_kg.ingest && python -m brain_kg.build_entity_graph   # (heavy step B)
```

## The one real brittle spot to watch
**Entity resolution is deterministic-but-naive** (canonical key = lowercase + stopword strip). It handles
this corpus's structured references well but *will fragment* on messy free-text entity mentions as the
corpus grows (e.g. person names, prose phrases). Watch for: many near-duplicate `kg_entities` rows, or a
falling cross-fact-link count. The fix path (deferred) is fuzzy/embedding entity resolution — gated on it
becoming a real problem, not built speculatively.

## Isolation guarantees (verified)
- Extraction reads the brain **read-only** (`default_transaction_read_only=on`); it cannot write it.
- All KG writes go only to `open_brain_kg`. The live brain is never modified by KG work.
- Torch/GLiNER2 live in a **separate venv** (`brain_kg/.venv-kg`), never the brain runtime.
