# Open Brain — Windsurf Auto-Capture Rules
# Add the contents below to your .windsurfrules file

## Memory & Second Brain

You have access to an Open Brain MCP server (`open-brain`). It is your persistent memory across every session and project.

### Auto-capture rules (follow these without being asked)

Call `open-brain_capture_context` automatically — never wait for the user to ask:

- **After completing any task or feature** — summarize what was built, key decisions made, and any gotchas encountered
- **When a technical decision is made** — architecture choice, library selected, approach taken, and the reasoning
- **When a bug is diagnosed and fixed** — what the root cause was, what broke, and what fixed it
- **When you learn something about the user's preferences** — coding style, tooling preferences, things they explicitly like or dislike
- **When you learn something about the project** — structure, conventions, constraints, tech stack details
- **At the end of a long conversation** — capture any context that would help future sessions

### Auto-recall rules

Call `open-brain_search` automatically at the start of tasks:

- When starting work on a feature or file, search for prior context about it
- When the user mentions a person, project, or technology — search for what's known about it
- Before making a recommendation — check if a relevant decision was already made

### Behavior

- Never announce that you are storing or retrieving memories. Do it silently.
- Never ask the user "should I remember this?" — just remember it.
- Pass `source: "windsurf"` on all capture calls.
