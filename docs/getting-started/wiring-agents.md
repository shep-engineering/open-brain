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

**Config file:** `C:\Users\<USERNAME>\.windsurf\mcp_config.json`

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

### Cursor

**Config file:** `C:\Users\<USERNAME>\.cursor\mcp.json`

Same JSON structure as Windsurf.

### Claude Desktop

**Config file:** `C:\Users\<USERNAME>\.claude\settings.json`

Same JSON structure as Windsurf.

### Claude Code CLI

```sh
claude mcp add open-brain \
  "C:\\path\\to\\open-brain\\.venv\\Scripts\\python.exe" \
  "C:\\path\\to\\open-brain\\server.py" \
  --env DATABASE_URL=postgresql://postgres:<your_password>@localhost:5432/openbrain \
  --env EMBEDDING_PROVIDER=ollama \
  --env OLLAMA_BASE_URL=http://localhost:11434 \
  --env METADATA_LLM_MODEL=qwen2.5:32b \
  --scope user
```

### VS Code (Copilot)

**Config file:** `C:\Users\<USERNAME>\AppData\Roaming\Code\User\mcp.json`

!!! note "VS Code uses `servers`, not `mcpServers`"
    VS Code's MCP config uses a `"servers"` key and requires `"type": "stdio"` in each entry.

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

---

## Verifying the Connection

After wiring, restart the client and check:

- **Windsurf:** MCP icon (plug) in Cascade panel, green dot, 11 tools
- **Cursor:** Chat panel, hammer icon, `open-brain` listed
- **Claude Code:** Run `claude mcp list`. Should show `open-brain: ... Connected`
- **Claude Desktop:** Ask "What tools do you have?" Should list the 11 tools

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
