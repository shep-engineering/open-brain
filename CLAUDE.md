# Archetype Orchestrator: Claude Code Behavioral Contract

> **These are mandatory rules, not suggestions.**
> Every gate listed here is enforced by git hooks. Skipping steps will cause
> commit or push failures. Do not attempt to bypass hooks.

## 0. Session Start: Always Do This First

Before any work, in order:

1. **Boot the brain (MANDATORY, no exceptions):** Call `mcp__open-brain__boot_session` (or `mcp__open-brain__search` as fallback) at the START of EVERY session, BEFORE your first action.
   ```bash
   # Via MCP tool:
   mcp__open-brain__boot_session project="<project>" source="claude"
   mcp__open-brain__search query="<task topic>"
   mcp__open-brain__search query="user preferences formatting rules"
   ```

   **If the brain is unavailable (MCP call fails):**
   1. **Attempt to start it yourself.** Detect the platform and run the correct startup script:
      - **Windows:** `cmd /c "<OPEN_BRAIN_ROOT>\scripts\windows\open-brain-on.cmd"` (via shell)
      - **macOS/Linux/WSL:** `bash <OPEN_BRAIN_ROOT>/scripts/open-brain-on.sh`
      Where `<OPEN_BRAIN_ROOT>` is the directory containing `server.py` (check your MCP config for the path).
   2. Wait 10 seconds after the script finishes, then retry the MCP call once.
   3. If the retry also fails, **notify the user and continue working without the brain:**
      > "Open Brain didn't come up after running the startup script. Continuing without it. Please check Docker/Postgres manually."
   4. **Do NOT freeze, loop indefinitely, or block.** The user's work takes priority over brain connectivity.
   5. Once the brain becomes available mid-session, resume using it silently.
2. **Read context checkpoints**: if the file exists:
   ```bash
   cat docs/planning/CONTEXT_CHECKPOINTS.md
   ```
3. **Run the pre-work gate**, non-negotiable:
   ```bash
   bash scripts/pre-work-check.sh "task description"
   ```
   This ensures you are on a feature branch, creates a rollback tag, and
   writes a task-start marker. If it fails, stop and fix the issue.

## 1. Branch Rules: Non-Negotiable

- **Never commit directly to `main`, `master`, or `develop`.**
  The pre-commit hook will block you.
- Always work on a feature branch:
  ```bash
  git checkout -b feat/description-of-task
  ```
- Branch naming convention: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`

## 1.5. Belief Revision: Supersede, Don't Just Add

When you encounter a memory in the brain that's wrong, outdated, or
contradicts current truth (especially a pinned guardrail), do NOT
just `remember()` the corrected version on top — that leaves both
memories active and confuses future agents.

Instead, call `supersede(old_memory_id, corrected_content, reason, source)`.
The brain creates the new memory through the standard pipeline AND
marks the old one as superseded so future searches return only the
current truth. The audit trail is preserved (you can still
`recall(old_id)` to see what was previously believed + a banner
pointing at the corrector).

If the old memory was a pinned guardrail and the new one should
inherit guardrail status, pass `inherit_pinned=True`.

If you make a mistake, `unsupersede(memory_id, source)` reverses it.

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

## 3. During Work: Checkpoints at Milestones

At every meaningful milestone (feature complete, bug fixed, refactor done):

```bash
bash scripts/context-checkpoint.sh "what was accomplished"
```

If open-brain is available, also capture context:
```bash
# Via MCP tool: mcp__open-brain__capture_context context="<what was done, decisions made, gotchas>" source="claude"
```

## 4. Code Standards: Always Enforced

These are checked by `validate.sh` and will block commits if violated:

- **No hardcoded secrets**: no passwords, API keys, or tokens in source code.
  Use environment variables. No exceptions.
- **No dangerous patterns**: `eval()`, `exec()`, `dangerouslySetInnerHTML`,
  string-concatenated SQL queries are flagged.
- **No hardcoded hosts/ports** in config files. Use environment variables.
- **Conventional commit messages**, format: `type(scope): description`
  Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`, `revert`
- **No `.env` files committed**. Must be in `.gitignore`.

## 5. Before Declaring Done: Post-Work Gate

Run this before every commit. It validates everything and creates the task-end marker:

```bash
bash scripts/post-work-check.sh "what I tested and what passed"
```

For release-impacting changes, this gate **requires**:
- `CHANGELOG.md` updated with an entry for this change
- `package.json` (or `pyproject.toml`) version bumped
- A matching `## [x.y.z] - YYYY-MM-DD` heading in `CHANGELOG.md`

If a legitimate exception exists:
```bash
bash scripts/post-work-check.sh "reason for exception"
```

## 6. Destructive Operations: Require Confirmation

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

## 7. Memory: Capture at Session End

If open-brain is available, always capture at the end of a session:
```bash
# Via MCP tool: mcp__open-brain__capture_context
# Include: what was built, decisions made, bugs fixed, gotchas hit
# source="claude"
```

Never ask the user "should I remember this?" Decide and capture silently.

## 8. Validation Reference

Run at any time to check governance state:

```bash
bash scripts/post-work-check.sh "what I tested and what passed"
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
