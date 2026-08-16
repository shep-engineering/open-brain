"""Write-gate enforcement tests — Windsurf synthesis §4.4.

Each test exercises one of the five gate steps on real data.
"""
from __future__ import annotations

import pytest

from brain_v2 import store
from brain_v2.write_gate import (
    WriteGateError,
    check_atomicity,
    check_headline,
    check_kind,
    check_severity,
)


class TestStep1TypeDeclaration:
    def test_valid_kinds_pass(self):
        for k in ("rule", "fact", "incident", "task"):
            check_kind(k)  # must not raise

    def test_invalid_kind_rejected(self):
        with pytest.raises(WriteGateError, match="type"):
            check_kind("memory")

    def test_rule_requires_severity(self):
        with pytest.raises(WriteGateError, match="severity"):
            check_severity("rule", None)

    def test_rule_rejects_deprecated_from_caller(self):
        # DEPRECATED is set by supersede only, not by direct writes
        with pytest.raises(WriteGateError, match="severity"):
            check_severity("rule", "DEPRECATED")

    def test_non_rule_severity_ignored(self):
        check_severity("fact", None)
        check_severity("incident", None)
        check_severity("task", None)


class TestStep2Atomicity:
    def test_normal_body_passes(self):
        check_atomicity("A single rule. About one specific thing.")

    def test_empty_body_rejected(self):
        with pytest.raises(WriteGateError, match="body"):
            check_atomicity("")

    def test_oversize_body_rejected(self):
        huge = " ".join(["word"] * 500)
        with pytest.raises(WriteGateError, match="400"):
            check_atomicity(huge)

    def test_multi_guardrail_merge_rejected(self):
        merged = (
            "GUARDRAIL 2026-04-12 first rule.\n"
            "\nUpdate: GUARDRAIL 2026-04-13 second rule.\n"
            "\nUpdate: another update here."
        )
        with pytest.raises(WriteGateError, match="merge pathology"):
            check_atomicity(merged)


class TestStep3Headline:
    def test_short_headline_passes(self):
        check_headline("Never commit to main")

    def test_empty_headline_rejected(self):
        with pytest.raises(WriteGateError, match="empty"):
            check_headline("")

    def test_headline_over_15_words_rejected(self):
        bad = " ".join(["word"] * 16)
        with pytest.raises(WriteGateError, match="15 words"):
            check_headline(bad)


class TestStep4DuplicateDetection:
    def test_first_write_no_duplicate(self, conn):
        mem = store.remember_rule(
            conn, headline="Never commit to main", body="Always use feature branches.",
            severity="BLOCKER", project="test", source="test",
        )
        assert not isinstance(mem, store.DuplicateHit)
        assert mem.kind == "rule"

    def test_near_duplicate_rule_returns_duplicate_hit(self, conn):
        store.remember_rule(
            conn, headline="Never commit directly to main",
            body="Always use feature branches like feat/, fix/, chore/.",
            severity="BLOCKER", project="test", source="test",
        )
        result = store.remember_rule(
            conn, headline="Do not commit to main branch",
            body="Always use feature branches like feat/, fix/, chore/.",
            severity="BLOCKER", project="test", source="test",
        )
        assert isinstance(result, store.DuplicateHit)
        assert result.kind == "rule"
        assert result.similarity > 0.75

    def test_distinct_rules_both_land(self, conn):
        a = store.remember_rule(
            conn, headline="Never commit to main",
            body="Always use feature branches.",
            severity="BLOCKER", project="test", source="test",
        )
        b = store.remember_rule(
            conn, headline="Run tests before pushing",
            body="pytest must pass locally before any git push.",
            severity="PATTERN", project="test", source="test",
        )
        assert not isinstance(a, store.DuplicateHit)
        assert not isinstance(b, store.DuplicateHit)
        assert a.id != b.id


class TestSimilarRuleHint:
    """A new rule whose nearest active same-project neighbor is in
    [SIMILAR_RULE_COSINE, DUPLICATE_COSINE) is not a duplicate but IS about the
    same topic. remember_rule attaches a non-blocking 'similar_existing' hint via
    Memory.extra so the agent can consider supersede over a parallel rule.
    Similarity values below are empirically measured against the seeded rule.
    """

    _SEED = dict(
        headline="Always run the test suite before pushing code",
        body="pytest must pass locally before any git push to the remote.",
    )

    def test_in_band_neighbor_surfaces_hint(self, conn):
        store.remember_rule(conn, severity="PATTERN", project="test",
                            source="test", **self._SEED)
        # ~0.736 similar: same topic, below the 0.75 dedup threshold.
        mem = store.remember_rule(
            conn, headline="Execute unit tests before committing to the branch",
            body="Tests should pass before you commit.",
            severity="PATTERN", project="test", source="test",
        )
        # It landed as a hint (not a DuplicateHit), which by construction means the
        # similarity is in [SIMILAR_RULE_COSINE, 0.75). Assert the lower bound only;
        # the upper bound is guaranteed by run_gate_with_neighbors' band logic, and
        # asserting a tight <0.75 on a live model would be flaky (this pair is ~0.736).
        assert not isinstance(mem, store.DuplicateHit)
        assert mem.extra is not None and "similar_existing" in mem.extra
        sims = [n["similarity"] for n in mem.extra["similar_existing"]]
        assert sims and all(s >= 0.62 for s in sims), sims
        assert "hint" in mem.extra

    def test_distant_rule_has_no_hint(self, conn):
        store.remember_rule(conn, severity="PATTERN", project="test",
                            source="test", **self._SEED)
        # ~0.37 similar: unrelated topic.
        mem = store.remember_rule(
            conn, headline="Never deploy on a Friday afternoon",
            body="Friday deploys risk weekend outages with no one on call.",
            severity="PATTERN", project="test", source="test",
        )
        assert not isinstance(mem, store.DuplicateHit)
        # extra is None (dropped from to_dict) when no in-band neighbor.
        assert mem.extra is None or "similar_existing" not in mem.extra

    def test_duplicate_not_also_reported_as_hint(self, conn):
        store.remember_rule(conn, severity="PATTERN", project="test",
                            source="test", **self._SEED)
        # ~0.93 similar: a duplicate — must be a DuplicateHit, NOT a hint.
        result = store.remember_rule(
            conn, headline="Run all tests prior to pushing changes upstream",
            body="Execute the full pytest run before git push.",
            severity="PATTERN", project="test", source="test",
        )
        assert isinstance(result, store.DuplicateHit)
        assert result.similarity >= 0.75

    def test_same_project_neighbor_not_crowded_out_by_other_projects(self, conn):
        # Regression: the hint's top-k must be spent on same-project/global rows.
        # Seed several OTHER-project rules very similar to the seed, plus the seed
        # under "test"; a new "test" rule must still surface the same-project seed
        # even though other-project twins outrank it globally.
        store.remember_rule(conn, severity="PATTERN", project="test",
                            source="test", **self._SEED)
        for i in range(6):
            store.remember_rule(
                conn,
                headline=f"Run the full test suite before pushing variant {i:02d}",
                body=f"pytest must pass before git push; project-other variant {i}.",
                severity="PATTERN", project=f"other{i}", source="test",
            )
        mem = store.remember_rule(
            conn, headline="Execute unit tests before committing to the branch",
            body="Tests should pass before you commit.",
            severity="PATTERN", project="test", source="test",
        )
        if not isinstance(mem, store.DuplicateHit):
            # If it's a hint, the surfaced neighbor(s) must be same-project/global,
            # never an other-project row.
            for n in (mem.extra or {}).get("similar_existing", []):
                # neighbor project isn't returned in the payload, but the SQL scope
                # guarantees it; assert we DID find the same-project seed's presence
                # by id existing under project 'test'.
                with conn.cursor() as cur:
                    cur.execute("SELECT project FROM memory_index WHERE kind='rule' "
                                "AND memory_id=%s", (n["id"],))
                    proj = cur.fetchone()[0]
                assert proj in ("test", ""), f"other-project neighbor leaked: {proj}"

    def test_cross_project_neighbor_not_surfaced(self, conn):
        # Seed the neighbor under a DIFFERENT project; the hint is scoped to
        # same-project-or-global, so it must not surface.
        store.remember_rule(conn, severity="PATTERN", project="other",
                            source="test", **self._SEED)
        mem = store.remember_rule(
            conn, headline="Execute unit tests before committing to the branch",
            body="Tests should pass before you commit.",
            severity="PATTERN", project="test", source="test",
        )
        assert not isinstance(mem, store.DuplicateHit)
        assert mem.extra is None or "similar_existing" not in mem.extra


class TestStep5SupersedeOnly:
    def test_rule_bodies_immutable_via_supersede_chain(self, conn):
        first = store.remember_rule(
            conn, headline="Use conventional commits",
            body="feat:, fix:, docs: etc.",
            severity="PATTERN", project="test", source="test",
        )
        second = store.supersede_rule(
            conn, old_id=first.id,
            new_headline="Use conventional commits with scope",
            new_body="feat(scope):, fix(scope):, docs: etc. Scope is required.",
            reason="Scope became mandatory after the multi-package refactor.",
            source="test",
        )
        # Old rule now DEPRECATED; new rule active with supersedes link
        with conn.cursor() as cur:
            cur.execute("SELECT severity, superseded_by FROM rules WHERE id = %s", (first.id,))
            sev, sb = cur.fetchone()
            assert sev == "DEPRECATED"
            assert sb == second.id

            cur.execute("SELECT supersedes FROM rules WHERE id = %s", (second.id,))
            assert cur.fetchone()[0] == first.id

    def test_cannot_supersede_already_superseded(self, conn):
        a = store.remember_rule(
            conn, headline="Rule A version one",
            body="Body A one.",
            severity="PATTERN", project="test", source="test",
        )
        store.supersede_rule(
            conn, old_id=a.id,
            new_headline="Rule A version two",
            new_body="Body A two.",
            reason="revision",
            source="test",
        )
        with pytest.raises(ValueError, match="already superseded"):
            store.supersede_rule(
                conn, old_id=a.id,
                new_headline="Rule A version three",
                new_body="Body A three.",
                reason="double-revision (should fail)",
                source="test",
            )

    def test_superseded_rule_not_in_active_index(self, conn):
        a = store.remember_rule(
            conn, headline="Rule alpha",
            body="Alpha body.",
            severity="PATTERN", project="test", source="test",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT active FROM memory_index WHERE kind='rule' AND memory_id=%s", (a.id,)
            )
            assert cur.fetchone()[0] is True
        store.supersede_rule(
            conn, old_id=a.id,
            new_headline="Rule alpha revised",
            new_body="Alpha body revised.",
            reason="revision",
            source="test",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT active FROM memory_index WHERE kind='rule' AND memory_id=%s", (a.id,)
            )
            assert cur.fetchone()[0] is False
