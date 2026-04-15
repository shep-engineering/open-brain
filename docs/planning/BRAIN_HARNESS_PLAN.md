# Open Brain — Harness Engineering Plan

**Context**: This plan reframes Open Brain through the lens of Anthropic's
harness-engineering body of work (April 2026: "Harness design for long-running
application development" + "Claude Managed Agents" + Claude Code
hooks/skills/subagents taxonomy).

**Thesis in one sentence**: Open Brain today is 90% **session store** and 10%
**harness**. The guardrail-memory pattern we keep reinforcing relies on the
agent remembering, reading, and applying rules — and empirically that fails
~1 in every 3 high-stakes moments. To make open-brain a *trustworthy*
cognition layer for other agents, it needs to grow active harness capabilities
alongside the existing memory store.

---

## Principles (adopted from Anthropic research)

1. **Harnesses encode assumptions about what the model can't do on its own —
   and those assumptions go stale as models improve.** Every harness component
   should be deletable. Re-audit each release.
2. **Self-evaluation blindness is the #1 LLM-agent failure mode.** The fix is
   a separate evaluator, not more self-discipline instructions.
3. **Hooks are deterministic; guardrails are advisory.** If a rule must fire,
   it needs to live on the control path, not in memory.
4. **Session as durable event log, separate from the harness process.** Lets
   you replay, audit, and recover across harness restarts.
5. **Opinionated about interfaces, unopinionated about harnesses.** The
   contract (tool signatures) outlasts any specific implementation.
6. **Start simple; add complexity only when a specific failure proves it
   necessary.** Don't build for hypothetical future problems.
7. **Back-pressure: silent on pass, loud on fail.** Noisy success logs cause
   agents to pattern-match "lots of green text" with "done."

---

## Where Open Brain is today (lens check)

| Layer | Open Brain component | Harness-engineering analog |
|---|---|---|
| Session (state) | PostgreSQL + pgvector, `remember` / `search` / `recall` | ✅ durable event log |
| Harness (brain) | `server.py` FastMCP, 19 tools | ⚠️ passive tool surface, no active enforcement |
| Sandbox (hands) | The agent's own process (Claude Code / Cursor) | ✅ naturally isolated |
| Skills | N/A — guardrails are raw text memories | ❌ no progressive disclosure |
| Hooks | N/A — compliance is enforcement-via-embarrassment | ❌ no deterministic gates |
| Evaluator | N/A — every agent grades its own work | ❌ self-evaluation blindness unchecked |
| Handoff artifact | N/A — no explicit context-reset protocol | ❌ each session rediscovers context |

The architecture is right for a **session store**. It's missing the active
components that Anthropic's research shows are load-bearing for reliable
long-horizon agent work.

---

## Proposed changes, phased

Phases are ordered by **observed-failure-to-fix ratio** — what actually fails
today, fixed first. Each phase is independently shippable.

### Phase 1 — Skills layer (progressive disclosure of guardrails) ✅ SHIPPED v0.12.0 (2026-04-14)

**Status:** Shipped. Design doc in `docs/planning/SKILLS_LAYER_DESIGN.md`. Implementation in `server.py` (`db_get_pinned`, `db_get_skills_by_keywords`, `db_get_skill_by_name`, `load_skill` MCP tool, `remember`/`search` param additions). Migration in `scripts/migrate_v5_skills_layer.py`. Tests in `tests/test_skills_layer.py` (13 passing). Note: migration of the existing 26 pinned guardrails into skill-triggered mode is opt-in follow-up work; v0.12.0 shipped the machinery only. Phase 4 (hook installer) can read `skill_trigger` to fire on tool-name triggers. Differences from the original plan: no cost-hint field (deferred), no regex triggers (keywords only), `projects` scope field added to support globally-unique names with per-project scoping.

**Problem it fixes**: today every guardrail loads at `boot_session` regardless
of task relevance. Current pinned-guardrail load is ~11 large memories; they
compete for instruction budget and get crowded out. Per the HumanLayer and
Chroma studies, heavy prompt steering causes agents to use the wrong tools.

**Change**: add a `skill` dimension to memories (alongside existing
guardrail/procedural/etc. types). Skills have:
- A **trigger** (regex, keyword set, or tool-name prefix)
- A **body** (the actual guidance)
- A **cost hint** (tokens estimated; affects whether to load)

`boot_session` returns only the always-on pinned set. Other skills are
returned by `search` when their trigger matches the query, or explicitly
via a new `load_skill(name)` MCP tool.

**Reuses**: existing memory storage; no schema migration beyond a
`skill_trigger` JSONB column and index.

**Signal of success**: boot-session payload size drops ~60%; agents cite
specific skills when relevant work comes up; miss rate on "the right skill
wasn't loaded" drops.

---

### Phase 2 — Evaluator subagent pattern for `brain_checkpoint`

**Problem it fixes**: `brain_checkpoint` today is a self-report. The agent
tells the brain "I did X, searched for Y." The brain believes it. This is
the self-evaluation blindness Anthropic calls out as the #1 failure.

**Change**: `brain_checkpoint` becomes a two-step:
1. Agent submits what it did + claims
2. Brain spins up a lightweight evaluator (small local model, or Claude
   Haiku) that receives the last N tool-call events, the claims, and a
   rubric. Evaluator returns pass / fail / specific divergence.

Failed checkpoints block further `remember` calls from that source until
resolution (same blocking pattern `boot_required` uses today).

**Interfaces stay stable**: `brain_checkpoint(source, claims)` — callers
don't change. Internal pipeline adds the evaluator pass.

**Signal of success**: claim-vs-evidence divergences get caught before the
agent moves on; false "done" claims drop measurably.

---

### Phase 3 — Session as event log (opt-in)

**Problem it fixes**: today the brain captures user-submitted memories but
not the *event stream* an agent produced along the way. That means
retrospective analysis ("what did the agent *actually* do in session X?")
requires the agent to have remembered to write it down. Agents don't.

**Change**: add `capture_event(source, kind, payload)` — cheap, high-volume
writes. Event kinds: `tool_call`, `tool_result`, `decision`, `correction`,
`checkpoint`. Optional — agents can opt in via a header or CLAUDE.md
setting. Events are distinct from memories (different table, different
retention).

`wake(session_id)` becomes possible: agents crashed mid-session can resume
with `getEvents(session_id, from_cursor)`. Matches Anthropic's Managed
Agents primitive.

**Signal of success**: when a session goes wrong, we can reconstruct the
failure from the event log without re-interviewing the user. Today that
reconstruction is impossible.

---

### Phase 4 — Guardrail-as-hook installer

**Problem it fixes**: the guardrails we've accumulated in this project alone
(#387, #3347, #5065, #5066, #5070, #5071, #5075, #5076, #5077, #4978 etc.)
live as text that agents read and then violate within the same session. We
saw this three times today in a single conversation.

**Change**: add an MCP tool `install_as_hook(memory_id, target_harness)`.
The brain knows how to render a guardrail into the hook format for common
harnesses (Claude Code `.claude/settings.json`, Cursor `.cursorrules`,
etc.). The agent installs the hook once per project; the harness enforces
it automatically on every subsequent tool call.

**Only applies to** guardrails whose trigger is *mechanically definable*
(branch name, tool call name, command content regex). Guardrails with
semantic triggers ("don't make assumptions") remain text-only.

**Design note**: the rendered hook body should be the **minimum** necessary
check. Per Anthropic's simplicity principle, a 5-line shell script that
fails closed beats a 50-line policy engine.

**Signal of success**: the same guardrail doesn't get re-violated across
sessions. Specifically: the violations that motivated memories #5065 and
#5066 never recur because their trigger is now a pre-tool-use hook.

#### First concrete prototype — `mirror-roadmap-to-global.sh` (2026-04-14)

Before building the generic `install_as_hook(memory_id, target_harness)`
MCP tool, we built ONE hand-written hook to validate the pattern end-to-
end on a real guardrail-violation that had just bitten us. This is the
"simplest thing that works" pass before generalizing.

**Triggering incident**: 2026-04-14 — resume-harbor Phase 3.3 (Interview
Prep Analysis dark-launch) was added to the project-internal
`docs/planning/ROADMAP.md` and to brain memory #5068 on 2026-04-13, but
the global `C:/Users/DAVE/Documents/projects/ROADMAP.md` was missed.
Shep caught it. Brain memory #1126 captures the discrepancy pattern.

**Hook**: `~/.claude/hooks/mirror-roadmap-to-global.sh` (PostToolUse,
matcher `Edit|Write`, timeout 5s, non-blocking exit 0).

**What it does**:
- Reads the tool call payload from stdin
- Extracts `tool_input.file_path`
- Normalizes Windows backslash paths to forward slashes
- Pattern-matches `*/docs/planning/ROADMAP.md`
- If matched (and not the global roadmap itself — explicit anti-loop
  exclusion), prints a `<post-tool-use-hook>...</post-tool-use-hook>`
  reminder block naming the affected project and the global roadmap path
- Otherwise: silent exit 0 (back-pressure principle: silent on pass)

**Verified empirically** with 4 smoke tests:

| Scenario | Expected | Observed |
|---|---|---|
| Edit to a non-roadmap file (`F:/open-brain/README.md`) | silent | silent ✓ |
| Edit to a project-internal roadmap (`F:/open-brain/docs/planning/ROADMAP.md`) | reminder fires, project name = "open-brain" | ✓ |
| Edit to the global roadmap (`C:/Users/DAVE/Documents/projects/ROADMAP.md`) | silent (no infinite-loop nag) | ✓ |
| Edit with Windows backslash path (`C:\Users\DAVE\Documents\projects\resume-harbor\docs\planning\ROADMAP.md`) | reminder fires, project name = "resume-harbor" | ✓ |

**Lessons for the generic installer (Phase 4 v1.0)**:

1. **Path normalization is non-trivial.** Hooks receive `file_path` in
   whatever format the tool call used — Windows can produce backslashes
   or forward slashes. Generic renderer must always normalize before
   pattern-matching.
2. **Anti-loop exclusions matter.** A hook that nags about editing
   "ROADMAP.md" anywhere will nag itself forever when applied to the
   global roadmap. Each rendered hook needs to explicitly exclude its
   own canonical target.
3. **Project name extraction from path is useful and cheap.** Walk up
   from the matched file: `dirname(dirname(dirname(path)))` reliably
   recovers the project root name for the standard `<project>/docs/
   planning/ROADMAP.md` layout. Generic renderer should expose this as
   `${project_name}` template variable.
4. **Settings watcher caveat must be in the user-facing handoff.** New
   hooks attach only when the settings watcher is watching `.claude/`,
   which it only does for directories that had a settings file at session
   start. Newly installed hooks don't fire in the same session unless
   the user opens `/hooks` (which reloads config) or restarts.
5. **JSON schema for `hooks.{event}[].matcher`** uses pipe-separated
   tool names (e.g. `"Edit|Write"`) and follows the existing pattern in
   `~/.claude/settings.json`. Renderer must MERGE into existing array,
   not replace, when adding alongside existing entries.

**Path to v1.0 of `install_as_hook`**: when a third or fourth hand-
written hook lands following this pattern, generalize. Don't generalize
on a sample size of one. The current hook is the reference shape.

---

### Phase 5 — Handoff artifacts for context resets

**Problem it fixes**: Anthropic's research is blunt — beyond a certain
context length, model performance degrades regardless of content quality.
Compaction (summarizing in place) preserves continuity but doesn't address
the "context anxiety" failure Sonnet 4.5 exhibited. Context resets with
structured handoff worked better.

**Change**: add `prepare_handoff(session_id, topic)`. Brain produces a
structured artifact: the currently-active project state, the last N
decisions, the guardrails-loaded-but-not-yet-applied, and the open
questions. New agents / restarted agents consume this instead of
re-deriving from scratch.

**Signal of success**: long-running work survives session boundaries
cleanly. Today a context-reset means losing ~everything informal; this
makes the reset a first-class operation.

---

### Phase 6 — Deletable-guardrail audit

**Problem it fixes**: we've been accumulating guardrails for a year. Every
new Claude release probably makes some of them obsolete. We've never done
the audit. Per Anthropic: *"when a new model lands, strip away pieces that
are no longer load-bearing."*

**Change**: add `model_version_written` and `last_observed_violation`
timestamps on each guardrail. A background job (or manual MCP tool
`audit_guardrails(model_version)`) flags guardrails whose last observed
violation was with an older model and which haven't fired since. User
confirms before soft-retiring.

**Signal of success**: the guardrail pool shrinks over time as the
underlying model improves, rather than monotonically growing.

---

## What NOT to build (consciously)

- **A 50-rule policy engine.** Simpler rule systems are strictly better than
  richer ones (Anthropic, HumanLayer, Chroma all agree). If one concrete
  failure hasn't happened, don't write the rule.
- **Auto-apply / auto-modify to agent code.** The brain should render hooks
  and offer them; it should never silently install into a user's repo.
- **A general "agent evaluator."** Evaluators should be task-specific with
  calibrated rubrics. A general-purpose one produces general-purpose praise.
- **Re-architecting the current DB schema.** The session store is the one
  layer that's working. Extend around it; don't rewrite it.
- **Custom harness for open-brain itself.** Claude Code already has hooks /
  skills / subagents. Use them. If we need something they don't provide, we
  almost certainly don't actually need it.

---

## Phase-by-phase cost estimate

| Phase | Effort | Risk | Value (estimated) |
|---|---|---|---|
| 1. Skills dimension | ~1 day | Low — additive schema | High — unclogs instruction budget |
| 2. Evaluator for checkpoint | ~3 days | Medium — needs rubric calibration | **Highest** — catches the #1 failure mode |
| 3. Event log | ~2 days | Low — new tool, new table | Medium — retrospective power |
| 4. Hook installer | ~2 days | Medium — render logic per harness | High — converts passive → active |
| 5. Handoff artifact | ~1 day | Low — composition of existing data | Medium — long-session hygiene |
| 6. Deletable-audit | ~0.5 day | Low | Low-per-event, high cumulative |

---

## Recommended sequence

1. **Phase 1 (Skills)** first — it's the cheapest and unblocks the later
   phases (Phase 4 needs a way to mark "this guardrail is hookable").
2. **Phase 4 (Hook installer)** second — it converts the guardrails that
   bit us this month into automatic enforcement. Highest durable value.
3. **Phase 2 (Evaluator)** third — it needs Phase 3's event log to work
   well. Build the event log first if the evaluator can't run cheaply
   without it.
4. **Phase 3 (Event log)** fourth. Paired with Phase 2.
5. **Phase 5 (Handoff)** fifth.
6. **Phase 6 (Audit)** continuously once any other phase lands.

---

## Guardrail-count scorecard (stop metric)

Before any phase lands, count pinned-guardrail memories for the project.
After each phase, recount. If the count isn't **going down** despite the
phase's whole purpose being to convert guardrails to hooks, something is
wrong — likely the hook surface is still less reliable than the guardrail
fallback, and people are writing the guardrail as backup.

Target: by end of Phase 4, at least 60% of currently-pinned guardrails
for this project have been converted to hooks or installed as skills.
The memory store becomes the fallback for semantic/judgment cases, not
the primary enforcement layer.

---

## Open questions for Shep

- **Scope of "harness":** does this plan stay focused on Open Brain the
  project, or does it extend to Open Brain the product (shipping harness
  capabilities as a feature other teams adopt)? The second is a much
  larger surface and implies different phasing.
- **Evaluator model choice:** local Ollama vs. cheap Claude Haiku via API
  vs. pattern-match against rules. Affects latency + cost + reliability.
- **Hook installer target IDEs:** Claude Code only for v1, or render for
  Cursor / Windsurf too? The latter multiplies per-harness rendering work.
- **Retention policy for event log:** event logs fill fast. 7 days default?
  Opt-in longer retention? This is a database-sizing decision.

No action on open questions until we talk.
