# GPT Memory Architecture Synthesis

Objectively, the three proposals are mostly aligned on the diagnosis and the first moves. The differences are in where they put the center of gravity.

**Claude’s proposal** is the strongest on external research grounding and the clearest on the macro principles: just in time retrieval, atomic notes, access based salience instead of time based TTL, and explicit agent managed memory operations. It also has the best argument for why a rendering first change is the highest ROI move. Its biggest weakness is that it leans a bit too far toward agent managed memory semantics as a later state, which is elegant but adds operational complexity before the write path is truly disciplined.

**GPT’s proposal** is the strongest on system shape and sequencing discipline. It cleanly separates policy memory, working context, knowledge memory, and incident log, and it is right to say this is primarily a context presentation and lifecycle control problem, not just a storage problem. It also correctly insists that action items are not “memory” and should be treated as obligations with their own lifecycle. Its weakness is that it is a little lighter than Windsurf on hard enforcement mechanisms at write time.

**Windsurf’s proposal** is the strongest on implementation rigor. It gives the most concrete write path enforcement model: required type, atomicity check, headline requirement, duplicate detection, explicit supersede path, immutable rule bodies, and a hard boot contract. It also gives the clearest rejection of merge as a valid RULE operation. Its weakness is that some parts are a bit too rigid too early, especially the fixed 2,000 token budget and the deterministic boot retrieval model without enough task relevance in the first phase.

## Best combined architecture

Take GPT’s four lane model as the top level architecture:

- **Policy memory** for rules
- **Working context** for current task state
- **Knowledge memory** for durable facts
- **Incident log** for historical failures and lessons

That is the cleanest mental model and solves the “four things jammed into one object called memory” problem.

Then take Windsurf’s write path enforcement almost verbatim for durability:

- every write must declare type
- no hybrid records
- headline required
- atomicity enforced
- duplicate check before write
- RULE records cannot merge
- RULE changes must go through supersede
- incident narratives do not get appended into rule bodies
- TASK is its own type, not buried inside other records

Then take Claude’s retrieval and salience model for intelligence and long term maintainability:

- boot should be lean and headline only
- full bodies come in just in time
- access based salience beats time based TTL
- task relevance should determine which non blocker items surface
- compaction and structured note taking should be explicit patterns, not accidental side effects

## What I would actually build

### 1. Canonical data model

Use these four record types:

**RULE**
- id
- headline
- canonical_rule
- severity: blocker | pattern
- scope: global | project
- tags
- supersedes / superseded_by
- linked_incident_ids
- created_at
- last_accessed_at
- access_count
- status: active | deprecated

**TASK**
- id
- content
- project
- priority
- state: open | blocked | done | stale
- created_session
- handoff_notes

**FACT**
- id
- headline
- body
- tags
- confidence
- validity_start
- validity_end optional
- decay_class: stable | time_sensitive
- last_accessed_at
- access_count

**INCIDENT**
- id
- timestamp
- project
- summary
- root_cause
- resolution
- linked_rule_ids
- linked_task_ids optional

This combines GPT’s lane separation with Windsurf’s type rigor and Claude’s temporal and salience thinking.

### 2. Boot contract

Boot should never load full memory bodies. It should load:

- all active blocker RULE headlines for global and project scope
- open TASKs for the active project
- top 3 to 5 relevant pattern RULE headlines based on task query
- top 3 to 5 relevant FACT headlines if needed
- optional session handoff
- optional incident references only as IDs and one line summaries

This is closer to GPT and Claude than Windsurf. Windsurf is right that there needs to be a hard budget, but I would not make it a blind fixed 2,000 tokens from day one. I would make it budgeted and measured, with a default target of 500 to 1,200 tokens and a hard upper bound if needed. Claude’s rendering first approach is the safest first move.

### 3. Merge policy

This is where all three are directionally right, and Windsurf is strongest.

My synthesized answer:

- **RULE: merge forbidden**
- **TASK: merge forbidden**
- **INCIDENT: merge discouraged, link instead**
- **FACT: merge allowed only for exact same fact with same semantic subject and same time validity window**

For RULE updates:
- old rule stays auditable
- new rule becomes canonical
- supersede chain is explicit
- rule body is effectively immutable once active, except through supersede

That is the cleanest way to kill wall growth.

### 4. Salience and decay

Claude is right that TTL style expiration is the wrong default. Windsurf is right that not all types should decay the same way.

So:

- **RULE** does not decay. It only gets superseded or deprecated.
- **TASK** does not decay. It changes state.
- **FACT** uses access based salience, with optional validity windows for time sensitive facts.
- **INCIDENT** becomes archival by default unless linked to an active rule or recently accessed.

This rejects time based liveness sweeps for memory surfacing while still allowing time sensitive facts to expire when they genuinely stop being true.

### 5. Acknowledgment gates

GPT and Windsurf are both right to reject universal acknowledgment theater.

Best synthesized version:
- no per rule acknowledgment at session start
- one lightweight boot acknowledgment like: “Loaded 3 blockers, 2 tasks, 4 relevant patterns”
- before any write or destructive action, emit a short structured check naming relevant blockers
- no gate on read only actions

That gives you auditability without ritualized fake compliance.

## What each proposal got wrong or overstated

Claude overweights external framework alignment a bit. Letta style agent managed memory is useful inspiration, but you do not need to adopt that philosophy wholesale to fix this problem. The immediate issue is not “the agent needs more semantic control.” The immediate issue is “the store and renderer are letting garbage accumulate and present badly.”

GPT slightly understates how much the write path matters. It is correct that rendering and lifecycle control are the first move, but without Windsurf style enforcement, the system will keep generating sludge faster than you can render around it.

Windsurf slightly over constrains early implementation. A fully deterministic boot retrieval and hard coded 2,000 token contract are clean on paper, but I would start with measured budgets and task relevance sooner, because your actual failure mode is not just “too much text,” it is “wrong text loaded with equal weight.”

## The best phased plan

### Phase 1
Rendering and separation first.

- create TASK as a separate type immediately
- boot becomes headline only
- blockers first
- tasks next
- pattern and fact headlines selected by task relevance
- no bodies at boot
- no incident bodies at boot
- instrument token counts and retrieval hit rates

This takes GPT’s and Claude’s advice and fixes the user visible failure fastest.

### Phase 2
Freeze the write path.

- RULE merge disabled
- required type on new writes
- required headline on new writes
- duplicate detection
- supersede path mandatory for overlapping rules
- rule edits only via supersede
- open action items migrated out of existing guardrail blobs

This is the most important structural fix, and Windsurf is strongest here.

### Phase 3
Canonicalization and migration.

- one time pass over existing blocker candidates
- split mixed memories into RULE, TASK, FACT, INCIDENT
- archive old blobs
- keep provenance links
- create current canonical blocker set with a hard cap, ideally 5 to 7 active blockers max

This is the step that turns the new model from “future clean” into “actually clean now.”

### Phase 4
Salience and archival behavior.

- add access_count and last_accessed_at
- decay FACT and INCIDENT surfacing based on access, not age alone
- let RULE survive until superseded
- add validity windows for time sensitive facts

This takes Claude’s strongest idea and applies it only where it belongs.

### Phase 5
Coordination and handoff.

- shared task leases
- optimistic versioning for mutable state
- per session handoff note
- write gate on destructive changes

GPT is right that the parallel session issue needs its own lane. It should not be solved indirectly through memory alone.

## Bottom line

If I had to reduce this to one sentence:

**Use GPT’s architecture, Windsurf’s enforcement, and Claude’s retrieval and salience model.**

If I had to reduce it to one immediate action:

**Implement headline only boot rendering plus a separate TASK type, then immediately ban RULE merges and require supersede.**

That is the highest leverage combined answer.
