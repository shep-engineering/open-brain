# Open Brain — Claude Code Hooks

These hooks enforce the mandatory "search brain first" workflow in Claude Code.

## What they do

- **`brain-reminder.sh`** — `UserPromptSubmit` hook. Injects a reminder into every user message so Claude sees it before responding.
- **`require-brain-search.sh`** — `PreToolUse` hook. **Blocks** all tool calls (Bash, Edit, Write, etc.) until `mcp__open-brain__search` has been called in the session. Read-only tools (Read, Glob, Grep, ToolSearch) and all `mcp__open-brain__*` tools are whitelisted.

## Install

### 1. Copy hooks to your Claude config

```bash
mkdir -p ~/.claude/hooks
cp hooks/brain-reminder.sh ~/.claude/hooks/
cp hooks/require-brain-search.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/*.sh
```

### 2. Add to `~/.claude/settings.json`

Add this `hooks` key to your existing settings:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$HOME/.claude/hooks/brain-reminder.sh\"",
            "timeout": 5
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "(?!mcp__open-brain__).*",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$HOME/.claude/hooks/require-brain-search.sh\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

### 3. Restart Claude Code

The hooks take effect on the next session.

## How it works

1. User sends a message
2. `UserPromptSubmit` fires → Claude sees the mandatory reminder
3. If Claude tries to use Bash/Edit/Write without searching the brain first → `PreToolUse` **blocks it**
4. Claude is forced to call `mcp__open-brain__search` first
5. After searching, all tools are unlocked for the rest of the session

## Requirements

- Claude Code 2.1+ (hooks support)
- `python` or `python3` on PATH (for JSON parsing)
- Open Brain MCP server configured in settings.json
