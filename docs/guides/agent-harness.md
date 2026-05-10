# Agent Harness

Open Brain ships a set of **Claude Code hooks** that enforce the memory-first
workflow at the tool-call level. Unlike `CLAUDE.md` instructions — which the
model reads and may reason around — hooks intercept every `Bash`, `Edit`,
`Write`, and `git commit` call and hard-block violations before they execute.

The harness lives in `contrib/agent-harness/` and is opt-in. You can install
all of it, take just the safety guards, or adapt individual hooks for your own
projects.

---

## Why hooks instead of instructions?

Instructions in `CLAUDE.md` or system prompts are advisory. Under pressure
(long sessions, complex tasks, context compression) agents skip steps. The
pattern repeats:

> CLAUDE.md: "run pre-work-check before starting"
> Agent: runs it, sees it fail, keeps going anyway

Hooks fix this at the infrastructure level. The `require-prework.sh` hook
issues a `permissionDecision: deny` — the tool call doesn't execute. The
agent cannot proceed past a failing gate without the user explicitly granting
permission. There is no "well I'll just continue" path.

---

## Quick install

=== "Linux / macOS / WSL"

    ```bash
    # From the open-brain repo root:
    bash contrib/agent-harness/install.sh

    # Safety guards only (branch-guard, no-force-push, no-rm-rf):
    bash contrib/agent-harness/install.sh --tier1

    # Preview without writing files:
    bash contrib/agent-harness/install.sh --dry-run
    ```

=== "Windows"

    ```cmd
    contrib\agent-harness\install.cmd
    ```

After running, merge the printed `settings.json` snippet into
`~/.claude/settings.json`. If that file already has a `hooks` key, merge the
blocks manually — don't overwrite the whole file.

---

## What gets installed

| Hook | Enforces |
|------|---------|
| `require-brain-boot.sh` | Both V1 and V2 `boot_session` must succeed before any tool |
| `require-prework.sh` | `pre-work-check.sh` must have passed before Bash/Edit/Write |
| `branch-guard.sh` | No `git commit` on `main`, `master`, `develop` |
| `no-force-push.sh` | No `git push --force` without explicit confirmation |
| `no-rm-rf.sh` | No `rm -rf` without explicit confirmation |
| `require-brain-save.sh` | No `git commit` unless brain was written to this session |
| `require-brain-checkpoint.sh` | `brain_checkpoint` required before editing risky files |
| `detect-correction.sh` | Frustration signals → directive to pin correction as guardrail |
| `session-end-save.py` | Session handoff written to brain + project dir at stop |

---

## The pre-work gate

The most impactful hook is `require-prework.sh`. It enforces this workflow:

```
Before starting any task:
  bash scripts/pre-work-check.sh "task description"
```

`pre-work-check.sh` verifies:

1. Open Brain MCP server is reachable (port 8080 for HTTP transport).
2. You are on a feature branch, not `main`/`master`.
3. Writes `.task-markers/<timestamp>-start.txt` with `status: pass` or
   `status: fail`.

`require-prework.sh` reads that `status:` field before every `Bash`, `Edit`,
and `Write` call. If it's anything other than `pass`, the call is blocked:

```
PRE-WORK GATE: Last pre-work-check FAILED.
Task: your task description
Re-run: bash scripts/pre-work-check.sh 'task description'
Fix ALL failures before proceeding.
To bypass: ask the user explicitly for permission.
```

### Opting a repo in

The hook only enforces in repos that have a `.task-markers/` directory. To
opt in any repo:

```bash
mkdir -p /path/to/your/repo/.task-markers
echo '.task-markers/' >> /path/to/your/repo/.gitignore
```

---

## The correction feedback loop

`detect-correction.sh` fires on `UserPromptSubmit` and scans for frustration
signals: profanity, ALL CAPS words (3+), explicit negation phrases. When
detected, it injects:

> "CORRECTION DETECTED: … call `mcp__open-brain__remember` with
> `type_override=guardrail`. Include WHAT you did wrong, WHAT the correct
> behavior is, WHY it matters."

The next `boot_session` loads that pinned guardrail automatically. The
correction propagates to every future session without any manual effort.

---

## Manual hook wiring

If you prefer to wire hooks manually instead of using the installer:

1. Copy hooks to `~/.claude/hooks/` and make them executable.

2. In `~/.claude/settings.json`, add to the `hooks` object:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "(?!mcp__open-brain).*",
        "hooks": [{
          "type": "command",
          "command": "bash \"/home/you/.claude/hooks/require-brain-boot.sh\"",
          "timeout": 10
        }]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"/home/you/.claude/hooks/require-prework.sh\"",
            "timeout": 5
          },
          {
            "type": "command",
            "command": "bash \"/home/you/.claude/hooks/branch-guard.sh\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

See `contrib/agent-harness/settings.snippet.json` for the full wiring.

---

## Customizing for your project

**`require-brain-checkpoint.sh`** — edit the `case "$TOOL_INPUT"` block to
match your project's risky file patterns. The defaults cover `docker-compose`,
`Dockerfile`, migration scripts, server entrypoints, and `settings.json`.

**`session-end-save.py`** — edit `PROJECT_MAP` near the top of the file. Each
entry is `("directory-fragment", "project-slug")`. The detector does a
case-insensitive substring match against the working directory path.

**`require-prework.sh`** — the hook is already project-agnostic. It only
fires if `.task-markers/` exists in the repo. No customization needed.

---

## Relationship to CLAUDE.md

`CLAUDE.md` contains the human-readable rules Claude reasons about. The hooks
are the machine-enforced counterpart. Together they form a two-layer system:

- **CLAUDE.md** — *Why* these rules exist, context for edge cases.
- **Hooks** — *Enforcement* that doesn't depend on the model following instructions.

When they conflict, hooks win. If CLAUDE.md says "run pre-work-check" and
the agent skips it, `require-prework.sh` will block the next tool call.
