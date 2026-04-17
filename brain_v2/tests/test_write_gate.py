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
