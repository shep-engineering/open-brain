"""v1 tool parity tests: annotate / rate / pin / unpin / scratch / brain_checkpoint.

All 8 store-level semantics exercised against real Postgres. Scratch +
brain_checkpoint tests additionally exercise in-process state at the
server.py layer.
"""
from __future__ import annotations

import time

import pytest

from brain_v2 import store


class TestAnnotate:
    def test_set_and_read(self, conn):
        r = store.remember_rule(
            conn, headline="Rule with annotation attached for testing",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        # Read-only: empty initially
        read = store.annotate(conn, kind="rule", memory_id=r.id)
        assert read["annotation"] == ""

        # Set
        result = store.annotate(
            conn, kind="rule", memory_id=r.id,
            note="Corrected in incident #42 — do not follow verbatim.",
        )
        assert result["cleared"] is False
        assert "Corrected" in result["annotation"]

        # Read again
        read = store.annotate(conn, kind="rule", memory_id=r.id)
        assert "Corrected" in read["annotation"]

    def test_clear(self, conn):
        f = store.remember_fact(
            conn, headline="Annotated fact about project deployment",
            body="Body.", project="test", source="test",
        )
        store.annotate(conn, kind="fact", memory_id=f.id,
                       note="This annotation will be cleared.")
        cleared = store.annotate(conn, kind="fact", memory_id=f.id, clear=True)
        assert cleared["cleared"] is True
        assert cleared["annotation"] is None
        # Verify stored
        read = store.annotate(conn, kind="fact", memory_id=f.id)
        assert read["annotation"] == ""

    def test_missing_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            store.annotate(conn, kind="rule", memory_id=99999, note="x")


class TestRate:
    def test_upvote(self, conn):
        r = store.remember_rule(
            conn, headline="Rate upvote target rule for testing",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        result = store.rate(conn, kind="rule", memory_id=r.id, direction="up")
        assert result["upvotes"] == 1
        assert result["downvotes"] == 0
        assert result["score"] == 1

    def test_downvote(self, conn):
        r = store.remember_rule(
            conn, headline="Rate downvote target rule for testing",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        result = store.rate(conn, kind="rule", memory_id=r.id, direction="down")
        assert result["upvotes"] == 0
        assert result["downvotes"] == 1
        assert result["score"] == -1

    def test_multiple_rates_accumulate(self, conn):
        r = store.remember_rule(
            conn, headline="Rate accumulation target rule here",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        store.rate(conn, kind="rule", memory_id=r.id, direction="up")
        store.rate(conn, kind="rule", memory_id=r.id, direction="up")
        result = store.rate(conn, kind="rule", memory_id=r.id, direction="down")
        assert result["upvotes"] == 2
        assert result["downvotes"] == 1
        assert result["score"] == 1

    def test_invalid_direction(self, conn):
        r = store.remember_rule(
            conn, headline="Rate bad direction rejection test rule",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        with pytest.raises(ValueError, match="direction"):
            store.rate(conn, kind="rule", memory_id=r.id, direction="neutral")

    def test_missing_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            store.rate(conn, kind="rule", memory_id=99999, direction="up")


class TestPinUnpin:
    def test_pin_project_scoped_succeeds(self, conn):
        r = store.remember_rule(
            conn, headline="Project scoped rule for pinning test",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        result = store.set_pinned(conn, kind="rule", memory_id=r.id, pinned=True)
        assert result["pinned"] is True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pinned FROM memory_index WHERE kind='rule' AND memory_id=%s",
                (r.id,),
            )
            assert cur.fetchone()[0] is True

    def test_pin_global_rejected(self, conn):
        r = store.remember_rule(
            conn, headline="Global rule cannot be pinned per v1 rule",
            body="Body.", severity="PATTERN",
            project="", source="test",
        )
        with pytest.raises(ValueError, match="Cannot pin"):
            store.set_pinned(conn, kind="rule", memory_id=r.id, pinned=True)
        # Verify not pinned
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pinned FROM memory_index WHERE kind='rule' AND memory_id=%s",
                (r.id,),
            )
            assert cur.fetchone()[0] is False

    def test_unpin(self, conn):
        r = store.remember_rule(
            conn, headline="Rule to be unpinned after pinning for test",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        store.set_pinned(conn, kind="rule", memory_id=r.id, pinned=True)
        result = store.set_pinned(conn, kind="rule", memory_id=r.id, pinned=False)
        assert result["pinned"] is False

    def test_unpin_global_is_noop_success(self, conn):
        """Unpinning a never-pinned global memory should not error."""
        r = store.remember_rule(
            conn, headline="Global rule unpin is a no-op success path",
            body="Body.", severity="PATTERN",
            project="", source="test",
        )
        result = store.set_pinned(conn, kind="rule", memory_id=r.id, pinned=False)
        assert result["pinned"] is False

    def test_pin_fact(self, conn):
        """Pinning works for any kind, not just rules."""
        f = store.remember_fact(
            conn, headline="Fact about project architecture to pin now",
            body="Body.", project="test", source="test",
        )
        result = store.set_pinned(conn, kind="fact", memory_id=f.id, pinned=True)
        assert result["pinned"] is True

    def test_pin_missing_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            store.set_pinned(conn, kind="rule", memory_id=99999, pinned=True)


class TestScratchpad:
    """Scratchpad is in-process state — exercised via server module."""

    def setup_method(self):
        # Clean slate before each test
        import brain_v2.server as srv
        srv._scratch.clear()
        srv._checkpoint_tracker.clear()

    def test_set_and_get(self):
        import json
        import brain_v2.server as srv
        r = json.loads(srv.scratch_set_v2(key="current_task", value="v1 parity tools"))
        assert r["success"] is True

        r = json.loads(srv.scratch_get_v2(key="current_task"))
        assert r["found"] is True
        assert r["value"] == "v1 parity tools"

    def test_get_missing(self):
        import json
        import brain_v2.server as srv
        r = json.loads(srv.scratch_get_v2(key="nonexistent"))
        assert r["found"] is False
        assert r["value"] is None

    def test_list(self):
        import json
        import brain_v2.server as srv
        srv.scratch_set_v2(key="k1", value="v1")
        srv.scratch_set_v2(key="k2", value="v2")
        r = json.loads(srv.scratch_list_v2())
        assert r["count"] == 2
        assert r["entries"]["k1"] == "v1"
        assert r["entries"]["k2"] == "v2"

    def test_overwrite(self):
        import json
        import brain_v2.server as srv
        srv.scratch_set_v2(key="x", value="first")
        srv.scratch_set_v2(key="x", value="second")
        r = json.loads(srv.scratch_get_v2(key="x"))
        assert r["value"] == "second"


class TestBrainCheckpoint:
    def setup_method(self):
        import brain_v2.server as srv
        srv._checkpoint_tracker.clear()

    def test_source_required(self):
        import json
        import brain_v2.server as srv
        r = json.loads(srv.brain_checkpoint_v2(action="edit infrastructure", source=""))
        assert r.get("blocked_by") == "source_required"

    def test_action_required(self):
        import json
        import brain_v2.server as srv
        r = json.loads(srv.brain_checkpoint_v2(action="", source="test"))
        assert "error" in r
        assert "action" in r["error"].lower()

    def test_surfaces_blocker_rules(self, conn):
        # Seed a BLOCKER rule scoped to the project
        store.remember_rule(
            conn, headline="Never deploy on Fridays without rollback plan",
            body="Body.", severity="BLOCKER",
            project="checkpoint_test", source="test",
        )
        import json
        import brain_v2.server as srv
        r = json.loads(srv.brain_checkpoint_v2(
            action="deploy to production",
            source="test",
            project="checkpoint_test",
        ))
        assert r["success"] is True
        assert r["guardrails_count"] >= 1
        headlines = [g["headline"] for g in r["guardrails"]]
        assert any("Fridays" in h for h in headlines)

    def test_cooldown_skips_repeat(self, conn):
        import json
        import brain_v2.server as srv
        srv._checkpoint_tracker.clear()
        # First call — runs
        r1 = json.loads(srv.brain_checkpoint_v2(
            action="edit schema migration",
            source="cooldown_src",
            project="checkpoint_test",
        ))
        assert r1.get("skipped") is not True
        # Second call immediately — skipped
        r2 = json.loads(srv.brain_checkpoint_v2(
            action="edit schema migration",
            source="cooldown_src",
            project="checkpoint_test",
        ))
        assert r2.get("skipped") is True

    def test_different_actions_not_skipped(self, conn):
        import json
        import brain_v2.server as srv
        srv._checkpoint_tracker.clear()
        r1 = json.loads(srv.brain_checkpoint_v2(
            action="edit database config",
            source="different_actions_src",
            project="checkpoint_test",
        ))
        r2 = json.loads(srv.brain_checkpoint_v2(
            action="push to remote branch",
            source="different_actions_src",
            project="checkpoint_test",
        ))
        assert r1.get("skipped") is not True
        assert r2.get("skipped") is not True


class TestIntegration:
    """End-to-end checks that each persistent tool persists correctly."""

    def test_annotation_persists_in_memory_index(self, conn):
        r = store.remember_rule(
            conn, headline="Persistence check for annotation column",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        store.annotate(conn, kind="rule", memory_id=r.id,
                       note="Annotation should persist across connections.")
        # Open a fresh connection
        with store.connect() as fresh:
            with fresh.cursor() as cur:
                cur.execute(
                    "SELECT annotation FROM memory_index "
                    "WHERE kind='rule' AND memory_id=%s",
                    (r.id,),
                )
                assert "persist" in cur.fetchone()[0]

    def test_vote_counts_persist(self, conn):
        r = store.remember_rule(
            conn, headline="Persistence check for vote counters",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        store.rate(conn, kind="rule", memory_id=r.id, direction="up")
        store.rate(conn, kind="rule", memory_id=r.id, direction="up")
        store.rate(conn, kind="rule", memory_id=r.id, direction="down")
        with store.connect() as fresh:
            with fresh.cursor() as cur:
                cur.execute(
                    "SELECT upvotes, downvotes FROM memory_index "
                    "WHERE kind='rule' AND memory_id=%s",
                    (r.id,),
                )
                up, down = cur.fetchone()
                assert up == 2 and down == 1
