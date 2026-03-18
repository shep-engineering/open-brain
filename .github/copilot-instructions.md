# Archetype Orchestrator — Copilot Instructions

> **These rules are enforced by git hooks. Skipping steps will block commits.**

## Session Start — Required

Before any work:

1. **Search memory** — if open-brain MCP is available, search for prior context on
   the task topic before writing any code.
2. **Read checkpoints** — `docs/planning/CONTEXT_CHECKPOINTS.md` if it exists.
3. **Pre-work gate** — non-negotiable:
   ```bash
   bash archetype-orchestrator/scripts/pre-work-check.sh
   ```

## Branch Rules

**Never commit directly to `main`, `master`, or `develop`.** The pre-commit hook
will block it. Always work on a feature branch:

```bash
git checkout -b feat/description
```

## Spec Discovery

Before writing code, discover what rules apply to this task:

```bash
python archetype-orchestrator/engine/discover.py --scan
python archetype-orchestrator/engine/discover.py --query "what the task is about"
```

Read the matched constitution file before proceeding.

## During Work — Checkpoints

At meaningful milestones:

```bash
bash archetype-orchestrator/scripts/context-checkpoint.sh "milestone"
```

If open-brain MCP is available, also call `capture_context` with decisions made
and gotchas hit. Pass `source="copilot"`.

## Destructive Operations

**Never run `rm -rf`, `git reset --hard`, `git push --force`, or any irreversible
operation without explicit user confirmation first.** State what will be affected
and wait for approval.

## Post-Work Gate

Before committing, run:

```bash
bash archetype-orchestrator/scripts/post-work-check.sh
```

For release-impacting changes: update `CHANGELOG.md` and bump version in
`package.json` or `pyproject.toml`.

## Validation

```bash
bash archetype-orchestrator/scripts/validate.sh        # staged
bash archetype-orchestrator/scripts/validate.sh --all  # full project
```

Checks: secrets, code safety, config safety, documentation, semver, conventional
commits, task markers, custom plugins.

## Planning Documents (docs/planning/)

- `DUAL_AGENT_WORKFLOW.md` — planner/builder roles
- `CONTEXT_LOOP.md` — iterative refinement
- `RALPH_LOOP.md` — review-adjust-learn-plan-handoff
- `SOURCE_CONSULTATION_MAP.md` — authoritative sources
