# Windsurf: Open Brain Memory Architecture Redesign
**Author:** Windsurf (Cascade)
**Date:** 2026-04-15
**Status:** Proposal — for review alongside Claude Code parallel analysis

---

## 1. Executive Diagnosis

The system has one root failure: it was designed with only an append path and no canonical form. Every correction enters as an additive write. Similar memories merge into larger blobs. Merged blobs are never split or archived. The result is a system that degrades monotonically over time regardless of how well individual memories are written.

The seven observed failure modes are all symptoms of this single root cause. This is not a guardrail problem. It is a data model problem. The guardrails themselves are fine. The structure that holds them is wrong.

Two secondary failures compound this:

**No rendering budget.** The system has no hard contract on how much it loads at boot. The boot payload grows with the memory corpus. This directly violates how attention works in LLMs. There is a well-documented "lost in the middle" failure mode (Liu et al., 2023) where content in the middle of a long context window receives significantly less attention than content at the edges. The most important rules, if buried in position 3-8 of a long merged block, are functionally invisible.

**No lifecycle model.** Memories have no type, no severity, no TTL, no state. A rule, a fact, an incident narrative, and an open action item are all stored identically. Retrieval cannot distinguish them. Boot cannot prioritize them.

The proposed direction (principles A through G) is correct directionally. It maps closely to what serious practitioners are building. The gaps are in (1) write-path enforcement that makes the model durable and (2) the missing decay and archiving pipeline.

---

## 2. Survey of Relevant Current Approaches

### MemGPT / Letta (most directly relevant)

MemGPT (Packer et al., 2023, now productized as Letta) introduced an OS-inspired virtual memory model for agents. Three tiers: main context (in-window, limited), recall storage (searchable episodic history), and archival storage (unlimited, retrieved explicitly). The agent itself manages eviction and retrieval.

The critical insight: the agent must be aware of its own memory limits and actively manage what stays in context. The current system does not give the agent that awareness or the tooling to act on it.

**Take:** the three-tier hierarchy and explicit eviction concept. **Skip:** agent-managed eviction adds complexity and latency. Pre-filtering at boot is simpler and sufficient for this use case.

### Zep

Session and user-level memory for AI applications. Stores episodic memory (conversation history), semantic memory (extracted facts), and uses temporal modeling: facts have a `valid_time` and can be superseded. Their temporal memory model is the closest production implementation to what is needed here.

Key mechanism: every fact has "valid from" and "valid until" timestamps. Retrieval always returns the current-truth version.

**Take:** temporal validity on facts, semantic extraction from conversations, clean separation between episodic (what happened) and semantic (what is true).

### Mem0

Most widely deployed memory layer for AI agents. Has the same additive problem described in the diagnosis. Their v1.1 introduced conflict detection that attempts to resolve contradictions, but in practice it merges rather than supersedes. Instructive failure: conflict detection without a canonical form just produces better-formatted walls of text.

**Take:** conflict detection approach. **Do not take:** their resolution strategy.

### Cognee

Builds a knowledge graph from memories rather than a flat vector store. Relationships are explicit edges. This solves hierarchy collapse differently: instead of flattening everything into blobs, it maintains a graph of atomic facts linked by relationships.

Verdict for this use case: overkill now. File as a future migration target if the flat model hits limits at scale.

### LangMem (LangChain)

Has a supersede concept built in and supports background memory consolidation pipelines. Less mature than Zep. The consolidation pipeline concept (background process that periodically deduplicates and canonicalizes) is the right direction.

**Take:** background consolidation pipeline concept.

### Constitutional AI (Anthropic)

Not a memory system, but the hierarchical rule cascade is directly applicable to guardrail design. High-level principles (BLOCKERs) cascade to specific rules (PATTERNs). A violation of a specific rule is explainable by reference to the principle it violates.

**Take:** the principle-to-rule cascade for guardrail hierarchy.

### "Lost in the Middle" (Liu et al., 2023)

LLMs perform significantly worse on retrieval from the middle of a long context versus the beginning or end. The practical implication: if the boot payload puts critical rules in positions 4-12 of a merged block, those rules are functionally degraded. Boot rendering must front-load BLOCKERs before anything else and enforce a hard payload size limit.

---

## 3. Proposed Target Architecture

### 3.1 Memory Type Taxonomy

Four types. Strictly enforced at write time. No hybrid types.

**RULE**
A single behavioral constraint. Atomic. One rule per memory.
Fields: `headline` (15 words max), `body` (200 words max), `severity` (BLOCKER or PATTERN), `project` (global or project-scoped), `linked_incident_ids` (optional, not loaded by default), `version_chain` (superseded chain, auditable).

**FACT**
A single project or domain fact. Examples: file paths, technology choices, team structure, architectural decisions.
Fields: `headline`, `body`, `tags`, `ttl` (hard expiry for time-sensitive, soft decay for stable), `confidence_score`.

**INCIDENT**
What happened, what was wrong, what was fixed. Episodic. Not proactively surfaced. Searchable by topic.
Fields: `timestamp`, `project`, `involved_parties`, `root_cause`, `resolution`, `linked_rule_ids`.

**TASK**
Open action items. Short-lived. Expires when complete or when project closes.
Fields: `content`, `project`, `priority`, `created_session`, `due_condition` (logical condition, not a date).

---

### 3.2 Severity Tiers (RULE type only)

| Tier | Behavior | Boot |
|---|---|---|
| BLOCKER | Always surface at boot, headline only. Non-negotiable. | Yes, always |
| PATTERN | Behavioral defaults. Surface when task-relevant. | Conditional |
| CONTEXT | Project/domain facts embedded in rules. Retrieved on query only. | No |
| DEPRECATED | Archived. Never surfaced. Auditable and recoverable. | Never |

---

### 3.3 Boot Payload Contract

**Hard size budget: 2,000 tokens maximum. Non-negotiable.**

If BLOCKERs + active TASKs exceed budget, truncate TASKs before truncating BLOCKERs.

Boot payload structure (strict ordering):

```
BLOCKERS (N)
1. <headline>
2. <headline>
...

ACTIVE TASKS (<project>)
- <content> [priority]
...

PATTERN RULES (3-5 most recently accessed, headlines only)
- <headline>
...

SESSION HANDOFF (if prior session ended with handoff note, 200 token max)
<handoff content>
```

That is the entire boot payload. No bodies. No incident history. No merged narrative context. If the agent needs the body of a rule, it fetches by ID. If it needs incident context, it searches.

---

### 3.4 Write Path Enforcement

Every new memory write triggers five checks in sequence:

1. **Type classification:** Is this RULE, FACT, INCIDENT, or TASK? Reject writes that do not declare type.

2. **Atomicity check:** Does this memory contain more than one rule or fact? Enforce by length and semantic density heuristic. If flagged, split before storing.

3. **Headline requirement:** Headline field required. Reject if missing or exceeds 15 words.

4. **Duplicate detection:** Compute cosine similarity against existing memories of same type. If similarity exceeds 0.75, flag as potential duplicate. Do not auto-merge. Require an explicit supersede decision.

5. **Supersede path (RULE type):** New memory replaces old. Old moves to DEPRECATED. Superseded memory retains its ID, timestamps, and content for audit. New memory carries `supersedes: [old_id]`. Merge is not a valid operation for RULE type.

---

### 3.5 Retrieval Model

Three retrieval contexts:

**Boot retrieval:** Deterministic. All BLOCKERs (global + project-scoped). Active TASKs for current project. Top 3-5 recently accessed PATTERN rules. Session handoff if present. No semantic search at boot.

**Task-conditioned retrieval:** Before a write action or complex task, agent searches RULE store with task description as query. Returns top-N PATTERN and CONTEXT rules by relevance. Agent loads bodies on demand.

**Incident search:** When diagnosing a failure or writing a new rule, agent searches INCIDENT store by topic. Returns incident summaries with linked rule IDs. Bodies retrievable on demand.

---

### 3.6 Memory Decay and Archiving

| Type | Decay behavior |
|---|---|
| RULE | Never decays. Only supersede or deprecate. |
| FACT | Soft decay via EWMA access scoring. Hard TTL for time-sensitive facts. Move to CONTEXT tier after 90 days no-access + low confidence. |
| INCIDENT | Soft archive after 90 days no-access. Still searchable. Never deleted. |
| TASK | Expires on completion or project close. No decay, just lifecycle state. |

---

### 3.7 Preventing Corrective Drift

The root cause of corrective drift is writing corrections at the wrong level. A single incident should not modify a rule body. It should create a new INCIDENT record and potentially trigger a rule edit via the supersede path.

**Enforcement: RULE bodies are immutable after creation.** To modify a rule, you must supersede it with a new version. This creates an explicit change history and prevents gradual drift through accumulated appends.

**Background canonicalization (Phase 3):** Periodic pipeline that scans RULE memories for semantic similarity, flags near-duplicates for human review, and enforces headline length compliance. Does not auto-resolve. Surfaces candidates.

---

### 3.8 On Acknowledgment Gates

Full per-rule acknowledgment (original option G) adds friction that will be skipped under time pressure. That is exactly the condition where you most need it to work. Do not implement it this way.

**Lightweight alternative (recommended):** At boot, agent's first action must include a structured acknowledgment: "BLOCKERS loaded: [N], TASKS loaded: [N]." One line. Session-level audit trail without blocking work.

**Write-gate acknowledgment (worth the complexity):** Before any write action (not read), agent states which BLOCKER rules are relevant to this write and confirms compliance. Two lines, fires only on writes. This version is worth implementing.

---

## 4. Phased Implementation Plan

### Phase 1: Stop the bleeding (1-2 days, highest leverage, lowest risk)

Changes that do not require rebuilding anything. They constrain the existing system.

1. Freeze merges on RULE type. Make merge an invalid operation for any memory declared as RULE. New corrections must go through supersede only.
2. Add headline field. Make it required on all new memories. Backfill existing memories gradually.
3. Tighten boot payload. Hard-cap at 2,000 tokens. Reorder so BLOCKERs appear first, always.
4. Create TASK as a separate type. Move all open action items out of RULE and FACT memories into TASK entries.

**Expected result:** boot payload shrinks significantly. BLOCKERs become visible. Active tasks are findable.

---

### Phase 2: Introduce structure (1 week, medium complexity)

5. Enforce memory type taxonomy. Add type field as required. Validate at write time.
6. Implement supersede-on-overlap detection. Cosine similarity check at write time for RULE type. Flag above 0.75. Do not auto-resolve.
7. Canonicalize existing BLOCKERs. One-time pass: extract all current BLOCKER-equivalent memories, rewrite each as a single atomic rule with a clean headline. Archive the originals.
8. Add TTL to FACT type. Identify time-sensitive facts and set hard TTL.

**Expected result:** no new merged blobs are created. Existing BLOCKERs are clean and atomic. Write path is defensible.

---

### Phase 3: Active memory management (2-4 weeks, higher complexity)

9. Task-conditioned retrieval at boot. Match task description against PATTERN rules via semantic search. Load top 3-5 relevant PATTERN headlines alongside BLOCKERs.
10. Background canonicalization pipeline. Periodic scan for near-duplicate rules. Surface candidates for human review. Do not auto-resolve.
11. Memory decay for FACTs. Implement EWMA access scoring. Move low-score FACTs to CONTEXT tier.
12. Lightweight write-gate acknowledgment. Before write actions, require agent to state relevant BLOCKERs and confirm compliance.
13. Incident store separation. Move all incident narratives from RULE memories to a separate INCIDENT store. Link by ID.

---

## 5. Sample Boot Payload: Before vs. After

### Before (current, failure mode)

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

[GUARDRAIL #5049]
GUARDRAIL: When writing recommendation request letters to senior technical 
leaders or engaging in feedback discussions with individuals like Dave, 
avoid both an overly explanatory tone and sycophantic agreement...
[continues for 300+ tokens]
```

The rule is buried inside its own incident history. The headline is not the first sentence. The agent must read 800+ tokens to extract 2 rules.

---

### After (proposed)

```
BLOCKERS (7)
1. Never use em-dashes in any output.
2. Always use existing pipeline scripts for document conversion. Never bypass with inline python.
3. All source .txt files must live in sources/ subfolders. Final .docx in parent folder.
4. Before building any artifact for a company with multiple open roles, list all variants and confirm target.
5. Never write recommendation letters in an explanatory tone to senior technical leaders.
6. Always search brain for task topic and user formatting rules at session start.
7. Write-gate: state relevant BLOCKERs before any write action.

ACTIVE TASKS (resume-creator)
- Update flashcard app for correct Netflix role [high]

PATTERN RULES (recently accessed)
- Use Tier 3 deep model for resume tailoring by default.
- candidate_profile.md is single source of truth; never read 15+ source docs.
- Boot phase must include two searches: task topic and user preferences.
```

Total: approximately 280 tokens. BLOCKERs are scannable in 10 seconds. Incident context for any rule is one fetch by ID.

---

## 6. Blunt Recommendation: What to Do First

**Do Phase 1 today.** It is the only change that stops the problem from getting worse before the root cause is fixed. Freezing merges on RULE type and hard-capping boot at 2,000 tokens costs almost nothing to implement and immediately improves signal clarity.

**The canonicalization pass (Phase 2, step 7) is the highest-leverage single action.** Existing BLOCKER-equivalent memories are merged narrative blobs. Rewriting each as a clean atomic headline with incident history archived but not loaded will have the largest visible impact on agent behavior.

**Do not implement acknowledgment gates until Phase 2 is complete.** Right now, gates would fire against the same wall-of-text BLOCKERs. That produces compliance theater, not compliance. Gates only work when the rules are clean enough to actually read in real time.

**One implementation note:** the supersede mechanism already exists (`mcp0_supersede`). It is not being used consistently because there is no enforcement at write time that prevents `mcp0_remember` from creating a duplicate alongside an existing memory instead of superseding it. The fix is a pre-write similarity check that routes to supersede when overlap is detected. That is a code-level guard, not a prompt-level request. This is the single most important code change to make.

---

## Appendix: Mechanisms Evaluated

| Mechanism | Worth the complexity? | Verdict |
|---|---|---|
| Acknowledgment gates (full) | No | Too high friction; skipped under pressure |
| Acknowledgment gates (lightweight write-gate) | Yes | Two lines, fires on writes only |
| Relevance scoring at boot | Yes (Phase 3) | High value once BLOCKERs are clean |
| Memory decay for RULEs | No | Rules do not decay; only supersede |
| Memory decay for FACTs | Yes (Phase 3) | EWMA scoring, TTL on time-sensitive |
| Canonicalization pipeline | Yes (Phase 3) | Must be human-reviewed, not auto-resolved |
| Graph-based memory (Cognee) | Not yet | File for future if flat model hits limits |
| Agent-managed eviction (MemGPT) | No | Too complex for current system maturity |
| Merge on RULE type | Never | Root cause of the problem; must be blocked |
