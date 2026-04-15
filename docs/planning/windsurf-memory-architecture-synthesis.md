# Windsurf: Open Brain Memory Architecture Synthesis
**Author:** Windsurf (Cascade)
**Date:** 2026-04-15
**Status:** Recommended — synthesized from Claude, GPT, and Windsurf parallel analyses
**Source docs:**
- `claude-memory-architecture-redesign.md`
- `gpt-memory-architecture-redesign.md`
- `windsurf-memory-architecture-redesign.md`

---

## Purpose

Three AI systems (Claude, GPT-4o, Windsurf/Cascade) independently analyzed the same open-brain memory architecture problem. This document extracts the best practices from each, calls out where they agree and where they differ, and produces a single recommended implementation plan.

---

## 1. Where All Three Agree (High Confidence)

These conclusions were reached independently by all three. Implement without further debate.

- **Four memory type taxonomy.** Labels differ across docs but the split is identical: behavioral rules, project facts, incident history, and active task state. These must be separate objects with separate retrieval policies.
- **Merge is the root pathology.** Auto-merge on similarity is the direct cause of walls of text. Supersede plus archive replaces it. Merge is never valid for rules.
- **Boot payload must be headline-only with a hard token budget.** Bodies are retrievable on demand via `recall(id)`. They are not preloaded.
- **Acknowledgment gates must be narrow.** Full per-rule acknowledgment produces ritual skimming under time pressure. Gates fire only on write actions, not reads.
- **Relevance scoring at boot is worth the complexity.** Task-conditioned retrieval scores memories against the current task before loading. Indiscriminate preloading wastes attention.

---

## 2. Where They Differ (and the Verdict)

### 2.1 Phase 1 scope

| Doc | Phase 1 scope |
|---|---|
| Claude | Rendering only. Zero storage changes. Return headline index, bodies via recall. |
| GPT | Rendering plus type classification, severity tagging, relevance scoring, token cap. |
| Windsurf | Rendering plus freeze merges plus separate TASKs. |

**Verdict: Claude wins.** Fix rendering first, prove it works, then touch storage. Mixing rendering and storage changes in Phase 1 creates two variables to debug instead of one. Freeze merges moves to Phase 2.

---

### 2.2 Active task state: stored memory or ephemeral?

| Doc | Position |
|---|---|
| GPT | Working context is NOT long-term memory. Regenerate per session. Never persist like rules or facts. |
| Claude and Windsurf | TASK is a stored memory type with persistence. |

**Verdict: GPT wins.** This is the sharpest distinction across the three documents. Active task state is mutable, session-scoped, and disposable when done. Persistent memory is durable, auditable, and cumulative. Conflating them is why action items get buried and go stale. Task state should be regenerated at each boot from a lightweight "current work" record, not retrieved from the memory corpus like a rule.

Implication: the TASK memory type can still exist for cross-session tracking (e.g., "build GPU selector before next release"), but the active session state (current task, open constraints, active assumptions, unresolved questions) should live in an ephemeral working context block regenerated at boot, not stored as a memory.

---

### 2.3 Blocker count cap

| Doc | Position |
|---|---|
| GPT | "Five blocker rules is plenty. More than that and you are back to sludge." Count cap plus token cap. |
| Claude and Windsurf | Token budget only. No count cap. |

**Verdict: GPT wins.** A token cap alone does not prevent the 50-blockers failure mode. Fifty atomic blocker headlines at 10 words each fit inside 2,000 tokens but the agent is cognitively overloaded. The correct constraint is both: count cap (5 BLOCKERs max at boot) and token budget (2,000 tokens total).

---

### 2.4 Access-based decay model

| Doc | Position |
|---|---|
| Claude | Ebbinghaus curve. Activation halves every 7 days without access. Reset on retrieval. Research-backed. |
| GPT | Strong decay for task state, weak decay for validated blocker rules. |
| Windsurf | EWMA for FACTs only. Nothing for RULEs. |

**Verdict: Claude's model, applied with GPT's nuance.** Decay rate should vary by type:
- Task state: fast decay (stale within days)
- Facts: medium decay (EWMA, Ebbinghaus-default 7-day halving)
- Rules: no decay, only supersede
- Incidents: soft archive after 90 days no-access

This directly validates the timeout rejection: time-based TTL is strictly inferior to access-based decay for rules and facts. The research literature (MemoryBank, AgeMem) confirms it.

---

### 2.5 Parallel session coordination

| Doc | Position |
|---|---|
| GPT | Task lease/lock, optimistic versioning, write conflict detection, handoff note on exit. |
| Claude | Mentions sibling sessions briefly. |
| Windsurf | Not addressed. |

**Verdict: GPT uniquely addresses this.** Without write conflict detection and a handoff note protocol, two sessions create incompatible truths. The current session registry is a start. It needs a task lease mechanism and explicit write conflict detection before multi-agent workflows are safe.

---

### 2.6 Write path enforcement

| Doc | Position |
|---|---|
| Windsurf | Five-step sequential check: type, atomicity, headline, duplicate detection (cosine 0.75 threshold), supersede routing. RULE bodies immutable. |
| Claude | Deferred to later phases. |
| GPT | Classifier step described, no specificity. |

**Verdict: Windsurf wins.** The write path is where the problem recurs. If you fix rendering but leave writes open, you are treating symptoms. The five-step sequence is the right gate. The 0.75 cosine threshold is a reasonable starting point. RULE body immutability is non-negotiable: to change a rule you supersede it, you do not append to it.

---

### 2.7 Compaction

| Doc | Position |
|---|---|
| Claude | Within-session compaction per Anthropic's pattern: when session nears context limit, summarize, reinitiate with summary. Preserve decisions, discard redundant tool output. |
| GPT and Windsurf | Not addressed. |

**Verdict: Claude uniquely addresses this.** Long sessions are a real failure mode. Compaction belongs in the design as Phase 5.

---

### 2.8 Research grounding

| Doc | Strength |
|---|---|
| Claude | Actual paper citations (A-MEM arxiv 2502.12110, MemGPT arxiv 2310.08560), LongMemEval benchmarks (Zep 63.8% vs Mem0 49.0%), Anthropic context engineering blog. |
| Windsurf | Good vendor coverage, no benchmark data. |
| GPT | Framework references, no papers. |

**Key data point from Claude worth knowing:** Zep beats Mem0 by 15 points on LongMemEval. Temporal graph beats pure vector similarity for "what is currently true" queries. This is exactly what `boot_session` answers every session. If open-brain ever considers an external memory layer, Zep is the evidence-backed choice over Mem0.

---

## 3. Unique Contributions Worth Keeping

### From Claude only
- Ebbinghaus decay model with specifics (halve every 7 days without access, reset on retrieval)
- Compaction pattern for long sessions (Phase 5, Anthropic-recommended)
- LongMemEval benchmark data validating Zep over Mem0
- Correction of SESSION_REGISTRY_DESIGN.md TTL anti-pattern confirmed by research
- Open questions for Dave (phase sequencing, Letta adoption, decay aggressiveness, tier naming)

### From GPT only
- Working context as ephemeral, not persistent memory. Regenerate per session.
- Hard count cap on BLOCKERs (5 is the suggested ceiling)
- Phase 5 parallel session coordination: task lease, optimistic versioning, write conflict detection
- Background memory reflection as a separate hot-path vs. cold-path distinction
- Framing: "This is a context presentation and lifecycle control problem, not a storage problem"

### From Windsurf only
- Immutable RULE bodies enforced at write time (not just convention)
- Five-step write path enforcement with explicit cosine similarity threshold (0.75)
- Mechanisms evaluated appendix (yes/no/verdict table)
- Constitutional AI principle-to-rule cascade for guardrail hierarchy
- Explicit field schemas for each memory type

---

## 4. Synthesized Target Architecture

### 4.1 Memory Type Taxonomy (four types, strictly enforced)

**RULE**
Single behavioral constraint. Atomic. Immutable body after creation. To modify: supersede only.
Fields: `headline` (15 words max), `body` (200 words max), `severity` (BLOCKER or PATTERN), `project` (global or scoped), `linked_incident_ids`, `supersedes`, `superseded_by`.

**FACT**
Single project or domain fact. Subject to access-based decay.
Fields: `headline`, `body`, `tags`, `ttl` (hard expiry for time-sensitive), `access_score` (EWMA), `confidence`.

**INCIDENT**
Episodic. What happened, what failed, what was fixed. Not proactively surfaced.
Fields: `timestamp`, `project`, `root_cause`, `resolution`, `linked_rule_ids`.

**TASK**
Cross-session obligation tracking (not active session state). Short-lived. Lifecycle: open, blocked, done, stale.
Fields: `content`, `project`, `priority`, `created_session`, `due_condition`.

**WORKING CONTEXT (ephemeral, not a stored memory type)**
Active session state. Regenerated at each boot, not persisted.
Contents: current task, open constraints, active assumptions, current artifacts, unresolved questions.

---

### 4.2 Severity Tiers (RULE type only)

| Tier | Boot behavior | Count cap |
|---|---|---|
| BLOCKER | Always surface, headline only | 5 max |
| PATTERN | Surface when task-relevant, headline only | Top 5 by relevance |
| CONTEXT | Retrieved on query only, never at boot | No cap |
| DEPRECATED | Never surfaced, auditable only | No cap |

---

### 4.3 Boot Payload Contract

Hard constraints: **5 BLOCKER headlines max, 2,000 tokens total.**

If BLOCKERs + active TASKs exceed budget, truncate TASKs before truncating BLOCKERs.

```
BLOCKERS (max 5)
1. <headline>
2. <headline>
...

WORKING CONTEXT
Task: <current task description>
Active constraints: <list>
Open questions: <list>
Source of truth: <reference>

ACTIVE TASKS (<project>)
- <content> [priority]
...

PATTERN RULES (top 5 task-relevant, headlines only)
- <headline>
...

INCIDENT REFERENCES (only when task touches a known failure class)
- <summary> [linked rule: #id]
...

SESSION HANDOFF (if prior session left a handoff note, 200 token max)
<handoff content>
```

---

### 4.4 Write Path Enforcement (five-step, sequential)

1. **Type declaration:** RULE, FACT, INCIDENT, or TASK required. Reject if absent.
2. **Atomicity check:** One rule or one fact per memory. Flag if multiple detected. Split before storing.
3. **Headline requirement:** Required. 15 words max. Reject if missing or over limit.
4. **Duplicate detection:** Cosine similarity against same-type memories. If similarity exceeds 0.75, route to supersede. Do not auto-merge. Do not create a duplicate alongside.
5. **Supersede enforcement (RULE type):** RULE bodies are immutable. Any correction creates a new RULE memory with `supersedes: [old_id]`. Old memory moves to DEPRECATED.

---

### 4.5 Retrieval Model

**Boot retrieval (deterministic):** All BLOCKERs (global + project-scoped, max 5), ephemeral WORKING CONTEXT regenerated from task args, active TASKs, top-5 PATTERN rules by task-relevance score, session handoff if present. No semantic search at boot.

**Task-conditioned retrieval:** Before write actions or complex tasks, semantic search against RULE store using current task as query. Returns PATTERN and CONTEXT headlines. Bodies loaded on demand via `recall(id)`.

**Incident search:** When diagnosing failures or writing new rules, semantic search against INCIDENT store by topic. Returns summaries with linked rule IDs.

---

### 4.6 Memory Decay by Type

| Type | Decay model |
|---|---|
| RULE | No decay. Supersede only. |
| FACT | Ebbinghaus access-based: EWMA score halves every 7 days without access, resets on retrieval. Move to CONTEXT tier at low score. Hard TTL for time-sensitive facts. |
| INCIDENT | Soft archive after 90 days no-access. Still searchable. Never deleted. |
| TASK | Lifecycle-based only: open, blocked, done, stale. No decay. |
| WORKING CONTEXT | Ephemeral. Discarded at session end. |

---

### 4.7 Acknowledgment Gates

**At boot:** Agent's first output must include: `BLOCKERS loaded: [N], TASKS loaded: [N].` One line. Session audit trail without friction.

**Before write actions:** Agent states which BLOCKERs are relevant to this write and confirms compliance. Two lines, fires only on writes (not reads). Worth the complexity. Do not implement for reads.

**Do not implement:** Universal per-rule acknowledgment. It produces ritual under time pressure. Ritual produces fake compliance.

---

### 4.8 Parallel Session Coordination

Before any write action in a multi-session environment:

- **Task lease:** Session claims a task before working on it. Other sessions see the claim and avoid conflicting writes.
- **Write conflict detection:** If two sessions attempt to modify the same RULE or FACT, surface the conflict before committing. Require explicit resolution.
- **Handoff note on exit:** Session writes a 200-token max handoff summary on clean exit. Loaded by the next session in the SESSION HANDOFF block.
- **Optimistic versioning:** Each memory has a version counter. Write path checks version before committing. Stale version = conflict detected.

---

## 5. Sample Boot Payload

```
BLOCKERS (5)
1. Never use em-dashes in any output.
2. Always use existing pipeline scripts for document conversion. Never bypass with inline python.
3. Before building any artifact for a company with multiple open roles, list all variants and confirm target.
4. Boot open-brain for the correct project before writing any files in that project.
5. Write-gate: state relevant BLOCKERs before any write action.

WORKING CONTEXT
Task: Synthesize three architecture docs into a recommended implementation plan
Active constraints: open-brain project scope, docs/planning location
Source of truth: claude/gpt/windsurf-memory-architecture-redesign.md

ACTIVE TASKS (open-brain)
- Implement Phase 1 boot renderer [high]
- Add GPU selector to dashboard [medium]

PATTERN RULES (task-relevant)
- Boot for the correct project before touching project files.
- Acknowledge action items before write tools unlock.
- Feature branches only. Never commit directly to main.

INCIDENT REFERENCES
- Wrong-project boot caused planning doc to be written without project guardrails (2026-04-15)
```

Total: approximately 300 tokens. Scannable in 15 seconds.

---

## 6. Phased Implementation Plan

### Phase 1: Rendering only (Claude's approach)
**1-2 days. Zero storage changes. Fully reversible.**

- Rewrite `boot_session` response shape: return headline index only
- Bodies retrievable via `recall(id)`
- Hard constraints: 5 BLOCKER max, 2,000 token total budget
- Regenerate WORKING CONTEXT from task args at boot (not from stored memories)
- Headline extraction: first sentence heuristic or optional `headline` field on `remember()`

**Success signal:** boot payload drops below 500 tokens. Agent surfaces action items correctly.

---

### Phase 2: Write path enforcement (Windsurf's approach)
**3-5 days. Touches write path and schema only.**

- Immutable RULE bodies enforced at write time
- Five-step write gate with cosine similarity duplicate detection
- Freeze merges on RULE type: route to supersede instead
- Separate TASK store with lifecycle states (open, blocked, done, stale)
- Add `headline` field as required on all new memories
- One-time canonicalization pass: rewrite existing BLOCKER-equivalent memories as clean atomic headlines. Archive originals.

**Success signal:** no new merged blobs created. Existing BLOCKERs are atomic and scannable.

---

### Phase 3: Decay and retrieval (Claude's model, GPT's nuance)
**1-2 weeks.**

- Ebbinghaus access-based decay for FACTs (EWMA, 7-day halving, reset on access)
- Task-conditioned retrieval at boot via semantic search
- Background canonicalization pipeline: periodic scan for near-duplicates, surface for human review, never auto-resolve
- Memory type taxonomy enforced at write time (type field required, validated)

---

### Phase 4: Parallel session coordination (GPT's model)
**1-2 weeks.**

- Task lease/claim mechanism in session registry
- Write conflict detection: version counter on each memory
- Handoff note protocol on clean session exit
- Optimistic versioning for concurrent writes

---

### Phase 5: Compaction (Claude's model)
**As needed.**

- Within-session compaction when approaching context limit
- Summarize, reinitiate with summary, preserve architectural decisions, discard redundant tool output
- Sub-agent summaries (1,000-2,000 tokens) for complex multi-domain work

---

## 7. Mechanisms Evaluated: Final Verdicts

| Mechanism | Worth it? | Phase | Notes |
|---|---|---|---|
| Headline-only boot rendering | Yes, highest priority | 1 | Solves 60% of the problem alone |
| Hard count cap on BLOCKERs | Yes | 1 | 5 max; token cap alone is not enough |
| Ephemeral working context | Yes | 1 | Do not store active task state as permanent memory |
| Immutable RULE bodies | Yes | 2 | Supersede only, never append |
| Write path five-step gate | Yes | 2 | Cosine 0.75 threshold, auto-route to supersede |
| Merge ban on RULE type | Yes | 2 | Root cause must be blocked at write time |
| Ebbinghaus decay for FACTs | Yes | 3 | Not time-based TTL; access-based only |
| Task-conditioned boot retrieval | Yes | 3 | High value once BLOCKERs are clean |
| Background canonicalization | Yes | 3 | Human-reviewed only, never auto-resolved |
| Parallel session write locks | Yes | 4 | Required before multi-agent workflows are safe |
| Compaction for long sessions | Yes | 5 | Anthropic-recommended; Phase 5 only |
| Full per-rule acknowledgment | No | Never | Produces ritual skimming, not compliance |
| Time-based TTL on rules | No | Never | Research-rejected; access-based only |
| Graph database (Cognee) | Not yet | Future | File for later if flat model hits scale limits |
| Agent-managed eviction (Letta) | Not yet | Future | Too complex for current maturity |
| Universal merge | Never | Never | This is the root cause. Do not rehabilitate it. |

---

## 8. Blunt Bottom Line

**Claude's document is the best-researched.** Ebbinghaus decay model, compaction pattern, and LongMemEval benchmark data are all unique contributions.

**GPT's document has the two sharpest architectural insights** neither other doc captured: ephemeral working context (not stored memory) and the blocker count cap. These change the shape of Phase 1 meaningfully.

**Windsurf's document has the most actionable write-path specification.** Immutable RULE bodies and the five-step gate are the correct enforcement mechanism.

**If you could only take one thing from each:** Claude gives you the decay model. GPT gives you the ephemeral task state distinction. Windsurf gives you the write gate.

**Do Phase 1 first.** Rendering-only. No storage changes. Measure the token reduction. If boot payload drops from 15,000 tokens to under 500, the direction is proven and Phase 2 is clearly justified.

**Do not build all of this at once.** Each phase stands alone. Phase 1 delivers most of the value. Phases 2-5 compound on it.
