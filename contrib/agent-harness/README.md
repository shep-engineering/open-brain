# Open Brain Agent Harness

A set of Claude Code hooks that turn Open Brain from a passive memory store
into an **active governance layer**. The hooks intercept tool calls at the
right moments and either block, warn, or inject context — enforcing the
workflows that agents should follow but often skip when left to their own
judgment.

This is the harness David Sheppard runs in his own Claude Code sessions.
You can adopt it entirely, pick the pieces that fit your workflow, or use
it as a template for your own hooks.

---

## What it does

| Hook | Event | What it enforces |
|------|-------|-----------------|
| `require-brain-boot.sh` | PreToolUse | No work until both V1 and V2 boot_session succeed |
| `require-prework.sh` | PreToolUse | No Bash/Edit/Write if pre-work-check.sh failed |
| `branch-guard.sh` | PreToolUse | No git commit on main/master/develop |
| `no-force-push.sh` | PreToolUse | Block force push without confirmation |
| `no-rm-rf.sh` | PreToolUse | Block recursive delete without confirmation |
| `require-brain-save.sh` | PreToolUse | No git commit without a brain capture this session |
| `require-brain-checkpoint.sh` | PreToolUse | Checkpoint required before editing risky files |
| `detect-correction.sh` | UserPromptSubmit | Injects directive to pin corrections as guardrails |
| `session-end-save.py` | Stop | Writes session handoff to brain and project dir |

---

## Quick install

### Linux / macOS / WSL

```bash
# From the open-brain repo root:
bash contrib/agent-harness/install.sh

# Safety guards only (no brain dependency):
bash contrib/agent-harness/install.sh --tier1

# Preview without writing:
bash contrib/agent-harness/install.sh --dry-run
```

### Windows (Command Prompt)

```cmd
contrib\agent-harness\install.cmd
```

After running, merge the printed snippet into `~/.claude/settings.json`.

---

## Manual install

1. Copy the hooks you want to `~/.claude/hooks/` (create the directory if it
   doesn't exist).
2. On Linux/macOS/WSL, make them executable: `chmod +x ~/.claude/hooks/*.sh`
3. Open `settings.snippet.json` in this directory.
4. Replace every `HOOKS_DIR` with the absolute path to your hooks directory
   (e.g. `/home/you/.claude/hooks` or `C:\Users\YOU\.claude\hooks`).
5. Merge the `hooks` block into your `~/.claude/settings.json`.

---

## Hook reference

### `require-brain-boot.sh`

**Event:** `PreToolUse` · **Matcher:** `(?!mcp__open-brain).*`

Blocks every non-brain tool call until both `mcp__open-brain__boot_session`
(V1) and `mcp__open-brain-v2__boot_session_v2` (V2) have been called and
returned success. This guarantees the agent always has full project context
(guardrails, history, corrections) before it can act.

Deadlock escape: brain startup scripts are always whitelisted, so the agent
can start the server even if the brain is down.

---

### `require-prework.sh`

**Event:** `PreToolUse` · **Matcher:** `Bash`, `Edit|Write`

Reads the `status:` field written by `scripts/pre-work-check.sh` into the
most recent `.task-markers/*-start.txt` in the current git repo. If the
status is not `pass`, every Bash/Edit/Write call is hard-blocked.

Only enforces in repos that have a `.task-markers/` directory. To opt in:

```bash
mkdir -p /path/to/your/repo/.task-markers
echo '.task-markers/' >> /path/to/your/repo/.gitignore
```

Then run `bash scripts/pre-work-check.sh "task description"` before
starting work. The check verifies: Open Brain is reachable, you're on a
feature branch (not main), and writes the pass/fail marker.

---

### `branch-guard.sh`

**Event:** `PreToolUse` · **Matcher:** `Bash`

Blocks `git commit` on `main`, `master`, and `develop`. Repos can opt out
by placing a `.no-branch-guard` file at their root.

---

### `no-force-push.sh`

**Event:** `PreToolUse` · **Matcher:** `Bash`

Blocks `git push --force` and `git push -f`. Prompts the agent to get
explicit user confirmation and suggests `--force-with-lease` instead.

---

### `no-rm-rf.sh`

**Event:** `PreToolUse` · **Matcher:** `Bash`

Blocks `rm -rf` and `rm --recursive`. Prompts the agent to get explicit
user confirmation and suggests moving to a backup first.

---

### `require-brain-save.sh`

**Event:** `PreToolUse` · **Matcher:** `Bash`

Blocks `git commit` if no brain write (`capture_context`, `remember`,
etc.) has happened in the current session. This ensures context is always
captured before code changes are committed — not after.

---

### `require-brain-checkpoint.sh`

**Event:** `PreToolUse` · **Matcher:** `Edit|Write`

Blocks edits to infrastructure, database, deployment, and configuration
files unless `brain_checkpoint` (V1 or V2) has been called in this session.
Checkpoint surfaces prior decisions, guardrails, and existing implementations
before risky edits.

**Customize** the `RISKY_PATTERNS` case block at the top of the hook for
your project's file categories.

---

### `detect-correction.sh`

**Event:** `UserPromptSubmit`

Detects frustration signals (profanity, ALL CAPS, explicit negation words)
and injects a directive into the agent's context:

> "CORRECTION DETECTED: … call `mcp__open-brain__remember` with
> `type_override=guardrail` … include WHAT you did wrong, WHAT the correct
> behavior is, WHY it matters."

This creates a feedback loop: every correction the user makes automatically
becomes a pinned guardrail that future sessions load at boot.

---

### `session-end-save.py`

**Event:** `Stop`

Parses the conversation transcript at session end and:

- Writes `LAST_SESSION_HANDOFF.md` to the project root with a structured
  summary of user requests, files edited, and git operations.
- Writes the same summary to Open Brain V1 and V2 databases if reachable.

**Customize** `PROJECT_MAP` at the top of the file to map your directory
names to project slugs.

---

## Tier summary

If you're unsure where to start:

**Tier 1 — Safety guards** (no brain required, universal value):
```
branch-guard.sh  no-force-push.sh  no-rm-rf.sh
```

**Tier 2 — Brain integration** (requires Open Brain running):
```
require-brain-boot.sh  require-prework.sh  require-brain-save.sh
require-brain-checkpoint.sh  detect-correction.sh  session-end-save.py
```

Install just Tier 1: `bash contrib/agent-harness/install.sh --tier1`

---

## How this relates to CLAUDE.md

`CLAUDE.md` in the repo root contains the human-readable rules that Claude
reads at the start of each session. The hooks in this directory are the
*machine-enforced* version of those rules — they intercept tool calls and
block violations before they happen. CLAUDE.md says "never commit to main";
`branch-guard.sh` makes it impossible. CLAUDE.md says "run pre-work-check
before starting"; `require-prework.sh` makes it mandatory.

The two layers work together: CLAUDE.md for context and reasoning,
hooks for hard enforcement.
