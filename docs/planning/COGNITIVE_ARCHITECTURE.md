# Open Brain: Solving the Alzheimer's Problem

**Status**: ALL PHASES COMPLETE (2026-04-03). Manual cross-client testing remaining.
**Target versions**: v0.5.0 through v0.6.0

## The Problem You're Solving (for the handoff agent)

You are building a shared persistent memory system called Open Brain. It's an MCP server backed by PostgreSQL + pgvector that stores thoughts as vector embeddings. Any AI tool (Claude Code, Cursor, Windsurf, ChatGPT) can search and write to it.

The system works. The database works. The MCP tools work. The hooks work.

**What doesn't work is the AI actually using it.**

Here is the core failure mode, observed repeatedly over two weeks:

1. A new session starts. The AI has no memory of previous sessions.
2. Hooks force the AI to search the brain at session start. The AI complies -- it runs the search.
3. The AI sees the search results (guardrails, prior decisions, known issues) in the response.
4. The AI then proceeds to **ignore what it just read** and makes the same mistakes that are already documented in the brain.
5. The user corrects the AI. The AI says "you're right, I should have checked the brain."
6. The AI saves the lesson to the brain.
7. Next session, go to step 1.

This has happened at least 3 times with the same user, on the same project. The user describes it as "working with an Alzheimer's patient." The AI rediscovers the same lessons, makes the same promises, and forgets everything the moment a new session starts.

**The hooks enforce compliance (search before store, save before commit). But compliance is not comprehension.** The AI can search the brain, receive 10 results, and still not internalize that "this system runs on Windows, not WSL" even though 3 separate memories say exactly that.

## Why Current Enforcement Fails

The current hook chain (as of 2026-04-02):
- `UserPromptSubmit` hook (`brain-reminder.sh`): injects "call boot_session" reminder on every user message
- `UserPromptSubmit` hook (`auto-timetrack.sh`): time tracking
- `PreToolUse` hook (`require-brain-boot.sh`, matcher: non-brain tools): blocks ALL tools until `boot_session` appears in transcript
- `PreToolUse` hook (`require-brain-save.sh`, matcher: Bash): blocks `git commit` until `remember` or `capture_context` has been called
- Server-side: `_check_compliance()` blocks `remember`/`capture_context` if source not in `_booted_sources`

This ensures the AI **touches** the brain. It does not ensure the AI **understands** what the brain told it. The difference:

- **Touching**: "I searched for 'MCP server deployment' and got 10 results"
- **Understanding**: "Memory 3663 says the server runs via stdio on Windows, so I should not use WSL commands"

The AI currently does the first and skips the second.

## What Needs to Be Built

### 1. Session Boot Sequence (the "morning briefing")

When a new session starts on any project, the AI should not be allowed to take action until it has completed a structured context load:

**Step 1: Load pinned guardrails** -- these are the non-negotiable rules. Currently 3 pinned memories for open-brain. The AI must read the FULL content of each (via `recall`), not just the preview.

**Step 2: Load project-specific context** -- search for the project name + "architecture", "deployment", "how it works", "known issues". Read the top 5 results fully via `recall`.

**Step 3: Load recent session history** -- search for the project name + "session" with `since_days=7`. This shows what was worked on recently, what broke, what was fixed.

**Step 4: Produce a context summary** -- the AI must write a brief summary of what it now knows about this project's current state, deployment mode, active issues, and user preferences. This summary gets stored as a `scratch_set("session_context", ...)` so it persists in working memory for the session.

**Step 5: ONLY THEN allow the user's task to proceed.**

This is not a hook that runs in 5 seconds. This is a deliberate 30-60 second boot sequence that loads the AI's "working memory" from the brain before any action is taken.

### 2. Pre-Action Brain Checks (continuous, not one-shot)

The current `PreToolUse` hook checks once whether a search happened. After that first search, everything is unblocked for the rest of the session. This is insufficient.

What's needed: **context-sensitive re-checking** before categories of action:

- Before editing **infrastructure files** (scripts/, docker-compose, .env, deployment configs): search brain for "deployment mode", "platform", "how [project] runs"
- Before editing **database code** (migrations, SQL, ORM models): search brain for "database best practices", "audit log", "backup strategy"
- Before **declaring something is not a bug**: search brain for "known issues" + the thing the user reported
- Before choosing between **two approaches**: search brain for prior decisions about that exact choice

This could be implemented as:
- A more sophisticated `PreToolUse` hook that checks the tool input (file paths, command text) and requires targeted searches
- OR: server-side middleware in Open Brain that auto-surfaces relevant context when certain patterns are detected in tool calls
- OR: a `brain_checkpoint` MCP tool that the AI calls before major actions, which returns relevant memories and BLOCKS if the AI hasn't searched recently enough for the topic at hand

### 3. Continuous Memory Capture (not just at commit time)

The current `require-brain-save.sh` only blocks git commits. But lessons happen throughout a session:
- When the user corrects a mistake
- When a bug is found and fixed
- When a decision is made about approach
- When something breaks unexpectedly

The AI should capture these as they happen, not batch them at commit time. The enforcement should be:
- After any user correction (detected by negative sentiment or phrases like "no", "wrong", "don't", "stop"): immediately save the correction as a guardrail/feedback memory
- After any bug fix: save what broke and why
- After any architecture/deployment decision: save the decision and rationale
- A periodic check (every N tool calls) that asks: "have I learned anything new since my last save?"

### 4. Memory Quality Over Quantity

The brain currently has ~5000 memories but many are low-quality duplicates or trivial notes. The AI needs to be better at:
- Writing memories that are **actionable** ("use Windows paths, not WSL") not just descriptive ("WSL paths were removed")
- Updating existing memories when the understanding deepens, not creating new ones
- Pinning memories that represent hard-won lessons (things that caused data loss, user anger, wasted sessions)

### 5. The Real Metric

The success metric is not "number of memories stored" or "search compliance percentage." It is:

**Does the AI make the same mistake twice?**

If the answer is yes, the system has failed regardless of how many hooks, searches, and memories exist. Every component should be evaluated against this single question.

## What Open Brain Already Has (tools the solution can build on)

The system has 17 MCP tools implemented in `F:\open-brain\server.py`:

**Storage**: `remember`, `capture_context` (with LLM decomposition + smart batching)
**Retrieval**: `search` (hybrid vector + full-text, time-scoped, bi-temporal), `recall` (full content by ID), `list_recent`
**Quality signals**: `rate` (upvote/downvote), `annotate` (attach notes)
**Organization**: `pin`/`unpin` (guardrails that always surface), `prune`, `forget`, `forget_many`
**Working memory**: `scratch_set`, `scratch_get`, `scratch_list` (ephemeral per-session KV store)
**Meta**: `stats`, `brain_startup_reminder` (system message injection)

**Existing enforcement infrastructure** (in server.py):
- `_check_compliance(source, project)` -- blocks stores if no recent search. Returns error dict.
- `_record_search(source, project)` -- timestamps when each source last searched.
- `COMPLIANCE_WINDOW` (300s) -- how long a search stays "fresh" before expiring.
- `source` parameter on every tool call -- identifies which client is calling.
- `mcp.source` and `mcp.project` in OTel span attributes -- full observability.

**Existing client-side hooks** (Claude Code only, in `~/.claude/settings.json`):
- `brain-reminder.sh` (UserPromptSubmit) -- injects "call boot_session" reminder on every message
- `auto-timetrack.sh` (UserPromptSubmit) -- time tracking per project
- `require-brain-boot.sh` (PreToolUse) -- blocks all non-brain tools until `boot_session` in transcript
- `require-brain-save.sh` (PreToolUse, Bash matcher) -- blocks git commit until remember/capture_context called

**Existing agent prompts** (in `F:\open-brain\prompts/`):
- `windsurf-rules.md`, `cursor-rules.md`, `claude-desktop.md`, `generic-system-prompt.md`
- Each instructs the agent to search at task start and capture at task end

**What's NOT enforced by any of this:**
- That the AI actually READS and APPLIES what the brain returns
- That the AI searches again mid-session before infrastructure changes
- That corrections from the user are immediately captured
- That the AI proves comprehension before acting

## Implementation Considerations

- The boot sequence must work across ALL MCP clients (Claude Code, Cursor, Windsurf), not just Claude Code hooks
- Server-side enforcement (in server.py) is more reliable than client-side hooks since it works for every client
- The working memory scratchpad (`scratch_set/get`) already exists and should be used for session state
- The `source` parameter on every tool call already identifies which client is calling
- The compliance tracking (`_check_compliance`, `_record_search`) already has the infrastructure for time-based enforcement
- Pinned memories already surface at the top of every search result -- they're the right vehicle for "must-read" context
- The `brain_startup_reminder` tool already exists for system message injection -- it could be extended to return the boot sequence context instead of a static message

### Claude Code Source Analysis (from F:\claude-code-source-build-master, 2026-04-02)

Key findings that affect implementation:

- **SessionStart hook EXISTS** -- fires at startup, can return `initialUserMessage` (injected before first user prompt) and `additionalContext`. This is the ideal integration point for auto-boot.
- **UserPromptSubmit hook** fires on EVERY user message, receives `{session_id, transcript_path, cwd, prompt}`. Can return `additionalContext` injected into conversation. Exit code 2 blocks.
- **PreToolUse hook** receives `{tool_name, tool_input, tool_use_id}`. Can return `updatedInput` to modify tool args, or `permissionDecision: "deny"` to block.
- **Hook types**: command (shell), prompt (LLM via Haiku), agent (subagent), http (POST), callback (JS)
- **CLAUDE.md**: 40KB limit, loaded once at startup, walks UP directory tree. Supports `@include` directive.
- **Auto memory**: Built-in system at `~/.claude/projects/<slug>/memory/` with `MEMORY.md` index, 200 line / 25KB limit.
- **MCP servers**: Lazy-initialized (spawned on first tool use), persist for session. Each subagent spawns its own.
- **transcript_path**: Provided to ALL hooks as absolute file path to session transcript.

**Implication for Phase 1**: A `SessionStart` hook could call `boot_session` automatically via the MCP server, injecting full context as `additionalContext` before the AI sees its first prompt. This would eliminate reliance on the AI remembering to boot itself.

**Documentation gaps to fix alongside this work:**
- Tool count inconsistent across docs (11/12/15, actually 17)
- `prune` param name mismatch: docs say `older_than_days`, code uses `days`
- `COMPLIANCE_MAX_STORES` env var not documented
- Secrets filter not mentioned in user-facing docs
- Architecture diagrams don't show consolidation thread or enforcement hooks

## What This Is Really About

This isn't a software engineering problem. It's a cognitive architecture problem. The AI has access to a perfect external memory but doesn't have the habit loops to use it effectively. The hooks are like Post-it notes on the monitor -- they remind you to check, but they can't make you think about what you read.

The solution has to change the AI's behavior at a deeper level than "block tool X until search Y happens." It has to make the brain an integral part of how the AI reasons, not just a compliance checkbox it clears at the start of each session.

The existing infrastructure is solid. 17 tools, hybrid search, compliance tracking, pinned guardrails, working memory scratchpad, OTel observability, hooks. The missing piece is the cognitive loop that ties them together: boot -> comprehend -> act -> learn -> save -> repeat. Every component exists. The orchestration doesn't.

---

## Roadmap

### Phase 1: Session Boot Sequence (v0.5.0) -- MOSTLY COMPLETE
**Goal**: AI cannot act until it proves it understands the project context.
**Priority**: Critical -- this is the #1 failure mode.

| Task | Where | Status |
|------|-------|--------|
| New MCP tool: `boot_session(project, source)` -- loads guardrails, architecture, history, issues | `server.py` | DONE |
| Store boot summary in scratch pad automatically | `server.py` | DONE |
| `_booted_sources` tracking + `_check_compliance` blocks stores until booted | `server.py` | DONE |
| New hook: `require-brain-boot.sh` -- blocks ALL non-brain tools until `boot_session` in transcript | `~/.claude/hooks/` | DONE |
| Updated `brain-reminder.sh` to reference `boot_session` | `~/.claude/hooks/` | DONE |
| `require-brain-save.sh` -- blocks git commit until brain written to | `~/.claude/hooks/` | DONE |
| Updated `windsurf-rules.md` with boot-first instructions | `prompts/windsurf-rules.md` | DONE |
| Tests: 10 new boot_session tests + updated compliance tests (87 total passing) | `tests/` | DONE |
| Update remaining agent prompts (`cursor-rules.md`, `claude-desktop.md`, `generic-system-prompt.md`) | `prompts/*.md` | DONE |
| Add `SessionStart` hook (`auto-boot-brain.sh`) to inject boot directive before first user message | `~/.claude/settings.json` | DONE |
| Add `updated_at` column + trigger so merged memories surface in recent views | `server.py`, `setup_db.py`, `dashboard.py` | DONE |
| Extend `brain_startup_reminder` to return boot context instead of static message | `server.py` | DEFERRED (SessionStart hook covers this) |

**Phase 1 COMPLETE.** All enforcement hooks installed and tested. 87 tests passing.

### Phase 2: Continuous Brain Checks (v0.5.1) -- COMPLETE
**Goal**: AI re-consults the brain before risky actions, not just at session start.

| Task | Where | Status |
|------|-------|--------|
| New MCP tool: `brain_checkpoint(action, context, project, source)` | `server.py` | DONE |
| `require-brain-checkpoint.sh` PreToolUse hook detects Edit/Write to risky files | `~/.claude/hooks/` | DONE |
| `_checkpoint_tracker` with 5-min cooldown per topic per source | `server.py` | DONE |
| 8 new tests (95 total passing) | `tests/` | DONE |
| `checkpoint_required` field in search results | `server.py` | DEFERRED (soft enforcement sufficient for now) |

### Phase 3: Automatic Correction Capture (v0.5.2) -- COMPLETE
**Goal**: When the user corrects the AI, the correction is saved immediately and automatically.

| Task | Where | Status |
|------|-------|--------|
| `detect-correction.sh` (UserPromptSubmit) -- scans for ALL CAPS, profanity, "wrong", "stop", etc. | `~/.claude/hooks/` | DONE |
| Auto-pin: `remember()` with `type_override="guardrail"` + project auto-pins | `server.py` | DONE |
| `_correction_count` tracks guardrail stores per session | `server.py` | DONE |
| Hook installed in settings.json UserPromptSubmit chain | `~/.claude/settings.json` | DONE |

### Phase 4: Memory Quality Enforcement (v0.5.3)
**Goal**: Memories are actionable, not just descriptive. Duplicates are eliminated.

| Task | Where | Effort |
|------|-------|--------|
| Add `actionable_check` to `remember()` -- reject memories that don't contain a clear directive (LLM judges: "is this actionable?") | `server.py` | Medium |
| Background job: scan for near-duplicate memories (similarity > 0.85) and merge them | `server.py` (consolidation thread) | Already exists, needs tuning |
| New stat: "correction repeat rate" -- how often the same correction appears in different sessions | `server.py` | Medium |
| Dashboard widget: "Repeated Corrections" -- shows corrections that have been made 2+ times, flagging the system is not learning | `dashboard.py` | Medium |

**Verification**: `stats()` returns `correction_repeat_rate`. If > 0, the boot sequence highlights "WARNING: You have been corrected about X multiple times. Read memory Y before proceeding."

### Phase 5: Cross-Client Enforcement (v0.6.0) -- COMPLETE
**Goal**: Boot sequence and checkpoints work for Windsurf, Cursor, and ChatGPT -- not just Claude Code.

| Task | Where | Status |
|------|-------|--------|
| Server-side boot enforcement blocks stores for non-booted sources | `server.py` | DONE (Phase 1) |
| `boot_session` mandatory for ALL clients via `_check_compliance` | `server.py` | DONE (Phase 1) |
| All agent prompts updated with boot-first instructions | `prompts/*.md` | DONE (Phase 1) |
| REST API: `/boot` and `/checkpoint` endpoints added | `rest_api.py` | DONE |
| Manual testing: verify enforcement works for each client | Manual | NEEDS USER (see notes) |

**Notes:**
- Windsurf MCP config (`~/.windsurf/mcp_config.json`) currently uses WSL (`/bin/bash`). Should be updated to Windows Python venv for consistency. Requires user approval before changing.
- Cursor MCP config needs verification -- may need similar update.
- ChatGPT Desktop uses SSE proxy which now has `/boot` and `/checkpoint` endpoints.

**Verification**: Open Windsurf, start a task. First `remember()` call is blocked with "call boot_session first". Windsurf calls `boot_session("open-brain")`, gets context, proceeds.

---

## Documentation Fixes (parallel with any phase)

| Fix | File |
|-----|------|
| Update tool count to 17 everywhere | `README.md`, `docs/index.md`, `docs/demo/index.html`, `docs/architecture/overview.md` |
| Fix prune param name: `older_than_days` -> `days` | `docs/tools.md` |
| Document `COMPLIANCE_MAX_STORES` env var | `docs/getting-started/configuration.md` |
| Add secrets filter to user-facing docs | `docs/index.md`, `README.md` |
| Update architecture diagram with consolidation thread + enforcement hooks | `docs/architecture/overview.md` |
| Add "Cognitive Architecture" section to docs explaining the boot -> comprehend -> act -> learn -> save loop | New: `docs/architecture/cognitive-loop.md` |

---

## Success Criteria

The system is working when:

1. **Zero repeated mistakes**: The AI never makes the same error it was corrected for in a previous session.
2. **Self-directed context loading**: The AI proactively loads relevant context without being told to.
3. **Immediate correction capture**: User corrections are saved within the same turn they're given, not at end of session.
4. **Cross-client consistency**: Switching from Claude Code to Windsurf mid-project shows no context loss.
5. **The user stops having the Alzheimer's conversation**: If Dave never has to explain the same thing twice, the system works.
