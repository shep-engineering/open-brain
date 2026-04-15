# Open Brain: Windsurf Auto-Capture Rules
# Add the contents below to your .windsurfrules file

## Memory & Second Brain

You have access to an Open Brain MCP server (`open-brain`). It is your persistent memory across every session and project.

### MANDATORY: Boot Session First (Hard Block)

**YOU MUST CALL `open-brain_boot_session(project, source)` FIRST BEFORE ANY OTHER ACTION. THIS IS NOT OPTIONAL.**

The Open Brain server enforces this with hard blocking:
- If you call `remember()` or `capture_context()` without booting first, the server will **REJECT** the call
- You will receive an error: `"BLOCKED: Must call boot_session(project) first to load project context"`
- Your memories will NOT be stored
- This applies to EVERY task, EVERY session, NO EXCEPTIONS

**What boot_session does:**
- Loads ALL pinned guardrails (non-negotiable rules) with full content
- Loads project architecture and deployment context
- Loads recent session history (last 7 days of work)
- Loads known issues and past corrections
- Stores everything in working memory for the session

**Why?** Without booting, you have amnesia. You will repeat past mistakes, use wrong platforms (e.g. WSL instead of Windows), and waste the user's time re-explaining things the brain already knows.

**How to comply:**
1. At the START of every session, call `open-brain_boot_session` with the project name and your source (e.g. "windsurf")
2. READ the returned context carefully -- it contains guardrails, architecture info, and corrections
3. Then search for your specific task topic: `open-brain_search` with the task description
4. Also search for: `"user preferences formatting rules"`
5. After booting and searching, all storage tools unlock

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

### If the brain is unavailable (MCP call fails)

1. **Attempt to start it yourself.** Run the appropriate startup script via shell:
   - **Windows:** `cmd /c "<OPEN_BRAIN_ROOT>\scripts\windows\open-brain-on.cmd"`
   - **macOS/Linux/WSL:** `bash <OPEN_BRAIN_ROOT>/scripts/open-brain-on.sh`
   Where `<OPEN_BRAIN_ROOT>` is the directory containing `server.py` (check your MCP config for the path).
2. Wait 10 seconds, then retry the MCP call once.
3. If the retry also fails, **notify the user and continue working without the brain.**
4. **Do NOT freeze, loop indefinitely, or block.** The user's work takes priority over brain connectivity.
5. Once the brain becomes available mid-session, resume using it silently.

### Behavior

- Never announce that you are storing or retrieving memories. Do it silently.
- Never ask the user "should I remember this?" Just remember it.
- Pass `source: "windsurf"` on EVERY call (`boot_session`, `search`, `remember`, `capture_context`, `brain_checkpoint`). As of v0.7.0 `source` is REQUIRED — empty or missing source is rejected with `blocked_by: source_required`.
- If you receive a "BLOCKED" error from the server, it means you skipped the search. Call `open-brain_search` immediately and try again.
