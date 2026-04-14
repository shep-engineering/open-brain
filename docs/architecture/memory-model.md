# Memory Model

Every memory in Open Brain is a row in PostgreSQL with a dense vector embedding, structured metadata, and quality signals.

---

## Schema

```sql
CREATE TABLE memories (
    id              SERIAL PRIMARY KEY,
    content         TEXT NOT NULL,
    embedding       VECTOR(768),          -- or 1024/1536 depending on model
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    project         TEXT DEFAULT '',
    annotation      TEXT DEFAULT '',
    access_count    INTEGER DEFAULT 0,
    last_accessed   TIMESTAMPTZ,
    upvotes         INTEGER DEFAULT 0,
    downvotes       INTEGER DEFAULT 0
);
```

---

## Metadata Fields

The `metadata` JSONB column contains:

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | One of: `decision`, `idea`, `meeting`, `person`, `insight`, `task`, `journal`, `reference`, `note` |
| `people` | string[] | Names extracted from content (`@mentions` or LLM-detected) |
| `topics` | string[] | Key subjects (capitalized nouns or LLM-extracted) |
| `action_items` | string[] | Sentences containing "need to", "must", "follow up", etc. |
| `tags` | string[] | Explicit `#hashtag` markers |
| `source` | string | Which agent captured it (`claude`, `windsurf`, `cursor`, etc.) |
| `auto_captured` | boolean | `true` if from `capture_context`, `false` if from `remember` |

---

## Memory Types

| Type | When to use |
|------|-------------|
| `decision` | Architectural choice, tooling selected, approach taken |
| `idea` | Brainstorm, "what if", proposal |
| `meeting` | Conversation notes, standup, call |
| `person` | Information about someone (role, preferences) |
| `insight` | Learned fact, discovery, "aha" moment |
| `task` | Action item, follow-up, reminder |
| `journal` | Reflection, personal log |
| `reference` | Link, article, resource |
| `procedural` | Workflow rules, how-to knowledge, step-by-step conventions, non-negotiables |
| `episodic` | Specific past events, "last time X happened", session recollections |
| `note` | Anything else (default) |

---

## Quality Signals

### Ratings

Call `rate(id, "up")` or `rate(id, "down")` after using a memory. The score (upvotes - downvotes) appears in search results, surfacing the most useful memories over time.

### Access Tracking

Every `recall` call bumps `access_count` and updates `last_accessed`. This data powers `prune`. Memories that are old and never accessed can be cleaned up.

### Annotations

Attach persistent notes to existing memories with `annotate`. Use this for corrections, gotchas, or warnings that should surface alongside the original memory.

---

## Deduplication

On every `remember` and `capture_context` call, Open Brain checks for near-duplicates:

1. Embed the new content
2. Find the closest existing memory by cosine similarity
3. If similarity >= threshold (default 0.92):
    - If the new content is longer (more detailed), update the existing memory
    - Otherwise, skip. The memory already exists

This prevents the same decision or fact from being stored dozens of times across sessions.

---

## Indexes

| Index Type | Column | Purpose |
|-----------|--------|---------|
| HNSW (m=16, ef=64) | `embedding` | Fast approximate nearest-neighbor search |
| GIN | `metadata` | Filter by type, people, topics |
| B-tree | `created_at DESC` | Time-range queries |
| B-tree | `project` | Project-scoped filtering |
| B-tree | `last_accessed` | Identify stale memories for pruning |
| Partial B-tree | `id WHERE superseded_by_id IS NULL` | Fast active-only scans (added v0.11.0) |
| Partial B-tree | `superseded_by_id WHERE NOT NULL` | Reverse-lookup superseded chain (added v0.11.0) |
| Partial GIN | `skill_trigger WHERE skill_trigger IS NOT NULL` | Skill-layer keyword scans (added v0.12.0) |

---

## Belief Revision (added v0.11.0)

Memories are **revisable beliefs**, not immutable facts. When an
agent learns a corrected version of a previously-stored memory, it
calls `supersede(old_id, new_content, reason, source)` — the brain
creates a new memory through the standard pipeline and writes its ID
into `old.superseded_by_id`. The old memory is preserved (audit
trail intact) but excluded from default search/recall results.

```
Before supersede:
  id=101 "ON script doesn't start MCP"  superseded_by_id=NULL
  id=102 "ON script DOES start MCP via tmux"  superseded_by_id=NULL

After supersede(101, "ON script does NOT start MCP server in v0.7.0+;
  start happens via the MCP client spawning server.py", reason=...)

  id=101 "ON script doesn't start MCP"  superseded_by_id=103
  id=102 "ON script DOES start MCP via tmux"  superseded_by_id=NULL
  id=103 "ON script does NOT start MCP server in v0.7.0+; ..."
                                              superseded_by_id=NULL  (NEW)

search() now returns 102, 103 only — 101 is filtered out.
search(include_superseded=True) returns all three.
recall(101) returns 101's content + a banner pointing at 103.
```

**Why structurally instead of by deletion:** preserves the audit
trail of past beliefs (you can always reconstruct what was thought
to be true at any point) AND prevents an agent that already has a
stale memory ID from getting silent "not found" errors after a
correction. The supersession metadata tells them where the truth
moved to.

**Schema columns:**
- `superseded_by_id INTEGER REFERENCES memories(id) ON DELETE SET NULL`
- `superseded_at TIMESTAMPTZ`
- `superseded_reason TEXT` (required on every supersede call —
  no silent overwrites)

**Dedup interaction:** `db_find_duplicate` and `db_find_related`
filter `WHERE superseded_by_id IS NULL`. Without this, re-storing
content similar to a corrected (now-superseded) memory would
false-match against the stale version and skip the write.

**Pinning inheritance** is opt-in via `inherit_pinned=True` on
`supersede()`. Defaults to NOT inheriting — explicit opt-in
prevents accidental promotion of a non-guardrail to guardrail
status when correcting a pinned memory.

See `tools.md` for the `supersede` and `unsupersede` MCP tool
reference. See `docs/planning/BELIEF_REVISION_DESIGN.md` (internal)
for the full design discussion + alternatives considered.

---

## Skills Layer (added v0.12.0)

Pinning meant "load this memory at every session boot". That worked
until the pinned set grew to ~26 guardrails on a single project and
started competing with actual task reasoning for the agent's
instruction budget. The skills layer decouples *priority* (pinned)
from *load behavior* (always-on vs. triggered).

A memory can now carry a `skill_trigger JSONB` payload:

```json
{
  "name": "ollama-shutdown-graceful",
  "keywords": ["ollama", "shutdown", "graceful"],
  "projects": [],
  "always_on": false
}
```

- **`name`** — globally unique. Enables explicit `load_skill(name)`
  lookup. Convention: lowercase, hyphen-separated.
- **`keywords`** — array of strings. Case-insensitive substring match
  against a query, OR across entries. One hit fires the skill.
- **`projects`** — empty array = global (loadable/surfaceable from
  anywhere); populated = only surfaces when the caller's project is
  in the list.
- **`always_on`** — if `true`, the memory still returns in
  `boot_session` exactly like a legacy pinned guardrail. Defaults
  `false` — the point of the layer.

**Load paths:**
1. **Boot** — `skill_trigger IS NULL` OR `always_on = true` AND pinned.
2. **Search keyword auto-match** — `skill_trigger.keywords` substring-match query; up to `OPEN_BRAIN_SKILL_TRIGGER_MAX` (default 5) surface at the top of the result set with `via_skill_trigger: "<name>"`.
3. **Explicit** — `load_skill(name, source, project)` fetches one by unique name.

**Superseded skills** are excluded from both auto-match and explicit
load (active-only default, matching v0.11.0 belief-revision).

**Schema column:**
- `skill_trigger JSONB DEFAULT NULL` — null means "behave like pre-v0.12.0".

**Backwards compatibility:** existing pinned memories have `skill_trigger = NULL` and load at boot exactly as before. Migration of individual guardrails to skill-triggered mode is opt-in via `supersede` (the corrector carries the new `skill_trigger`).

See `tools.md` for the `load_skill` MCP tool reference and the
`remember` / `search` / `boot_session` changes. See
`docs/planning/SKILLS_LAYER_DESIGN.md` for the full design discussion,
alternatives considered, and Phase 4 hook-installer integration notes.
