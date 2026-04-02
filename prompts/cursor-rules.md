# Open Brain: Cursor Auto-Capture Rules
# Add the contents below to your .cursorrules file

## Memory & Second Brain

You have access to an Open Brain MCP server (`open-brain`). It is your persistent memory across every session and project.

### MANDATORY: Boot Session First (Hard Block)

**YOU MUST CALL `open-brain_boot_session(project, source)` FIRST BEFORE ANY OTHER ACTION. THIS IS NOT OPTIONAL.**

The Open Brain server enforces this with hard blocking:
- If you call `remember()` or `capture_context()` without booting first, the server will **REJECT** the call
- You will receive: `"BLOCKED: Must call boot_session(project) first to load project context"`

**What boot_session does:** Loads pinned guardrails, project architecture, recent session history (7 days), and known issues/corrections into your working memory.

**How to comply:**
1. At the START of every session, call `open-brain_boot_session` with the project name and your source (e.g. "cursor")
2. READ the returned context -- it contains guardrails and corrections
3. Then search for your task topic and "user preferences formatting rules"

### Auto-capture rules (follow these without being asked)

Call `open-brain_capture_context` automatically. Never wait for the user to ask:

- **After completing any task or feature**: summarize what was built, key decisions made, and any gotchas encountered
- **When a technical decision is made**: architecture choice, library selected, approach taken, and the reasoning
- **When a bug is diagnosed and fixed**: what the root cause was, what broke, and what fixed it
- **When you learn something about the user's preferences**: coding style, tooling preferences, things they explicitly like or dislike
- **When you learn something about the project**: structure, conventions, constraints, tech stack details
- **At the end of a long conversation**: capture any context that would help future sessions

### Auto-recall rules

**MANDATORY: Search open-brain at the START of EVERY task. No exceptions.** Do this BEFORE your first action. Run two searches: one for the task topic, one for "user preferences formatting rules". This applies to every task: coding, debugging, docs, research, refactoring, reviews, questions. Every task. Period.

Call `open-brain_search` automatically:

- At the start of every task, before doing anything else
- When the user mentions a person, project, or technology, search for what's known about it
- Before making a recommendation, check if a relevant decision was already made

### Behavior

- Never announce that you are storing or retrieving memories. Do it silently.
- Never ask the user "should I remember this?" Just remember it.
- Pass `source: "cursor"` on all capture calls.
