# Archetype Orchestrator: Agent Instructions (GitHub Copilot Agents / OpenAI Codex)

> **These rules are enforced by git hooks. Skipping steps will block commits.**

## Open Brain: Check Availability First

**Before anything else**, verify Open Brain is reachable by attempting `mcp0_search`.

- If it **responds**: proceed normally with memory searches.
- If it **fails** (connection refused, timeout, any error): **stop immediately** and tell the user:

  > "Open Brain is not running. Please start it using the **'Open Brain ON'** shortcut on your Desktop, then let me know and I'll continue."

Do NOT proceed with any task until Open Brain is confirmed running. Do NOT create new startup scripts — the correct ones already exist. Never replace or duplicate them.

**CRITICAL — partial tool availability:** If some brain tools are visible in your tool list (e.g. capture/remember tools) but `mcp0_search` is NOT listed, do **NOT** skip the memory searches. Attempt the search call anyway — the tool list can be incomplete at session start. If the call succeeds, continue normally. Only stop and notify the user if the call itself throws an error. Never silently skip mandatory boot searches because a tool was absent from the initial listing.

### How to start Open Brain by OS

| OS | How to start | Script location |
|----|-------------|-----------------|
| **Windows** | Double-click **"Open Brain ON"** shortcut on Desktop | `<OPEN_BRAIN_ROOT>\scripts\windows\open-brain-on.cmd` |
| **WSL / Linux / Mac** | `bash /path/to/open-brain/scripts/open-brain-on.sh` | `<OPEN_BRAIN_ROOT>\scripts\open-brain-on.sh` (WSL: `/mnt/f/open-brain/scripts/open-brain-on.sh`) |

### How to stop Open Brain by OS

| OS | How to stop | Script location |
|----|------------|-----------------|
| **Windows** | Double-click **"Open Brain OFF"** shortcut on Desktop | `<OPEN_BRAIN_ROOT>\scripts\windows\open-brain-off.cmd` |
| **WSL / Linux / Mac** | `bash /path/to/open-brain/scripts/open-brain-off.sh` | `<OPEN_BRAIN_ROOT>\scripts\open-brain-off.sh` |

---

## Session Start: Required

Before any work, in order:

1. **Search memory (MANDATORY, no exceptions):** Search open-brain at the START of EVERY task. Do this BEFORE your first action. Run two searches: one for the task topic, one for "user preferences formatting rules". This applies to every task: coding, debugging, docs, research, refactoring, reviews, questions. Every task. Period.
2. **Read checkpoints**: read `docs/planning/CONTEXT_CHECKPOINTS.md` if it exists.
3. **Pre-work gate**, non-negotiable:
   ```bash
   bash scripts/pre-work-check.sh "task description"
   ```
   This ensures you are on a feature branch, creates a rollback tag, and writes
   a task-start marker. If it fails, stop and resolve the issue before continuing.

## Branch Rules: Non-Negotiable

**Never commit directly to `main`, `master`, or `develop`.** The pre-commit hook
will block you. Use a feature branch:

```bash
git checkout -b feat/description-of-task
```

## Discover Before You Act

Before writing a single line of code:

```bash
# What specs are available?
python archetype-orchestrator/engine/discover.py --scan

# Which spec matches this task?
python archetype-orchestrator/engine/discover.py --query "task description"
```

Read the returned constitution file before proceeding. If no spec matches,
universal governance rules still apply.

## During Work: Checkpoints

At every meaningful milestone:

```bash
bash scripts/context-checkpoint.sh "what was accomplished"
```

If open-brain MCP is available, also call `capture_context` with decisions made,
bugs fixed, and gotchas hit. Pass `source="codex"`.

## Destructive Operations: Require Confirmation

**Never run `rm -rf`, `git reset --hard`, `git push --force`, or any irreversible
operation without explicit user confirmation.**

Before any destructive action:
1. State exactly what will be deleted or overwritten
2. Wait for explicit user approval
3. Prefer reversible alternatives (move to backup, use rollback tags)

## Post-Work Gate

Before declaring a task done:

```bash
bash scripts/post-work-check.sh "what I tested and what passed"
```

For release-impacting changes this requires:
- `CHANGELOG.md` updated with an entry
- `package.json` or `pyproject.toml` version bumped
- Matching `## [x.y.z] - YYYY-MM-DD` heading in `CHANGELOG.md`

## Validation

```bash
bash scripts/post-work-check.sh "what I tested and what passed"
```

Checks: secrets, code safety (eval/exec/SQL injection), config safety,
documentation completeness, semver, conventional commits, task markers, plugins.

## Planning Documents (docs/planning/)

- `DUAL_AGENT_WORKFLOW.md`: planner/builder separation
- `CONTEXT_LOOP.md`: iterative refinement process
- `RALPH_LOOP.md`: review-adjust-learn-plan-handoff loop
- `SOURCE_CONSULTATION_MAP.md`: authoritative source registry
