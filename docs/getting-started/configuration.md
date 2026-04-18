# Configuration

All configuration is via environment variables in `.env` (or passed through your MCP client's `env` block).

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://postgres:<your_password>@localhost:5432/openbrain` | PostgreSQL connection string |
| `EMBEDDING_PROVIDER` | `ollama` | `ollama` or `openai` |
| `EMBEDDING_DIMENSIONS` | `768` | Must match your embedding model. Set before `setup_db.py`. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model name |
| `OPENAI_API_KEY` | *(empty)* | Required only for OpenAI embeddings |
| `METADATA_LLM_MODEL` | *(empty)* | e.g. `qwen2.5:14b`. Enables LLM metadata extraction and smart merge. Empty = heuristic only. |
| `DEDUP_THRESHOLD` | `0.92` | Cosine similarity threshold for hard deduplication (no LLM needed) |
| `OPEN_BRAIN_MERGE_LOWER_THRESHOLD` | `0.70` | Lower bound of smart-merge gray zone `[lower, DEDUP_THRESHOLD)`. Memories in this range are sent to the LLM for ADD/MERGE/REPLACE/SKIP decision. Requires `METADATA_LLM_MODEL`. |
| `OPEN_BRAIN_CONSOLIDATION_INTERVAL` | `0` | Seconds between background consolidation passes (0 = disabled). Requires `METADATA_LLM_MODEL`. |
| `COMPLIANCE_WINDOW` | `300` | Seconds before a search is considered stale for compliance warnings |
| `COMPLIANCE_MAX_STORES` | `5` | Max stores (remember/capture_context) allowed without a recent search. Set to `0` to disable. |
| `OPEN_BRAIN_DECAY_LAMBDA` | `0.005` | Recency decay rate. Score multiplied by `exp(-lambda * uptime_days_since_access)`. Set to `0` to disable. |
| `OPEN_BRAIN_HYBRID_WEIGHT` | `0.3` | Weight for full-text component in hybrid search. `0.3` = 70% vector + 30% keyword. Set to `0` for pure vector. |
| `OPEN_BRAIN_UPTIME_FLUSH_INTERVAL` | `60` | Seconds between uptime counter flushes to DB. Max uptime lost on hard kill. |
| `OPEN_BRAIN_PORT` | `8080` | HTTP transport port |
| `OPEN_BRAIN_HOST` | `0.0.0.0` | HTTP transport host |
| `OPEN_BRAIN_CHECK_INTERVAL` | `0` | Hours between unwired-agent checks. 0 = disabled. |

---

## Embedding Models

| Model | Provider | Dimensions | Cost |
|-------|----------|-----------|------|
| `nomic-embed-text` | Ollama (local) | 768 | **Free** |
| `mxbai-embed-large` | Ollama (local) | 1024 | **Free** |
| `text-embedding-3-small` | OpenAI | 1536 | ~$0.02/1M tokens |

!!! warning "Dimension lock-in"
    Set `EMBEDDING_DIMENSIONS` **before** running `setup_db.py`. Changing dimensions later requires dropping and recreating the `memories` table.

---

## Metadata LLM

For richer metadata extraction **and smart memory merging**, point at a local Ollama model:

```env
METADATA_LLM_MODEL=qwen2.5:14b
```

When set, the LLM is used for:

1. **Metadata extraction** — richer people/topic/tag/type classification (~2-5s per capture)
2. **Smart merge decisions** — when a new memory is semantically related to an existing one (similarity in the gray zone `[0.70, 0.92)`), the LLM decides:
   - `ADD` — distinct enough, store separately
   - `MERGE` — related, LLM writes a combined memory
   - `REPLACE` — new contradicts/supersedes old
   - `SKIP` — essentially a repeat
3. **Background consolidation** — if `OPEN_BRAIN_CONSOLIDATION_INTERVAL > 0`, a background thread periodically merges related memories

If the LLM call fails for any reason, it automatically falls back to fast heuristic extraction and `ADD` (store separately).

!!! tip "Two models at once"
    If you use both `nomic-embed-text` and a metadata model, start Ollama with `OLLAMA_MAX_LOADED_MODELS=2` to avoid repeated model evictions.

---

## Transport Modes

```sh
# Default: stdio (for editors/CLI)
python server.py

# HTTP streaming (for claude.ai connectors, ChatGPT Desktop)
python server.py --transport http --port 8080

# Both simultaneously
python server.py --transport both
```

---

## brain_v2 Configuration

v2 has its own environment variables. All are optional — defaults target the v2 container on port 5433.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPEN_BRAIN_V2_DATABASE_URL` | `postgresql://postgres:password@localhost:5433/open_brain_v2` | v2 PostgreSQL connection string |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Shared with v1 |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model (v2 does not use a metadata LLM) |
| `OPEN_BRAIN_V2_EMBED_TIMEOUT` | `120` | Seconds for Ollama embedding calls (handles cold model loads) |
| `OPEN_BRAIN_V2_EMBEDDING_DIMS` | `768` | Must match your embedding model |
| `OPEN_BRAIN_V2_BOOT_TOKEN_CAP` | `2000` | Maximum tokens in boot payload |
| `OPEN_BRAIN_V2_BOOT_BLOCKER_CAP` | `5` | Max BLOCKER rules in boot |
| `OPEN_BRAIN_V2_BOOT_PATTERN_CAP` | `5` | Max PATTERN rules in boot |
| `OPEN_BRAIN_V2_BOOT_TASK_CAP` | `20` | Max active tasks in boot |
| `OPEN_BRAIN_V2_BOOT_HANDOFF_CAP` | `200` | Max tokens for handoff note |
| `OPEN_BRAIN_V2_DUPLICATE_COSINE` | `0.75` | Cosine threshold for duplicate detection |
| `OPEN_BRAIN_V2_FACT_HALFLIFE_DAYS` | `7.0` | Ebbinghaus decay halflife for facts |
| `OPEN_BRAIN_V2_FACT_DECAY_THRESHOLD` | `0.1` | Score below which facts deactivate |
| `OPEN_BRAIN_V2_INCIDENT_ARCHIVE_DAYS` | `90` | Days before incidents auto-archive |
| `OPEN_BRAIN_V2_PRUNE_MIN_DAYS` | `30` | Hard floor — never prune anything newer |
| `OPEN_BRAIN_V2_PRUNE_MAX_DELETE` | `50` | Max rows deleted per prune call |
| `OPEN_BRAIN_V2_SKILL_TRIGGER_MAX` | `5` | Max skills surfaced per search query |
| `OPEN_BRAIN_V2_SLOW_CALL_MS` | `10000` | Threshold for slow-call alerts (ms) |
| `OPEN_BRAIN_V2_HEADLINE_WORD_CAP` | `15` | Max words in a headline |
| `OPEN_BRAIN_V2_BODY_WORD_CAP` | `200` | Soft ceiling for body length (hard ceiling = 2x) |

!!! note "No metadata LLM"
    v2 intentionally does NOT use a metadata LLM in the write path. Type classification in `capture_context_v2` is keyword-based heuristic. This eliminates the Ollama model-thrashing cost documented in the [infra cost addendum](../architecture/v2-architecture.md).
