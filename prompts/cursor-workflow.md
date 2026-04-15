---
description: consult open brain before acting (Cursor)
---

1. Before coding/deciding, search the brain for prior mistakes/decisions.
   - In Cursor, use `open-brain.search query="<topic or suspected mistake>"`
2. After bugs/decisions, capture immediately.
   - `open-brain.capture_context context="<issue/fix/guardrail>" source="cursor"`
3. Before shipping, re-check similar past issues to avoid regressions.
4. After finishing, capture learnings and guardrails.

Reminders:
- MCP config: `C:\Users\<USERNAME>\.cursor\mcp.json` with `open-brain` stdio server
- Rules: paste `prompts/cursor-rules.md` into Cursor Settings → General → Rules for AI
- Tools: `capture_context`, `search`, `remember`, `list_recent`, `stats`, `forget`
