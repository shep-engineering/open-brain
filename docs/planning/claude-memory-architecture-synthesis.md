# Open Brain Memory Architecture: Synthesis

> **Status:** Proposed, synthesized from three parallel agent proposals.
> **Author:** Claude (resume-creator session), synthesizing work by Claude, GPT, and Windsurf.
> **Date:** 2026-04-15
> **Supersedes:** [`claude-memory-architecture-redesign.md`](claude-memory-architecture-redesign.md). Where this document disagrees with v1, this document wins.
> **Companions (kept for audit):**
> - `claude-memory-architecture-redesign.md` (Claude v1, research-heavy proposal)
> - `gpt-memory-architecture-redesign.md` (GPT proposal, governance framing)
> - `windsurf-memory-architecture-redesign.md` (Windsurf proposal, implementation-heavy)

---

## 0. What this document is

Shep asked three agents (Claude, GPT, Windsurf) to independently design a fix for open-brain's walls-of-text-at-boot problem after a session where buried action items caused a ~$500K opportunity miss and a TTL sweep broke a live session. This document is the synthesis of those three proposals, with explicit adjudication of the disagreements.

The three proposals converge on ~80% of the answer. The remaining 20% is where they disagree, and some of those disagreements are load-bearing. This document picks the strongest position on each.

---

## 1. Top-line finding

The three proposals agree that:

1. The current brain accumulates corrections additively with no subtractive mechanism, producing walls of merged text.
2. The fix is a data model change, not a storage scaling change.
3. Atomic memories with supersession (not merge) is the root fix.
4. Memory types must be separated and enforced at write time.
5. Boot payload must be small, headline-only, hard-capped.
6. Action items belong in a separate store, not in rule memories.
7. Incidents (episodic records) are searchable but not preloaded.
8. Background canonicalization should flag duplicates for human review, never auto-resolve.

They diverge meaningfully on four questions. This synthesis picks the winning answer for each.

---

## 2. Adjudicated disagreements

### 2.1 Do RULEs decay?

- **Claude v1:** Yes, access-based Ebbinghaus curve across all memory types.
- **Windsurf:** No. Rules are immutable. Supersede or deprecate, nothing else.
- **GPT:** Decay selectively by type.

**Winner: Windsurf.** Rules are not facts. A rule's relevance doesn't drop because it wasn't cited recently. Applying decay to rules would surface the failure mode Shep and the desktop Claude spent hours fixing (valid rules disappearing unpredictably). Access-based decay belongs on FACTs and INCIDENTs, not RULEs.

### 2.2 Should acknowledgment gates be universal or narrow?

- **Claude v1:** Broad, per-blocker, enforced at boot (Phase 5).
- **GPT:** Narrow. Only before risky writes. Universal gates become ceremony, skimming, fake compliance.
- **Windsurf:** Lightweight. One-line boot ack, two-line write-gate. No per-rule acknowledgment.

**Winner: GPT + Windsurf.** Ritualized acknowledgment is a worse failure than no acknowledgment because it produces theatrical compliance. The write-gate version catches the actual risk points (write actions) without poisoning every boot with ceremony.

### 2.3 Agent-managed memory ops vs server-side enforcement?

- **Claude v1:** Letta-style. Agent owns supersede, archive, link, summarize, split via explicit tool calls.
- **Windsurf:** Server-side supersede-on-overlap is enough. Add agent tools later if needed.
- **GPT:** Mixed, leaning server-side.

**Winner: Windsurf.** The agent has demonstrated it will skip steps under pressure. Enforcement has to live in the store, not in agent discipline. Letta-style agent tools are a Phase 4 extension, not a Phase 1 dependency.

### 2.4 Is this a rendering problem or a write-path problem?

- **Claude v1 + GPT:** Rendering first, write-path later.
- **Windsurf:** Write-path first. Specifically, the pre-write similarity check that routes to supersede instead of merge is the single most important code change.

**Winner: Windsurf, and this is the most important disagreement.** A better renderer applied to merged walls is cosmetic. Blocking new merges at write time is the only way to stop the corpus from getting worse while everything else is being built. Claude v1's "rendering only" Phase 1 would let the underlying corruption keep accumulating.

---

## 3. Synthesized data model

Four atomic types, enforced by a required `type` field at write time. Hybrid types are invalid.

### 3.1 RULE

A single behavioral constraint. Immutable after creation. Modification requires supersede.

**Fields:**
- `id` (stable)
- `headline` (≤15 words)
- `body` (≤200 words)
- `severity`: `BLOCKER` | `PATTERN` | `DEPRECATED`
- `project` (global or project-scoped)
- `linked_incident_ids` (optional, not loaded by default)
- `supersedes` / `superseded_by` (version chain)
- `validation_status`
- `created_at` (timestamps, no decay)

**Lifecycle:** immutable. Never decays. Modify only via supersede; old rule moves to `DEPRECATED`, retains ID and content for audit.

### 3.2 FACT

A single project or domain fact. Examples: file paths, technology choices, architectural decisions.

**Fields:**
- `id`, `headline`, `body`, `tags`
- `ttl` (hard expiry for time-sensitive facts; absent for stable)
- `confidence_score`
- `last_accessed_at`, `access_count` (for EWMA decay)

**Lifecycle:** decays via access-based EWMA. Hard TTL on time-sensitive entries. Demote to CONTEXT tier after 90 days no-access plus low confidence.

### 3.3 INCIDENT

Episodic record: what happened, what was wrong, what was fixed.

**Fields:**
- `timestamp`, `project`, `involved_parties`
- `root_cause`, `resolution`
- `linked_rule_ids` (which rules this incident informed)

**Lifecycle:** soft archive after 90 days no-access. Searchable by topic and linked rule. Never deleted. Never preloaded.

### 3.4 TASK

Open obligations. Not memory. Separate lifecycle state machine.

**Fields:**
- `content`, `project`, `priority`
- `created_session`
- `due_condition` (logical condition, not a date)
- `status`: `open` | `blocked` | `done` | `stale`

**Lifecycle:** no decay. Lifecycle state transitions only. Expires on completion or project close.

---

## 4. Write path: five checks, non-negotiable

Every new memory write passes this gauntlet before it can land:

1. **Type declared and valid.** Reject writes missing the `type` field.
2. **Atomicity check.** Does this memory contain more than one rule or fact? Length + semantic density heuristic. If flagged, split before storing.
3. **Headline present and ≤15 words.** Reject if missing or too long.
4. **Duplicate detection.** Compute cosine similarity against existing same-type memories. Similarity >0.75 flags the supersede path.
5. **Supersede, not merge, for RULE type.** New memory replaces old. Old moves to `DEPRECATED`, retains ID and content, carries `superseded_by` link. **Merge is an invalid operation for RULE type.**

This is the critical enforcement point. Everything else is downstream of this working.

---

## 5. Boot payload contract

**Hard cap: 2,000 tokens.** Non-negotiable. If BLOCKERs plus ACTIVE TASKs exceed budget, truncate TASKs before BLOCKERs.

Strict ordering:

```
BLOCKERS (N, headlines only)
  1. <headline>
  2. <headline>
  ...

ACTIVE TASKS (<project>)
  - <content> [priority]
  ...

PATTERN RULES (3-5 task-relevant, headlines only, semantic-retrieved against task arg)
  - <headline>
  ...

SESSION HANDOFF (if prior session ended with handoff, ≤200 tokens)
  <handoff content>
```

No bodies. No incident narratives. No merged archaeology. Agent fetches bodies by ID via `recall(id)`.

### 5.1 Why hard-cap matters

Per Liu et al. (2023) "Lost in the Middle," LLMs perform significantly worse on retrieval from the middle of long contexts versus the edges. Critical rules buried at position 4-12 of a long merged block are functionally invisible to the agent. The only defense is a hard size cap with BLOCKERs front-loaded.

---

## 6. Retrieval model

Three retrieval contexts:

**Boot retrieval (deterministic):** All BLOCKERs (global plus project-scoped). Active TASKs for current project. Top 3-5 task-relevant PATTERN rules via semantic match against `task` arg. Session handoff if present. No incident retrieval at boot.

**Task-conditioned retrieval (semantic):** Before a write action or complex task, agent searches RULE and FACT stores with task description as query. Returns top-N results. Agent loads bodies on demand via `recall(id)`.

**Incident search (explicit):** When diagnosing a failure or writing a new rule, agent searches INCIDENT store by topic. Returns summaries with linked rule IDs. Bodies retrievable on demand.

---

## 7. Decay and archiving

| Type | Decay policy |
|---|---|
| RULE | Never decays. Supersede or deprecate only. |
| FACT | Soft decay via EWMA access scoring. Hard TTL for time-sensitive. |
| INCIDENT | Soft archive after 90 days no-access. Still searchable. |
| TASK | No decay. Lifecycle state only. |

Access resets the EWMA decay curve for FACTs. This is the only place Ebbinghaus-style decay belongs.

---

## 8. Acknowledgment gates: minimal, not universal

Two gates only:

**Boot acknowledgment.** One line, structured. First response after `boot_session`:
```
BLOCKERS loaded: N. TASKS loaded: N. Handoff present: yes/no.
```

**Write-gate acknowledgment.** Before any write tool call, two lines:
```
Relevant BLOCKERs: [list of IDs]. Compliance: confirmed.
```

No per-rule acknowledgment. No ceremony. The write-gate fires on writes only, which is where real risk lives. Universal gates produce theatrical compliance, which is worse than no gate.

---

## 9. Background canonicalization

Periodic pipeline (scheduled, not inline):

- Scan RULE memories for semantic similarity clusters
- Flag oversized records (>200 word body, >15 word headline)
- Detect FACTs with stale access patterns ready for demotion
- Identify recurring INCIDENT patterns that suggest rule refinement

**Surfaces candidates for human review. Does not auto-resolve.** The auto-merge failure Shep lived through is the canonical case against auto-resolution.

---

## 10. Phased implementation: ordered by ROI

### Phase 1: Stop the bleeding (1-2 days, highest leverage)

1. **Block merges on RULE type at write time.** Single most important code change. The existing `mcp0_supersede` tool works; `mcp0_remember` currently creates duplicates alongside existing memories instead of routing to supersede. Add the pre-write cosine similarity check. Route >0.75 similarity to supersede. Without this, everything else is cosmetic.
2. Hard-cap boot payload at 2,000 tokens. Reorder BLOCKERs first. Truncate TASKs before BLOCKERs if over budget.
3. Extract TASKs to a separate store. Action items are obligations, not memories.

### Phase 2: Introduce structure (1 week)

4. **Canonicalize existing BLOCKER-equivalent memories.** One-time pass. Rewrite each merged wall as an atomic rule with clean headline. Archive merged originals (retain for audit). Human-in-the-loop. This is the highest-impact single task after Phase 1 lands.
5. Enforce `type` field on new writes. Reject writes missing type classification.
6. Require `headline` on new writes. ≤15 words.
7. Add hard TTL to time-sensitive FACTs.

### Phase 3: Active memory management (2-4 weeks)

8. Task-conditioned semantic retrieval at boot. Match `task` arg against PATTERN rules; load top 3-5 headlines.
9. Access-based EWMA decay for FACTs only.
10. Background canonicalization pipeline. Human-reviewed.
11. Write-gate acknowledgment (after Phase 2 — rules must be clean enough to read in real time first).
12. INCIDENT store separation. Move incident narratives out of RULE bodies into searchable INCIDENT store with rule links.

### Phase 4: Optional extensions

13. Agent-managed memory ops (Letta-style): `supersede`, `archive`, `link`, `summarize_cluster`, `split`. Only if Phases 1-3 prove insufficient.
14. Compaction and sub-agent patterns from Anthropic's playbook. Only for long multi-hour sessions where Phase 1-3 hygiene doesn't solve context pressure.

---

## 11. Sample boot payload: before vs after

### Before (current failure mode)

```
[GUARDRAIL #840] (note)
Interview prep guides, including the one for Inspire11 that resonated
with David's values and incorporates notable Netflix open source projects,
are created in Word format and included in Tier 1/2/3 output lists,
utilizing scripts/interview_prep_to_docx.py...

Update: GUARDRAIL 2026-04-13: When updating an existing document in
resume-creator, ALWAYS use the existing pipeline scripts... Dave was
frustrated when I tried to bypass the established pipeline twice in one
session. WHY: The pipeline scripts encode the project's styling...

Update: Interview prep documents stored in C:\Users\DAVE\...
[continues for 400+ tokens]
...
```

Agent reads 800+ tokens to extract 2 rules. Action item buried at position 9.

### After (proposed)

```
BLOCKERS (7)
1. Never use em-dashes in any output.
2. Always use existing pipeline scripts for document conversion.
3. Source .txt files live in sources/ subfolders. Final .docx in parent.
4. For multi-role companies, list variants and confirm target before building.
5. No explanatory tone in recommendation letters to senior technical leaders.
6. At session start, search brain for task topic and user formatting rules.
7. State relevant BLOCKERs before any write action.

ACTIVE TASKS (resume-creator)
- Update flashcard app for correct Netflix role [high]

PATTERN RULES (task-relevant)
- Use Tier 3 deep model for resume tailoring by default.
- candidate_profile.md is single source of truth.
- Boot phase includes task topic and user-preferences searches.
```

~280 tokens. BLOCKERs scannable in 10 seconds. Incident context one `recall(id)` away.

---

## 12. What is explicitly retracted from Claude v1

Three positions from the v1 doc are wrong and are retracted here:

1. **Access-based decay across all memory types.** Rules don't decay. Decay belongs on FACTs only. Windsurf's position is correct.
2. **Broad per-blocker acknowledgment gates as Phase 5.** Would become ritual. GPT and Windsurf are right: narrow write-gate is the only acknowledgment worth implementing.
3. **Agent-managed memory ops (Letta-style) as foundational.** Too complex for current state. Server-side write enforcement is the right Phase 1. Agent tools are a Phase 4 extension.

Claude v1's only strongest contribution was research grounding (A-MEM, Zep LongMemEval benchmark, Anthropic context engineering guidance). That's preserved here as context but doesn't drive the design.

---

## 13. One-sentence synthesis

Build Windsurf's data model and write-path enforcement first; use GPT's governance framing and separation of action items; apply access-based decay only to facts; never ritualize acknowledgment; and ship the merge-to-supersede enforcement at write time before anything else.

---

## 14. Open questions back to Shep

1. Should `mcp0_remember` reject writes missing `type`, or auto-classify with a flag for human review? (Strict vs. permissive transition.)
2. Decay half-life for FACTs: 7 days (Ebbinghaus default) or longer for project-context facts? Facts about team structure should probably decay slower than session-local facts.
3. The one-time canonicalization pass on existing BLOCKERs needs human review per rule. Want to do that in one dedicated session or spread across normal sessions as rules get touched?
4. Should the write-gate acknowledgment be enforced by the MCP server (blocks the tool call until ack fires) or by hook (soft reminder)? Hard enforcement is stricter but adds failure modes.

---

## 15. Sources

Same as Claude v1 plus the three sibling proposals:

- [Anthropic: Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [A-MEM: Agentic Memory for LLM Agents (arxiv 2502.12110)](https://arxiv.org/pdf/2502.12110)
- [MemGPT / Letta (arxiv 2310.08560)](https://arxiv.org/abs/2310.08560)
- [Liu et al., Lost in the Middle, 2023](https://arxiv.org/abs/2307.03172)
- [Agent Memory: Letta vs Mem0 vs Zep vs Cognee](https://forum.letta.com/t/agent-memory-letta-vs-mem0-vs-zep-vs-cognee/88)
- [Zep's LongMemEval benchmark response](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)
- [Context Engineering (Weaviate)](https://weaviate.io/blog/context-engineering)
- Sibling agent proposals: `claude-memory-architecture-redesign.md`, `gpt-memory-architecture-redesign.md`, `windsurf-memory-architecture-redesign.md`
