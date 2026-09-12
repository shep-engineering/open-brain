# Fact supersession prototype — PLAN
*2026-09-11. Branch `feat/kg-adjacent-memory-engine` (branch-and-hold). The cheapest real memory fix
identified by the KG homework: facts have no correction path, so stale facts are immortal.*

## The problem (verified live)
`facts` has NO `superseded_by`/`active`/`decayed` column. 861/871 facts are active and none can be
corrected. The write gate's DuplicateHit already emits `hint: "route to supersede(old_id=..., ...)"`
(store.py:118) for facts — but there is NO `supersede_fact` function; only `supersede_rule` exists
(store.py:212). The store recommends an operation its own schema cannot perform.

## The fix (mirror the rule-supersede pattern, which already works)
`supersede_rule` (store.py:212-258) is the proven template: insert new row, set old's
`superseded_by` + move old to a retired state, deactivate old's `memory_index` entry, insert new
index entry, audit. Facts get the same, adapted to the facts schema.

### Schema delta (facts table) — put in `schema.py` (idempotent, one source of truth)
Add THREE nullable columns (bidirectional mirror of rules; backward-compatible):
```
ALTER TABLE facts ADD COLUMN IF NOT EXISTS supersedes       INTEGER REFERENCES facts(id) ON DELETE SET NULL;
ALTER TABLE facts ADD COLUMN IF NOT EXISTS superseded_by    INTEGER REFERENCES facts(id) ON DELETE SET NULL;
ALTER TABLE facts ADD COLUMN IF NOT EXISTS supersede_reason TEXT;
```
`supersedes` added too (PLAN-gate #4: rules keep BOTH directions; store.py:238/246). No `active` column
on facts is needed for retrieval: `memory_index.active` is the ONLY search filter (reviewer CONFIRMED
store.py:406-420; skill-merge is rules-only so it can't resurface a fact). Supersession sets the OLD
fact's `memory_index.active=FALSE` via `_index_deactivate`. The ALTER lives in schema.py so the
throwaway test and the eventual live ship use the same idempotent source.

### CRITICAL: decay must be superseded-aware (PLAN-gate #1 — the fix self-reverts without this)
`decay_facts` (maintenance.py:108-127) recomputes a score for every fact and **reactivates** any
inactive fact scoring >= threshold. A just-superseded fact has `last_accessed=NULL` → score uses
`created_at`=now → ~1.0 → the next boot-time maintenance flips its index row back to active, undoing the
supersede and re-creating the duplicate. Rules are immune (decay only touches `kind='fact'`). FIX:
- the deactivate scan and the reactivate branch must both exclude `f.superseded_by IS NOT NULL`;
- a superseded fact is NEVER reactivated by decay.
Regression test: supersede a fact, run `decay_facts`, assert it STAYS inactive + absent from search.

### Code delta (store.py) — a new `supersede_fact` (raw INSERT, skip dedup)
Mirror `supersede_rule` EXACTLY (PLAN-gate #2 — do NOT call remember_fact, it self-rejects as a dup):
- SELECT old fact's `project, superseded_by`; chain guard — raise if `superseded_by` already set;
- `run_gate(kind="fact", headline, body, embedding_vec=None)` — skips dup detection (mirror store.py:232);
- **raw** `INSERT INTO facts (headline, body, project, tags, ttl, source, supersedes)` — new fact starts
  with DEFAULT confidence/access_count/last_accessed (PLAN-gate #7: do NOT copy them from the old fact);
- `UPDATE facts SET superseded_by=new, supersede_reason=reason WHERE id=old`;
- `_index_deactivate(cur, "fact", old_id)`; `_index_insert(...new...)`; `_audit("SUPERSEDE","fact",old_id,...)`;
- commit; return the new Memory.

### Tool delta (server.py) — `supersede_fact_v2(old_id, new_headline, new_body, reason, ...)`
Mirror `supersede_rule_v2` (server.py:630-652) INCLUDING the wiring (PLAN-gate #5): `@mcp.tool()`,
`ensure_schema()`, `_check_write_gate`, and the error mapping (`WriteGateError`→step=write_gate,
`ValueError`→chain-guard/not-found, generic fallback). The DuplicateHit hint for facts (store.py:118)
then becomes truthful — a caller CAN act on it.

## NON-DESTRUCTIVE protocol (hard requirement) — use the EXISTING vetted test DB (PLAN-gate #3/#6)
- Prototype + prove on the EXISTING `open_brain_v2_test` (port 5435), via `brain_v2/tests/` +
  `apply_schema` — NOT a hand-invented DB, and NEVER by cloning live. `brain_v2/tests/conftest.py:17-42`
  already forces the URL to 5435 and hard-`pytest.exit`s unless it contains `open_brain_v2_test` AND
  `5435` (a 3-layer guard) — so hitting live 5433 is structurally impossible.
- Do NOT run the ALTER against live `open_brain_v2` this session — schema change to the live brain =
  Shep-gated ship step (documented, not executed).
- Test preconditions (PLAN-gate #6, NOT hermetic): Ollama `qwen3-embedding:8b` @ :11434 AND the 5435
  container up. If either is absent the DIFF gate reports BLOCKED (never a pass).
- HONEST deliverable framing (PLAN-gate #8): tonight ships a VALIDATED, ready-to-ship patch gated on one
  human go. The 861 live immortal facts stay immortal until Shep runs the ship step. This is a proven
  patch, not a live improvement yet.

## Validator loop
- PLAN gate (now): adversarial reviewer — is mirroring supersede_rule correct for facts? does setting
  memory_index.active=FALSE actually remove the stale fact from search? any way this touches live data?
  is the chain guard right? does decay/TTL interact badly?
- EXECUTE: implement on throwaway DB; tests that actually run.
- DIFF gate: reviewer on the diff + a real test run proving: (1) after supersede_fact, search returns
  the NEW fact and NOT the old; (2) the old fact's row still exists (audit) with superseded_by set;
  (3) double-supersede is refused; (4) live open_brain_v2 untouched.

## Success criteria
1. `supersede_fact` works on the throwaway DB: stale fact disappears from search, new one appears,
   old row retained with the link. Tests green (actually executed).
2. A seed-then-supersede-then-search test PROVES the stale fact is gone from results (the exact
   failure the KG couldn't fix cheaply).
3. Live `open_brain_v2` byte-for-byte unchanged (row counts + git-clean on any live schema).
