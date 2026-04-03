# Tools Reference

Open Brain exposes 19 MCP tools: 16 user-facing tools and 3 cognitive architecture tools for agent compliance enforcement.

---

## Core Tools

### `remember`

Store a single explicit thought, note, or decision.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `thought` | string | Yes | The content to store |
| `type_override` | string | No | Force a specific memory type |
| `project` | string | No | Tag with a project name for scoped filtering |

**Called by:** Agents (after decisions) or users directly.

---

### `capture_context`

The primary auto-capture tool. Ingests raw conversation or session text, decomposes it into atomic memories, and stores them all at once using [smart batching](architecture/smart-batching.md).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `context` | string | Yes | Raw session text to process |
| `source` | string | No | Which agent is capturing (e.g. `"claude"`, `"cursor"`) |
| `project` | string | No | Tag with a project name |

**Called by:** Agents automatically after completing tasks, fixing bugs, or making decisions.

---

### `search`

Semantic search by meaning, not keywords. Returns previews (first 200 chars) to save tokens.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | What to search for (natural language) |
| `limit` | int | No | Max results (default 10, max 50) |
| `type_filter` | string | No | Filter by memory type: `decision`, `idea`, `meeting`, `person`, `insight`, `task`, `journal`, `reference`, `note`, `guardrail`, `procedural`, `episodic` |
| `people_filter` | string[] | No | Filter to memories mentioning specific people |
| `project` | string | No | Filter to a specific project |
| `since_days` | int | No | Only return memories created in the last N days (0 = no limit) |
| `until_days` | int | No | Only return memories older than N days (0 = no limit) |
| `as_of` | string | No | ISO 8601 timestamp. Only return memories whose `valid_time` ≤ this date (bi-temporal query). E.g. `'2025-03-01'` |
| `source` | string | No | Agent identifier for compliance tracking |

Results are ranked by a hybrid score: vector similarity (70%) + full-text rank (30%), multiplied by an uptime-based recency decay factor. Pinned memories for the project always appear first.

**Called by:** Agents automatically before starting tasks.

---

### `recall`

Fetch the full content of a memory by ID. Use after `search` to get complete text.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `memory_id` | int | Yes | The memory ID from search results |

**Side effects:** Bumps `access_count`, updates `last_accessed`, and records `last_accessed_uptime` (used for decay scoring).

---

### `remember` (updated)

Now accepts an optional `valid_time` parameter for bi-temporal backdating.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content` | string | Yes | The thought or information to store |
| `source` | string | No | Where captured from |
| `type_override` | string | No | Override auto-detected type |
| `project` | string | No | Project scope |
| `valid_time` | string | No | ISO 8601 timestamp of when the event actually happened (e.g. `'2025-03-01'`). Defaults to now. Used for `as_of` queries. |

---

## Quality Tools

### `rate`

Rate a memory as useful or not useful. Quality signals help surface the best memories.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `memory_id` | int | Yes | The memory ID |
| `direction` | string | Yes | `"up"` or `"down"` |

---

### `annotate`

Attach a persistent note to an existing memory: corrections, gotchas, or extra context.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `memory_id` | int | Yes | The memory ID |
| `note` | string | No | The annotation text. Omit to read the current annotation. |
| `clear` | bool | No | Set `true` to remove the annotation |

---

## Browse Tools

### `list_recent`

Browse the most recent captures.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `limit` | int | No | Max results (default 20, max 100) |
| `days` | int | No | Only show memories from the last N days |

---

### `stats`

Get database statistics: total memories, breakdown by type, captures in the last 7 and 30 days.

*No parameters.*

---

## Cleanup Tools

### `prune`

Remove stale memories that are old and rarely accessed.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | int | No | Delete memories older than this many days (default 90, minimum 30) |
| `min_access` | int | No | Minimum access count to keep |
| `dry_run` | bool | No | Preview what would be deleted (default `true`) |

---

### `forget`

Hard-delete a single memory by ID.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `memory_id` | int | Yes | The memory ID to delete |

---

### `forget_many`

Batch-delete multiple memories by ID.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `memory_ids` | int[] | Yes | List of memory IDs to delete |

---

## Working Memory Tools

Ephemeral key-value store for in-session context. **Cleared on every server restart.** Use for current task, active file, reasoning state. Use `remember` for persistent storage.

### `scratch_set`

Store a value in working memory.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `key` | string | Yes | Label for this context (e.g. `current_task`, `active_file`) |
| `value` | string | Yes | The value to store |

---

### `scratch_get`

Retrieve a value from working memory by key.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `key` | string | Yes | The label to look up |

Returns `{"key": ..., "value": ..., "found": true/false}`.

---

### `scratch_list`

List all keys and values currently in working memory. No parameters.

---

## Pinning Tools

### `pin`

Pin a memory so it always appears at the top of search results for its project, regardless of query similarity. Use for workflow rules, conventions, and guardrails agents must always see.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `memory_id` | int | Yes | The memory ID to pin |

!!! note
    Memory must belong to a project to be pinned. Global memories (no project) cannot be pinned.

---

### `unpin`

Remove the pin from a memory, returning it to normal search ranking.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `memory_id` | int | Yes | The memory ID to unpin |

---

## Agent Workflow

```
1. Agent starts task
   |
   v
2. search(): find prior context (silent)
   |
   v
3. Work happens (coding, research, discussion)
   |
   v
4. capture_context(): store what was learned (silent)
   |
   v
5. Later: another task on same topic
   |
   v
6. search(): prior decision is already there
```

The user never has to think about memory. Agents capture and recall automatically.

---

## Cognitive Architecture Tools

These tools enforce the session boot and continuous brain check workflow. They are called automatically by Claude Code hooks and agent system prompts.

### `boot_session`

Initialize your brain for a new session. Loads full project context before the AI can act.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `project` | string | No | The project to load context for (e.g. "open-brain") |
| `source` | string | No | Which agent is booting (e.g. "claude", "windsurf") |

**Returns:** Pinned guardrails (full content), project architecture context, recent session history (7 days), known issues/corrections, and repeated correction warnings. All stored in the session scratchpad.

**Enforcement:** Server blocks `remember()` and `capture_context()` until `boot_session` has been called. Claude Code's PreToolUse hook blocks ALL non-brain tools until boot appears in the transcript.

---

### `brain_checkpoint`

Check the brain before a risky action. Searches for relevant memories and surfaces guardrails.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | Yes | What you're about to do (e.g. "edit infrastructure script") |
| `context` | string | No | Specific files or changes |
| `project` | string | No | Project scope |
| `source` | string | No | Which agent is calling |

**Returns:** Guardrails and relevant memories with similarity scores. 5-minute cooldown per topic per source.

**Enforcement:** Claude Code's PreToolUse hook BLOCKS editing infrastructure, database, deployment, server, and configuration files until `brain_checkpoint` has been called.

---

### `brain_startup_reminder`

System message injection tool. Returns a structured reminder about Open Brain enforcement. Used by hooks to inject context into the AI's conversation.

---

## Security

### Secrets Filter

All content passed to Open Brain is scanned for API keys, tokens, private keys, and database passwords before embedding or storage. Matches include:

- AWS, Google Cloud, Azure credentials
- JWT/Bearer tokens
- SSH/RSA private keys
- Database connection strings with passwords
- Generic API key patterns

**Mode:** Reject (default) -- blocks storage entirely and returns an error. The secrets filter runs before embedding, so sensitive content never reaches the embedding model.
