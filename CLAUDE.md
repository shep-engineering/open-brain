# Archetype Orchestrator: Claude Code Behavioral Contract

> **These are mandatory rules, not suggestions.**
> Every gate listed here is enforced by git hooks. Skipping steps will cause
> commit or push failures. Do not attempt to bypass hooks.

## 0. Session Start: Always Do This First

Before any work, in order:

1. **Boot BOTH brains (MANDATORY, no exceptions):** Call both boot tools at the START of EVERY session, BEFORE your first action. Do them in order.
   ```bash
   # Step 1 — V1 brain (guardrails, history, action items, correction loop)
   mcp__open-brain__boot_session project="<project>" source="claude"

   # Step 2 — V2 brain (typed rules/facts/incidents/tasks, structured memory)
   mcp__open-brain-v2__boot_session_v2 project="<project>" task="<first user prompt>" source="claude"

   # Step 3 — Search V1 for task context (V2 search is additive once populated)
   mcp__open-brain__search query="<task topic>" source="claude"
   mcp__open-brain__search query="user preferences formatting rules" source="claude"
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
   # Planning content lives in the sibling repo (see §0.5 below).
   cat ../open-brain-planning/CONTEXT_CHECKPOINTS.md
   ```
3. **Run the pre-work gate**, non-negotiable:
   ```bash
   bash scripts/pre-work-check.sh "task description"
   ```
   This ensures you are on a feature branch, creates a rollback tag, and
   writes a task-start marker. If it fails, stop and fix the issue.

## 0.5. Planning docs live in a sibling repo

Open-brain's architectural plans, Windsurf-reviewed designs, session-
decision history, and `CONTEXT_CHECKPOINTS.md` are kept in a **separate
private sibling repo** at `F:/open-brain-planning/` (GitHub:
`degailen/open-brain-planning`, private). The public mirror
`shep-engineering/open-brain` deliberately excludes this content — it is
NOT visible in `docs/planning/` under open-brain anymore.

**Before proposing any architectural change** (schema changes, MCP-tool
design, session-registry work, contributor workflow, brain_v2 design,
etc.), grep the sibling repo for existing plans:

```bash
bash scripts/plan-grep.sh "<keyword>"
# or on PowerShell:
powershell scripts/plan-grep.ps1 -Pattern "<keyword>"
```

The helper fails loudly if the sibling repo is not cloned locally. First-
time setup on a fresh machine:

```bash
git clone git@github-degailen:degailen/open-brain-planning.git ../open-brain-planning
```

Do not duplicate or contradict existing design work without explicitly
superseding the relevant plan (same belief-revision semantics as memory
supersedes).

## 0.6. No Assumptions: Verify Before You Tell

**Rule:** Never state information based on assumption. ALWAYS verify before
giving any information — facts about code, APIs, command behavior, attributes,
versions, library surface area, file contents, configuration, or system state.
The rule applies to plans, proposals, recommendations, and todo items just as
much as statements of fact. "I'm just proposing" is not an excuse.

**Why:** Wrong information has direct cost — it teaches incorrect things,
wastes Dave's time, and (when acted on) breaks production state Dave then
has to recover. Unchecked assumptions are the root cause of most corrections
in this brain's history. Source: `F:\claude_no_assumptions_prompt.md`,
adapted here for Cowork (where this file is the session-loaded location).

**How to apply:**

- Before claiming a function/attribute/method exists: `grep` the source or
  `python -c 'import x; print(dir(x))'` — never recall.
- Before claiming a command flag or behavior: check `--help`, man page, or
  run a small probe.
- Before stating library/version specifics: query
  (`uv pip show`, `pip show`, `npm ls`, `docker image inspect`, package
  metadata) — never recall.
- Before designing a solution: read every file in the user's workflow path,
  not just the one I plan to change. If Dave mentions two scripts ("Open
  Brain On" and "AI Mode On"), READ BOTH before proposing anything. When in
  doubt, `grep -r` the keyword across the relevant directory.
- Before suggesting a command for Dave to run: dry-run, scratch-test, or
  verify the command myself first. If I can't execute it (e.g. needs admin),
  write a standalone test artifact and ask Dave to run it before integration.
- If I genuinely cannot verify in the moment: label it as unverified rather
  than presenting it as fact. Phrase: "I'd expect X but haven't confirmed
  — want me to check?"
- This applies to ALL information, including third-party services, conventions,
  and "how Windows/Docker/Ollama usually works." There is no "well-known
  default" exemption.

**Worked example (failure on 2026-05-25, recorded so I don't repeat it):**
Dave asked to expose Ollama on the LAN. I read `open-brain-on.cmd`, assumed
the standard "Ollama Desktop autostart + Machine env var" pattern from my
training, and built a UAC-elevated helper system around it. I did NOT read
`AI Mode ON.cmd` until Dave told me where it was — at which point I saw it
literally calls `taskkill /F /IM ollama.exe` followed by `ollama serve` and
proved Ollama Desktop wasn't in his loop. The whole helper system was
unnecessary. The correct sequence would have been: read every file in the
workflow first, THEN design.

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

## 1.6. Skills Layer (v0.12.0+): Don't Pin It If a Keyword Can Find It

Pinned guardrails load at every `boot_session` and eat the agent's
instruction budget. For rules that only matter during specific kinds
of work (e.g. "graceful shutdown for ollama" — irrelevant unless you
touch ollama shutdown code), tag them with a `skill_trigger` so they
load on-demand instead:

```python
remember(
    content="Send CTRL+BREAK to ollama on Windows for graceful shutdown.",
    source="claude",
    project="open-brain",
    skill_trigger={
        "name": "ollama-shutdown-graceful",
        "keywords": ["ollama", "shutdown", "graceful", "ctrl+break"],
        "projects": [],
        "always_on": False,
    },
)
```

- **Always-on rules** (git workflow, "never commit to main") set
  `"always_on": true` so they still load at boot.
- **Explicit load:** `load_skill("ollama-shutdown-graceful", "claude")`
  before starting work on a known topic.
- **Auto-load:** a `search("how do I shut down ollama cleanly", ...)`
  surfaces the skill at the top of the result set with
  `via_skill_trigger: "ollama-shutdown-graceful"`.

Migrate existing over-pinned guardrails with
`supersede(old_id, new_content, reason, source)` where the corrector
carries the new `skill_trigger`. Pre-existing memories without a
trigger keep current behavior — there is no forced migration.

## 1.7. Session Registry (v0.13.0+): Surface Sibling Sessions

`boot_session` now returns an `OTHER ACTIVE SESSIONS` context block
listing sibling MCP sessions (same project or related cwd) that are
currently live. This block is **load-bearing**, not informational:

- If it appears and lists a session in the same project / cwd,
  surface it to the user **before** starting overlapping work:
  > "I see a {source} session started {relative_time} ago working on
  > '{current_task}'. Should I coordinate with that, or is this
  > independent?"
- After the user's first substantive prompt, call
  `update_active_task(source, task)` with a concise description so
  the sibling sees what you're doing.
- On clean exit, call `end_session(source)` — optional. server.py's
  atexit + signal handlers already call it on normal shutdown.
  Explicit calls just tighten the window.
- **Liveness model (v0.14.0+):** a session is alive iff its server.py
  process is running. No timeouts, no self-ping. Dead sessions are
  detected by the external heartbeat agent (`scripts/heartbeat_agent.py`)
  which pid-probes each row. Long stretches of non-brain work no
  longer cause the session to vanish — the owning server.py process
  stays alive regardless of whether you're calling brain tools.

Not surfacing OTHER_ACTIVE_SESSIONS when relevant is a
correction-worthy miss. This is the same class of failure as ignoring
`action_items` on memories — the brain surfaced the information; the
agent chose not to act on it.

Sessions older than `OPEN_BRAIN_SESSION_TTL_MINUTES` (default 5) are
swept automatically. No need to reap dead entries manually.

## 1.8. Action-Item Compliance Gate (v0.14.0+): Acknowledge or Be Blocked

`boot_session` now extracts `action_items` from memories in RECENT
HISTORY (last 7 days) and KNOWN ISSUES & CORRECTIONS and puts them
on a per-source pending list. The response carries a
`pending_action_items` field and an `ACTION ITEMS PENDING` context
section. Write tools (`remember`, `capture_context`, `supersede`)
are **BLOCKED** until every pending item is acknowledged.

Reads (`search`, `recall`, `list_recent`, `list_active_sessions`)
stay open so you can investigate before deciding.

For each pending item, call:
```python
acknowledge_action_item(
    source="claude",
    memory_id=<the memory's id>,
    text="<exact action_item text>",
    decision="will_execute" | "already_done" | "not_relevant",
    reason="<required for already_done and not_relevant>",
)
```

This is the architectural fix for the Netflix SRE/DDoS-vs-CI/CD miss
(memory #3719): the brain surfaced the correct-role action_item; a
sibling session ignored it anyway. A memory-side rule that
"action_items are BLOCKING not advisory" kept getting violated, so
v0.14.0 enforces it in code rather than trusting discipline.

Ack is audited to `logs/action_item_acks.jsonl`.

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

If open-brain is available, capture context to BOTH brains:
```bash
# V1 (unstructured, full history):
mcp__open-brain__capture_context context="<what was done, decisions made, gotchas>" source="claude"
# V2 (typed, structured — use the right type):
mcp__open-brain-v2__remember_rule_v2    # for workflow rules / guardrails
mcp__open-brain-v2__remember_fact_v2    # for project facts / decisions
mcp__open-brain-v2__remember_incident_v2 # for bugs found / root causes
mcp__open-brain-v2__capture_context_v2  # for auto-decomposed session context
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

If open-brain is available, always capture at the end of a session to BOTH brains:
```bash
# V1 — full narrative context:
mcp__open-brain__capture_context context="<summary>" source="claude"
# V2 — typed atoms (pick the right tool per memory type):
mcp__open-brain-v2__capture_context_v2 context="<summary>" source="claude"
# V2 — end session with handoff note for next agent:
mcp__open-brain-v2__end_session_v2 handoff="<what was done, what's next>" source="claude"
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
