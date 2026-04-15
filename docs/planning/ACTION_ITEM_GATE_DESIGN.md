# Action-Item Compliance Gate — Design

**Status:** PROPOSED — design pass, not yet implemented.
**Branch:** `feat/action-item-gate`
**Target:** open-brain v0.14.0.
**Motivating incident:** memory #3719 (2026-04-14 Netflix SRE/DDoS vs. CI/CD miss, ~$500K/yr opportunity lost). Brain surfaced action_item "Update flashcard app for correct role (SRE/Edge/DDoS EM)" in `boot_session`; sibling session built a CI/CD flashcard app anyway. #3719 escalated: "action_items are BLOCKING, not advisory." v0.13.0 session registry closed the *visibility* gap; this closes the *compliance* gap — same incident, different fix.

---

## 1. Problem

`boot_session` returns memories (pinned guardrails, recent history, known issues) and each memory's metadata may carry an `action_items: list[str]`. Today those are formatted into text previews and displayed to the session. Compliance is entirely on the agent's discipline.

Observation: the discipline keeps failing across sessions. Memory-side guardrails ("action_items are BLOCKING, not advisory") get ignored because they compete with instruction budget (skills-layer research) and can be skimmed as vibes (per #3719). The fix needs to be architectural, not behavioral.

---

## 2. Goals

1. Any session that boots must **engage** with each live action_item returned by `boot_session` before doing write work.
2. "Engage" = explicit decision per item: `will_execute` / `already_done` / `not_relevant` — each with a short reason for the latter two.
3. False positives (stale TODOs from months ago, duplicate items surfaced across memories) must be easy to clear so the gate doesn't become a rubber-stamp ritual.
4. Zero new schema. Per-source in-memory set; re-ack on reboot is acceptable (matches session-registry TTL semantics).
5. Gate applies to **write** tools only. Reads (`search`, `recall`, `list_recent`) stay open so the session can investigate before acknowledging.

---

## 3. Non-Goals

- Blocking non-brain tools (Edit, Bash, WebFetch). That's Phase 4 hook-installer scope — a PreToolUse hook could extend this gate to Edit/Bash. Not v0.14.0.
- Persisting acknowledgments across sessions. An action_item the user has *actually* resolved gets expressed by the user editing/superseding the originating memory, not by the ack surviving a restart.
- Auto-classifying action_items (NLP-ish "this looks stale"). Add friction where it matters; let the agent decide.

---

## 4. Data model

**No schema changes.** Action_items already live in `memories.metadata.action_items` as `list[str]`.

**Per-source in-memory state (server.py):**

```python
_pending_action_items: dict[str, list[dict]] = {}
#   source -> list of {
#     "memory_id": int,
#     "index":     int,    # index within that memory's action_items list
#     "text":      str,    # dedup key
#     "origin":    str,    # "recent_history" | "known_issues"
#   }
```

Populated at `boot_session`. Cleared per-item by `acknowledge_action_item`. No persistence — ephemeral, matches session TTL semantics.

---

## 5. Extraction scope

`boot_session` currently returns 5 sections. Only two of them can plausibly carry pending action:

| Section | Action_items surfaced? |
|---|---|
| PINNED GUARDRAILS | ❌ Rules, not tasks. Their action_items (if any) are how-to-apply text, not pending work. |
| PROJECT CONTEXT | ❌ Architecture facts. |
| RECENT HISTORY (7 days) | ✅ Recent session output; most likely to carry live tasks. |
| KNOWN ISSUES & CORRECTIONS | ✅ Open bugs/gaps. |
| REPEATED CORRECTIONS | ❌ Derived; already covered by the KNOWN ISSUES pass. |
| OTHER ACTIVE SESSIONS (v0.13.0) | ❌ Sibling state, no action_items. |

Extraction: after loading each memory set, read `metadata.action_items`, append `{memory_id, index, text, origin}` to `_pending_action_items[source]`. Dedupe by `text` across origins — same task surfaced in both recent & issues counts once.

**Cap:** at most `OPEN_BRAIN_ACTION_ITEM_GATE_MAX` (default 10) pending items. If boot extracts more, keep the most recent by `memory.created_at` and drop the rest — prevents the gate from becoming a wall of stale TODOs.

---

## 6. MCP tool changes

### 6.1 `boot_session` — modified

Response adds:
- `pending_action_items` top-level field (list of `{memory_id, index, text, origin}`)
- New `ACTION ITEMS PENDING` section (when non-empty) with an instruction line: *"You MUST call `acknowledge_action_item(...)` for each before using write tools (remember, capture_context, supersede, pin, unpin, forget, load_skill, brain_checkpoint)."*

Empty pending list → section omitted entirely (no-op gate, backwards-compat).

### 6.2 `acknowledge_action_item` — new

```python
acknowledge_action_item(
    source: str,
    memory_id: int,
    text: str,
    decision: str,          # "will_execute" | "already_done" | "not_relevant"
    reason: str = "",
) -> str
```

- Validates `decision` is one of the three.
- Requires `reason` for `already_done` / `not_relevant` (enforces "explain why you're dismissing this").
- Looks up the pending entry by `(memory_id, text)` — exact text match. Removes from `_pending_action_items[source]`.
- Logs to an append-only file (`logs/action_item_acks.jsonl`) for post-hoc audit: who acked what, when, why. Not DB-persisted (keeps schema untouched) but durable enough for review.
- Returns `{success: True, remaining: N}` where N is the new pending count for this source.

Silently no-ops if the item isn't pending (already acked or never was). Never error on that — prevents session from being stuck in error-retry loops.

### 6.3 Compliance gate

New `_check_action_item_gate(source: str) -> dict | None`. Called from inside `_check_compliance` (existing gate hook) when the tool is in the **write set**:

Write set: `remember`, `capture_context`, `brain_checkpoint`, `supersede`, `pin`, `unpin`, `forget`, `forget_many`, `prune`, `load_skill`, `annotate`, `rate`.

Read/maintenance set (allowed through the gate): `search`, `recall`, `list_recent`, `list_active_sessions`, `update_active_task`, `end_session`, `acknowledge_action_item`, `stats`, `scratch_get`, `scratch_set`, `scratch_list`, `capture_context` — wait, capture_context IS a write. Kept in write set.

If `_pending_action_items[source]` is non-empty:
```json
{
  "success": false,
  "blocked_by": "action_items_pending",
  "error": "You must acknowledge each pending action_item before write tools unlock.",
  "pending": [ {memory_id, text, origin}, ... ],
  "hint": "Call acknowledge_action_item(source, memory_id, text, decision, reason) for each."
}
```

---

## 7. Interaction with existing compliance gate

The existing `_check_compliance` already blocks on search-before-store. Two independent gates:

1. Search-first gate: block `remember` / `capture_context` if `stores_since_search >= COMPLIANCE_MAX_STORES`.
2. Action-item gate: block the **write set** if `_pending_action_items[source]` is non-empty.

Gate 2 takes precedence — engagement with pending action_items is a higher-order failure than polling discipline.

---

## 8. Ack audit log

`logs/action_item_acks.jsonl`:
```json
{"timestamp": "2026-04-15T00:45:12Z", "source": "claude", "memory_id": 5072,
 "text": "Update flashcard app for correct role (SRE/Edge/DDoS EM)",
 "decision": "not_relevant", "reason": "Current session is brain infra work, not interview prep"}
```

Appended; never rotated by server.py. If operator wants rotation, wrap it in logrotate. Read path: `grep` / `jq`. No dashboard in v0.14.0.

---

## 9. Tests (`tests/test_action_item_gate.py`)

1. `test_boot_extracts_action_items_from_recent_history` — memory with action_items in recent window surfaces in pending list.
2. `test_boot_extracts_action_items_from_known_issues` — same, for issues section.
3. `test_boot_skips_action_items_on_pinned_guardrails` — pinned memories' action_items do NOT enter the gate.
4. `test_boot_dedupes_identical_action_item_text` — same text across two memories counts once.
5. `test_boot_caps_at_max` — more than `OPEN_BRAIN_ACTION_ITEM_GATE_MAX` items keeps only the most-recent.
6. `test_write_blocked_when_pending` — `remember()` returns `blocked_by=action_items_pending`.
7. `test_read_allowed_when_pending` — `search()` / `recall()` work fine.
8. `test_acknowledge_clears_one_item` — ack reduces pending count by 1; blocking still holds if others remain.
9. `test_acknowledge_all_unblocks_writes` — after full ack, `remember()` goes through.
10. `test_acknowledge_requires_reason_for_not_relevant` — missing reason → error.
11. `test_acknowledge_requires_reason_for_already_done` — missing reason → error.
12. `test_acknowledge_noop_on_unknown_item` — acking a non-pending item returns success (idempotent).
13. `test_boot_with_no_action_items_no_gate_section` — clean boot → no ACTION ITEMS PENDING section.
14. `test_audit_log_appended_on_ack` — ack writes one JSONL line to the audit file.

~14 tests, same pattern as session-registry.

---

## 10. Rollout

1. Branch `feat/action-item-gate`.
2. No schema changes → no migration, no production backup required.
3. Implement server.py + tests on test DB.
4. Full regression (belief, pinned, skills, session_registry, new gate).
5. Commit + merge + tag v0.14.0 + push degailen.
6. Shep main orphan refresh + gh-pages redeploy.
7. Measure: track ack audit log for a week to see (a) how often the gate fires, (b) whether sessions are giving thoughtful `not_relevant` reasons vs. rubber-stamping. Iteration on cap / scope in v0.14.1 if needed.

---

## 11. Open questions (answering inline as decisions)

1. **What if the user wants to bypass the gate for a quick task?** Deferred: no bypass in v0.14.0. Session must ack. If the friction is wrong, raise the cap or narrow the origin scope in v0.14.1. Don't build an escape hatch that immediately becomes the default.
2. **What if two sessions boot into the same project, extract the same action_items, and one acks them all?** They each get their own `_pending_action_items` — isolation is per-source+session. "Already done by sibling" is a valid `already_done` ack reason; the sibling can see other active sessions via v0.13.0 and decide.
3. **Are pinned guardrails with action_items truly safe to skip?** Most pinned action_items read as "how to apply" instructions, not pending tasks. But if a guardrail has a genuine task like "test every endpoint before claiming done," the guardrail text itself surfaces on every boot and the session ought to apply it — no one-shot ack needed. Skipping pinned is the right call. If this proves wrong, v0.14.1 can extend origin set.
4. **Should ack be reversible (undo)?** No. Ack is cheap; if wrong, the next boot will re-surface the item (since the memory still has the action_item). Reversibility is friction without benefit.

---

## 12. Scope summary

| Aspect | v0.14.0 |
|---|---|
| New schema | none |
| New MCP tool | `acknowledge_action_item` |
| Modified MCP tool | `boot_session` (response shape + ACTION ITEMS PENDING section) |
| Gated write set | remember, capture_context, brain_checkpoint, supersede, pin, unpin, forget, forget_many, prune, load_skill, annotate, rate |
| Audit | `logs/action_item_acks.jsonl` (append-only) |
| Tests | 14 |
| Tool count | 25 → 26 |
| Backwards compat | Boots with no action_items are unchanged (no gate section, no blocking). |
