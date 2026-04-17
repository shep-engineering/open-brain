"""
Open Brain v2 — memory architecture rebuild per
docs/planning/windsurf-memory-architecture-synthesis.md (best-of-breed) +
docs/planning/infra-cost-addendum.md (falsifiable Ollama-cost check).

v2 runs alongside v1 on a separate Postgres container (port 5433, DB
open_brain_v2). v1 is untouched. MCP server name: open-brain-v2.
Tool namespace: mcp__open-brain-v2__*.

Phase 1 contracts:
- Headline-only boot payload (5 BLOCKER max, 2000 token cap)
- Bodies via recall(id)
- Ephemeral WORKING CONTEXT regenerated from task args
- In-session temporal cache (recency boost, ephemeral)
- Four atomic memory types: RULE / FACT / INCIDENT / TASK
- Write-path gauntlet: type + headline + atomicity + duplicate check +
  supersede-on-overlap. Merge is an invalid operation for RULE type.
- Immutable RULE bodies (supersede-only)
"""

__version__ = "2.0.0"
