---
description: consult open brain before acting (Claude)
---

1. Before coding or deciding, search the brain for prior mistakes/decisions.
   - Use `open-brain.search query="<topic or suspected mistake>"`
2. After bugs/decisions, capture immediately.
   - `open-brain.capture_context context="<issue/fix/guardrail>" source="claude-vscode"`
3. Before shipping, re-check similar past issues to avoid regressions.
4. After finishing, capture learnings and guardrails.

Reminders:
- MCP server: `open-brain` (stdio) via `claude mcp add ... --scope user`
- Dual GPU env (for metadata LLM): OLLAMA_NUM_GPU=2, CUDA_VISIBLE_DEVICES=0,1
- Tools: `capture_context`, `search`, `remember`, `list_recent`, `stats`, `forget`
