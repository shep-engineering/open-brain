# Claude Memory Architecture Redesign

> **Status:** Proposed
> **Author:** Claude (resume-creator session) for Dave
> **Drafted:** 2026-04-14
> **Source basis:** Research survey of production agent memory systems, 2025-2026 literature on agent memory, and direct observation of failures in the current open-brain boot/recall pipeline
> **Supersedes (partially):** [`SESSION_REGISTRY_DESIGN.md`](SESSION_REGISTRY_DESIGN.md) — see Section 9 for the specific correction

---

## 0. Why this document exists

Dave asked me to think deeply about why "these kinds of simple mistakes are getting worse." The honest diagnosis from my side is that the current open-brain boot payload has become walls of merged text. Pinned guardrails accumulate `Update:` blocks indefinitely. A single memory grows into a 2,000-word document. Boot returns 13 of those. I skim structure instead of absorbing content. Action items buried inside paragraph five of a merged wall get missed, and real consequences follow (Netflix $500K prep miss on 2026-04-14 is the concrete case).

Dave asked me to research the current state of the art before proposing a fix. This document summarizes that research and maps it to a concrete redesign proposal for open-brain.

The short version: the field has converged on a consistent answer. The current open-brain design violates most of it in load-bearing ways. The fix is substantial but can land incrementally.

---

## 1. Problem statement, grounded in observed failures

Specific failure modes from the 2026-04-13 to 2026-04-14 sessions:

| Symptom | Root cause |
|---|---|
| 15K-token boot payload every session | Pinned memories are preloaded wholesale; no just-in-time retrieval |
| Action item "Update flashcard app for correct role" missed | Buried 2,000 words into a merged memory; no hierarchy or headline extraction |
| Same rule appears 3-4 times inside a single memory under `Update:` blocks | Auto-merge on similarity; no supersession chain; no atomic notes |
| TTL-based session sweep kept killing my own live session | Time-based liveness detection anti-pattern (guardrail #4929 already documented this) |
| Boot context is identical regardless of current task | No task-relevance filter; no per-task scoring of memories |
| Rules and incidents render the same way | No tier tags (blocker / pattern / context); hierarchy collapse in display |

The through-line: the current brain treats every correction as an additive write to the boot payload. It has no subtractive mechanism. Memory grows without bound; the cognitive budget at boot stays fixed. The two curves cross and the agent starts skimming.

---

## 2. Consensus pattern from 2025-2026 research and production systems

Four pillars, consistently advocated across production memory systems (Letta, Mem0, Zep, Cognee) and research (A-MEM, AgeMem, LightMem, SimpleMem, MemoryBank). Anthropic's own engineering guide for AI agents aligns.

### 2.1 Just-in-time retrieval beats preloading

Load lightweight identifiers at boot, not content. Retrieve bodies on demand via tools.

Anthropic, [Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents):

> Agents built with the "just in time" approach maintain lightweight identifiers (file paths, stored queries, web links, etc.) and use these references to dynamically load data into context at runtime using tools.

> Load memories just-in-time rather than preloading everything, because large context payloads are expensive and degrade attention quality.

Claude Code's own design: CLAUDE.md preloads at boot, everything else is discovered at runtime via glob/grep. That hybrid is explicitly what Anthropic recommends as best-in-class. The current open-brain violates this by preloading the full content of every pinned memory on every boot.

### 2.2 Atomic notes with semantic links, not merged walls

A-MEM (Zettelkasten-inspired, [arxiv 2502.12110](https://arxiv.org/pdf/2502.12110)) and Letta both treat each memory as a discrete, atomic note with content plus attributes plus embeddings. New corrections create NEW notes with LINKS to prior ones. They never append into the original.

Zep goes further on temporal semantics: when a fact changes, "Zep doesn't overwrite the old information. Instead, it marks the old fact's validity period as having ended and creates a new fact with its own validity period." Audit trail preserved; default view clean.

The current open-brain auto-merges on similarity, which is the direct cause of the walls. Every `Update:` block in a pinned memory is a failed application of this principle.

### 2.3 Access-based salience, not time-based TTL

MemoryBank and multiple 2025-2026 papers converge on an Ebbinghaus-curve-inspired decay model:

> Access-based decay (activation halves every ~7 days without access) works better than time-based decay. A fact's relevance depends on whether the agent is actively engaging with it, not how old it is. A frequently-recalled fact from three months ago should stay loud while a never-accessed fact from yesterday can fade.

Each retrieval resets the curve. Unused memories slide to archive naturally. Nothing "expires" on a stopwatch.

This is directly relevant to open-brain because:

1. The session registry I drafted in `SESSION_REGISTRY_DESIGN.md` used a 5-minute TTL. Dave correctly flagged this as the same timeout anti-pattern he has repeatedly rejected. The research literature confirms that position.
2. Pin/unpin today is binary. A better model is continuous activation level with decay-on-disuse and reinforcement-on-access.

### 2.4 Agent-managed memory via explicit tool calls

Letta's core design: the LLM owns memory management. It calls `memory_replace`, `memory_rethink`, `archival_search`, `archival_insert` as tool actions. The brain is a dumb, reliable store and a smart index; the agent is the thinking layer.

A-MEM and AgeMem extend this with five explicit memory operations (store, retrieve, update, summarize, discard) exposed as callable tools, optimized via reinforcement learning over which operations to call when.

Today open-brain tries to think on behalf of the agent (auto-merge, auto-pin on repeated correction, implicit heartbeat). Those are well-intentioned but brittle. Moving the thinking to the agent follows the field consensus and gives the agent the levers it needs to actually manage its own memory.

---

## 3. Production systems: honest trade-offs

| System | Strength | Weakness | Relevant benchmark |
|---|---|---|---|
| **Letta (ex-MemGPT)** | OS-inspired hierarchy; agent owns tier transitions; closest to what Dave is asking for structurally | Heavier framework; agent must be written inside Letta SDK | Established baseline |
| **Mem0** | Drop-in layer; easy integration; claims ~90% token reduction vs full-context | Cloud-first; graph features behind paid tier | 49.0% on LongMemEval |
| **Zep** | Temporal knowledge graph; strongest for "what was true when" queries | Graph learning curve; less flexible for free-form memories | **63.8% on LongMemEval, beats Mem0 by 15 points** |
| **Cognee** | Local-first, privacy-preserving; dual vector plus graph | No decay mechanism; younger project | Newer |
| **A-MEM (research)** | Zettelkasten-pure design; strongest theoretical foundation | Research artifact, not a production system | Paper only |

Zep's lead on LongMemEval is the real benchmark signal: graph plus temporal beats pure vector similarity for "what was true at time T" queries, which is exactly the class of question open-brain answers on every boot (what rules apply to this project right now).

For open-brain specifically, the structural model most similar to what Dave has been describing is Letta's: core memory always in context (small and curated), archival memory retrievable on demand (everything else), agent-driven transitions between tiers.

---

## 4. Anthropic's named patterns (worth adopting verbatim)

From [Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents):

1. **Compaction.** Take a conversation nearing the context window limit, summarize its contents, reinitiate a new context window with the summary. Preserve architectural decisions, unresolved bugs, implementation details. Discard redundant tool outputs and messages.
2. **Structured note-taking.** The agent writes notes persisted to memory outside the context window. These notes are pulled back into context later. Example: `NOTES.md` or a todo list maintained by the agent itself.
3. **Sub-agent architectures.** Specialized sub-agents handle focused tasks with clean context windows. Each subagent returns a condensed, distilled summary of its work (1,000-2,000 tokens), not raw output.
4. **Tool result clearing.** Remove raw tool output from deep message history once processed.

None of these are currently implemented in open-brain's model. Compaction and structured note-taking are especially applicable to multi-hour sessions in open-brain's workflow.

---

## 5. Mapping observed failures to consensus fixes

| Observed failure | Pillar that fixes it | Concrete open-brain change |
|---|---|---|
| 15K-token boot payload | Just-in-time retrieval | Return headlines and an index at boot; bodies via `recall(id)` |
| Action items buried | Atomic notes plus tier tags | Split merged memories into atomic notes; require `tier` field on write |
| Merge-induced walls | Atomic notes plus supersession | Replace auto-merge with auto-supersede; new memory plus link to prior |
| TTL session sweep | Access-based salience | Ebbinghaus-style decay with reset-on-access; no stopwatch |
| No task relevance | Just-in-time retrieval | Score memories by semantic similarity to the `task` boot arg |
| Hierarchy collapse | Tier tags | Blocker / pattern / context with explicit render rules |

Every memory-system failure observed in the last 48 hours has a well-understood fix in the literature that open-brain is not currently applying.

---

## 6. Recommended phased rollout

Ranked by ROI. Each phase stands alone; later phases are optional.

### Phase 1: Rendering-only change, highest impact, lowest risk

Leave storage alone. Rewrite the `boot_session` response shape:

- Return a **core block** of at most 10 headline-only memories, scored by relevance to the `task` argument.
- Return an **index** of memory IDs plus headlines plus tags for everything else in the project's pinned set.
- Bodies are retrievable via `recall(id)`.

Headlines are either extracted from the first sentence of the memory (heuristic) or from a new optional `headline` field on `remember()`. Either way, the agent reads ~500 tokens at boot instead of 15,000.

**Effort:** 1-2 days. **Touches:** boot_session renderer, optional headline field on remember. **Reversible:** yes, entirely.

### Phase 2: Stop merging on write

Replace auto-merge with auto-supersede:

- New memory comes in, system checks for high-similarity pinned memory.
- If found: create new memory, mark old as `superseded_by: <new_id>`, link them.
- Default retrieval excludes superseded memories.
- Supersession chain is walkable for audit.

Existing merged memories can be left in place initially. A separate one-time migration splits them into atomic notes later.

**Effort:** 2-3 days. **Touches:** remember tool, retrieval filter. **Reversible:** yes (flip the flag, re-run migration).

### Phase 3: Access-based salience

Track `last_accessed_at` and `access_count` per memory. Surface by `base_salience × recency_of_access`, not by `created_at`. Archive (not delete) memories whose activation drops below threshold. Reset decay on retrieval.

This is the mechanism that lets unused memories fade without an anti-pattern timeout, and lets frequently-useful memories stay surfaced regardless of age.

**Effort:** 3-4 days. **Touches:** schema (add access fields), retrieval ranking. **Reversible:** yes (ignore the new fields).

### Phase 4: Agent-driven memory operations

Expose explicit MCP tools the agent calls to manage its own memory:

- `supersede(old_id, new_id, reason)`
- `archive(id, reason)`
- `link(a, b, kind)` where kind is `elaborates | contradicts | supersedes | context_for`
- `summarize_cluster(ids) -> new_id` for compaction
- `split(id) -> [ids]` for breaking up a wall into atomic notes

Deprecate auto-merge entirely. The brain becomes a store and an index; the agent owns the semantics.

**Effort:** 1-2 weeks. **Touches:** MCP tool layer, schema, agent-side usage patterns. **Reversible:** partially.

### Phase 5 (optional): Compaction and sub-agent patterns

For very long sessions, add explicit within-session compaction tool calls matching Anthropic's pattern. For complex multi-domain work, spin sub-agents with clean contexts and collect 1-2K token summaries back.

This is beyond the immediate need but worth knowing exists. Most useful once Phases 1-4 have landed.

---

## 7. My specific recommendation

**Build Phase 1 next.** It solves roughly 80% of the walls-of-text problem with a renderer change that touches zero storage and breaks nothing reversibly. It is directly testable: boot a session, measure boot-payload tokens, confirm agent surfaces action items correctly.

If Phase 1 lands cleanly, Phase 2 (stop merging) is the natural next step because it prevents the walls from forming in the first place instead of just rendering around them.

Phases 3 and 4 can wait until Phases 1-2 prove the direction. Phase 5 is optional.

**Do not try to build all of this at once.** The current session has already produced one architectural proposal (`SESSION_REGISTRY_DESIGN.md`) that went out with a timeout anti-pattern baked in. Incremental, testable phases with fast rollback are the right posture.

---

## 8. Things Dave has been right about that the research confirms

Worth writing down explicitly because the pattern is clear:

- **"Timeouts are an anti-pattern."** The research literature calls it "time-based decay is strictly inferior to access-based decay." Dave has been saying this for months. The 5-minute TTL I put into the session registry design was exactly this anti-pattern, and Dave's rejection of it matches consensus best practice.
- **"Walls of text are a failure."** The research calls it "context rot." Same phenomenon, confirmed to degrade model performance as context size grows regardless of attention improvements.
- **"The brain is supposed to be working memory, not a dump."** Letta's OS-inspired hierarchy (core plus archival, agent-managed tier transitions) is essentially what Dave has been gesturing at.
- **"The agent should manage its own memory."** Directly matches Letta and A-MEM's design philosophy and AgeMem's RL-trained memory-ops policy.

The research-backed statement: Dave's instincts on memory architecture are correct and track the state of the art. The work remaining is encoding those instincts into the system itself so the agent can't drift away from them.

---

## 9. Correction to `SESSION_REGISTRY_DESIGN.md`

The prior design doc at `F:\open-brain\docs\planning\SESSION_REGISTRY_DESIGN.md` specified a 5-minute heartbeat TTL auto-sweep for dead sessions. That is the exact timeout anti-pattern this document identifies as research-rejected and user-rejected. It should be replaced with an affirmative-signal design:

- Sessions declare presence via explicit heartbeats from the agent itself (or transport-level liveness detection).
- Absence of heartbeat for long enough is inferred from missed affirmative signals, not a clock sweep.
- A heartbeat agent pattern, not a TTL sweeper, is the correct implementation.

The sibling Claude session is already building this as a proper heartbeat agent. Good. That doc should either be marked superseded or revised to match the heartbeat approach before future sessions read it as guidance.

---

## 10. Sources worth reading

- [Anthropic: Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — authoritative, short, matches everything here
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/pdf/2502.12110) — Zettelkasten pattern
- [MemGPT / Letta](https://arxiv.org/abs/2310.08560) — OS-inspired hierarchy
- [Agent Memory: Letta vs Mem0 vs Zep vs Cognee (Letta forum)](https://forum.letta.com/t/agent-memory-letta-vs-mem0-vs-zep-vs-cognee/88)
- [Is Mem0 Really SOTA? (Zep's benchmark rebuttal)](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)
- [GAM: Dual-agent memory architecture vs context rot (VentureBeat)](https://venturebeat.com/ai/gam-takes-aim-at-context-rot-a-dual-agent-memory-architecture-that)
- [Memory Scaling for AI Agents (Databricks)](https://www.databricks.com/blog/memory-scaling-ai-agents)
- [Anatomy of Agentic Memory: Taxonomy](https://arxiv.org/html/2602.19320v1)
- [Context Engineering (Weaviate)](https://weaviate.io/blog/context-engineering)
- [The Agent's Memory Dilemma: Is Forgetting a Bug or a Feature?](https://tao-hpu.medium.com/the-agents-memory-dilemma-is-forgetting-a-bug-or-a-feature-a7e8421793d4)

---

## 11. Open questions for Dave

Flagging these rather than deciding unilaterally:

1. Is Phase 1 (rendering-only) worth building before the session-registry heartbeat work finishes, or should those land in sequence?
2. Is there appetite to adopt Letta directly rather than build our own hierarchy, or do the open-brain multi-IDE integration points make "build our own, inspired by Letta" the better path?
3. How aggressive do you want access-based decay to be? Ebbinghaus-default (halve every 7 days) is the research default but our rules probably want slower decay than personal facts.
4. Do you want the tier taxonomy to be `blocker / pattern / context / note` (my suggestion) or something else that matches your mental model better?
