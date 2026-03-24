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
