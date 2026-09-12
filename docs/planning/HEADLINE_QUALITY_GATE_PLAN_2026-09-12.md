# Write-gate headline quality check — PLAN
*2026-09-12. Branch feat/kg-adjacent-memory-engine (branch-and-hold). Small additive validation fix.*

## Problem (verified live, repeatedly)
Junk facts headlined `1`, `2`, `3`, `4` (ids 16,17,27-32, resume-creator numbered garbage) pollute
retrieval — they surfaced as noise in the initial diagnosis, ranked ABOVE real content in the KG
bench distractors, and are the confident garbage returned for off-corpus queries (fact 873). Root
cause: `check_headline` (write_gate.py:73-81) only enforces non-empty + ≤15 words. A headline of `"1"`
passes. So the corpus keeps accreting non-descriptive headlines that embed poorly and mislead search.

## Fix (root cause, at write time, additive) — PLAN-gate-corrected predicate
Extend `check_headline` to reject a headline with **NO ASCII letter** (and nothing else — the
">=2 tokens" idea was WRONG: it false-rejects real live headlines NIST, Config, CLAUDE, Emergent,
auto_rebuild, AI_REMEDIATION_PLAN). Exact predicate:
```python
_ALPHA = re.compile(r"[A-Za-z]")
if not _ALPHA.search(headline.strip()):
    raise WriteGateError("step 3 (headline): non-descriptive — needs at least one letter "
                         "(got a bare number or punctuation). Write a real headline.")
```
Rejects `"1"`,`"42"`,`"3."`,`"—"`; accepts `"RLS Pattern A"`,`"ARC-822 done"`,`"NIST"`,`"Emergent"`.
Use `[A-Za-z]` (not `\w`, which matches digits/underscore; not whole-string `isalpha()`, which fails
on `"RLS Pattern A"`). Runs on EVERY write path (remember_*, supersede_*, capture_context decompose).

## Decompose fix (REQUIRED, or the change drops real content)
`decompose._make_headline` (decompose.py:70-77) turns a numbered list item `"1. do X"` into headline
`"1"`. Under the new gate capture_context would REJECT that chunk and SILENTLY DROP the real body
(per-chunk catch, only log.warning). FIX `_make_headline`: strip a leading enumerator (`N.`/`N)`/bullet)
before taking the first sentence; if the slice still has no letter, fall back to the first
HEADLINE_WORD_CAP words of the body. Add a decompose test proving a numbered-list chunk is STORED.

## Scope / non-goals
- Does NOT touch existing data (the 55 junk memories (41 facts + 14 rules, `headline !~ '[A-Za-z]'`) already in live stay — forgetting them is a separate
  Shep-gated data mutation, fact 873).
- Conservative threshold: must NOT reject legitimate short headlines. "RLS Pattern A" (3 words, real),
  "ARC-822 done" (2 tokens, real) must pass. Only bare-number / no-alpha / single-token-nonword headlines
  are rejected. Verify against real examples before finalizing the predicate.
- capture_context's heuristic decompose may generate a numeric headline from a list item; if so it must
  fall back to a descriptive slice of the body, not crash. Check decompose.py interaction.

## Validator loop (scaled to risk = small)
- PLAN gate: one adversarial reviewer — is the predicate correct (no false-rejects of real short
  headlines)? does it break capture_context decompose? any existing test that writes a numeric headline?
- EXECUTE: extend check_headline + tests (reject "1"/"3."/"—"; accept "RLS Pattern A", "ARC-822 done",
  "Use pooler").
- DIFF gate: reviewer on the diff + run the full write_gate + capture_context test suites (no regressions).

## Success criteria
1. check_headline rejects "1", "42", "3.", "—", "" ; accepts real short headlines. Tests green.
2. Existing brain_v2 write_gate + capture_context suites still pass (no false-reject regression).
3. Live open_brain_v2 unchanged (this is code only; the gate affects FUTURE writes).
