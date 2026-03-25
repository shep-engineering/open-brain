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
| `METADATA_LLM_MODEL` | *(empty)* | e.g. `qwen2.5:32b`. Empty = heuristic only. |
| `DEDUP_THRESHOLD` | `0.92` | Cosine similarity threshold for deduplication |
| `COMPLIANCE_WINDOW` | `300` | Seconds before a search is considered stale for compliance warnings |
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

For richer people/topic/tag extraction, point at a local Ollama model:

```env
METADATA_LLM_MODEL=qwen2.5:32b
```

This adds ~2-5s per capture but significantly improves metadata quality. If the LLM call fails, it automatically falls back to fast heuristic extraction.

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
