"""Final v1-parity tests: forget_many / unsupersede / brain_startup_reminder / prune.

All 4 tools exercised against real Postgres. prune tests verify the
hard safeguards (30-day floor, 50-row cap, dry-run default, excluded
pinned/rules).
"""
from __future__ import annotations

import json

import pytest

from brain_v2 import store
from brain_v2.config import PRUNE_MAX_DELETE, PRUNE_MIN_DAYS


def _age_memory_index(conn, days_ago: float) -> None:
    """Age every memory_index row by N days for prune testing."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE memory_index SET created_at = NOW() - make_interval(days => %s)",
            (int(days_ago),),
        )
    conn.commit()


class TestForgetMany:
    def test_batch_forget(self, conn):
        # Diverse content so the cosine-dup gate doesn't reject duplicates
        a = store.remember_fact(
            conn, headline="Project uses Postgres 16 with pgvector extension",
            body="DB choice.", project="test", source="test",
        )
        b = store.remember_fact(
            conn, headline="Embedding model is nomic-embed-text 768 dims",
            body="Vector dim.", project="test", source="test",
        )
        c = store.remember_fact(
            conn, headline="Deployment targets Docker containers on port 5433",
            body="Runtime.", project="test", source="test",
        )
        items = [{"kind": "fact", "memory_id": x.id} for x in (a, b, c)]
        result = store.forget_many(conn, items=items,
                                    reason="batch cleanup", source="test")
        assert result["forgotten_count"] == 3
        assert result["total_requested"] == 3
        for x in (a, b, c):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT active, forgotten_at FROM memory_index "
                    "WHERE kind='fact' AND memory_id=%s",
                    (x.id,),
                )
                active, forgotten_at = cur.fetchone()
                assert active is False
                assert forgotten_at is not None

    def test_partial_not_found(self, conn):
        a = store.remember_fact(
            conn, headline="Valid fact that will be forgotten in batch",
            body="Body.", project="test", source="test",
        )
        items = [
            {"kind": "fact", "memory_id": a.id},
            {"kind": "fact", "memory_id": 99999},  # does not exist
        ]
        result = store.forget_many(conn, items=items,
                                    reason="mixed", source="test")
        assert result["forgotten_count"] == 1
        assert ("fact", a.id) in result["forgotten"]
        assert ("fact", 99999) in result["not_found"]

    def test_idempotent_second_call(self, conn):
        a = store.remember_fact(
            conn, headline="Fact forgotten twice via batch idempotency test",
            body="Body.", project="test", source="test",
        )
        items = [{"kind": "fact", "memory_id": a.id}]
        first = store.forget_many(conn, items=items, reason="first", source="test")
        assert first["forgotten_count"] == 1
        second = store.forget_many(conn, items=items, reason="second", source="test")
        assert second["forgotten_count"] == 0
        assert ("fact", a.id) in second["already_forgotten"]

    def test_empty_batch(self, conn):
        result = store.forget_many(conn, items=[], reason="", source="test")
        assert result["forgotten_count"] == 0
        assert result["total_requested"] == 0

    def test_mixed_kinds(self, conn):
        r = store.remember_rule(
            conn, headline="Rule for mixed-kind batch forget test",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        f = store.remember_fact(
            conn, headline="Fact for mixed-kind batch forget test alongside",
            body="Body.", project="test", source="test",
        )
        items = [
            {"kind": "rule", "memory_id": r.id},
            {"kind": "fact", "memory_id": f.id},
        ]
        result = store.forget_many(conn, items=items, reason="mixed", source="test")
        assert result["forgotten_count"] == 2


class TestUnsupersede:
    def test_reverses_chain(self, conn):
        old = store.remember_rule(
            conn, headline="Original rule before supersession for test",
            body="Original body.", severity="BLOCKER",
            project="test", source="test",
        )
        new = store.supersede_rule(
            conn, old_id=old.id,
            new_headline="Revised rule replacement for supersession test",
            new_body="Revised body.", reason="testing supersede",
            source="test",
        )
        # Old is DEPRECATED + deactivated, new is active
        with conn.cursor() as cur:
            cur.execute("SELECT severity, superseded_by FROM rules WHERE id = %s", (old.id,))
            sev, sby = cur.fetchone()
            assert sev == "DEPRECATED"
            assert sby == new.id

        # Unsupersede
        result = store.unsupersede_rule(conn, old_id=old.id, source="test")
        assert result["old_id"] == old.id
        assert result["former_corrector"] == new.id
        # Old is now active again, severity restored to BLOCKER
        with conn.cursor() as cur:
            cur.execute("SELECT severity, superseded_by FROM rules WHERE id = %s", (old.id,))
            sev, sby = cur.fetchone()
            assert sev == "BLOCKER"
            assert sby is None
            cur.execute("SELECT active, severity FROM memory_index WHERE kind='rule' AND memory_id=%s", (old.id,))
            active, mi_sev = cur.fetchone()
            assert active is True
            assert mi_sev == "BLOCKER"

    def test_corrector_retired_by_default(self, conn):
        # INVERTED (was test_corrector_remains_active, which encoded the
        # double-active BUG). unsupersede now retires the corrector by default so
        # a superseded rule and its corrector are never both active.
        old = store.remember_rule(
            conn, headline="Rule about corrector active state persistence",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        new = store.supersede_rule(
            conn, old_id=old.id,
            new_headline="Corrector rule retired on undo by default",
            new_body="Corrector body.",
            reason="test", source="test",
        )
        result = store.unsupersede_rule(conn, old_id=old.id, source="test")
        assert result["corrector_retired"] is True
        with conn.cursor() as cur:
            # original active again
            cur.execute("SELECT active FROM memory_index WHERE kind='rule' AND memory_id=%s", (old.id,))
            assert cur.fetchone()[0] is True
            # corrector now INACTIVE (retired)
            cur.execute("SELECT active FROM memory_index WHERE kind='rule' AND memory_id=%s", (new.id,))
            assert cur.fetchone()[0] is False

    def test_keep_corrector_leaves_both_active(self, conn):
        # Opt-in preservation of the old both-active behavior.
        old = store.remember_rule(
            conn, headline="Rule for keep-corrector both-active path",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        new = store.supersede_rule(
            conn, old_id=old.id,
            new_headline="Corrector kept active via keep_corrector flag",
            new_body="Corrector body.",
            reason="test", source="test",
        )
        result = store.unsupersede_rule(conn, old_id=old.id, source="test",
                                        keep_corrector=True)
        assert result["corrector_retired"] is False
        with conn.cursor() as cur:
            cur.execute("SELECT active FROM memory_index WHERE kind='rule' AND memory_id=%s", (old.id,))
            assert cur.fetchone()[0] is True
            cur.execute("SELECT active FROM memory_index WHERE kind='rule' AND memory_id=%s", (new.id,))
            assert cur.fetchone()[0] is True

    def test_unsupersede_mid_chain_refused(self, conn):
        # r1 -> r2 -> r3: r2 (r1's corrector) is itself superseded. Undoing r1
        # can't yield a single clean state, so it must be refused by default.
        r1 = store.remember_rule(
            conn, headline="Mid-chain rule one original version",
            body="v1.", severity="PATTERN", project="test", source="test",
        )
        r2 = store.supersede_rule(
            conn, old_id=r1.id, new_headline="Mid-chain rule two revision",
            new_body="v2.", reason="rev1", source="test",
        )
        store.supersede_rule(
            conn, old_id=r2.id, new_headline="Mid-chain rule three revision",
            new_body="v3.", reason="rev2", source="test",
        )
        import pytest
        with pytest.raises(ValueError, match="mid-chain"):
            store.unsupersede_rule(conn, old_id=r1.id, source="test")

    def test_not_superseded_raises(self, conn):
        r = store.remember_rule(
            conn, headline="Rule never superseded for negative test case",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        with pytest.raises(ValueError, match="not superseded"):
            store.unsupersede_rule(conn, old_id=r.id, source="test")

    def test_missing_id_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            store.unsupersede_rule(conn, old_id=99999, source="test")


class TestBrainStartupReminder:
    def test_returns_structured_message(self):
        import brain_v2.server as srv
        payload = json.loads(srv.brain_startup_reminder_v2())
        assert payload["type"] == "system_message"
        assert payload["level"] == "mandatory"
        assert "v2" in payload["title"].lower() or "v2" in payload["message"].lower()
        assert payload["action"] == "display_at_session_start"

    def test_message_mentions_boot_session_v2(self):
        import brain_v2.server as srv
        payload = json.loads(srv.brain_startup_reminder_v2())
        assert "boot_session_v2" in payload["message"]

    def test_message_mentions_action_items_ack(self):
        import brain_v2.server as srv
        payload = json.loads(srv.brain_startup_reminder_v2())
        # Should reference the action-item compliance gate
        assert "acknowledge_action_item_v2" in payload["message"]


class TestPruneSafeguards:
    def test_days_below_minimum_raises(self, conn):
        with pytest.raises(ValueError, match="minimum"):
            store.prune(conn, days=PRUNE_MIN_DAYS - 1, dry_run=False)

    def test_dry_run_default_does_not_delete(self, conn):
        # Seed an eligible fact, age it
        f = store.remember_fact(
            conn, headline="Stale fact eligible for prune dry-run preview",
            body="Body.", project="test", source="test",
        )
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0)  # dry_run default True
        assert result["dry_run"] is True
        # Verify the row still exists
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM facts WHERE id = %s", (f.id,))
            assert cur.fetchone()[0] == 1

    def test_explicit_dry_run_false_deletes(self, conn):
        f = store.remember_fact(
            conn, headline="Stale fact to be permanently pruned for real",
            body="Body.", project="test", source="test",
        )
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0, dry_run=False)
        assert result["dry_run"] is False
        assert result["deleted"] >= 1
        # Row is gone
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM facts WHERE id = %s", (f.id,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT COUNT(*) FROM memory_index WHERE kind='fact' AND memory_id=%s", (f.id,))
            assert cur.fetchone()[0] == 0

    def test_pinned_fact_never_pruned(self, conn):
        f = store.remember_fact(
            conn, headline="Pinned fact must survive prune operations",
            body="Body.", project="test", source="test",
        )
        store.set_pinned(conn, kind="fact", memory_id=f.id, pinned=True)
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0, dry_run=False)
        assert result["deleted"] == 0
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM facts WHERE id = %s", (f.id,))
            assert cur.fetchone()[0] == 1

    def test_rules_never_pruned(self, conn):
        r = store.remember_rule(
            conn, headline="Rule that must never be pruned even when stale",
            body="Body.", severity="PATTERN",
            project="test", source="test",
        )
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0, dry_run=False)
        # Even if prune fires, rule should not be in the deleted list
        assert r.id not in result.get("deleted_ids", {}).get("rule", [])
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rules WHERE id = %s", (r.id,))
            assert cur.fetchone()[0] == 1

    def test_access_count_filter_for_facts(self, conn):
        # Seed two facts with diverse content
        f1 = store.remember_fact(
            conn, headline="Rarely accessed fact about ancient project tooling",
            body="Body one.", project="test", source="test",
        )
        f2 = store.remember_fact(
            conn, headline="Frequently accessed fact about current architecture",
            body="Body two.", project="test", source="test",
        )
        # Bump access on f2 by recalling
        store.recall(conn, kind="fact", memory_id=f2.id)
        store.recall(conn, kind="fact", memory_id=f2.id)
        _age_memory_index(conn, days_ago=100)
        # Prune with min_access=0 should only target f1
        result = store.prune(conn, days=90, min_access=0, dry_run=False)
        assert f1.id in result["deleted_ids"]["fact"]
        assert f2.id not in result["deleted_ids"]["fact"]

    def test_archived_incidents_eligible(self, conn):
        i = store.remember_incident(
            conn, headline="Archived incident eligible for pruning after 90 days",
            body="Body.", project="test", source="test",
        )
        # Mark as archived via the archive mechanism
        with conn.cursor() as cur:
            cur.execute("UPDATE incidents SET archived = TRUE WHERE id = %s", (i.id,))
            cur.execute("UPDATE memory_index SET active = FALSE WHERE kind='incident' AND memory_id=%s", (i.id,))
        conn.commit()
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0, dry_run=False)
        assert i.id in result["deleted_ids"]["incident"]

    def test_non_archived_incidents_not_pruned(self, conn):
        i = store.remember_incident(
            conn, headline="Active incident should not be pruned automatically",
            body="Body.", project="test", source="test",
        )
        # NOT archived
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0, dry_run=False)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM incidents WHERE id = %s", (i.id,))
            assert cur.fetchone()[0] == 1

    def test_done_tasks_eligible(self, conn):
        t = store.remember_task(
            conn, content="Completed task eligible for pruning after age",
            project="test", source="test",
        )
        store.update_task_status(conn, task_id=t.id, status="done", source="test")
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0, dry_run=False)
        assert t.id in result["deleted_ids"]["task"]

    def test_open_tasks_not_pruned(self, conn):
        t = store.remember_task(
            conn, content="Open task should not be pruned regardless of age",
            project="test", source="test",
        )
        # Leave as open
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0, dry_run=False)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM tasks WHERE id = %s", (t.id,))
            assert cur.fetchone()[0] == 1

    def test_hard_cap_per_call(self, conn, monkeypatch):
        """Seed more than PRUNE_MAX_DELETE eligible rows, verify cap.

        Monkeypatch the cap down to 3 for speed — the contract being
        tested is 'prune never exceeds the configured cap per call,'
        which holds regardless of the cap's numeric value.
        """
        import brain_v2.config as _cfg
        monkeypatch.setattr(_cfg, "PRUNE_MAX_DELETE", 3)
        seeds = [
            "Ephemeral alpha task about tooling choices",
            "Ephemeral beta task about deployment pipeline",
            "Ephemeral gamma task about metric dashboards",
            "Ephemeral delta task about test flake triage",
            "Ephemeral epsilon task about release cadence",
        ]
        for content in seeds:
            t = store.remember_task(conn, content=content,
                                     project="test", source="test")
            store.update_task_status(conn, task_id=t.id, status="done", source="test")
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0, dry_run=False)
        assert result["deleted"] == 3
        # Second call should clear the remaining 2
        result2 = store.prune(conn, days=90, min_access=0, dry_run=False)
        assert result2["deleted"] == 2

    def test_dry_run_reports_would_total(self, conn):
        for i in range(3):
            t = store.remember_task(
                conn,
                content=f"Done task {i} with distinct topic about {['pipelines', 'deployments', 'monitoring'][i]}",
                project="test", source="test",
            )
            store.update_task_status(conn, task_id=t.id, status="done", source="test")
        _age_memory_index(conn, days_ago=100)
        result = store.prune(conn, days=90, min_access=0, dry_run=True)
        assert result["dry_run"] is True
        assert result["would_delete_total"] >= 3
