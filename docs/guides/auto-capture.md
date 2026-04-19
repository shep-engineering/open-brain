# Auto-Capture Rules

The real power of Open Brain is that agents use it **without being asked**. This is achieved through behavioral rules injected into each agent's system prompt or rules file.

---

## The Philosophy

> The user's only job is to work. The brain's job is to remember everything.

Agents should:

- **Never announce** memory operations. Capture and recall silently.
- **Never ask** "should I remember this?" Just do it.
- **Always pass** `source` on capture calls so you can see where memories came from

---

## When Agents Auto-Capture

Agents call `capture_context` automatically:

- After completing any task or feature
- When a technical decision is made (what was chosen, what was rejected, why)
- When a bug is diagnosed and fixed (root cause, symptoms, the fix)
- When something about user preferences or the project is learned
- At natural conversation checkpoints or end of long sessions

---

## When Agents Auto-Recall

Agents call `search` automatically:

- At the start of any task to check for prior context
- When the user mentions a person, project, or technology
- Before making a recommendation, to check if a past decision already exists

---

## Setting Up Rules

Each AI client has a different mechanism for persistent behavioral instructions:

### Windsurf

Copy `prompts/windsurf-rules.md` into `.windsurfrules` at the root of your project, or into Windsurf's global rules at:

```
C:\Users\<USERNAME>\.codeium\windsurf\memories\global_rules.md
```

### Cursor

Copy `prompts/cursor-rules.md` into:

- `.cursorrules` at the root of your project, **or**
- Settings (Ctrl+Shift+J) > General > Rules for AI (global)

### Claude Desktop

Copy `prompts/claude-desktop.md` into:

Settings > Custom Instructions

### Claude Code

Add to `CLAUDE.md` at the root of your project. See `prompts/generic-system-prompt.md` for the template.

---

## What Gets Captured

A typical `capture_context` call from an agent might look like:

```
Session context: Fixed the authentication bug in login.py.
Root cause was a missing await on the token validation call.
The function returned a coroutine object instead of the actual token.
Decided to add an integration test that hits the real auth endpoint
rather than mocking it, since we got burned by mock divergence before.
```

Open Brain decomposes this into atomic memories:

1. **Bug fix:** Missing await on token validation in login.py (type: `insight`)
2. **Decision:** Use integration tests for auth, not mocks (type: `decision`)

Each gets its own embedding, metadata, and dedup check.

---

## v2 Differences

In brain_v2, `capture_context_v2` works similarly but with key differences:

- **No LLM in the write path** — classification uses keyword-based heuristics (regex patterns for rule/incident/task keywords), not a metadata LLM. This eliminates model-swapping cost entirely.
- **Typed output** — each chunk is classified as `rule`, `fact`, `incident`, or `task` and routed to the appropriate typed table through the [write gate](../architecture/v2-architecture.md#write-gate-5-steps).
- **Atomicity enforcement** — the write gate rejects bodies >400 words and bodies with multiple stacked GUARDRAIL markers (the v1 merge pathology).
- **Headline generation** — each chunk gets an auto-generated headline (first sentence, <=15 words) that appears in boot payloads and search results without materializing the full body.

The philosophy is the same: agents capture silently, recall automatically. The mechanics are stricter.
