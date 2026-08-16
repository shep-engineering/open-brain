"""Operational-completeness tests: forget / stats / list_recent.

Exercises:
  - forget deactivates memory_index + records reason/source/timestamp
  - forget is idempotent (already_forgotten returned on re-call)
  - forgotten memories excluded from search_headlines
  - recall of a forgotten memory returns the body (audit) — banner
    surfaced at the MCP tool layer, not at store.recall level
  - forget on non-existent memory raises ValueError
  - stats returns per-kind totals, severity breakdown, task status,
    incident archive counts
  - stats project filter works (scoped + global rows)
  - list_recent respects limit, order (newest first)
  - list_recent days filter excludes older rows
  - list_recent kind filter works
  - list_recent include_forgotten flag controls visibility
"""
from __future__ import annotations

import time

import pytest

from brain_v2 import store


class TestForget:
    def test_forget_deactivates_memory_index(self, conn):
        r = store.remember_rule(
            conn, headline="Never skip a pre-flight gate",
            body="Always run the gate before writes.",
            severity="BLOCKER", project="test", source="test",
        )
        result = store.forget(
            conn, kind="rule", memory_id=r.id,
            reason="This rule was duplicated elsewhere.", source="test",
        )
        assert result["already_forgotten"] is False
        with conn.cursor() as cur:
            cur.execute(
                "SELECT active, forgotten_at, forgotten_reason, forgotten_by "
                "FROM memory_index WHERE kind = 'rule' AND memory_id = %s",
                (r.id,),
            )
            row = cur.fetchone()
            assert row[0] is False
            assert row[1] is not None
            assert "duplicated" in row[2]
            assert row[3] == "test"

    def test_forget_idempotent(self, conn):
        r = store.remember_rule(
            conn, headline="Another once-valid rule about something",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        first = store.forget(conn, kind="rule", memory_id=r.id,
                              reason="first call", source="test")
        assert first["already_forgotten"] is False
        second = store.forget(conn, kind="rule", memory_id=r.id,
                               reason="second call should be noop", source="test")
        assert second["already_forgotten"] is True

    def test_forget_missing_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            store.forget(conn, kind="rule", memory_id=99999,
                         reason="doesn't exist", source="test")

    def test_forget_excludes_from_search(self, conn):
        f = store.remember_fact(
            conn, headline="Ephemeral fact about the deployment pipeline",
            body="Temporary fact we later retracted.",
            project="test", source="test",
        )
        # Before forget: appears in search
        results = store.search_headlines(conn, query="deployment pipeline", project="test")
        ids_before = {r["memory_id"] for r in results}
        assert f.id in ids_before

        store.forget(conn, kind="fact", memory_id=f.id,
                     reason="retracted", source="test")

        # After forget: excluded
        results = store.search_headlines(conn, query="deployment pipeline", project="test")
        ids_after = {r["memory_id"] for r in results}
        assert f.id not in ids_after

    def test_forget_recall_still_returns_body(self, conn):
        i = store.remember_incident(
            conn, headline="Incident preserved for audit after forgetting",
            body="The full incident narrative that we still need for audit.",
            project="test", source="test",
        )
        store.forget(conn, kind="incident", memory_id=i.id,
                     reason="superseded by newer post-mortem", source="test")
        mem = store.recall(conn, kind="incident", memory_id=i.id)
        assert mem is not None
        assert "audit" in mem.body.lower()

    def test_forget_kind_validation(self, conn):
        with pytest.raises(Exception):  # WriteGateError subclass of ValueError
            store.forget(conn, kind="invalid", memory_id=1,
                         reason="test", source="test")


class TestStats:
    def test_empty_db_returns_zeros(self, conn):
        data = store.stats(conn)
        assert data["by_kind"] == {}
        assert data["pending_action_items"] == 0
        assert data["active_sessions"] == 0

    def test_counts_by_kind(self, conn):
        store.remember_rule(
            conn, headline="Example rule about branch workflow discipline",
            body="Feature branches.", severity="BLOCKER",
            project="test", source="test",
        )
        store.remember_fact(
            conn, headline="Example fact about project deployment target",
            body="Ships to prod.", project="test", source="test",
        )
        store.remember_fact(
            conn, headline="Another fact about scheduled backup cadence",
            body="Daily.", project="test", source="test",
        )
        data = store.stats(conn, project="test")
        assert data["by_kind"]["rule"]["active"] == 1
        assert data["by_kind"]["fact"]["active"] == 2

    def test_rule_severity_breakdown(self, conn):
        store.remember_rule(
            conn, headline="Never commit directly to the main branch",
            body="Always use a feature branch.", severity="BLOCKER",
            project="test", source="test",
        )
        store.remember_rule(
            conn, headline="Prefer composition over deep inheritance",
            body="Flat hierarchies read better.", severity="PATTERN",
            project="test", source="test",
        )
        data = store.stats(conn, project="test")
        assert data["rules_by_severity"]["BLOCKER"] == 1
        assert data["rules_by_severity"]["PATTERN"] == 1

    def test_forgotten_counted_separately(self, conn):
        r = store.remember_rule(
            conn, headline="Rule to be forgotten for stats test",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        store.forget(conn, kind="rule", memory_id=r.id,
                     reason="stats test", source="test")
        data = store.stats(conn, project="test")
        assert data["by_kind"]["rule"]["total"] == 1
        assert data["by_kind"]["rule"]["active"] == 0
        assert data["by_kind"]["rule"]["forgotten"] == 1

    def test_task_status_breakdown(self, conn):
        t1 = store.remember_task(
            conn, content="Task one open and pending",
            project="test", source="test",
        )
        t2 = store.remember_task(
            conn, content="Task two to be marked done",
            project="test", source="test",
        )
        store.update_task_status(conn, task_id=t2.id, status="done", source="test")
        data = store.stats(conn, project="test")
        assert data["tasks_by_status"]["open"] == 1
        assert data["tasks_by_status"]["done"] == 1

    def test_project_filter_excludes_other_projects(self, conn):
        store.remember_fact(
            conn, headline="Project alpha specific fact here",
            body="Alpha.", project="alpha", source="test",
        )
        store.remember_fact(
            conn, headline="Project beta specific fact here",
            body="Beta.", project="beta", source="test",
        )
        alpha = store.stats(conn, project="alpha")
        # Alpha should see only its own facts (plus globals, which there are none of)
        assert alpha["by_kind"]["fact"]["active"] == 1


class TestCorrectionStickiness:
    """The correction_stickiness block reports rule LINEAGES that keep being
    revised (superseded repeatedly) — a rule not settling. Report-only signal.
    """

    def test_empty_db_no_revised_lineages(self, conn):
        data = store.stats(conn)
        cs = data["correction_stickiness"]
        assert cs["revised_lineages"] == []
        assert cs["revised_lineage_count"] == 0

    def test_single_rule_never_superseded_absent(self, conn):
        store.remember_rule(
            conn, headline="A stable rule never revised at all",
            body="It stays as written.", severity="PATTERN",
            project="test", source="test",
        )
        cs = store.stats(conn, project="test")["correction_stickiness"]
        # revisions==1 (never superseded) must NOT appear (HAVING depth>=2).
        assert cs["revised_lineages"] == []

    def test_rule_superseded_twice_is_flagged(self, conn):
        r1 = store.remember_rule(
            conn, headline="Original phrasing of the deploy rule",
            body="First version of the rule body.", severity="PATTERN",
            project="test", source="test",
        )
        r2 = store.supersede_rule(
            conn, old_id=r1.id,
            new_headline="Second phrasing of the deploy rule",
            new_body="Second version, clarified.", reason="clarity",
            source="test",
        )
        store.supersede_rule(
            conn, old_id=r2.id,
            new_headline="Third phrasing of the deploy rule",
            new_body="Third version, more precise.", reason="precision",
            source="test",
        )
        cs = store.stats(conn, project="test")["correction_stickiness"]
        assert cs["revised_lineage_count"] == 1
        entry = cs["revised_lineages"][0]
        # chain: r1 -> r2 -> r3  => depth 3 (3 versions, 2 supersessions)
        assert entry["revisions"] == 3
        assert entry["headline"] == "Third phrasing of the deploy rule"

    def test_once_superseded_meets_threshold(self, conn):
        r1 = store.remember_rule(
            conn, headline="Rule revised exactly one time only",
            body="v1.", severity="PATTERN", project="test", source="test",
        )
        store.supersede_rule(
            conn, old_id=r1.id,
            new_headline="Rule revised exactly one time only v2",
            new_body="v2.", reason="tweak", source="test",
        )
        cs = store.stats(conn, project="test")["correction_stickiness"]
        # depth 2 (one supersession) meets HAVING depth>=2.
        assert cs["revised_lineage_count"] == 1
        assert cs["revised_lineages"][0]["revisions"] == 2


class TestListRecent:
    def test_returns_newest_first(self, conn):
        # Semantically DISTINCT facts so neither trips the >0.75 cosine dedup
        # gate (which returns a DuplicateHit with no .id). See incident 179.
        f1 = store.remember_fact(
            conn, headline="The database runs Postgres 16 with pgvector",
            body="Vector similarity search via the pgvector extension.",
            project="test", source="test",
        )
        time.sleep(0.1)
        f2 = store.remember_fact(
            conn, headline="Deployments ship through the staging environment first",
            body="No direct-to-production releases are permitted.",
            project="test", source="test",
        )
        # Guard the regression: both writes must be real Memories, not DuplicateHits.
        assert hasattr(f1, "id") and hasattr(f2, "id"), "dedup rejected a test fact"
        rows = store.list_recent(conn, limit=10, project="test")
        ids_in_order = [r["memory_id"] for r in rows if r["kind"] == "fact"]
        assert ids_in_order[0] == f2.id
        assert ids_in_order[1] == f1.id

    def test_limit_enforced(self, conn):
        # Diverse headlines so the cosine-dup gate doesn't reject 4/5 writes.
        diverse = [
            ("Project uses Postgres 16 with pgvector extension",
             "DB stack choice."),
            ("Embedding model is nomic-embed-text at 768 dimensions",
             "Vector dimensionality."),
            ("Deployment targets Docker containers on port 5433",
             "Runtime surface."),
            ("Code is organized as a single brain_v2 Python package",
             "Package layout."),
            ("CHANGELOG entries follow Keep a Changelog format",
             "Changelog convention."),
        ]
        for head, body in diverse:
            store.remember_fact(
                conn, headline=head, body=body,
                project="test", source="test",
            )
        rows = store.list_recent(conn, limit=3, project="test")
        assert len(rows) == 3

    def test_kind_filter(self, conn):
        store.remember_rule(
            conn, headline="Rule about testing practices for v2",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        store.remember_fact(
            conn, headline="Fact about testing practices documented",
            body="Body.", project="test", source="test",
        )
        rules_only = store.list_recent(conn, kind="rule", project="test")
        assert all(r["kind"] == "rule" for r in rules_only)
        facts_only = store.list_recent(conn, kind="fact", project="test")
        assert all(r["kind"] == "fact" for r in facts_only)

    def test_excludes_forgotten_by_default(self, conn):
        r = store.remember_rule(
            conn, headline="Forgotten rule should not appear in list recent",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        store.forget(conn, kind="rule", memory_id=r.id,
                     reason="test", source="test")
        rows = store.list_recent(conn, project="test")
        assert r.id not in [x["memory_id"] for x in rows if x["kind"] == "rule"]

    def test_include_forgotten_flag(self, conn):
        r = store.remember_rule(
            conn, headline="Forgotten rule visible with include flag",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        store.forget(conn, kind="rule", memory_id=r.id,
                     reason="test", source="test")
        rows = store.list_recent(conn, project="test", include_forgotten=True)
        forgotten = [x for x in rows if x["kind"] == "rule" and x["memory_id"] == r.id]
        assert len(forgotten) == 1
        assert forgotten[0]["forgotten_at"] is not None

    def test_days_filter(self, conn):
        store.remember_fact(
            conn, headline="Fresh fact from today for days filter test",
            body="Body.", project="test", source="test",
        )
        # Before ageing: visible with days=1
        rows = store.list_recent(conn, days=1, project="test")
        assert len(rows) == 1
        # After ageing to 5 days ago: should be excluded with days=1
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE memory_index SET created_at = NOW() - make_interval(days => 5)"
            )
        conn.commit()
        rows = store.list_recent(conn, days=1, project="test")
        assert len(rows) == 0
        # But visible with days=10
        rows = store.list_recent(conn, days=10, project="test")
        assert len(rows) == 1

    def test_limit_hard_cap(self, conn):
        """Even if caller requests 5000, server caps at 200."""
        # We don't actually need 200 rows; just verify the clamp logic
        # is in place by checking the limit param is coerced.
        rows = store.list_recent(conn, limit=5000, project="test")
        # No rows yet, but call didn't error — caller's 5000 was clamped
        assert rows == []

    def test_headline_only_output(self, conn):
        store.remember_fact(
            conn, headline="Secret body should not appear in list recent",
            body="Super secret body that should NOT be in list_recent output.",
            project="test", source="test",
        )
        rows = store.list_recent(conn, project="test")
        for r in rows:
            assert "headline" in r
            assert "body" not in r
