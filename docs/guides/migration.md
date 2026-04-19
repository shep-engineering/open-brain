# Migration

## v1 Schema Migrations (internal)

v1 has had several internal schema migrations (adding columns, indexes, audit triggers). These are applied automatically by `scripts/setup_db.py` or on first server boot:

```sh
python scripts/setup_db.py
```

Safe to re-run. Existing memories are unaffected.

---

## v1 to brain_v2 (architecture migration)

brain_v2 is a separate system running alongside v1, not an in-place upgrade. There is no automated v1→v2 data migration — this is intentional per the [bifurcation guardrail](../architecture/v2-architecture.md).

### What's different

| Aspect | v1 | v2 |
|--------|----|----|
| Database | `openbrain` on port 5432 | `open_brain_v2` on port 5433 |
| MCP namespace | `mcp__open-brain__*` | `mcp__open-brain-v2__*` |
| Storage | Single `memories` table | 4 typed tables + `memory_index` |
| Write path | Auto-detect type via LLM | 5-step write gate, no LLM |
| Boot payload | Full content (15K+ tokens) | Headline-only (2K token cap) |
| Rule modification | Direct update | Immutable bodies, supersede-only |

### Running both simultaneously

Both servers run side by side. All MCP clients connect to both:

1. Start v1: `python server.py` (or via MCP client config)
2. Start v2: `python brain_v2/server.py` (or via MCP client config)
3. Start v2 database: `docker compose -f docker-compose.v2.yml up -d`

v1 remains authoritative until explicit cutover. v2 builds its own corpus from new agent interactions during the soak period.

### Manual data seeding (optional)

If you want to seed v2 with important v1 memories:

1. Query v1 for pinned guardrails: `search(query="guardrail", project="your-project")`
2. For each important memory, store in v2 as the appropriate type:
   - Behavioral rules → `remember_rule_v2(headline, body, severity="BLOCKER")`
   - Project facts → `remember_fact_v2(headline, body)`
   - Past incidents → `remember_incident_v2(headline, body, root_cause, resolution)`

v2's write gate will enforce atomic headlines (<=15 words) and body limits (<=400 words), so v1 memories that were merged walls of text will need to be split into atomic units.

### Cutover (future)

Cutover from v1 to v2 is explicitly out of scope during the soak period. The plan:

1. Soak v2 for 2+ weeks with all agents actively using it
2. Review `metrics_v2` for error patterns, `tool_events` for usage data
3. Once v2 is proven stable under real multi-agent load, plan the cutover
4. Cutover requires Dave's explicit sign-off

---

## Migrating from Another Memory System

If you're moving from a different AI memory tool (Claude's built-in memory, ChatGPT memory, etc.), run this prompt in your current AI:

```
Export everything you know about me, including my projects, preferences, key people,
past decisions, ongoing work, and constraints, as a series of plain-text
notes, one per line. I'm migrating to a new memory system.
```

Then feed each line to `remember` (v1) or `capture_context_v2` (v2) to seed your Open Brain.
