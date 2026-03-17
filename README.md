# 🧠 Open Brain

> **Agent-readable second brain** — one PostgreSQL database, one MCP server, every AI you use.

Stores your thoughts as vector embeddings so any AI tool (Claude, ChatGPT, Cursor, Windsurf, VS Code, etc.) can search your memory **by meaning** — not just keywords. Local-first. You own the data. ~$0.10–$0.30/month to run.

Supports both MCP stdio (editors/CLI) and streamable HTTP transport, plus a `wire` command to auto-register common MCP clients.

---

## Architecture

```
Your thought
    │
    ▼
[remember / capture_context]
    │
    ├─► Ollama / OpenAI  →  vector embedding (768 or 1536 dims)
    ├─► heuristic / LLM  →  metadata (type, people, topics, action_items)
    └─► project scoping   →  optional project tag for filtering
              │
              ▼
    PostgreSQL + pgvector
    (+ annotations, ratings, access tracking)
              │
              ▼
    MCP Server (stdio / HTTP)  ←  server.py  (11 tools)
              │
    ┌─────────┼──────────┐
    ▼         ▼          ▼
 Claude    Cursor    Windsurf  (any MCP-compatible client)

Agent workflow:
  search (previews) → recall (full content) → use → rate (up/down)
                                                  → annotate (add notes)
```

---

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| **Python 3.10+** | Runs the MCP server | [python.org](https://python.org) |
| **Docker Desktop** | Hosts PostgreSQL + pgvector | [docker.com](https://docker.com) |
| **Ollama** | Local embeddings (free) | [ollama.com](https://ollama.com) |

---

## Quick Start (~15 minutes)

### 1. Create a virtual environment and install dependencies

```sh
python -m venv .venv
```

```sh
# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

```sh
pip install -r requirements.txt
```

### 2. Configure environment

```sh
cp .env.example .env        # macOS / Linux
# Windows: copy .env.example .env
```

Edit `.env` — defaults work out of the box for local Ollama + Docker.

If you run Ollama on Windows and the MCP server from WSL, enable mirrored networking in `C:\Users\<USERNAME>\.wslconfig`:

```ini
[wsl2]
localhostForwarding=true
networkingMode=mirrored
```

Then run:

```powershell
wsl --shutdown
```

This allows WSL processes to reach Windows Ollama at `http://localhost:11434` reliably.

### 3. Start PostgreSQL

```sh
docker compose up -d
```

### 4. Initialize the database

```sh
python scripts/setup_db.py
```

### 5. Pull the embedding model

```sh
ollama pull nomic-embed-text
```

Optional but recommended for richer metadata extraction:

```sh
ollama pull qwen2.5:32b
```

If you use both `nomic-embed-text` and a metadata model such as `qwen2.5:32b`, start Ollama with:

```cmd
set OLLAMA_MAX_LOADED_MODELS=2
```

Without this, Ollama may repeatedly evict one model to load the other, which makes captures much slower.

### 6. Add to your MCP client (see below), then verify

```sh
python server.py   # should start without errors
```

Or use the built-in auto-wiring command:

```sh
python server.py wire
python server.py wire --check
```

---

## Wiring into Windsurf

### Step 1 — Make sure Docker is running

Open-brain needs PostgreSQL running. From `F:\open-brain`:

```sh
docker compose up -d
```

Verify with:

```sh
docker ps
```

You should see `open-brain-db` running.

---

### Step 2 — Edit the Windsurf MCP config file

Windsurf's MCP config lives at this exact path (it may not exist yet — create it if so):

```
C:\Users\<USERNAME>\.windsurf\mcp_config.json
```

> **Note:** Do not confuse this with `C:\Users\<USERNAME>\.codeium\windsurf\` — that folder is Windsurf's internal storage and is not where MCP is configured.

Open or create that file and paste in the following. **If the file already has other MCP servers, add just the `"open-brain"` block inside the existing `"mcpServers"` object.**

```json
{
  "mcpServers": {
    "open-brain": {
      "command": "F:\\open-brain\\.venv\\Scripts\\python.exe",
      "args": ["F:\\open-brain\\server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:your_password@localhost:5432/openbrain",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "METADATA_LLM_MODEL": "qwen2.5:32b"
      }
    }
  }
}
```

> **Note:** The `env` block overrides `.env` entirely when running as an MCP server. Never put `DATABASE_URL` in `.env` — it will leak into git.

---

### Step 3 — Restart Windsurf

Windsurf only reads `mcp_config.json` at startup. Fully quit and reopen it.

---

### Step 4 — Verify the MCP server loaded

In Windsurf, click the **MCP icon** (plug icon) in the top-right of the Cascade panel. You should see `open-brain` listed with a green dot and 11 tools:

- `capture_context`, `search`, `recall`, `remember`, `annotate`
- `rate`, `list_recent`, `stats`, `prune`, `forget`, `forget_many`

If it shows red / failed, check that:
1. `F:\open-brain\.venv\Scripts\python.exe` exists
2. Docker is running (`docker ps` shows `open-brain-db`)
3. Ollama is running (`http://localhost:11434` reachable)

---

### Step 5 — Verify auto-capture is enabled

Open `C:\Users\<USERNAME>\.codeium\windsurf\memories\global_rules.md` and confirm it contains the Open Brain auto-capture rules. If not, copy the contents of `F:\open-brain\prompts\windsurf-rules.md` into it.

This file is already configured on this machine. ✅

---

## Wiring into Cursor

### Step 1 — Edit the Cursor MCP config file

Cursor's global MCP config lives at:

```
C:\Users\<USERNAME>\.cursor\mcp.json
```

Create that file if it doesn't exist, and paste in:

```json
{
  "mcpServers": {
    "open-brain": {
      "command": "F:\\open-brain\\.venv\\Scripts\\python.exe",
      "args": ["F:\\open-brain\\server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:your_password@localhost:5432/openbrain",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "METADATA_LLM_MODEL": "llama3.2:3b"
      }
    }
  }
}
```

### Step 2 — Enable auto-capture rules

Open Cursor → **Settings** (Ctrl+Shift+J) → **General** → scroll to **Rules for AI**. Paste the entire contents of `F:\open-brain\prompts\cursor-rules.md` into that field.

Alternatively, create a `.cursorrules` file at the root of any project you want the brain active in.

### Step 3 — Restart Cursor

Cursor reads `mcp.json` at startup. Fully quit and reopen it.

### Step 4 — Verify

In Cursor, open the chat panel and look for the MCP tools icon (hammer). Click it — you should see `open-brain` with 11 tools listed. A red dot means the server failed to start — check Docker is running and the `.venv` path is correct.

---

## Wiring into Claude Desktop

Newer versions of Claude Desktop store config in `~/.claude/settings.json`, not `%APPDATA%\Claude\`.

### Step 1 — Edit `settings.json`

```
C:\Users\<USERNAME>\.claude\settings.json
```

Edit (or create) the file with:

```json
{
  "mcpServers": {
    "open-brain": {
      "command": "F:\\open-brain\\.venv\\Scripts\\python.exe",
      "args": ["F:\\open-brain\\server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:your_password@localhost:5432/openbrain",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "METADATA_LLM_MODEL": "qwen2.5:32b"
      }
    }
  }
}
```

### Step 2 — Add auto-capture instructions

In Claude Desktop: click your **profile icon** (bottom-left) → **Settings** → **Custom Instructions**.

Paste the entire contents of `F:\open-brain\prompts\claude-desktop.md` into that field. This tells Claude to silently capture memories without being asked.

### Step 3 — Register via CLI (Claude Code / VS Code extension)

If you're using the Claude Code CLI or the Claude VS Code extension, `settings.json` alone isn't enough — you must register via the CLI.

**From PowerShell / Windows cmd:**

```powershell
claude mcp add open-brain "F:\open-brain\.venv\Scripts\python.exe" "F:\open-brain\server.py" ^
  --env DATABASE_URL=postgresql://postgres:your_password@localhost:5432/openbrain ^
  --env EMBEDDING_PROVIDER=ollama ^
  --env OLLAMA_BASE_URL=http://localhost:11434 ^
  --env METADATA_LLM_MODEL=qwen2.5:32b ^
  --scope user
```

**From WSL:**

```sh
claude mcp add open-brain "F:\\open-brain\\.venv\\Scripts\\python.exe" "F:\\open-brain\\server.py" \
  --env DATABASE_URL=postgresql://postgres:your_password@localhost:5432/openbrain \
  --env EMBEDDING_PROVIDER=ollama \
  --env OLLAMA_BASE_URL=http://localhost:11434 \
  --env METADATA_LLM_MODEL=qwen2.5:32b \
  --scope user
```

This writes to `C:\Users\<USERNAME>\.claude.json` (user-scoped, active in every project). Verify:

```sh
claude mcp list
```

You should see: `open-brain: ... ✓ Connected`

### Step 4 — Restart / Reload

- **Claude Desktop:** fully quit and reopen.
- **VS Code extension:** Ctrl+Shift+P → "Developer: Reload Window"

### Step 5 — Verify

In any conversation, ask: *"What tools do you have available?"* — Claude should list the 11 open-brain tools. If not, confirm Docker is running (`docker ps` shows `open-brain-db`).

---

## Wiring into ChatGPT Desktop

> **⚠️ Limitation:** ChatGPT Desktop's MCP support requires a **remote HTTPS endpoint** — it does not support local stdio servers the way Windsurf, Cursor, and Claude do. You cannot point it directly at `server.py` the same way.

To use Open Brain with ChatGPT Desktop you have two options:

### Option A — Use a local SSE proxy (recommended)

The `mcp` package can expose a stdio server over SSE (Server-Sent Events) on localhost:

```sh
.venv\Scripts\python.exe -m mcp.server.sse --port 8765 -- python server.py
```

Then in ChatGPT Desktop, open **Settings** → **Connected Apps** → **Add MCP Server** and enter:

```
http://localhost:8765/sse
```

This process must be running whenever you use ChatGPT Desktop with the brain.

### Option B — Tunnel via ngrok

If you want ChatGPT Desktop to reach the server when it's not on localhost (e.g. ChatGPT mobile):

1. Install ngrok: `winget install ngrok`
2. Run the SSE proxy as above on port 8765
3. Run: `ngrok http 8765`
4. Use the `https://xxxx.ngrok.io/sse` URL in ChatGPT Desktop

### Auto-capture instructions

ChatGPT Desktop uses **Custom Instructions** for persistent behavior:

Settings (gear icon) → **Personalization** → **Custom Instructions** → paste the contents of `F:\open-brain\prompts\generic-system-prompt.md` into the **"What would you like ChatGPT to know?"** field.

---

## How It Works — Auto-Capture

You should **never have to tell it to remember anything**. That defeats the purpose.

The brain is wired into AI agents via system prompts and rules files (see `prompts/`). The agents call `capture_context` automatically — after completing tasks, when decisions are made, when bugs are fixed, when something about your preferences or project is learned. You just work. The brain captures.

`capture_context` batches work in phases: first embeddings for all extracted items, then metadata extraction for all items, then database writes. This avoids repeated model thrashing when using separate Ollama models for embeddings and metadata.

On recall, agents call `search` automatically at the start of tasks to surface relevant prior context before you even ask.

**The user's only job: work. The brain's job: remember everything.**

**Don’t repeat mistakes:** before changing code or making a decision, consult the brain. See `.windsurf/workflows/consult-open-brain.md` for the step-by-step consult-before-action workflow.

---

## Tools

| Tool | Who calls it | Description |
|------|-------------|-------------|
| `capture_context` | **Agent, automatically** | Ingests raw session/conversation text, extracts and stores multiple atomic memories at once. Accepts optional `project` to scope memories. |
| `search` | **Agent, automatically** | Semantic search by meaning — returns previews (first 200 chars) to save tokens. Filter by `type`, `people`, or `project`. |
| `recall` | Agent or user | Fetch full content of a memory by ID (after finding it via search). Tracks access count. |
| `remember` | Agent or user | Store a single explicit fact or note. Accepts optional `project` to scope. |
| `annotate` | Agent or user | Attach a persistent note to an existing memory (corrections, gotchas, extra context). |
| `rate` | Agent or user | Rate a memory as useful (`up`) or not useful (`down`). Quality signals surface the best memories over time. |
| `list_recent` | Agent or user | Browse recent captures with optional day filter |
| `stats` | User | Total memories, breakdown by type, recent activity |
| `prune` | User | Remove stale memories (older than N days with low access count). Supports dry-run preview. |
| `forget` | User | Hard-delete a memory by ID |
| `forget_many` | User | Batch-delete multiple memories by ID |

### v2 Features (inspired by Context Hub)

**Project scoping:** Tag memories with a project name on capture (`project="my-app"`), then filter searches to that project. Prevents cross-project noise without separate databases.

**Incremental retrieval:** `search` now returns 200-char previews instead of full content. Use `recall` with a memory ID to get the complete text. Saves tokens when scanning.

**Annotations:** Attach notes to existing memories without replacing them. Add corrections, gotchas, or warnings that surface in future searches. Clear with `annotate(id, clear=True)`.

**Quality signals:** `rate` memories up or down after using them. Score (upvotes - downvotes) appears in search results, helping surface the best memories.

**Access tracking:** Every `recall` bumps the memory's access count and last-accessed timestamp. `prune` uses this to identify stale, never-accessed memories.

### Upgrading from v1

Run the migration script to add the new columns to your existing database:

```sh
python scripts/migrate_v2.py
```

Safe to re-run. Existing memories are unaffected — new columns have sensible defaults.

### Wiring agents for auto-capture

Copy the relevant rules into your agent configuration:

| Client | File to edit | Source |
|--------|-------------|--------|
| Windsurf | `.windsurfrules` in your project | `prompts/windsurf-rules.md` |
| Cursor | `.cursorrules` in your project | `prompts/cursor-rules.md` |
| Claude Desktop | Settings → System Prompt | `prompts/claude-desktop.md` |
| Any other MCP client | System prompt field | `prompts/generic-system-prompt.md` |

---

## Embedding Models

| Model | Provider | Dimensions | Cost |
|-------|----------|-----------|------|
| `nomic-embed-text` | Ollama (local) | 768 | **Free** |
| `mxbai-embed-large` | Ollama (local) | 1024 | **Free** |
| `text-embedding-3-small` | OpenAI | 1536 | ~$0.02/1M tokens |

> ⚠️ **Set `EMBEDDING_DIMENSIONS` before running `setup_db.py`** — changing dimensions later requires dropping and recreating the `memories` table.

---

## Optional: LLM Metadata Extraction

For richer people/topic/tag extraction, point at a local Ollama model:

```env
METADATA_LLM_MODEL=qwen2.5:32b
```

```sh
ollama pull qwen2.5:32b
```

Adds ~2–5 s per capture but significantly improves metadata quality for people names and nuanced topics. If the LLM call fails, it automatically falls back to fast heuristic extraction.

If you use a metadata model and `nomic-embed-text` together, set `OLLAMA_MAX_LOADED_MODELS=2` before starting Ollama.

---

## Migration from Existing Second Brain

Run this prompt inside your current AI (Claude, ChatGPT, etc.):

```
Export everything you know about me — my projects, preferences, key people,
past decisions, ongoing work, and constraints — as a series of plain-text
notes, one per line. I'm migrating to a new memory system.
```

Then feed each line to `remember` to seed your Open Brain.

---

## File Structure

```
F:\open-brain\
├── server.py               # Python MCP server — 11 tools
├── wire.py                 # MCP client auto-discovery + auto-wiring CLI
├── requirements.txt        # Python dependencies
├── test_server.py          # End-to-end test suite
├── .venv/                  # Virtual environment (never commit)
├── .env                    # Local secrets (never commit)
├── .env.example            # Config template
├── .gitignore
├── prompts/
│   ├── windsurf-rules.md   # Paste into .windsurfrules
│   ├── cursor-rules.md     # Paste into .cursorrules
│   ├── claude-desktop.md   # Paste into Claude Desktop system prompt
│   └── generic-system-prompt.md
├── scripts/
│   ├── setup_db.py         # One-time DB initialization (includes v2 schema)
│   ├── migrate_v2.py       # Migration for existing DBs: project, annotations, access tracking, ratings
│   └── ensure-stack.sh     # Verify/start Ollama + open-brain-db from WSL
├── docker-compose.yml      # PostgreSQL + pgvector
└── README.md
```
