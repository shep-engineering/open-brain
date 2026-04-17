"""Recall / search / temporal-cache tests."""
from __future__ import annotations

from brain_v2 import store, temporal_cache


class TestRecallReturnsBody:
    def test_recall_returns_full_body(self, conn):
        r = store.remember_rule(
            conn, headline="Run tests before push",
            body="pytest must pass locally before any git push; no exceptions.",
            severity="BLOCKER", project="test", source="test",
        )
        fetched = store.recall(conn, kind="rule", memory_id=r.id)
        assert fetched is not None
        assert fetched.body == "pytest must pass locally before any git push; no exceptions."
        assert fetched.headline == "Run tests before push"
        assert fetched.severity == "BLOCKER"

    def test_recall_missing_returns_none(self, conn):
        assert store.recall(conn, kind="rule", memory_id=99999) is None

    def test_recall_fact_increments_access_count(self, conn):
        f = store.remember_fact(
            conn, headline="Project uses Postgres 16",
            body="The v2 container runs pgvector/pgvector:pg16.",
            project="test", source="test",
        )
        store.recall(conn, kind="fact", memory_id=f.id)
        store.recall(conn, kind="fact", memory_id=f.id)
        with conn.cursor() as cur:
            cur.execute("SELECT access_count FROM facts WHERE id = %s", (f.id,))
            assert cur.fetchone()[0] == 2


class TestSearchHeadlineOnly:
    def test_search_returns_headlines_not_bodies(self, conn):
        store.remember_rule(
            conn, headline="Prefer feature branches",
            body="Always use feat/, fix/, chore/, docs/ prefixes.",
            severity="PATTERN", project="test", source="test",
        )
        results = store.search_headlines(conn, query="git branching strategy", project="test")
        assert results
        for r in results:
            assert "headline" in r
            assert "body" not in r

    def test_search_respects_kind_filter(self, conn):
        store.remember_rule(conn, headline="Rule kind example",
                            body="Body.", severity="PATTERN",
                            project="test", source="test")
        store.remember_fact(conn, headline="Fact kind example",
                            body="Body.", project="test", source="test")
        only_facts = store.search_headlines(conn, query="example", kind="fact", project="test")
        assert all(r["kind"] == "fact" for r in only_facts)
        only_rules = store.search_headlines(conn, query="example", kind="rule", project="test")
        assert all(r["kind"] == "rule" for r in only_rules)


class TestTemporalCache:
    def test_cache_boost_after_mark(self):
        c = temporal_cache.SessionCache(session_id="s1")
        c.mark_retrieved("rule", 42)
        assert c.boost_for("rule", 42) > 0.0
        assert c.boost_for("rule", 99) == 0.0

    def test_link_boost(self):
        c = temporal_cache.SessionCache(session_id="s2")
        c.apply_link_boost([("rule", 1), ("incident", 7)])
        assert c.boost_for("rule", 1) > 0.0
        assert c.boost_for("incident", 7) > 0.0

    def test_reset_clears_cache(self):
        c = temporal_cache.get("s3")
        c.mark_retrieved("rule", 1)
        temporal_cache.reset("s3")
        fresh = temporal_cache.get("s3")
        assert fresh.boost_for("rule", 1) == 0.0


class TestTaskLifecycle:
    def test_task_status_transitions(self, conn):
        t = store.remember_task(
            conn, content="Write the falsifiable Ollama check",
            project="test", priority="high", source="test",
        )
        store.update_task_status(conn, task_id=t.id, status="done", source="test")
        # done tasks go inactive in memory_index
        with conn.cursor() as cur:
            cur.execute("SELECT active FROM memory_index WHERE kind='task' AND memory_id=%s", (t.id,))
            assert cur.fetchone()[0] is False
            cur.execute("SELECT status FROM tasks WHERE id=%s", (t.id,))
            assert cur.fetchone()[0] == "done"

    def test_invalid_status_rejected(self, conn):
        t = store.remember_task(conn, content="A task",
                                 project="test", source="test")
        import pytest
        with pytest.raises(ValueError, match="invalid status"):
            store.update_task_status(conn, task_id=t.id, status="wip", source="test")
