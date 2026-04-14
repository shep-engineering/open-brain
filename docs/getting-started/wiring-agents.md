# Wiring Agents

Open Brain includes a `wire` command that auto-discovers AI tools on your system and injects the MCP configuration.

---

## Auto-Wire (Recommended)

```sh
# See what needs wiring (read-only)
python server.py wire --check

# Wire everything
python server.py wire
```

The wire command scans for:

- Claude Desktop
- Claude Code CLI
- Cursor
- Windsurf
- VS Code (Copilot)
- Continue
- Codex CLI

---

## Manual Wiring

If you prefer to configure manually, add the following to each client's MCP config file.

### Windsurf

**Config file:**

- **Windows:** `C:\Users\<USERNAME>\.windsurf\mcp_config.json`
- **Linux / macOS:** `~/.windsurf/mcp_config.json`

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

### Cursor

**Config file:**

- **Windows:** `C:\Users\<USERNAME>\.cursor\mcp.json`
- **Linux / macOS:** `~/.cursor/mcp.json`

Same JSON structure as Windsurf (use the appropriate platform example above).

### Claude Desktop

**Config file:**

- **Windows:** `C:\Users\<USERNAME>\.claude\settings.json`
- **Linux / macOS:** `~/.claude/settings.json`

Same JSON structure as Windsurf (use the appropriate platform example above).

### Claude Code CLI

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

### VS Code (Copilot)

**Config file:**

- **Windows:** `C:\Users\<USERNAME>\AppData\Roaming\Code\User\mcp.json`
- **Linux:** `~/.config/Code/User/mcp.json`
- **macOS:** `~/Library/Application Support/Code/User/mcp.json`

!!! note "VS Code uses `servers`, not `mcpServers`"
    VS Code's MCP config uses a `"servers"` key and requires `"type": "stdio"` in each entry.

**Windows:**

```json
{
  "servers": {
    "open-brain": {
      "type": "stdio",
      "command": "C:\\path\\to\\open-brain\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\open-brain\\server.py"]
    }
  }
}
```

**Linux / macOS / WSL:**

```json
{
  "servers": {
    "open-brain": {
      "type": "stdio",
      "command": "/path/to/open-brain/.venv/bin/python",
      "args": ["/path/to/open-brain/server.py"]
    }
  }
}
```

---

## Verifying the Connection

After wiring, restart the client and check:

- **Windsurf:** MCP icon (plug) in Cascade panel, green dot, 19 tools
- **Cursor:** Chat panel, hammer icon, `open-brain` listed
- **Claude Code:** Run `claude mcp list`. Should show `open-brain: ... Connected`
- **Claude Desktop:** Ask "What tools do you have?" Should list the 19 tools

---

## Claude Code Enforcement Hooks

When you run `python server.py wire`, Open Brain automatically installs **enforcement hooks** for Claude Code. These hooks guarantee that Claude searches your brain before taking any action -- not as a suggestion, but as a hard block.

**What gets installed:**

| File | Location | Purpose |
|------|----------|---------|
| `brain-reminder.sh` | `~/.claude/hooks/` | Injects a mandatory reminder on every user message |
| `require-brain-search.sh` | `~/.claude/hooks/` | **Blocks all tool calls** until `mcp__open-brain__search` is called |
| Hook config | `~/.claude/settings.json` | Registers both hooks with Claude Code |

**How it works:**

1. You send Claude a message
2. `UserPromptSubmit` hook fires -- Claude sees the mandatory reminder
3. If Claude tries to use Bash, Edit, Write, etc. without searching the brain first, the `PreToolUse` hook **blocks it** with an error
4. Claude is forced to call `mcp__open-brain__search` first
5. After searching, all tools are unlocked for the rest of the session

Read-only tools (Read, Glob, Grep, ToolSearch) and all `mcp__open-brain__*` tools are whitelisted so Claude can still explore before searching.

**Manual install (if not using `wire`):**

See `hooks/README.md` in the Open Brain repo for manual installation steps.

---

## Enabling Auto-Capture Behavior

Wiring the MCP connection gives agents *access* to the tools. To make them *use* the tools automatically, add the behavioral rules:

| Client | Where to add rules | Source file |
|--------|--------------------|-------------|
| Windsurf | `.windsurfrules` in project root | `prompts/windsurf-rules.md` |
| Cursor | `.cursorrules` or Settings > User Rules | `prompts/cursor-rules.md` |
| Claude Desktop | Settings > Custom Instructions | `prompts/claude-desktop.md` |
| Claude Code | `CLAUDE.md` in project root | `prompts/generic-system-prompt.md` |
| Any other client | System prompt field | `prompts/generic-system-prompt.md` |

These rules tell agents to silently capture context after tasks and recall prior context before starting new ones.

!!! tip "Claude Code gets enforcement, other agents get guidance"
    Claude Code is the only agent with hard enforcement (hooks block tools until brain is searched). Other agents rely on behavioral rules in their config files, plus the `compliance_warning` field in server responses when a search hasn't been performed recently.
