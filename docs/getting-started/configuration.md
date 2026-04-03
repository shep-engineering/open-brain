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
