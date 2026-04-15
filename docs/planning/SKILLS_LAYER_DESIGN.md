# Skills Layer — Brain Harness Phase 1 Design

**Status:** PROPOSED — design pass, not yet implemented.
**Branch:** `feat/brain-skills-layer`
**Backup:** `backups/brain-pre-skills-layer-20260414-145617.sql` (3327 memories, 26 pinned, 1 superseded baseline).
**Targets:** open-brain v0.12.0.
**Lineage:** Phase 1 of the 6-phase plan in `BRAIN_HARNESS_PLAN.md`.

---

## Problem statement

Every `boot_session` call currently returns ALL pinned memories for the
project — today that's **26 pinned guardrails** for open-brain,
totaling ~15–20 KB of text injected into every session's instruction
budget regardless of the task. Most are not relevant to any given
action: you don't need the "ollama shutdown graceful signal" guardrail
when you're writing a resume, and you don't need the "resume trust
engine scoring targets" when you're debugging the dashboard.

Per the HumanLayer + Chroma research referenced in
`BRAIN_HARNESS_PLAN.md`:

> "Heavy prompt steering causes agents to use the wrong tools.
> Instructions compete for 'instruction budget' — context space
> reserved for task reasoning. Verbose prompts push the agent into
> the 'dumb zone' at longer context lengths."

The symptom in practice: Claude loads 26 guardrails at boot, then
visibly forgets specific ones ~15–30 turns later under pressure, then
Shep has to re-surface the guideline. A smaller, task-relevant boot
payload would reduce the forgetting rate AND free budget for actual
task reasoning.

---

## Core idea

Extend the memory model with a **skill-trigger** dimension. Skills are
memories that should load ONLY when a condition matches — a query
keyword, a regex against the task description, or an explicit
`load_skill(name)` call — rather than always at boot.

The existing always-on pinned mechanism stays in place for the
narrow set of rules that MUST fire on every session (git workflow
rules, "never commit to main", etc.). Everything else moves to
skill-triggered.

---

## Design space considered

| Option | Description | Verdict |
|---|---|---|
| **(a) New `skill_trigger` JSONB column on memories** | Extends the existing `memories` table with a nullable JSONB column storing `{keywords, regex, tool_names, always_on}`. Memory with `skill_trigger IS NULL` behaves like today. | ✅ **Adopted** — minimal schema change, reuses all existing indexes + search infra |
| **(b) Separate `skills` table** | Skills live in their own table with FK to memories (or replace memories entirely for skill content). | ❌ More migration work; splits the memory store; creates dedup inconsistency (would a skill dedup against a regular memory?) |
| **(c) Skill as a new memory `type` value** | Use `metadata.type = "skill"` to mark, store triggers in metadata JSONB | ❌ Conflates type (semantic category) with loading behavior (mechanism). The same memory might be type=decision AND skill-triggered — we want composition, not replacement |
| **(d) External skill registry file** | YAML file like `.claude/skills/` listing which memory IDs trigger on which keywords | ❌ Creates drift between brain and filesystem; same failure mode as cross-roadmap drift (#1126) |

**Adopted: (a)** — `skill_trigger` JSONB column on memories, nullable.
Null means "load always if pinned, ignore if not" (current behavior).
Non-null means "skill-triggered; pinned-status becomes load-gate rather
than always-on".

---

## Schema addition

```sql
ALTER TABLE memories
    ADD COLUMN skill_trigger JSONB DEFAULT NULL;

-- Lightweight index for the "is this a skill?" filter path.
-- Partial so it only covers rows that actually participate in the
-- skill layer — most memories won't.
CREATE INDEX idx_memories_skill_trigger
    ON memories USING gin (skill_trigger)
    WHERE skill_trigger IS NOT NULL;
```

**`skill_trigger` shape:**

```json
{
  "keywords": ["ollama", "graceful", "ctrl+break"],
  "name": "ollama-shutdown-graceful",
  "always_on": false
}
```

- `keywords`: array of strings. Case-insensitive substring match against
  the search query or task context.
- `name`: a unique identifier for explicit `load_skill(name)` lookup.
  Convention: lowercase, hyphen-separated, project-namespaced if
  needed.
- `always_on`: boolean. If `true`, the skill loads at boot regardless
  of triggers (the narrow set that must fire every session). If `false`
  or missing, skill is triggered by keyword/name only.

**Migration reversibility:** single column drop.

---

## MCP tool surface

### New tool — `load_skill`

```python
@mcp.tool()
def load_skill(name: str, source: str) -> str:
    """Load a specific skill by its trigger name.

    Skills are memories with `skill_trigger.name` set. Call this when
    you're about to start work on a topic you know has a named skill
    (e.g. 'ollama-shutdown-graceful' before touching ollama shutdown
    code). Returns the skill content the same way `recall` does.

    Args:
        name:   The skill name from its skill_trigger.name field.
        source: REQUIRED agent identifier.
    """
```

### Modified tool — `remember`

Add optional `skill_trigger` param:

```python
def remember(
    content: str,
    source: str,
    type_override: str = "",
    project: str = "",
    valid_time: str = "",
    projects: list[str] | None = None,
    skill_trigger: dict | None = None,  # NEW
) -> str:
```

When `skill_trigger` is provided, the new memory is created with that
JSONB value. Callers can construct skills directly without needing a
separate tool.

### Modified tool — `boot_session`

Today: returns all pinned memories for the project.

New behavior: returns only memories where `pinned = TRUE` AND
(`skill_trigger IS NULL` OR `skill_trigger->>'always_on' = 'true'`).

This is the core of Phase 1 — the boot payload shrinks to only the
always-on set. Skill-triggered memories are NOT returned at boot;
they surface later via search auto-match or explicit load_skill.

### Modified tool — `search`

When `search` results include memories whose `skill_trigger.keywords`
match any word in the `query`, those memories are bumped to the top
(above regular semantic matches, below always-on pinned memories) and
marked `"via_skill_trigger": true` in the response. This is the
auto-load-when-relevant path.

---

## Migration strategy for existing pinned memories

All 26 currently-pinned open-brain guardrails stay `skill_trigger = NULL`
(null means "current behavior" — they still load at boot). That means
**v0.12.0 is backwards-compatible at load time** — the boot payload
doesn't shrink until someone explicitly tags guardrails with a skill
trigger.

Then, as follow-up work in the same release, we can opt specific
guardrails into skill-triggered mode by running `supersede` on them
(using the v0.11.0 mechanism!) with a new content + `skill_trigger`
set on the corrector. This is a nice self-eating dog-food moment:
Phase 1 uses Phase 0 (belief revision) to migrate content.

Tagging candidates for "convert to skill-triggered" (not blocking v0.12.0):

| Guardrail | Current type | Proposed trigger keywords |
|---|---|---|
| #5070 ollama shutdown | guardrail | `ollama`, `shutdown`, `graceful`, `ctrl+break` |
| #5071 Win32 console graceful | guardrail | `windows`, `win32`, `console`, `detach`, `signal` |
| #5065 dashboard hang regression | guardrail | `dashboard`, `splash`, `hang`, `regression` |
| #5066 dashboard hang verification | guardrail | `dashboard`, `window title`, `verify` |
| #3347 test E2E | task | **leave always_on** — test discipline applies broadly |
| #387 workflow rules | guardrail | **leave always_on** — branch/commit rules apply to every session |
| #827 DB safety | procedural | `database`, `migration`, `schema`, `backup` |

Not an exhaustive list; can be expanded in follow-up session. Rough
target: reduce boot payload from 26 → ~8 always-on guardrails (~70%
reduction).

---

## Search auto-match mechanics

The existing `search` in `server.py` builds its result set via hybrid
vector + FTS scoring. The skill-trigger auto-match layers ON TOP:

```
1. Fetch pinned + always_on memories (always included — small set)
2. Run normal hybrid search for `limit` results
3. Additionally: fetch memories whose
     skill_trigger->>'keywords' ?| query_words
   (`?|` is Postgres JSONB "any of these strings is a value in the array")
4. Merge: pinned first, then skill-triggered (with flag), then semantic
5. De-dup by ID (a memory could be in multiple layers)
```

Cost: one extra query per search call, hitting the partial GIN index.
Fast.

**Tuning parameters** (via existing env vars pattern):
- `OPEN_BRAIN_SKILL_TRIGGER_MAX`: max skill-triggered memories to add (default: 5 — don't swamp results)

---

## What this does NOT do (v0.12.0 scope)

- **Regex triggers.** Added only `keywords` initially. Regex is more
  expressive but harder to reason about. Add in v0.12.1 if specific
  failures argue for it.
- **Tool-name prefix triggers** ("load this skill when the agent is
  about to use Edit"). Requires hook integration. Deferred to when
  the hook installer (Phase 4) ships.
- **Cost accounting.** Token-weighted loading. Over-engineering for v1.
- **Automatic skill creation from search patterns.** Agent learning
  "this guardrail kept getting violated, maybe it needs a trigger" is
  a whole feature. Not here.
- **Skill registry UI in the dashboard.** Might come; not scoped.

---

## Tests (`tests/test_skills_layer.py`)

Same conventions as `test_belief_revision.py` — hit test DB, boot-gate
the test source via the cleanup fixture.

1. `test_skill_trigger_column_exists` — schema sanity
2. `test_remember_with_skill_trigger_stores_jsonb` — round-trip
3. `test_boot_session_excludes_non_always_on_skills` — core Phase 1 win
4. `test_boot_session_includes_always_on_skills` — safety valve works
5. `test_boot_session_includes_pinned_without_skill_trigger` — backwards-compat
6. `test_search_auto_matches_skill_by_keyword` — auto-load path
7. `test_search_skill_match_flagged_in_response` — `via_skill_trigger` flag surfaces
8. `test_search_skill_match_respects_max` — hard cap
9. `test_load_skill_by_name_returns_content` — explicit load path
10. `test_load_skill_unknown_name_404` — graceful not-found
11. `test_load_skill_requires_source` — compliance gate same as search/remember
12. `test_skill_trigger_on_superseded_memory_ignored` — belief-revision interaction
13. `test_migration_is_idempotent` — v5 migration safety
14. `test_migration_is_reversible` — v5 migration safety

---

## Files to add/modify

| File | Change |
|---|---|
| `scripts/migrate_v5_skills_layer.py` | **New.** Adds `skill_trigger` column + GIN partial index. Same pattern as v4. |
| `server.py` | Modify `db_get_pinned`, `db_search`, `boot_session`. Add `db_get_skills_by_keywords`, `db_get_skill_by_name`, `load_skill` MCP tool, `remember` param. |
| `tests/test_skills_layer.py` | **New.** 14 tests. |
| `docs/tools.md` | Add `load_skill` entry, update `remember` + `search` + `boot_session`, tool count 21 → 22. |
| `docs/architecture/memory-model.md` | Add "Skills layer" section. |
| `CHANGELOG.md` | v0.12.0 entry. |
| `CLAUDE.md` | Brief note on skill_trigger usage. |
| `docs/planning/BRAIN_HARNESS_PLAN.md` | Mark Phase 1 done; note skill_trigger shape for Phase 4 hook installer to read. |

---

## Decisions locked in (2026-04-14 approved by Shep)

1. **Trigger match semantics:** case-insensitive substring match of each keyword against the query. OR across keywords. ✅
2. **Naming scope:** skill `name` is globally unique; `skill_trigger.projects` controls applicability (empty array = global, populated = scoped). Updates propagate to all projects that read a shared skill — "amazing lessons learned benefit everyone" is the design goal. ✅
3. **Superseded skills:** active-only by default on both search and load_skill. `include_superseded=True` override. ✅
4. **v0.12.0 ships machinery only.** Existing 26 pinned guardrails stay `skill_trigger = NULL` (backwards-compatible). Migration of individual guardrails to skill-triggered is a follow-up session. ✅
5. **Dashboard surfacing:** deferred. Revisit in a later phase. ⏸

## Original open questions (now answered above)

1. **Trigger match semantics.**
   - Default proposal: case-insensitive, each `keyword` substring-matched
     against the `query` string (OR across keywords — any one hitting
     fires the skill).
   - Alternative: tokenize query, require whole-word match.
   - Alternative: use vector similarity between keywords and query.

   Pick one. I lean case-insensitive substring (cheapest, most
   forgiving, tunable via keyword selection).

2. **Skill name uniqueness scope.**
   - Per-project (`name` must be unique within a project): two
     projects can both have a skill named `testing-discipline`.
   - Global (`name` unique across the entire brain).

   I lean per-project. Matches the existing project-scope pattern.

3. **Search `include_superseded` + skill triggers.** If a superseded
   memory has a skill_trigger that matches, does it surface on a
   normal search (when include_superseded=False)?

   Proposal: NO — active-only by default for skills too. Matches the
   belief-revision default. Override via `include_superseded=True`.

4. **"Always-on" threshold / policy.** Which existing pinned
   guardrails migrate to skill-triggered vs stay always-on? I sketched
   a table above; finalizing is a follow-up session (post-v0.12.0)
   since it requires you to decide each one.

   Question right now: do you want v0.12.0 to SHIP the Phase 1
   machinery with all 26 existing pinned as always-on (zero UX change
   for you until you migrate them), OR do you want me to pre-migrate
   some obvious candidates as part of v0.12.0?

   My pick: ship the machinery only. You migrate on your terms.

5. **Dashboard surfacing.** Should the dashboard show which skills are
   loaded in the current session / a count of "skill hits" (skill
   auto-matched this turn)? Out-of-scope for v0.12.0 unless you want
   it in-scope.

   My pick: out-of-scope.

---

## Rollout sequence

Same pattern as v0.11.0:

1. ✅ Backup (`backups/brain-pre-skills-layer-20260414-145617.sql`)
2. ✅ Feature branch
3. ⏸ Design doc (this) — **paused for Shep review**
4. Migration script
5. Test DB: clean apply + idempotent + reversible
6. Implement + tests
7. Full suite regression check
8. Commit + merge + tag v0.12.0 + push to degailen
9. Apply migration to production
10. Shep migrates selected guardrails to skill-triggered over time
    (uses existing `supersede` from v0.11.0)
11. Measure: does boot-session payload shrink? Does agent forgetting
    rate drop? (Qualitative — Shep reports back)
12. Re-push shep/main + gh-pages

---

## Effort estimate

| Step | Time |
|---|---|
| Migration v5 + idempotency/reversibility tests | 20 min |
| Schema-level helpers (`db_get_skills_by_keywords`, `db_get_skill_by_name`) | 30 min |
| `load_skill` MCP tool | 30 min |
| `boot_session` filter + `search` auto-match | 1 hour |
| `remember` param + dedup interaction check | 30 min |
| Test suite (14 tests) | 1.5 hours |
| Documentation | 45 min |
| Commit/merge/tag/push | 15 min |
| Production apply | 10 min |
| Re-push shep mirror + gh-pages | 10 min |
| **Total** | **~5.5 hours** |

Faster than v0.11.0 because schema is even smaller (1 column vs 3 +
indexes) and no dedup logic needs changes.
