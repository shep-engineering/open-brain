# Archetype Orchestrator — Agent Instructions (GitHub Copilot Agents / OpenAI Codex)

> **These rules are enforced by git hooks. Skipping steps will block commits.**

## Session Start — Required

Before any work, in order:

1. **Search memory** — if open-brain MCP is available, search for prior context
   on the task topic. Do not start coding until you've checked for prior decisions.
2. **Read checkpoints** — read `docs/planning/CONTEXT_CHECKPOINTS.md` if it exists.
3. **Pre-work gate** — non-negotiable:
   ```bash
   bash archetype-orchestrator/scripts/pre-work-check.sh
   ```
   This ensures you are on a feature branch, creates a rollback tag, and writes
   a task-start marker. If it fails, stop and resolve the issue before continuing.

## Branch Rules — Non-Negotiable

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

## During Work — Checkpoints

At every meaningful milestone:

```bash
bash archetype-orchestrator/scripts/context-checkpoint.sh "what was accomplished"
```

If open-brain MCP is available, also call `capture_context` with decisions made,
bugs fixed, and gotchas hit. Pass `source="codex"`.

## Destructive Operations — Require Confirmation

**Never run `rm -rf`, `git reset --hard`, `git push --force`, or any irreversible
operation without explicit user confirmation.**

Before any destructive action:
1. State exactly what will be deleted or overwritten
2. Wait for explicit user approval
3. Prefer reversible alternatives (move to backup, use rollback tags)

## Post-Work Gate

Before declaring a task done:

```bash
bash archetype-orchestrator/scripts/post-work-check.sh
```

For release-impacting changes this requires:
- `CHANGELOG.md` updated with an entry
- `package.json` or `pyproject.toml` version bumped
- Matching `## [x.y.z] - YYYY-MM-DD` heading in `CHANGELOG.md`

## Validation

```bash
bash archetype-orchestrator/scripts/validate.sh          # staged changes
bash archetype-orchestrator/scripts/validate.sh --all    # entire project
```

Checks: secrets, code safety (eval/exec/SQL injection), config safety,
documentation completeness, semver, conventional commits, task markers, plugins.

## Planning Documents (docs/planning/)

- `DUAL_AGENT_WORKFLOW.md` — planner/builder separation
- `CONTEXT_LOOP.md` — iterative refinement process
- `RALPH_LOOP.md` — review-adjust-learn-plan-handoff loop
- `SOURCE_CONSULTATION_MAP.md` — authoritative source registry
