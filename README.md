# 🧠 Open Brain

> **Agent-readable second brain**: one PostgreSQL database, one MCP server, every AI you use.

---

## ⚠️ Which "Open Brain"?

**"Open Brain" is a surprisingly common project name.** This repo is **David Sheppard's personal AI memory server** (`shep-engineering/open-brain`). Multiple other projects share the name or a variant, including:

- [`NateBJones-Projects/OB1`](https://github.com/NateBJones-Projects/OB1) (Nate B. Jones' "Open Brain" — cloud-hosted personal memory)
- [`impara/openBrain`](https://github.com/impara/openBrain), [`Mihai-Codes/OpenBrain`](https://github.com/Mihai-Codes/OpenBrain), [`rolders/open-brain`](https://github.com/rolders/open-brain) (all AI-memory projects with similar tag lines)
- [openbrainai.com](https://openbrainai.com) (clinical language-assessment platform for healthcare)
- [Open Brain Institute](https://www.openbraininstitute.org/) (neuroscience simulation research)

**We are not affiliated with any of them.** Any naming overlap is coincidental convergent-naming.

- **We do**: Store AI conversation memories locally using vector embeddings; expose them via MCP to Claude, Cursor, Windsurf, ChatGPT Desktop, and VS Code Copilot.
- **We do NOT**: Analyze speech, diagnose language disorders, provide clinical tools, simulate biological brains, or provide hosted/SaaS infrastructure.

See [DISAMBIGUATION.md](DISAMBIGUATION.md) for the full rundown and how to tell us apart from each similarly-named project.

Stores your thoughts as vector embeddings so any AI tool (Claude, ChatGPT, Cursor, Windsurf, VS Code, etc.) can search your memory **by meaning**, not just keywords. Local-first. You own the data. ~$0.10-$0.30/month to run.

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
    MCP Server (stdio / HTTP)  ←  server.py  (19 tools)
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

Edit `.env`. Defaults work out of the box for local Ollama + Docker.

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

This setup step is idempotent: it creates a fresh database or upgrades an
existing v1 install to the current schema expected by `server.py`.

Optional but recommended after setup or upgrades:

```sh
python scripts/verify_setup_schema.py
```

That verifier checks the install landed the columns behind belief revision,
skills, bitemporal storage, session registry, and uptime tracking.

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

This also installs **Claude Code enforcement hooks** that block tool use until Open Brain is searched. See [Wiring Agents](docs/getting-started/wiring-agents.md#claude-code-enforcement-hooks) for details.

### Agent Harness (optional, recommended)

`contrib/agent-harness/` ships nine Claude Code hooks that enforce the
memory-first workflow at the **tool-call level** — not just as instructions the
model can reason around:

| Hook | What it blocks |
|------|---------------|
| `require-brain-boot.sh` | Any tool call until both V1 + V2 boot_session succeed |
| `require-prework.sh` | Bash/Edit/Write if pre-work-check.sh failed or wasn't run |
| `branch-guard.sh` | git commit on main/master/develop |
| `no-force-push.sh` | git push --force without explicit confirmation |
| `no-rm-rf.sh` | rm -rf without explicit confirmation |
| `require-brain-save.sh` | git commit unless brain was written to this session |
| `require-brain-checkpoint.sh` | Edits to risky files without a brain checkpoint first |
| `detect-correction.sh` | (UserPromptSubmit) Injects directive to pin corrections as guardrails |
| `session-end-save.py` | (Stop) Writes session handoff to brain + project dir |

**Install:**

```bash
# Linux / macOS / WSL:
bash contrib/agent-harness/install.sh

# Windows:
contrib\agent-harness\install.cmd
```

After running, merge the printed snippet into `~/.claude/settings.json`.
See [Agent Harness guide](https://shep-engineering.github.io/open-brain/guides/agent-harness/) for full documentation.

### 7. Launch the Dashboard (Windows)

A dark monitoring dashboard ships with Open Brain. It shows memory counts, service health (PostgreSQL, Ollama, MCP), recent captures, most-accessed memories, and an observability strip with tool call metrics from OpenTelemetry traces.

**Live updates via PostgreSQL LISTEN/NOTIFY** -- the dashboard does not poll on a timer. A database trigger fires whenever memories are created, updated, or deleted, and the dashboard refreshes instantly. Service health checks (Ollama, MCP) run on a separate 60-second interval.

**One-step launch** -- the dashboard detects whether Open Brain is running and starts it automatically if not:

```cmd
# Double-click the Desktop shortcut (created below), or run directly:
.venv\Scripts\pythonw.exe dashboard.py
```

To create the Desktop shortcuts:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\create-desktop-shortcuts.ps1
```

This creates four shortcuts on your Desktop:
- **Open Brain ON** -- starts Docker, Postgres, Ollama, MCP server
- **Open Brain OFF** -- stops all services and frees VRAM
- **Open Brain SSE Proxy** -- starts the SSE bridge (for remote clients)
- **Open Brain Dashboard** -- monitoring GUI, auto-starts services if needed

> **Note:** The dashboard requires `customtkinter` and `pillow`, both included in `requirements.txt`.

---

## Wiring into Windsurf

### Step 1: Make sure Docker is running

Open-brain needs PostgreSQL running. From your open-brain directory:

```sh
docker compose up -d
```

Verify with:

```sh
docker ps
```

You should see `open-brain-db` running.

---

### Step 2: Edit the Windsurf MCP config file

Windsurf's MCP config lives at (create it if it does not exist yet):

- **Windows:** `C:\Users\<USERNAME>\.windsurf\mcp_config.json`
- **Linux / macOS:** `~/.windsurf/mcp_config.json`

> **Note (Windows):** Do not confuse this with `C:\Users\<USERNAME>\.codeium\windsurf\`. That folder is Windsurf's internal storage and is not where MCP is configured.

Open or create that file and paste in the following. **If the file already has other MCP servers, add just the `"open-brain"` block inside the existing `"mcpServers"` object.**

**Windows:**

```json
{
  "mcpServers": {
    "open-brain": {
      "command": "C:\\path\\to\\open-brain\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\open-brain\\server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:<your_password>@localhost:5432/openbrain",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "METADATA_LLM_MODEL": "qwen2.5:32b"
      }
    }
  }
}
```

**Linux / macOS / WSL:**

```json
{
  "mcpServers": {
    "open-brain": {
      "command": "/path/to/open-brain/.venv/bin/python",
      "args": ["/path/to/open-brain/server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:<your_password>@localhost:5432/openbrain",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "METADATA_LLM_MODEL": "qwen2.5:32b"
      }
    }
  }
}
```

> **Note:** The `env` block overrides `.env` entirely when running as an MCP server. Never put `DATABASE_URL` in `.env`, as it will leak into git.

---

### Step 3: Restart Windsurf

Windsurf only reads `mcp_config.json` at startup. Fully quit and reopen it.

---

### Step 4: Verify the MCP server loaded

In Windsurf, click the **MCP icon** (plug icon) in the top-right of the Cascade panel. You should see `open-brain` listed with a green dot and 12 tools:

- `capture_context`, `search`, `recall`, `remember`, `annotate`
- `rate`, `list_recent`, `stats`, `prune`, `forget`, `forget_many`, `pin`

If it shows red / failed, check that:
1. Your `.venv` python path exists (`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux/macOS)
2. Docker is running (`docker ps` shows `open-brain-db`)
3. Ollama is running (`http://localhost:11434` reachable)

---

### Step 5: Verify auto-capture is enabled

Open `C:\Users\<USERNAME>\.codeium\windsurf\memories\global_rules.md` and confirm it contains the Open Brain auto-capture rules. If not, copy the contents of `prompts/windsurf-rules.md` into it.


---

## Wiring into Cursor

### Step 1: Edit the Cursor MCP config file

Cursor's global MCP config lives at:

- **Windows:** `C:\Users\<USERNAME>\.cursor\mcp.json`
- **Linux / macOS:** `~/.cursor/mcp.json`

Create that file if it doesn't exist, and paste in:

**Windows:**

```json
{
  "mcpServers": {
    "open-brain": {
      "command": "C:\\path\\to\\open-brain\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\open-brain\\server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:<your_password>@localhost:5432/openbrain",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "METADATA_LLM_MODEL": "llama3.2:3b"
      }
    }
  }
}
```

**Linux / macOS / WSL:**

```json
{
  "mcpServers": {
    "open-brain": {
      "command": "/path/to/open-brain/.venv/bin/python",
      "args": ["/path/to/open-brain/server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:<your_password>@localhost:5432/openbrain",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "METADATA_LLM_MODEL": "llama3.2:3b"
      }
    }
  }
}
```

### Step 2: Enable auto-capture rules

Open Cursor → **Settings** (Ctrl+Shift+J) → **General** → scroll to **Rules for AI**. Paste the entire contents of `prompts/cursor-rules.md` into that field.

Alternatively, create a `.cursorrules` file at the root of any project you want the brain active in.

### Step 3: Restart Cursor

Cursor reads `mcp.json` at startup. Fully quit and reopen it.

### Step 4: Verify

In Cursor, open the chat panel and look for the MCP tools icon (hammer). Click it. You should see `open-brain` with 12 tools listed. A red dot means the server failed to start. Check that Docker is running and the `.venv` path is correct.

---

## Wiring into Claude Desktop

Newer versions of Claude Desktop store config in `~/.claude/settings.json`, not `%APPDATA%\Claude\`.

### Step 1: Edit `settings.json`

- **Windows:** `C:\Users\<USERNAME>\.claude\settings.json`
- **Linux / macOS:** `~/.claude/settings.json`

Edit (or create) the file with:

**Windows:**

```json
{
  "mcpServers": {
    "open-brain": {
      "command": "C:\\path\\to\\open-brain\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\open-brain\\server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:<your_password>@localhost:5432/openbrain",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "METADATA_LLM_MODEL": "qwen2.5:32b"
      }
    }
  }
}
```

**Linux / macOS / WSL:**

```json
{
  "mcpServers": {
    "open-brain": {
      "command": "/path/to/open-brain/.venv/bin/python",
      "args": ["/path/to/open-brain/server.py"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:<your_password>@localhost:5432/openbrain",
        "EMBEDDING_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "METADATA_LLM_MODEL": "qwen2.5:32b"
      }
    }
  }
}
```

### Step 2: Add auto-capture instructions

In Claude Desktop: click your **profile icon** (bottom-left) → **Settings** → **Custom Instructions**.

Paste the entire contents of `prompts/claude-desktop.md` into that field. This tells Claude to silently capture memories without being asked.

### Step 3: Register via CLI (Claude Code / VS Code extension)

If you're using the Claude Code CLI or the Claude VS Code extension, `settings.json` alone isn't enough. You must also register via the CLI.

**Windows (PowerShell / cmd):**

```powershell
claude mcp add open-brain "C:\path\to\open-brain\.venv\Scripts\python.exe" "C:\path\to\open-brain\server.py" ^
  --env DATABASE_URL=postgresql://postgres:<your_password>@localhost:5432/openbrain ^
  --env EMBEDDING_PROVIDER=ollama ^
  --env OLLAMA_BASE_URL=http://localhost:11434 ^
  --env METADATA_LLM_MODEL=qwen2.5:32b ^
  --scope user
```

**Linux / macOS / WSL:**

```sh
claude mcp add open-brain "/path/to/open-brain/.venv/bin/python" "/path/to/open-brain/server.py" \
  --env DATABASE_URL=postgresql://postgres:<your_password>@localhost:5432/openbrain \
  --env EMBEDDING_PROVIDER=ollama \
  --env OLLAMA_BASE_URL=http://localhost:11434 \
  --env METADATA_LLM_MODEL=qwen2.5:32b \
  --scope user
```

This writes to `~/.claude.json` (user-scoped, active in every project). Verify:

```sh
claude mcp list
```

You should see: `open-brain: ... ✓ Connected`

### Step 4: Restart / Reload

- **Claude Desktop:** fully quit and reopen.
- **VS Code extension:** Ctrl+Shift+P → "Developer: Reload Window"

### Step 5: Verify

In any conversation, ask: *"What tools do you have available?"* Claude should list the 12 open-brain tools. If not, confirm Docker is running (`docker ps` shows `open-brain-db`).

---

## Wiring into ChatGPT Desktop

> **Limitation:** ChatGPT Desktop's MCP support requires a **remote HTTPS endpoint**. It does not support local stdio servers the way Windsurf, Cursor, and Claude do. You cannot point it directly at `server.py` the same way.

To use Open Brain with ChatGPT Desktop you have two options:

### Option A: Use a local SSE proxy (recommended)

The `mcp` package can expose a stdio server over SSE (Server-Sent Events) on localhost.

**Windows:**

```sh
.venv\Scripts\python.exe -m mcp.server.sse --port 8765 -- python server.py
```

**Linux / macOS / WSL:**

```sh
.venv/bin/python -m mcp.server.sse --port 8765 -- python server.py
```

Then in ChatGPT Desktop, open **Settings** → **Connected Apps** → **Add MCP Server** and enter:

```
http://localhost:8765/sse
```

This process must be running whenever you use ChatGPT Desktop with the brain.

### Option B: Tunnel via ngrok

If you want ChatGPT Desktop to reach the server when it's not on localhost (e.g. ChatGPT mobile):

1. Install ngrok (`winget install ngrok` on Windows, or see [ngrok.com](https://ngrok.com) for other platforms)
2. Run the SSE proxy as above on port 8765
3. Run: `ngrok http 8765`
4. Use the `https://xxxx.ngrok.io/sse` URL in ChatGPT Desktop

### Auto-capture instructions

ChatGPT Desktop uses **Custom Instructions** for persistent behavior:

Settings (gear icon) → **Personalization** → **Custom Instructions** → paste the contents of `prompts/generic-system-prompt.md` into the **"What would you like ChatGPT to know?"** field.

---

## How It Works: Auto-Capture

You should **never have to tell it to remember anything**. That defeats the purpose.

The brain is wired into AI agents via system prompts and rules files (see `prompts/`). The agents call `capture_context` automatically after completing tasks, when decisions are made, when bugs are fixed, when something about your preferences or project is learned. You just work. The brain captures.

`capture_context` batches work in phases: first embeddings for all extracted items, then metadata extraction for all items, then database writes. This avoids repeated model thrashing when using separate Ollama models for embeddings and metadata.

On recall, agents call `search` automatically at the start of tasks to surface relevant prior context before you even ask.

**The user’s only job: work. The brain’s job: remember everything.**

**Don’t repeat mistakes:** before changing code or making a decision, consult the brain. See `.windsurf/workflows/consult-open-brain.md` for the step-by-step consult-before-action workflow.

### Self-Healing: What Happens When the Brain Is Down

All agent prompt files in `prompts/` include a self-healing fallback so agents never freeze when the brain is unavailable:

1. **Auto-start:** The agent detects the brain is down and runs the startup script itself (`scripts/windows/open-brain-on.cmd` or `scripts/open-brain-on.sh`).
2. **Retry:** Waits 10 seconds, then retries the MCP call once.
3. **Graceful degradation:** If the brain still isn’t up, the agent notifies the user and continues working without it. No freezing, no infinite loops.
4. **Silent reconnect:** Once the brain comes back mid-session, the agent resumes using it automatically.

This means users never need to manually start infrastructure before working. The agent handles it.

---

## Tools

| Tool | Who calls it | Description |
|------|-------------|-------------|
| `capture_context` | **Agent, automatically** | Ingests raw session/conversation text, extracts and stores multiple atomic memories at once. Accepts optional `project` to scope memories. |
| `search` | **Agent, automatically** | Semantic search by meaning. Returns previews (first 200 chars) to save tokens. Filter by `type`, `people`, `project`, `since_days`, or `until_days`. Hybrid vector + full-text scoring with uptime-based recency decay. |
| `recall` | Agent or user | Fetch full content of a memory by ID (after finding it via search). Tracks access count. |
| `remember` | Agent or user | Store a single explicit fact or note. Accepts optional `project` to scope. |
| `annotate` | Agent or user | Attach a persistent note to an existing memory (corrections, gotchas, extra context). |
| `rate` | Agent or user | Rate a memory as useful (`up`) or not useful (`down`). Quality signals surface the best memories over time. |
| `list_recent` | Agent or user | Browse recent captures with optional day filter |
| `stats` | User | Total memories, breakdown by type, recent activity |
| `prune` | User | Remove stale memories (older than N days with low access count). Supports dry-run preview. |
| `forget` | User | Hard-delete a memory by ID |
| `forget_many` | User | Batch-delete multiple memories by ID |
| `scratch_set` | Agent or user | Store a key-value pair in working memory (ephemeral, cleared on restart) |
| `scratch_get` | Agent or user | Retrieve a value from working memory by key |
| `scratch_list` | Agent or user | List all current working memory entries |

### v4.3 Features

**Smart UPDATE/MERGE on store:** When a new memory is semantically related to an existing one (similarity in the gray zone `[0.70, 0.92)`), `qwen2.5:14b` decides whether to `ADD`, `MERGE`, `REPLACE`, or `SKIP`. On `MERGE`, the LLM writes a single combined memory preserving all unique facts. On `REPLACE`, contradicted memories are overwritten. The `action` field in responses now includes `"merged"` and `"replaced"`. Configurable via `OPEN_BRAIN_MERGE_LOWER_THRESHOLD`.

**Background consolidation:** Set `OPEN_BRAIN_CONSOLIDATION_INTERVAL=3600` to run hourly LLM-driven passes over all memories, merging and replacing related ones automatically. Disabled by default. Requires `METADATA_LLM_MODEL`.

### v4.2 Features

**Working memory scratchpad:** Three new tools (`scratch_set`, `scratch_get`, `scratch_list`) give agents an ephemeral key-value store for in-session context. Track current task, active file, reasoning state -- cleared automatically on restart. Never pollutes long-term memory.

**Bi-temporal modelling:** Every memory now has two timestamps: `valid_time` (when the event happened, user-supplied) and `transaction_time` (when Open Brain learned about it). Pass `as_of='2025-03-01'` to `search()` to retrieve only what was known as of that date. Use `remember(valid_time='...')` to backdate memories.

### v4 Features

**Uptime-based recency decay:** Memories not recently accessed score lower in search results. Decay accumulates only while the server is running -- power outages, overnight gaps, and vacations cost you nothing. A background thread flushes the uptime counter to the DB every 60 seconds (configurable). Configure with `OPEN_BRAIN_DECAY_LAMBDA` (default `0.005`).

**Hybrid vector + full-text search:** Search combines cosine similarity with PostgreSQL full-text ranking (`ts_rank`). Better retrieval of exact names, project codes, and dates. Auto-migrates a `tsvector` column and GIN index on first start. Configure the blend with `OPEN_BRAIN_HYBRID_WEIGHT` (default `0.3`).

**Time-scoped search:** `search()` now accepts `since_days` and `until_days` to restrict results by creation date. Ask "what did I decide last week?" with `since_days=7`.

**New memory types:** `procedural` (workflow rules, conventions, non-negotiables) and `episodic` (specific past events, session recollections) added alongside the existing types.

**Pinned memories (guardrails):** Pin memories to a project so they always appear at the top of search results, regardless of query. Use for workflow rules agents must always see.

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

Safe to re-run. Existing memories are unaffected. New columns have sensible defaults.

### Wiring agents for auto-capture

Copy the relevant rules into your agent configuration:

| Client | File to edit | Source |
|--------|-------------|--------|
| Windsurf | `.windsurfrules` in your project | `prompts/windsurf-rules.md` |
| Cursor | `.cursorrules` in your project | `prompts/cursor-rules.md` |
| Claude Desktop | Settings → System Prompt | `prompts/claude-desktop.md` |
| Any other MCP client | System prompt field | `prompts/generic-system-prompt.md` |

---

## Testing

Tests run against an **isolated test database** — never production. A separate Docker container (`open-brain-test-db`) on port 5434 with database `openbrain_test`. Three safety layers prevent tests from ever touching production.

### Quick Start

```sh
# Start the test database (separate container, ephemeral)
docker compose -f docker-compose.test.yml up -d

# Run all tests
pytest tests/ -v

# Or use the convenience script
bash scripts/test-db.sh -v
```

For the install regression fixed here, verify the contributor setup path with:

```sh
python scripts/setup_db.py
python scripts/verify_setup_schema.py
```

### How It Works

1. `conftest.py` overrides `DATABASE_URL` before `server.py` is imported
2. A session fixture hard-exits if the URL points to production
3. Fake embeddings (deterministic SHA-256 vectors) — no Ollama needed for tests

To run with real Ollama embeddings: `pytest tests/ -m ollama -v`

See the full [Testing guide](docs/guides/testing.md) for architecture, safety details, and troubleshooting.

---

## Embedding Models

| Model | Provider | Dimensions | Cost |
|-------|----------|-----------|------|
| `nomic-embed-text` | Ollama (local) | 768 | **Free** |
| `mxbai-embed-large` | Ollama (local) | 1024 | **Free** |
| `text-embedding-3-small` | OpenAI | 1536 | ~$0.02/1M tokens |

> **Set `EMBEDDING_DIMENSIONS` before running `setup_db.py`.** Changing dimensions later requires dropping and recreating the `memories` table.

---

## Optional: LLM Metadata Extraction

For richer people/topic/tag extraction, point at a local Ollama model:

```env
METADATA_LLM_MODEL=qwen2.5:32b
```

```sh
ollama pull qwen2.5:32b
```

Adds ~2-5s per capture but significantly improves metadata quality for people names and nuanced topics. If the LLM call fails, it automatically falls back to fast heuristic extraction.

If you use a metadata model and `nomic-embed-text` together, set `OLLAMA_MAX_LOADED_MODELS=2` before starting Ollama.

---

## Migration from Existing Second Brain

Run this prompt inside your current AI (Claude, ChatGPT, etc.):

```
Export everything you know about me (my projects, preferences, key people,
past decisions, ongoing work, and constraints) as a series of plain-text
notes, one per line. I'm migrating to a new memory system.
```

Then feed each line to `remember` to seed your Open Brain.

---

## File Structure

```
open-brain/
├── server.py               # Python MCP server, 12 tools
├── wire.py                 # MCP client auto-discovery + auto-wiring CLI
├── hooks/                  # Claude Code enforcement hooks (installed by wire)
│   ├── brain-reminder.sh   # UserPromptSubmit: mandatory brain search reminder
│   └── require-brain-search.sh  # PreToolUse: blocks tools until brain searched
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
│   ├── setup_db.py         # One-time DB initialization / upgrade to current v1 schema
│   ├── migrate_v2.py       # Migration for existing DBs: project, annotations, access tracking, ratings
│   └── ensure-stack.sh     # Verify/start Ollama + open-brain-db from WSL
├── docker-compose.yml      # PostgreSQL + pgvector (production)
├── docker-compose.test.yml # Isolated test database (port 5434)
├── conftest.py             # pytest safety: forces test DB, fake embeddings
├── pyproject.toml          # pytest configuration
└── README.md
```
