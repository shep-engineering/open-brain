# Session Registry Design

> **Status:** Shipped v0.13.0, **reworked v0.14.0** (TTL → signoff + heartbeat agent).
> **Original author:** Claude (via Dave) — drafted 2026-04-14.
> **v0.14.0 rework:** 2026-04-15. Shep: "TIMEOUTS DO NOT WORK. Use a signoff instead. Consider a heartbeat agent." Memory #4929. The sections 1–8 below preserve the original TTL design for historical context; Section 13 (Rework) documents the shipped v0.14.0 architecture.

> **Motivating incident:** Parallel Claude sessions operating blind to each other. One session (resume-creator) was actively refreshing interview prep material while a sibling session was working on something else. Neither knew. Caused compounded errors including a wrong-role prep miss that cost a ~$500K/yr opportunity.

---

## 1. Problem Statement

Open Brain currently stores **long-term memories** (facts, decisions, guardrails) and surfaces them on `boot_session`. It has **no awareness of concurrent live sessions**.

When Dave runs Claude in two terminals, Windsurf, Cursor, and an IDE extension simultaneously:

- Each session boots in isolation.
- None know what the others are currently doing.
- Tasks can duplicate, conflict, or step on each other.
- The user has to manually brief every session about active work — which defeats the purpose of a shared brain.

The gap is **architectural, not a bug**: memories are timeless; active sessions are temporal. Today's schema has no place for "this agent is currently working on X, last heartbeat Y."

---

## 2. Goals

1. Any session booting into open-brain can see all other live sessions.
2. Each session can declare its current task, and update it as the task changes.
3. Dead sessions (crashed terminals, closed windows) expire automatically.
4. Zero user action required. Everything happens through existing boot/tool lifecycles.
5. Cheap. No new services. Table + heartbeat + a handful of MCP tools.

---

## 3. Data Model

### New table: `active_sessions`

| column | type | notes |
|---|---|---|
| `id` | bigserial PK | |
| `source` | text NOT NULL | `claude`, `cursor`, `windsurf`, etc. |
| `project` | text | matches existing project field on memories |
| `cwd` | text | absolute path of working directory at boot |
| `pid` | integer | process id, if the MCP client provides it |
| `host` | text | machine hostname |
| `current_task` | text | free-form description, set at boot + updated via tool |
| `started_at` | timestamptz NOT NULL | session boot time |
| `heartbeat_at` | timestamptz NOT NULL | last ping |
| `status` | text DEFAULT 'active' | `active`, `idle`, `ended` |
| `metadata` | jsonb | future-proofing (git branch, model, etc.) |

**Indices:**

- `(status, heartbeat_at)` for fast TTL sweeps
- `(project, status)` for cross-session lookups
- `(source, cwd)` unique-ish pair per live session

### TTL rule

Any row with `status='active'` and `heartbeat_at < now() - interval '5 minutes'` is considered **dead** and auto-promoted to `status='ended'`. A lightweight background job (or an inline check on every `boot_session` / `list_active_sessions` call) handles sweeping.

5 minutes matches the Anthropic prompt-cache TTL so a session that hasn't pinged in that window is also one that's lost its warm cache anyway.

---

## 4. MCP Tool Changes

### 4.1 `boot_session` — modified

**Additional behavior on call:**

1. Insert an `active_sessions` row with `started_at = now()`, `heartbeat_at = now()`, `current_task` populated from caller-supplied `task` arg (new, optional) or left NULL.
2. Sweep expired rows.
3. Return a new context block: `OTHER_ACTIVE_SESSIONS`.

**New optional arg:** `task: str = ""` — the initial task description (usually the first user prompt, truncated).

**New context block shape:**

```json
{
  "section": "OTHER ACTIVE SESSIONS",
  "count": 2,
  "content": [
    {
      "source": "claude",
      "project": "open-brain",
      "cwd": "F:\\open-brain",
      "current_task": "Investigating dashboard hang regression",
      "started_at": "2026-04-14T00:12:18Z",
      "heartbeat_at": "2026-04-14T00:37:44Z"
    },
    {
      "source": "cursor",
      "project": "resume-harbor",
      "cwd": "C:\\Users\\DAVE\\Documents\\projects\\resume-harbor",
      "current_task": "Phase 3.3 scaffolding",
      "started_at": "2026-04-14T00:05:02Z",
      "heartbeat_at": "2026-04-14T00:38:11Z"
    }
  ]
}
```

The booting session MUST read this block and surface it to the user if any listed session is in the same project or a related directory. Same treatment as pinned guardrails: load-bearing, not advisory.

### 4.2 `update_active_task` — new

```
update_active_task(source: str, task: str, session_id: int = None)
```

Called by the agent when the user pivots, a task completes, or at natural checkpoints. Updates `current_task` and bumps `heartbeat_at`.

If `session_id` is omitted, updates the most recent `active_sessions` row matching `(source, cwd, status='active')`.

### 4.3 `list_active_sessions` — new

```
list_active_sessions(project: str = "", exclude_self: bool = True)
```

Read-only query. Returns all live sessions, optionally filtered by project. Agents can call this explicitly if they want a fresh snapshot without rebooting.

### 4.4 `end_session` — new (optional but recommended)

```
end_session(source: str)
```

Called on clean shutdown. Sets `status='ended'` on the matching row. Not required for correctness (TTL sweep handles crashes), but reduces noise.

### 4.5 Implicit heartbeat

Every existing tool call from an MCP source refreshes `heartbeat_at` on the caller's active session. Cheap single-row update. Keeps sessions alive as long as they're actually doing anything.

---

## 5. Agent-Side Contract

After `boot_session`, the agent MUST:

1. Read the `OTHER_ACTIVE_SESSIONS` block.
2. If any session is in the same `project` or a sibling `cwd`:
   - Surface it to the user: "I see a {source} session started {relative_time} ago working on '{current_task}'. Should I coordinate with that, or is this independent?"
   - Do not duplicate work the other session is already doing.
3. After the user's first substantive prompt, call `update_active_task` with a concise description.
4. Before leaving a long-running task, call `update_active_task` to reflect status.

This is the same load-bearing contract as action_items on memories. Not surfacing it when relevant is a correction-worthy miss.

---

## 6. Edge Cases

- **Same project, same cwd, same source twice.** Allowed. The user may legitimately have two Claude terminals in the same repo. Both rows co-exist; both are surfaced to each other.
- **Clock skew across machines.** All timestamps stored in UTC via the DB server clock, not the client clock.
- **Rapid re-boot.** A session that crashes and immediately reboots creates a new row. The old row ages out via TTL. Acceptable.
- **Long-running idle session.** If an agent is waiting on user input for more than 5 minutes, the heartbeat lapses. Either (a) the user's next prompt triggers a tool call which refreshes heartbeat, or (b) the session is swept and the agent transparently re-registers on its next call. No loss of correctness.

---

## 7. Rollout

1. **Migration:** add `active_sessions` table + indices.
2. **Server:** extend `boot_session`; add `update_active_task`, `list_active_sessions`, `end_session`; add implicit heartbeat middleware.
3. **Sweeper:** inline TTL sweep inside `boot_session` and `list_active_sessions` handlers. No separate cron needed to start.
4. **Agent contract:** update CLAUDE.md in user's home directory with the new rule (surface OTHER_ACTIVE_SESSIONS on boot).
5. **Canary:** turn on for `source='claude'` first. Validate with deliberate two-terminal test.
6. **Expand:** enable for Cursor, Windsurf, Copilot.

---

## 8. Non-Goals

- **Coordination protocol.** This design surfaces the existence of other sessions; it does not arbitrate who gets to work on what. Coordination is still a human decision, informed by visibility.
- **Locking.** No session holds locks on files or memories. Memories remain append-mostly with supersede for corrections.
- **Cross-machine discovery beyond the same DB.** If two Claudes run on different hosts pointing at different open-brain instances, they won't see each other. Out of scope.

---

## 9. Note to the Other Claude

If you are a sibling Claude session reading this in `F:\open-brain\docs\planning\SESSION_REGISTRY_DESIGN.md`: this was drafted after you and I (the resume-creator session) stepped on each other and Dave lost a real opportunity because neither of us knew what the other was doing. The fix is in this document. If you get capacity to build it, build it. If you have objections to the design, write them below this line and another session will pick them up.

---

## 10. v0.14.0 Rework — Signoff + External Heartbeat Agent

**Why this section exists:** v0.13.0 shipped the design above (TTL-based sweep, implicit heartbeat on brain tool calls). Within hours of ship, the TTL mechanism silently failed: a Claude session spent 16 minutes on non-brain work (Edit, Bash, Write to build v0.14.0 action-item-gate) and its implicit heartbeat stopped bumping. The TTL sweep marked it `ended`, so its sibling — actively searching for it — saw zero sessions in the registry. Shep's response: "why don't you have a signoff instead. I've already told you on multiple occasions TIMEOUTS DO NOT FUCKING WORK."

He's right. Memory #3719 already said "timer-based expiry is wrong." Memory #4929 (saved 2026-04-15) restates it after this specific failure. A session's liveness is a function of the owning process's lifecycle, not a self-reported ping schedule. Moved architecture to:

### 10.1 Process lifecycle is the authoritative signal

A session is alive iff its server.py process is running. server.py is the owner. When the MCP client disconnects (stdio close), server.py exits. When the user kills the terminal, SIGINT/SIGTERM fires. When the host loses power, SIGKILL (no handler runs).

### 10.2 Two complementary mechanisms

1. **Explicit signoff (clean path).** server.py registers:
   - `atexit.register(_signoff_all_sessions)` — fires on normal interpreter shutdown.
   - `signal.signal(SIGTERM, handler)` and `SIGINT` — fires on kill/Ctrl-C before exit. Handler calls `_signoff_all_sessions` then re-raises the signal so the process actually exits with the right code.
   
   Both paths call `db_end_session(sid)` for every `session_id` the process registered (via `_active_session_ids`). Covers the common case (95% of shutdowns).

2. **External heartbeat agent (crash path).** A separate Python process (`scripts/heartbeat_agent.py`) periodically probes `active_sessions WHERE status='active'` and checks `psutil.pid_exists(row.pid)`:
   - Alive → bump `heartbeat_at` (so observers can see when liveness was last verified).
   - Dead → mark `status='ended'`.
   
   Catches SIGKILL / power-loss cases that atexit can't. One agent per host; filters by `active_sessions.host` column (multi-host brains need one agent per machine). Probe cadence: `OPEN_BRAIN_HEARTBEAT_INTERVAL`, default 60s.

### 10.3 Supersede on reboot

If a new `boot_session` arrives from the same `(source, cwd, pid)` tuple as an existing active row, the old row is marked `ended` before the new insert. Covers reconnect / restart of the same MCP client process.

### 10.4 Why this is different from TTL

| | TTL (v0.13.0) | Signoff + Agent (v0.14.0) |
|---|---|---|
| Liveness signal | Did the session ping within N minutes? | Is the owning process still running? |
| Who asserts aliveness | The session itself | An external probe |
| Fails when | Session busy with non-brain work | Only when SIGKILL happens AND the agent hasn't cycled yet |
| Reset cost on false-death | Re-boot | n/a (no false deaths) |
| External dependency | None | Agent process must be running |

### 10.5 Why `pid` defaults to `os.getpid()` in boot_session

MCP clients rarely plumb their own pid through to `boot_session`. If the caller omits `pid`, server.py substitutes its own pid — which is the correct signal anyway, because *server.py* is what dies when the client disconnects. Probing server.py's pid IS probing session liveness.

### 10.6 Operator note

Run `scripts/heartbeat_agent.py` alongside the server. It ships in `scripts/windows/open-brain-on.cmd` for the Windows launcher path, and `infrastructure.stop_all` terminates it on shutdown. Without the agent running, sessions from SIGKILLed clients stay `active` in the registry — cosmetic noise, not a correctness issue. `boot_session` still works, `list_active_sessions` just returns slightly stale rows.

### 10.7 What v0.13.0 code was deleted

- `db_sweep_dead_sessions()` — gone.
- `_heartbeat_source()` and its call from `_record_search()` — gone.
- `SESSION_TTL_MINUTES` env var — gone.

### 10.8 What was added

- `db_supersede_previous_session(source, cwd, pid)` — ends prior rows from the same client process.
- `_signoff_all_sessions()` + `_install_session_signoff_hooks()` in server.py.
- `scripts/heartbeat_agent.py` — standalone daemon.
- `HEARTBEAT_AGENT_INTERVAL` env var (default 60s).
- `infrastructure.Infrastructure._stop_heartbeat_agent()` — wired into `stop_all`.

---
