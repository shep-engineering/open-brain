"""Boot payload contract tests — Windsurf synthesis §4.3.

Verifies:
  - Headline-only output (no bodies)
  - 5 BLOCKER count cap enforced
  - 2,000 token total cap enforced
  - WORKING CONTEXT is regenerated from `task` arg, not stored
  - Truncation order: tasks first, then patterns, then blockers
  - Superseded rules never appear
"""
from __future__ import annotations

from brain_v2 import boot, store
from brain_v2.config import (
    BOOT_BLOCKER_COUNT_CAP,
    BOOT_PATTERN_COUNT_CAP,
    BOOT_TOKEN_CAP,
)


# Diverse vocabularies so cosine similarity between seeds stays below the 0.75
# write-gate threshold. A "Blocker rule number N" headline would look like every
# other such headline to nomic-embed; real blockers are semantically distinct.
_DIVERSE_TOPICS = [
    ("Never commit directly to main", "Feature branches required for all work."),
    ("Tests must pass before any push", "pytest runs locally before git push."),
    ("No hardcoded secrets in source", "Environment variables only for credentials."),
    ("Use conventional commit messages", "Format: type(scope): short imperative description."),
    ("Update the CHANGELOG with each release", "Dated entry describing user-visible changes."),
    ("Prefer smaller PRs over large ones", "Review capacity drops sharply past 400 lines."),
    ("Document public APIs with docstrings", "Every exported function gets a one-line summary."),
    ("Profile before optimizing hot paths", "Measure first; guesswork wastes cycles."),
    ("Back up the database before migrations", "pg_dump to backups/ before any schema change."),
    ("Validate input at system boundaries", "Trust internal callers, verify external ones."),
    ("Pin dependency versions in lockfiles", "Reproducible builds require exact versions."),
    ("Log errors with actionable context", "Include inputs that reproduce the failure."),
    ("Review dependencies for license compatibility", "Check SPDX identifiers before adding a package."),
    ("Archive old branches after merging", "Delete local and remote refs to reduce noise."),
    ("Prefer composition over deep inheritance", "Flat hierarchies are easier to reason about."),
]


def _seed_rule(conn, headline: str, severity: str, project: str = "test", topic_idx: int | None = None):
    if topic_idx is not None:
        h, b = _DIVERSE_TOPICS[topic_idx % len(_DIVERSE_TOPICS)]
        return store.remember_rule(
            conn, headline=h, body=b,
            severity=severity, project=project, source="test",
        )
    return store.remember_rule(
        conn, headline=headline,
        body=f"Body supporting the rule '{headline[:40]}'.",
        severity=severity, project=project, source="test",
    )


class TestBlockerCountCap:
    def test_more_than_cap_blockers_truncated_to_cap(self, conn):
        for i in range(BOOT_BLOCKER_COUNT_CAP + 3):
            _seed_rule(conn, "", "BLOCKER", topic_idx=i)
        payload = boot.build(conn, project="test", task="any", source="test")
        assert len(payload.blockers) == BOOT_BLOCKER_COUNT_CAP

    def test_fewer_than_cap_blockers_all_present(self, conn):
        for i in range(3):
            _seed_rule(conn, "", "BLOCKER", topic_idx=i)
        payload = boot.build(conn, project="test", task="any", source="test")
        assert len(payload.blockers) == 3


class TestHeadlineOnlyPayload:
    def test_blockers_have_headline_no_body(self, conn):
        _seed_rule(conn, "", "BLOCKER", topic_idx=0)
        payload = boot.build(conn, project="test", task="any", source="test")
        for b in payload.blockers:
            assert "headline" in b
            assert "body" not in b

    def test_patterns_have_headline_no_body(self, conn):
        _seed_rule(conn, "", "PATTERN", topic_idx=0)
        payload = boot.build(conn, project="test", task="example", source="test")
        assert payload.patterns
        for p in payload.patterns:
            assert "headline" in p
            assert "body" not in p


class TestPatternCountCap:
    def test_pattern_count_capped(self, conn):
        for i in range(BOOT_PATTERN_COUNT_CAP + 4):
            _seed_rule(conn, "", "PATTERN", topic_idx=i)
        payload = boot.build(conn, project="test", task="pattern work", source="test")
        assert len(payload.patterns) == BOOT_PATTERN_COUNT_CAP


class TestTokenCap:
    def test_fits_inside_token_cap(self, conn):
        # 15 diverse PATTERNs; write-gate duplicate detection may reject some,
        # which is itself a valid outcome. The assertion is about token_estimate.
        for i in range(15):
            _seed_rule(conn, "", "PATTERN", topic_idx=i)
        payload = boot.build(conn, project="test", task="task description", source="test")
        assert payload.token_estimate <= BOOT_TOKEN_CAP

    def test_tasks_truncated_before_blockers(self, conn):
        for i in range(BOOT_BLOCKER_COUNT_CAP):
            _seed_rule(conn, "", "BLOCKER", topic_idx=i)
        # Fewer tasks but each large enough that together they exceed the
        # 2K token budget, forcing truncation. Each content is ~2300 chars
        # ≈ 575 tokens; 12 of them = ~6900 tokens, well over cap.
        for i in range(12):
            store.remember_task(
                conn,
                content=(
                    f"Task {i:02d} action item short\n"
                    + ("additional detail line describing the subtask in depth. " * 40)
                ),
                project="test", priority="medium", source="test",
            )
        payload = boot.build(conn, project="test", task="any", source="test")
        # All 5 blockers must survive; at least some tasks got dropped
        assert len(payload.blockers) == BOOT_BLOCKER_COUNT_CAP
        assert any(s.startswith("task:") for s in payload.truncated)


class TestWorkingContextEphemeral:
    def test_working_context_reflects_task_arg(self, conn):
        task_text = "implement the whatsit module on branch feat/xyz"
        payload = boot.build(conn, project="test", task=task_text, source="test")
        assert payload.working_context["task"] == task_text
        assert payload.working_context["project"] == "test"

    def test_working_context_not_persisted(self, conn):
        boot.build(conn, project="test", task="transient task text", source="test")
        # WORKING CONTEXT must NOT show up in any stored table
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memory_index")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT COUNT(*) FROM tasks")
            assert cur.fetchone()[0] == 0


class TestSupersededRulesExcluded:
    def test_superseded_blocker_does_not_appear(self, conn):
        r = store.remember_rule(
            conn, headline="Original specific phrasing for rule X",
            body="Original body text for rule X.",
            severity="BLOCKER", project="test", source="test",
        )
        store.supersede_rule(
            conn, old_id=r.id,
            new_headline="Clarified phrasing with extra context",
            new_body="Revised body text for rule X with more detail.",
            reason="clarity revision",
            source="test",
        )
        payload = boot.build(conn, project="test", task="any", source="test")
        ids = {b["memory_id"] for b in payload.blockers}
        assert r.id not in ids
        # New rule is present
        assert any(b["headline"] == "Clarified phrasing with extra context"
                   for b in payload.blockers)


class TestProjectScoping:
    def test_project_scoped_and_global_both_load(self, conn):
        store.remember_rule(
            conn, headline="Global workflow rule applying everywhere",
            body="Affects all projects.", severity="BLOCKER",
            project="", source="test",
        )
        store.remember_rule(
            conn, headline="Alpha-specific deployment constraint",
            body="Only applies to project alpha.", severity="BLOCKER",
            project="alpha", source="test",
        )
        store.remember_rule(
            conn, headline="Beta dashboard configuration requirement",
            body="Only applies to project beta.", severity="BLOCKER",
            project="beta", source="test",
        )
        payload = boot.build(conn, project="alpha", task="any", source="test")
        heads = {b["headline"] for b in payload.blockers}
        assert "Global workflow rule applying everywhere" in heads
        assert "Alpha-specific deployment constraint" in heads
        assert "Beta dashboard configuration requirement" not in heads
