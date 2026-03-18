# Archetype Orchestrator — Claude Code Behavioral Contract

> **These are mandatory rules, not suggestions.**
> Every gate listed here is enforced by git hooks. Skipping steps will cause
> commit or push failures. Do not attempt to bypass hooks.

## 0. Session Start — Always Do This First

Before any work, in order:

1. **Search memory** — if open-brain is available, search for prior context:
   ```bash
   # Via MCP tool: mcp__open-brain__search query="<task topic>"
   ```
2. **Read context checkpoints** — if the file exists:
   ```bash
   cat docs/planning/CONTEXT_CHECKPOINTS.md
   ```
3. **Run the pre-work gate** — non-negotiable:
   ```bash
   bash archetype-orchestrator/scripts/pre-work-check.sh
   # Windows: powershell -ExecutionPolicy Bypass -File archetype-orchestrator/scripts/pre-work-check.ps1
   ```
   This ensures you are on a feature branch, creates a rollback tag, and
   writes a task-start marker. If it fails, stop and fix the issue.

## 1. Branch Rules — Non-Negotiable

- **Never commit directly to `main`, `master`, or `develop`.**
  The pre-commit hook will block you.
- Always work on a feature branch:
  ```bash
  git checkout -b feat/description-of-task
  ```
- Branch naming convention: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`

## 2. Discover Before You Act

Before writing any code, know what rules apply to this task:

```bash
# What specs are available?
python archetype-orchestrator/engine/discover.py --scan

# Which spec matches this task?
python archetype-orchestrator/engine/discover.py --query "describe your task"
```

Read the returned constitution file **before writing a single line of code**.
If no spec matches, universal governance rules still apply.

## 3. During Work — Checkpoints at Milestones

At every meaningful milestone (feature complete, bug fixed, refactor done):

```bash
bash archetype-orchestrator/scripts/context-checkpoint.sh "what was accomplished"
```

If open-brain is available, also capture context:
```bash
# Via MCP tool: mcp__open-brain__capture_context context="<what was done, decisions made, gotchas>" source="claude"
```

## 4. Code Standards — Always Enforced

These are checked by `validate.sh` and will block commits if violated:

- **No hardcoded secrets** — no passwords, API keys, or tokens in source code.
  Use environment variables. No exceptions.
- **No dangerous patterns** — `eval()`, `exec()`, `dangerouslySetInnerHTML`,
  string-concatenated SQL queries are flagged.
- **No hardcoded hosts/ports** in config files — use environment variables.
- **Conventional commit messages** — format: `type(scope): description`
  Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`, `revert`
- **No `.env` files committed** — must be in `.gitignore`.

## 5. Before Declaring Done — Post-Work Gate

Run this before every commit. It validates everything and creates the task-end marker:

```bash
bash archetype-orchestrator/scripts/post-work-check.sh
# Windows: powershell -ExecutionPolicy Bypass -File archetype-orchestrator/scripts/post-work-check.ps1
```

For release-impacting changes, this gate **requires**:
- `CHANGELOG.md` updated with an entry for this change
- `package.json` (or `pyproject.toml`) version bumped
- A matching `## [x.y.z] - YYYY-MM-DD` heading in `CHANGELOG.md`

If a legitimate exception exists:
```bash
bash archetype-orchestrator/scripts/post-work-check.sh --allow-release-exception "reason"
```

## 6. Destructive Operations — Require Confirmation

**Never run `rm -rf`, `git reset --hard`, `git push --force`, or any
irreversible operation without explicit user confirmation first.**

Before any destructive action:
1. State exactly what will be deleted/overwritten
2. Ask the user to confirm
3. Prefer reversible alternatives (move to backup folder, create rollback tag)

The pre-work gate creates rollback tags automatically. Use them:
```bash
git reset --hard pre-change/<timestamp>-<branch>
```

## 7. Memory — Capture at Session End

If open-brain is available, always capture at the end of a session:
```bash
# Via MCP tool: mcp__open-brain__capture_context
# Include: what was built, decisions made, bugs fixed, gotchas hit
# source="claude"
```

Never ask the user "should I remember this?" — decide and capture silently.

## 8. Validation Reference

Run at any time to check governance state:

```bash
bash archetype-orchestrator/scripts/validate.sh          # staged changes only
bash archetype-orchestrator/scripts/validate.sh --all    # entire project
```

| Check | What it catches |
|-------|----------------|
| 🔒 secrets | Hardcoded passwords, keys, tokens, connection string credentials |
| 🛡️ code-safety | eval/exec, SQL injection, dangerouslySetInnerHTML |
| 🔗 config-safety | Hardcoded hosts/ports in config files |
| 📚 documentation | Planning docs, markdown line lengths |
| 🏷️ semver | Valid versions in package.json / pyproject.toml, conventional commits |
| 🧭 task-workflow | Task start/end markers present |
| 🧩 plugins | Custom checks from validate.d/ |

## 9. Planning Documents

Maintain in `docs/planning/` when required by project config:

| Document | Purpose |
|----------|---------|
| `DUAL_AGENT_WORKFLOW.md` | Protocol for planner/builder agent splits |
| `CONTEXT_LOOP.md` | Session continuity loop definition |
| `RALPH_LOOP.md` | Review-Adjust-Learn-Plan-Handoff cycle |
| `SOURCE_CONSULTATION_MAP.md` | Authoritative sources by topic |
| `CONTEXT_CHECKPOINTS.md` | Running milestone log |
