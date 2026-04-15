# Belief Revision in Open Brain — Design Doc

**Status:** PROPOSED — design pass, not yet implemented.
**Branch:** `feat/brain-belief-revision`
**Backup:** `backups/brain-pre-belief-revision-20260414-102607.sql` (3326 memories baseline, taken before any schema change).
**Targets:** open-brain v0.11.0.

---

## Problem statement

Open Brain stores memories indefinitely and has no native mechanism to
mark older memories as superseded by newer, corrected ones. The
canonical example is **memory #3663**, which currently asserts BOTH
"the ON script does NOT start the MCP server" (a principle) AND
"launch server.py via the start command" (a contradicting action item).
Future agents that recall #3663 receive mixed signals and act on
whichever fragment matches the surface pattern of their query.

The 2026-04-14 roadmap-drift incident (memory #1126) is the same class
of failure at coarser granularity — facts in two of three places, one
of them stale.

Today's mitigations are weak:
- **Manual memory deletion** loses audit trail
- **Confidence decay** (already partially implemented via uptime decay)
  ranks older memories lower but does NOT prevent contradictions
- **Pinning** preserves guardrails but doesn't resolve when two pinned
  memories contradict
- **Boot-session preamble** dumps all guardrails; agent has to mentally
  reconcile contradictions on every turn

The fix is **explicit supersession**: a structural relationship between
a corrected memory and the corrector that lets search/recall filter to
the current truth by default, while preserving the audit trail of
what was previously believed.

---

## Design space considered

| Option | Description | Verdict |
|---|---|---|
| **(a) Supersede relation** | Add `superseded_by_id` column; new memory points old at itself; search filters out superseded by default | ✅ **Adopted (primary)** |
| **(b) Auto-detect contradictions in recall** | LLM scans search results for semantic conflict, surfaces warnings | ❌ Deferred — adds latency to every recall, false-positive risk; consider for v0.12+ once supersession baseline exists |
| **(c) Confidence decay on conflict** | Older memories lose ranking when newer ones contradict | ❌ Orthogonal — already partially implemented as uptime decay; doesn't address contradictions |
| **(d) Explicit `supersede` MCP tool** | Agent (or user) calls `supersede(old_id, new_content, reason)` to mark + replace | ✅ **Adopted (the agent-facing API for option a)** |

**Adopted approach: (a) + (d) combined.** Supersession is a first-class
relation in the schema; agents call a dedicated MCP tool to create the
relation. (b) and (c) layer on later if real failures argue for them.

---

## Schema additions

Three new columns on `memories`, all nullable so existing rows stay valid:

```sql
ALTER TABLE memories
    ADD COLUMN superseded_by_id INTEGER REFERENCES memories(id) ON DELETE SET NULL,
    ADD COLUMN superseded_at TIMESTAMPTZ,
    ADD COLUMN superseded_reason TEXT;

-- Active (non-superseded) memories are the common search/recall target.
-- Partial index keeps it cheap.
CREATE INDEX idx_memories_active
    ON memories (id)
    WHERE superseded_by_id IS NULL;

-- Reverse lookup: "what memory does this one supersede?" used by the
-- audit-trail UI on the dashboard memory popup.
CREATE INDEX idx_memories_superseded_by
    ON memories (superseded_by_id)
    WHERE superseded_by_id IS NOT NULL;
```

**Reversibility:** the migration is fully reversible by
`ALTER TABLE memories DROP COLUMN ...` for each of the three columns.
Backup taken before applying.

**Audit trail:** rows are NEVER deleted on supersession. The old
memory persists with its content + embedding intact; only the three
new columns get populated. The existing `memories_audit` table also
records the UPDATE.

---

## MCP tool surface

### New tool — `supersede`

```python
@mcp.tool()
def supersede(
    old_memory_id: int,
    new_content: str,
    reason: str,
    source: str,
    type_override: str = "",
    project: str = "",
) -> str:
    """Mark an existing memory as superseded by a new corrected memory.

    The old memory is preserved (audit trail) but excluded from default
    search/recall results. The new memory is created with all the
    standard processing (embedding, metadata extraction, dedup, etc.)
    and its ID is written to old.superseded_by_id.

    Args:
        old_memory_id: ID of the memory being corrected.
        new_content: The corrected/replacement content.
        reason: Why the old memory is wrong or outdated. Stored on the
                old memory's superseded_reason column. Required — agents
                must justify supersession to prevent casual overwrites.
        source: REQUIRED. Which agent is supersedeing.
        type_override: Optional type for the new memory.
        project: Project tag for the new memory (defaults to old.project).

    Returns:
        JSON with both memory IDs:
            {"old_id": <int>, "new_id": <int>, "superseded_at": "..."}
    """
```

**Validation:**
- `old_memory_id` must exist; otherwise return `not_found` error
- `old_memory_id` must NOT already be superseded (chains form a tree,
  not a DAG; if you want to correct a corrector, supersede the LATEST,
  not the original)
- `reason` must be non-empty (no silent overwrites)
- `source` required (same as `remember`)
- `new_content` runs through `secrets_filter` like any new memory

### Modified tools — `search` / `recall` / `list_recent`

All three gain a single new optional param:

```python
include_superseded: bool = False
```

- **Default behavior (False):** results filtered to active memories only
  (`WHERE superseded_by_id IS NULL`).
- **`include_superseded=True`:** all memories included; superseded ones
  surface with metadata indicating their supersession (so agents
  consuming them know).

`recall(memory_id)` is special-cased: if you ASK FOR a superseded
memory by ID directly, you get its content (audit semantics) plus a
banner block in the response pointing at the superseder ID. Same
shape as the existing pinning indicator.

### Optional v1.1 — `unsupersede`

If we make a mistake and supersede something wrongly, we need a way
back. Cheap one-line tool:

```python
@mcp.tool()
def unsupersede(memory_id: int, source: str) -> str:
    """Reverse a supersession by clearing superseded_by_id, _at, _reason.
    The new memory created during the supersede() call is NOT deleted —
    it remains active. If you want to undo entirely, also forget() the
    new memory."""
```

**Decision: ship in v0.11.0** — it's 5 lines and the safety net matters.

---

## Search / recall semantics — concrete examples

```
Memories table:
  id=100  content="docker-compose port 5432"   superseded_by_id=NULL
  id=101  content="ON script doesn't start MCP" superseded_by_id=NULL
  id=102  content="ON script DOES start MCP via tmux"
                                               superseded_by_id=NULL

User calls supersede(101, "ON script does NOT start MCP server in
v0.7.0+; start happens via the MCP client (Claude Code, Cursor, etc.)
spawning server.py", reason="Original was contradicted by 102; this
explanation reconciles both")

Result:
  id=101  superseded_by_id=103, superseded_at=now(), superseded_reason="..."
  id=102  superseded_by_id=NULL  (unchanged — wasn't the target)
  id=103  content="ON script does NOT start MCP server..."  superseded_by_id=NULL  (NEW)

search("how does MCP server start", include_superseded=False)
  -> returns 102, 103 only (101 filtered)

search("how does MCP server start", include_superseded=True)
  -> returns 101 (with superseded_by=103 marker), 102, 103

recall(101)
  -> returns 101's content + a banner: "This memory was superseded by
     #103 on 2026-04-14: 'Original was contradicted by 102; this
     explanation reconciles both'"

recall(103)
  -> returns 103's content normally (no banner — it's active)
```

---

## Dedup interaction

The existing dedup logic compares new content against ALL existing
memories above a similarity threshold. For supersession to work
cleanly, dedup must:

- **NOT match against superseded memories.** If you supersede #101 and
  later try to remember the same corrected content, you'd false-match
  against #101 (which is still in the table) and skip the write.
- Filter dedup query to `WHERE superseded_by_id IS NULL`.

This is a one-line change to the dedup query in `server.py`.

---

## Tests

`tests/test_belief_revision.py`, all hitting the test DB on port 5434:

1. `test_supersede_basic` — supersede a memory, verify both IDs returned
   and old.superseded_by_id == new.id
2. `test_supersede_filters_search` — search excludes superseded by default
3. `test_supersede_include_flag` — `include_superseded=True` returns both
4. `test_recall_superseded_returns_banner` — recall(old_id) includes
   supersession metadata
5. `test_supersede_chains_blocked` — superseding an already-superseded
   memory returns an error pointing at the latest in the chain
6. `test_supersede_requires_reason` — empty `reason` rejected
7. `test_supersede_requires_source` — empty `source` rejected
8. `test_supersede_preserves_audit` — `memories_audit` table records the
   UPDATE on the old memory
9. `test_dedup_ignores_superseded` — re-storing content similar to a
   superseded memory does NOT skip the write
10. `test_unsupersede_reverses` — `unsupersede()` clears the relation
11. `test_unsupersede_does_not_delete_new` — the corrector memory
    survives; only the relation is cleared
12. `test_secrets_filter_runs_on_supersede` — secret content in
    `new_content` is rejected by the same filter `remember()` uses
13. `test_list_recent_filters_by_default` — `list_recent` excludes
    superseded unless `include_superseded=True`

---

## Migration script — `scripts/migrate_v4_belief_revision.py`

Standalone Python script (matches existing `migrate_v2.py`,
`migrate_v3_pinned.py` pattern). Idempotent — checks if columns
already exist before adding.

```python
#!/usr/bin/env python3
"""Migration v4 — Belief revision (supersession) columns + indexes.

Adds three nullable columns + two indexes to the memories table.
Idempotent; safe to re-run.
"""
import os, psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")

MIGRATIONS = [
    ("superseded_by_id column", """
        ALTER TABLE memories
        ADD COLUMN IF NOT EXISTS superseded_by_id INTEGER
            REFERENCES memories(id) ON DELETE SET NULL;
    """),
    ("superseded_at column", """
        ALTER TABLE memories ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;
    """),
    ("superseded_reason column", """
        ALTER TABLE memories ADD COLUMN IF NOT EXISTS superseded_reason TEXT;
    """),
    ("active partial index", """
        CREATE INDEX IF NOT EXISTS idx_memories_active
            ON memories (id) WHERE superseded_by_id IS NULL;
    """),
    ("reverse-lookup partial index", """
        CREATE INDEX IF NOT EXISTS idx_memories_superseded_by
            ON memories (superseded_by_id) WHERE superseded_by_id IS NOT NULL;
    """),
]

def main():
    print(f"Connecting to {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else DATABASE_URL}")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    for name, sql in MIGRATIONS:
        print(f"  applying: {name} ...", end=" ", flush=True)
        cur.execute(sql)
        print("OK")
    print("Migration v4 complete.")

if __name__ == "__main__":
    main()
```

---

## Documentation updates required

| File | Section to add/update |
|---|---|
| `docs/tools.md` | New `supersede` entry; new `unsupersede` entry; update `search` / `recall` / `list_recent` rows with the new `include_superseded` param |
| `docs/architecture/memory-model.md` | New "Belief revision" section after the existing memory-types content |
| `CHANGELOG.md` | v0.11.0 entry covering schema migration, new tools, modified search/recall semantics, dedup fix |
| `docs/planning/BRAIN_HARNESS_PLAN.md` | Add cross-reference: belief revision is the per-memory dual of Phase 6 (Deletable-guardrail audit) at the per-rule level |
| `prompts/*.md` | Mention `supersede` as a tool agents should use when they encounter contradictions |
| `CLAUDE.md` | One-line in workflow rules: "When you encounter a contradicting memory, call supersede(old_id, corrected_content, reason) instead of deleting." |

---

## Rollout sequence

Per guardrail #827 — schema migrations are HIGH RISK:

1. ✅ Backup taken (above)
2. ✅ Working on isolated branch (`feat/brain-belief-revision`)
3. Implement against test DB (`docker-compose.test.yml`, port 5434)
4. Verify migration is idempotent (run twice, second time is no-op)
5. Verify migration is reversible (drop columns, no orphaned data)
6. All tests green against test DB
7. **Pause for Shep design review + go-no-go**
8. Apply migration to production brain
9. Smoke test: call `supersede(3663, ..., reason=...)` on the canonical contradicting-facts example. Verify subsequent `search` results no longer return #3663 by default; `recall(3663)` returns content + supersession banner.
10. Tag v0.11.0, push to degailen, ship.

---

## Open questions for Shep (need answers before step 7)

1. **Tool naming.** I propose `supersede` (clear semantics, neutral
   tone). Alternatives: `correct`, `update_memory`, `revise`. Strong
   preference?

2. **`recall(superseded_id)` behavior.** I propose: return the original
   content AND a metadata banner pointing at the superseder. Alternative:
   auto-redirect to the superseder transparently (cleaner UX, but loses
   the audit-by-ID-lookup semantics). Pick.

3. **Should pinning be inheritable?** If a pinned guardrail gets
   superseded, should the new memory auto-inherit the pinned status? I
   propose YES (otherwise the brain quietly loses guardrail enforcement
   when an agent corrects a pinned rule). Concern: an agent could
   accidentally promote a non-guardrail to guardrail status by
   supersedeing a pinned memory with non-guardrail content. Mitigation:
   require explicit `inherit_pinned=True` flag.

4. **Auto-supersession by similarity?** Future v0.12+ — when remember()
   gets called with content that's very similar to an existing memory
   AND clearly contradicts it, should the brain auto-supersede? OR keep
   it strictly explicit (manual supersession only)? I propose
   strictly explicit for v0.11.0; auto for v0.12+ only if real failures
   argue for it.

5. **Dashboard surfacing.** Should the dashboard show a "superseded
   chain" view for any memory? Could be a row count "this memory has 3
   superseded predecessors" + clickable expansion. Out-of-scope for
   v0.11.0 backend work but worth flagging for future UI work.

---

## Effort estimate

| Step | Time |
|---|---|
| Migration script + idempotency tests | 30 min |
| `supersede` MCP tool implementation | 1 hour |
| `unsupersede` MCP tool implementation | 15 min |
| Search/recall/list_recent param + filter | 1 hour |
| Dedup query update | 15 min |
| Test suite (13 tests above) | 2 hours |
| Documentation pass | 1 hour |
| Production smoke test on #3663 + verify | 30 min |
| **Total** | **~6.5 hours** |

Faster than my initial 2–3 day estimate because the schema is small
(3 columns, 2 indexes) and the API surface is tightly scoped.
