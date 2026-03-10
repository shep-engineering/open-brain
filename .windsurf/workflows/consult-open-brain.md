---
description: consult open brain before acting
---

1. Before starting any task or making a decision, search the brain for prior context to avoid repeating mistakes.
   - In wired clients (Windsurf, Cursor, Claude): rely on auto-search or manually call `search` for the task topic.
2. When a mistake or issue is identified, capture it immediately.
   - Use `capture_context` with a short summary of the error, root cause, and fix.
3. Before shipping changes, re-check the brain for similar past issues.
   - Confirm the current fix doesn’t reintroduce a past regression.
4. After completion, capture learnings.
   - Include what went wrong, what was fixed, and any new guardrails.

Quick commands (any client wired to open-brain):
- Recall: `open-brain.search query="<topic or suspected mistake>"`
- Capture: `open-brain.capture_context context="<what happened / fix / guardrail>" source="<client>"`
