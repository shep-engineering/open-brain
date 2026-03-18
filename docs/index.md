# Open Brain

<video autoplay loop muted playsinline style="width:100%; border-radius:12px; margin:20px 0;">
  <source src="assets/videos/brain-video.mp4" type="video/mp4">
</video>

> **Agent-readable second brain.** One PostgreSQL database, one MCP server, every AI you use.

Open Brain stores your thoughts as vector embeddings so any AI tool (Claude Code, Cursor, Windsurf, ChatGPT Desktop, VS Code Copilot) can search your memory **by meaning**, not just keywords. Local-first. You own the data. ~$0/month to run.

---

## The Problem

Every AI coding tool has amnesia. Close the chat, lose the context. Switch from Cursor to Claude Code? Start from scratch. That decision you made last Tuesday? Gone.

**Open Brain fixes this.** One shared memory layer that every agent reads and writes to, automatically, silently, without you lifting a finger.

---

## How It Works

```
Your thought
    |
    v
[remember / capture_context]
    |
    +---> Ollama / OpenAI  -->  vector embedding (768-1536 dims)
    +---> heuristic / LLM  -->  metadata (type, people, topics, action_items)
    +---> project scoping   -->  optional project tag for filtering
              |
              v
    PostgreSQL + pgvector
    (+ annotations, ratings, access tracking)
              |
              v
    MCP Server (stdio / HTTP)  <--  server.py  (11 tools)
              |
    +---------+-----------+
    v         v           v
 Claude    Cursor    Windsurf   ...any MCP client
```

---

## Key Features

- **Cross-agent memory:** Capture in Claude Code, recall in Cursor. One brain, every tool.
- **Semantic search:** Find memories by meaning, not keywords. "That database decision" finds it even if you never typed those words.
- **Auto-capture:** Agents store decisions, bugs, and context as you work. You never have to say "remember this."
- **Auto-recall:** Agents search the brain before starting tasks. Prior context surfaces automatically.
- **One-command wiring:** `python server.py wire` auto-discovers and configures every AI tool on your system.
- **Smart batching:** Embeddings and metadata extraction are batched to avoid GPU model thrashing.
- **Quality signals:** Rate memories up/down. Access tracking surfaces the most useful memories.
- **Project scoping:** Tag memories by project. Search within a project without noise from others.
- **100% local:** PostgreSQL + pgvector + Ollama. No cloud. No API keys required. Your data stays yours.

---

## Supported Clients

| Client | Transport | Status |
|--------|-----------|--------|
| Claude Code (CLI / VS Code) | stdio | Fully supported |
| Windsurf | stdio | Fully supported |
| Cursor | stdio | Fully supported |
| VS Code Copilot | stdio | Fully supported |
| Claude Desktop | stdio | Fully supported |
| ChatGPT Desktop | SSE proxy | Supported via `mcp.server.sse` |
| Continue | stdio | Fully supported |
| Any MCP client | stdio / HTTP | Fully supported |

---

## Quick Links

- [Installation](getting-started/installation.md): Get up and running in ~15 minutes
- [Wiring Agents](getting-started/wiring-agents.md): Connect your AI tools to the brain
- [Tools Reference](tools.md): All 11 MCP tools explained
- [Architecture](architecture/overview.md): How it all fits together
