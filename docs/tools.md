# Tools Reference

Open Brain runs two MCP servers side by side:

- **v1** (`mcp__open-brain__*`) — 26 tools. Single `memories` table.
- **v2** (`mcp__open-brain-v2__*`) — 39 tools. Typed tables, write gate, headline-only boot. See [v2 Architecture](architecture/v2-architecture.md).

---

## v1 Tools

The v1 tools below use the `mcp__open-brain__` namespace.

---

## Core Tools

### `remember`

Store a single explicit thought, note, or decision.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content` | string | Yes | The content to store |
| `source` | string | **Yes** | Which agent is storing (e.g. `"claude"`, `"cursor"`). REQUIRED since v0.7.0 for per-agent session compliance. |
| `type_override` | string | No | Force a specific memory type |
| `project` | string | No | Tag with a project name for scoped filtering |
| `skill_trigger` | dict | No | Tag this memory as a skill (v0.12.0+). See [Skills Layer](#skills-layer-v0120) for shape and behavior. |

**Called by:** Agents (after decisions) or users directly.

---

### `capture_context`

The primary auto-capture tool. Ingests raw conversation or session text, decomposes it into atomic memories, and stores them all at once using [smart batching](architecture/smart-batching.md).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `context` | string | Yes | Raw session text to process |
| `source` | string | **Yes** | Which agent is capturing (e.g. `"claude"`, `"cursor"`). REQUIRED since v0.7.0. |
| `project` | string | No | Tag with a project name |

**Called by:** Agents automatically after completing tasks, fixing bugs, or making decisions.

---

### `search`

Semantic search by meaning, not keywords. Returns previews (first 200 chars) to save tokens.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | What to search for (natural language) |
| `source` | string | **Yes** | Agent identifier for per-agent compliance tracking. REQUIRED since v0.7.0 — a search from one source does NOT reset another source's counter. |
| `limit` | int | No | Max results (default 10, max 50) |
| `type_filter` | string | No | Filter by memory type: `decision`, `idea`, `meeting`, `person`, `insight`, `task`, `journal`, `reference`, `note`, `guardrail`, `procedural`, `episodic` |
| `people_filter` | string[] | No | Filter to memories mentioning specific people |
| `project` | string | No | Filter to a specific project |
| `since_days` | int | No | Only return memories created in the last N days (0 = no limit) |
| `until_days` | int | No | Only return memories older than N days (0 = no limit) |
| `as_of` | string | No | ISO 8601 timestamp. Only return memories whose `valid_time` ≤ this date (bi-temporal query). E.g. `'2025-03-01'` |
| `include_superseded` | bool | No | If `true`, also include memories that have been superseded by a corrected newer one (audit/historical view). Default `false` — normal search only returns current truth. *(Added v0.11.0.)* |

Results are ranked by a hybrid score: vector similarity (70%) + full-text rank (30%), multiplied by an uptime-based recency decay factor. Pinned memories for the project always appear first, followed by skill-triggered auto-matches (see [Skills Layer](#skills-layer-v0120)), then semantic matches.

**Skill auto-match** *(added v0.12.0):* if any memory has `skill_trigger.keywords` that substring-match the query, up to `OPEN_BRAIN_SKILL_TRIGGER_MAX` (default 5) are inserted at the top of the result set with a `via_skill_trigger: "<name>"` flag.

**Called by:** Agents automatically before starting tasks.

> **BREAKING (v0.7.0):** `source` is now required on `search`, `remember`, `capture_context`, `boot_session`, and `brain_checkpoint`. Empty or omitted source is rejected with `blocked_by: source_required`. Rationale: per-agent session compliance can't be tracked without attribution, and agents were inconsistently plumbing `source` through, causing compliance blocks to fire on calls that should have been resetting their own counter.

---

### `recall`

Fetch the full content of a memory by ID. Use after `search` to get complete text.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `memory_id` | int | Yes | The memory ID from search results |

**Side effects:** Bumps `access_count`, updates `last_accessed`, and records `last_accessed_uptime` (used for decay scoring).

**Superseded-memory behavior** *(added v0.11.0):* if you `recall` a memory whose `superseded_by_id` is set, the response includes the original content (audit semantics — "show me what we used to believe") plus a `banner` field, `superseded_by_id`, `superseded_at`, and `superseded_reason` pointing at the corrector. Use `recall(superseded_by_id)` to read the current truth.

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
| `include_superseded` | bool | No | If `true`, include memories that have been superseded. Default `false`. *(Added v0.11.0.)* |

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

## Belief-Revision Tools (added v0.11.0)

### `supersede`

Mark an existing memory as superseded by a corrected newer one. The old memory is preserved (audit trail intact) but excluded from default `search` / `recall` / `list_recent` results so agents see only current truth. Use this **instead of `forget()`** when knowledge has been REVISED rather than ABANDONED.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `old_memory_id` | int | **Yes** | ID of the memory being corrected. Must exist and not already be superseded (chains form a tree, not a DAG — supersede the LATEST in the chain, not the original). |
| `new_content` | string | **Yes** | The corrected/replacement content. Runs through the standard pipeline (embedding, metadata extraction, secrets-filter, dedup against active memories). |
| `reason` | string | **Yes** | Why the old memory is wrong or outdated. Required — no silent overwrites. Stored on the old memory's `superseded_reason` column for audit. |
| `source` | string | **Yes** | Agent identifier (e.g. `"claude"`, `"cursor"`). Required, same compliance gating as `remember`. |
| `type_override` | string | No | Override auto-detected type for the new memory. |
| `project` | string | No | Project tag for the new memory. Defaults to the old memory's project. |
| `inherit_pinned` | bool | No | If `true` AND the old memory was pinned, the new memory is auto-pinned. Default `false` — explicit opt-in to avoid accidentally promoting a non-guardrail to guardrail status. |

**Returns** JSON with both IDs:
```json
{
  "success": true,
  "old_id": 3663,
  "new_id": 5072,
  "new_action": "stored",
  "old_pinned": true,
  "new_pinned": false,
  "type": "decision",
  "project": "open-brain"
}
```

---

### `unsupersede`

Reverse a supersession by clearing the supersession metadata. Both memories end up active. To fully undo, also call `forget()` on the corrector.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `memory_id` | int | **Yes** | ID of the memory whose supersession to clear. |
| `source` | string | **Yes** | Agent identifier. |

---

## Skills Layer (v0.12.0+)

Memories tagged with a `skill_trigger` JSONB payload become **conditionally-loaded** — they don't ship on every `boot_session`, and instead surface when a keyword matches a query or when explicitly loaded by name. This shrinks the instruction budget spent on always-on guardrails.

`skill_trigger` shape:
```json
{
  "name": "ollama-shutdown-graceful",
  "keywords": ["ollama", "shutdown", "graceful"],
  "projects": [],
  "always_on": false
}
```

- `name` — globally unique identifier for `load_skill()` lookup.
- `keywords` — case-insensitive substring match against the query (OR across keywords).
- `projects` — empty array = global skill, loadable from any project. Populated = only loadable/surfaced inside those projects.
- `always_on` — if `true`, the memory still loads at boot (for the narrow set of rules that must fire every session). Default `false`.

Memories with `skill_trigger = NULL` behave exactly as before (backwards-compatible).

### `load_skill`

Load a specific skill by its trigger name. Use when you're about to start work on a topic you know has a named skill (e.g. `ollama-shutdown-graceful` before touching ollama shutdown code). Returns `success`, `content`, `id`, `type`, `project`, and `skill_trigger` metadata.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | **Yes** | The `skill_trigger.name` of the skill. |
| `source` | string | **Yes** | Agent identifier. |
| `project` | string | No | Current project scope. Required to load project-scoped skills. |

Only active (non-superseded) skills are returned.

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
| `task` | string | No | Short free-form description of the session's intent (typically the first user prompt, truncated). Populates `active_sessions.current_task` so sibling sessions see what you're doing. *(Added v0.13.0.)* |
| `cwd` | string | No | Absolute path of the caller's working directory. Strongly recommended — it's how sibling-session lookups distinguish "same repo, different window" from "unrelated." *(Added v0.13.0.)* |
| `pid` | int | No | Process id of the MCP client, if known. *(Added v0.13.0.)* |
| `host` | string | No | Hostname of the MCP client. *(Added v0.13.0.)* |

**Returns:** Pinned guardrails (full content), project architecture context, recent session history (7 days), known issues/corrections, repeated correction warnings, and (v0.13.0+) an `OTHER ACTIVE SESSIONS` section listing sibling live MCP sessions. All stored in the session scratchpad. The response also includes `session_id` for later use with `update_active_task` / `end_session`.

*(v0.12.0)* The pinned-guardrails set returned at boot now excludes skill-tagged memories unless `skill_trigger.always_on` is `true`. Skill-triggered memories instead surface via `search` keyword auto-match or explicit `load_skill(name)`. See [Skills Layer](#skills-layer-v0120).

*(v0.13.0 / reworked v0.14.0)* Registers an `active_sessions` row (defaulting `pid` to `os.getpid()` and `host` to `socket.gethostname()` when caller omits them), supersedes any prior row from the same (source, cwd, pid) client process, and surfaces other live sessions in the same project as a load-bearing context block. Liveness is maintained by an external heartbeat agent — no TTL. See [Session Registry](#session-registry-v0130).

*(v0.14.0)* Scans RECENT HISTORY and KNOWN ISSUES memories for `action_items`, populates a per-source pending list, and adds an `ACTION ITEMS PENDING` section with `pending_action_items` in the response. Write-set tools (`remember`, `capture_context`, `supersede`) are BLOCKED until every item is acknowledged via `acknowledge_action_item`. See [Action-Item Compliance Gate](#action-item-compliance-gate-v0140).

**Enforcement:** Server blocks `remember()` and `capture_context()` until `boot_session` has been called. Claude Code's PreToolUse hook blocks ALL non-brain tools until boot appears in the transcript.

---

## Session Registry (v0.13.0+)

Live MCP-client sessions are tracked in an `active_sessions` table so a booting session can see what sibling sessions are currently doing. Closes the parallel-session blind spot: before v0.13.0, a Claude session in one terminal had zero visibility into a Claude session in another terminal working on the same project, even though both were connected to the same brain.

Three tools manage session state; `boot_session` registers implicitly. Liveness (v0.14.0+) is detected by the **heartbeat agent** (`scripts/heartbeat_agent.py`) — a separate process that periodically pid-probes each active row via `psutil.pid_exists` and marks dead rows `status='ended'`. server.py's atexit + SIGTERM/SIGINT handlers also call `db_end_session` on clean shutdown. No TTL. No self-ping. Probe interval: `OPEN_BRAIN_HEARTBEAT_INTERVAL` (default 60s).

### `update_active_task`

Update the caller's `current_task` and bump heartbeat. Call when the user pivots or a task completes.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source` | string | **Yes** | Caller's source identifier (same as boot). |
| `task` | string | **Yes** | New task description. |
| `session_id` | int | No | Defaults to the session id cached at boot. Pass explicitly only when booting happened in a different process. |

### `list_active_sessions`

Read-only snapshot of live sessions. Sweeps dead rows first.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source` | string | **Yes** | Caller's source identifier. |
| `project` | string | No | Filter to one project (empty string = all projects across the brain). |
| `exclude_self` | bool | No | Default `true` — omits the caller's own session. |

**Cross-project visibility:** pass `project=""` (empty string, not the caller's default project) to see sessions in every project. `boot_session`'s `OTHER ACTIVE SESSIONS` block is always project-scoped by design — to see cross-project siblings, call `list_active_sessions` explicitly.

**Liveness model (v0.14.0+):** a session is alive iff its owning server.py process is running. Liveness is detected externally by the heartbeat agent (`scripts/heartbeat_agent.py`) which pid-probes each row via `psutil.pid_exists`. Rows from dead processes are marked `status='ended'` on the next probe cycle (default 60s, `OPEN_BRAIN_HEARTBEAT_INTERVAL`). Clean shutdowns (MCP client disconnect, SIGTERM, SIGINT) also call `db_end_session` via server.py's atexit hooks. Only SIGKILL / power-loss leaves a stale row, and the agent cleans those up within one cycle.

No TTL. No self-ping. Sessions doing long non-brain work stay visible as long as their server.py process is alive.

### `end_session`

Clean-shutdown path. Marks the session `ended` and clears the cached id.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source` | string | **Yes** | Caller's source identifier. |
| `session_id` | int | No | Defaults to the cached id. |

Optional — server.py's atexit hook and the heartbeat agent already handle clean shutdowns and crashed processes respectively — but calling this explicitly tightens the window between shutdown and the next sibling query.

---

## Action-Item Compliance Gate (v0.14.0+)

Memories carry `action_items` in their metadata. At `boot_session`, the server scans memories surfaced in RECENT HISTORY and KNOWN ISSUES for action_items, populates a per-source pending list (deduped, capped at `OPEN_BRAIN_ACTION_ITEM_GATE_MAX`, default 10), and surfaces them as an `ACTION ITEMS PENDING` section. Write-set tools (`remember`, `capture_context`, `supersede`) are **BLOCKED** with `blocked_by: action_items_pending` until every item is acknowledged. Reads stay open so the session can investigate before deciding.

Pinned guardrails are skipped — they're rules, not pending tasks.

Pending state is ephemeral (in-memory, per-source). Re-ack is required on reboot, matching session-registry TTL semantics. A server restart drops the state; next `boot_session` re-surfaces unresolved items.

Acks are logged to `logs/action_item_acks.jsonl` (append-only JSONL).

### `acknowledge_action_item`

Engage with one pending action_item. Call once per item; writes unlock when the pending list is empty.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source` | string | **Yes** | Same source used at boot. |
| `memory_id` | int | **Yes** | The id of the memory that carried this action_item. |
| `text` | string | **Yes** | Exact text (from `pending_action_items` in the boot response). |
| `decision` | string | **Yes** | One of `will_execute`, `already_done`, `not_relevant`. |
| `reason` | string | Conditional | Required for `already_done` and `not_relevant` — explain why. |

Returns `{success, decision, text, removed, remaining}`. Idempotent — acking a non-pending item returns success with `removed: 0`.

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

---
---

## v2 Tools

All v2 tools use the `mcp__open-brain-v2__` namespace. v2 enforces typed memories — every write specifies a `kind` (`rule`, `fact`, `incident`, `task`). Bodies are immutable for rules (supersede-only). See [v2 Architecture](architecture/v2-architecture.md) for the full design.

---

### Boot + Session

#### `boot_session_v2`

Initialize v2 for a new session. Returns headline-only payload (2K token cap).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `project` | string | No | Project to load context for |
| `task` | string | No | What this session is about to work on |
| `source` | string | No | Agent name (`claude`, `windsurf`, `cursor`) |
| `handoff` | string | No | Explicit handoff text (auto-populated from prior session if empty) |
| `cwd` | string | No | Caller's working directory |
| `pid` | int | No | Caller's process ID (defaults to server's PID) |
| `host` | string | No | Caller's hostname |

**Returns:** BLOCKERs (5 max), PATTERNs (5 max, task-relevance ranked), active tasks, working context, pending action items, other active sessions, handoff, token estimate.

#### `end_session_v2`

End a session, optionally writing a handoff note for the next session.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `handoff` | string | No | Handoff note (<=2000 chars) |
| `session_id` | int | No | Defaults to this server's session |
| `source` | string | No | For audit |

#### `update_active_task_v2`

Update task description + bump heartbeat.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task` | string | Yes | New task description |
| `session_id` | int | No | Defaults to this server's session |

#### `list_active_sessions_v2`

List live sessions, optionally filtered by project.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `project` | string | No | Filter by project |
| `exclude_self` | bool | No | Hide this session (default true) |

#### `write_handoff_v2`

Write a handoff note without ending the session (mid-session checkpoint).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content` | string | Yes | Handoff text (<=2000 chars) |
| `source` | string | No | For audit |
| `project` | string | No | Project scope |

---

### Write Tools

All write tools are blocked if pending action items exist. Call `acknowledge_action_item_v2` first.

#### `remember_rule_v2`

Write a new RULE. Rejects duplicates (>0.75 cosine) — caller must route to `supersede_rule_v2`.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `headline` | string | Yes | <=15 words |
| `body` | string | Yes | <=400 words (hard ceiling) |
| `severity` | string | No | `BLOCKER` or `PATTERN` (default `PATTERN`) |
| `project` | string | No | Project scope |
| `source` | string | No | Agent identifier |
| `skill_trigger` | dict | No | Tag as a skill: `{name, keywords, projects, always_on}` |

#### `remember_fact_v2`

Write a new FACT. Subject to Ebbinghaus decay.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `headline` | string | Yes | <=15 words |
| `body` | string | Yes | <=400 words |
| `project` | string | No | Project scope |
| `tags` | string[] | No | Searchable tags |
| `ttl` | string | No | ISO 8601 expiry timestamp (hard TTL) |
| `source` | string | No | Agent identifier |

#### `remember_incident_v2`

Write a new INCIDENT (episodic record). Duplicate check skipped — same event can recur.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `headline` | string | Yes | <=15 words |
| `body` | string | Yes | <=400 words |
| `project` | string | No | Project scope |
| `root_cause` | string | No | What caused it |
| `resolution` | string | No | How it was fixed |
| `linked_rule_ids` | int[] | No | Rules related to this incident |
| `source` | string | No | Agent identifier |

#### `remember_task_v2`

Write a cross-session TASK obligation.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content` | string | Yes | Task description (headline auto-generated from first line) |
| `project` | string | No | Project scope |
| `priority` | string | No | `low`, `medium` (default), or `high` |
| `due_condition` | string | No | When this task should be done |
| `source` | string | No | Agent identifier |

#### `capture_context_v2`

Auto-decompose raw text into typed atomic memories. Classification is keyword-based heuristic (no LLM).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `context` | string | Yes | Raw text to capture |
| `source` | string | No | Agent identifier |
| `project` | string | No | Project scope |

#### `supersede_rule_v2`

Revise a rule. Old rule goes DEPRECATED, new rule links to it. This is the ONLY way to modify a rule.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `old_id` | int | Yes | Rule to supersede |
| `new_headline` | string | Yes | <=15 words |
| `new_body` | string | Yes | <=400 words |
| `reason` | string | Yes | Why the old rule is wrong |
| `source` | string | No | Agent identifier |
| `severity` | string | No | Override severity (defaults to old rule's severity) |

#### `update_task_status_v2`

Transition a task's lifecycle state.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | int | Yes | Task to update |
| `status` | string | Yes | `open`, `blocked`, `done`, or `stale` |
| `source` | string | No | Agent identifier |

---

### Read Tools

#### `recall_v2`

Fetch full body of a memory by kind + ID. Forgotten memories still return (with a banner).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `kind` | string | Yes | `rule`, `fact`, `incident`, or `task` |
| `memory_id` | int | Yes | ID within that kind |

#### `search_v2`

Semantic search over headlines. Bodies NOT returned — call `recall_v2` for full content.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | Yes | Natural language query |
| `kind` | string | No | Filter by memory kind |
| `project` | string | No | Filter by project |
| `limit` | int | No | Max results (default 10) |

Skill keyword matches are merged into results with `via_skill_trigger` annotation.

#### `list_recent_v2`

Browse recent memories (headline-only), newest first.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `limit` | int | No | Max rows (default 20, hard cap 200) |
| `days` | int | No | Only last N days (0 = no limit) |
| `kind` | string | No | Filter by kind |
| `project` | string | No | Filter by project |
| `include_forgotten` | bool | No | Include forgotten rows (default false) |

#### `stats_v2`

Corpus statistics: per-kind totals, severity breakdown, task status, pending action items, active sessions.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `project` | string | No | Scope to project (empty = global) |

---

### Curation Tools

#### `annotate_v2`

Attach or clear a persistent note on a memory.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `kind` | string | Yes | `rule`, `fact`, `incident`, or `task` |
| `memory_id` | int | Yes | ID within that kind |
| `note` | string | No | Text to set (omit to read current) |
| `clear` | bool | No | True to remove annotation |

#### `rate_v2`

Upvote or downvote a memory.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `kind` | string | Yes | Memory kind |
| `memory_id` | int | Yes | ID within that kind |
| `direction` | string | Yes | `up` or `down` |

#### `pin_v2` / `unpin_v2`

Pin/unpin a memory. Only project-scoped memories can be pinned.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `kind` | string | Yes | Memory kind |
| `memory_id` | int | Yes | ID within that kind |

---

### Cleanup Tools

#### `forget_v2`

Soft-delete. Deactivates the memory_index row. Body preserved for audit.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `kind` | string | Yes | Memory kind |
| `memory_id` | int | Yes | ID within that kind |
| `reason` | string | No | Why it's being forgotten |
| `source` | string | No | Agent identifier |

#### `forget_many_v2`

Batch soft-delete. Partial success allowed.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `kinds` | string[] | Yes | Parallel list of kinds |
| `memory_ids` | int[] | Yes | Parallel list of IDs |
| `reason` | string | No | Applied to all |
| `source` | string | No | Agent identifier |

#### `unsupersede_v2`

Reverse a rule supersession. Both rules end up active.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `old_id` | int | Yes | Rule whose supersession to reverse |
| `source` | string | Yes | Agent identifier |

#### `prune_v2`

Hard-delete stale memories with safeguards. Rules are never pruned.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | int | No | Age threshold (minimum 30, default 90) |
| `min_access` | int | No | Facts with access_count > this are kept |
| `dry_run` | bool | No | Preview only (default true) |

---

### Maintenance Tools

#### `run_maintenance_v2`

Run all maintenance jobs: fact decay + incident archive.

#### `run_maintenance_if_due_v2`

Run maintenance only if the last run was more than `hours` ago. Safe to fire on every boot.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `hours` | float | No | Rate-limit window (default 24) |

#### `decay_facts_v2`

Run only the Ebbinghaus fact decay job.

#### `archive_incidents_v2`

Run only the incident archive job (90-day default).

---

### Action Items

#### `acknowledge_action_item_v2`

Acknowledge a pending action item. Writes are blocked until all are acknowledged.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `item_id` | int | Yes | Action item ID |
| `decision` | string | Yes | `will_execute`, `already_done`, or `not_relevant` |
| `source` | string | No | Agent identifier |
| `reason` | string | Conditional | Required for `already_done` and `not_relevant` |

#### `create_action_item_v2`

Manually create an action item linked to a memory.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_kind` | string | Yes | Kind of the source memory |
| `source_id` | int | Yes | ID of the source memory |
| `text` | string | Yes | Action item text |
| `project` | string | No | Project scope |

---

### Skills

#### `load_skill_v2`

Load a specific skill by its trigger name.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Skill trigger name |
| `source` | string | No | Agent identifier |
| `project` | string | No | Project scope (required for project-scoped skills) |

---

### Working Memory

#### `scratch_set_v2` / `scratch_get_v2` / `scratch_list_v2`

Ephemeral key-value store. Cleared on server restart.

---

### Observability

#### `health_v2`

DB connectivity, Ollama reachability (socket-level, 5s timeout), table row counts, server uptime, tool list.

#### `metrics_v2`

Per-tool call counts, error rates, avg/p99 latency for the current server process lifetime.

#### `recent_errors_v2`

Last N errors from the in-memory ring buffer.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `n` | int | No | Number of errors to return (default 20) |

---

### Cognitive Architecture

#### `brain_checkpoint_v2`

Check the brain before a risky action. Surfaces pinned BLOCKER rules + task-relevant PATTERN rules.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | Yes | What you're about to do |
| `source` | string | Yes | Agent identifier |
| `context` | string | No | Additional context |
| `project` | string | No | Project scope |

5-minute cooldown per action per source.

#### `brain_startup_reminder_v2`

Returns the v2 mandatory boot-first system message. Display at session start.
