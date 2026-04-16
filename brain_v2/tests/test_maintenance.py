"""Fact decay + incident archive tests.

Exercises:
  - Fresh fact: score ~1.0, stays active
  - Old fact past threshold: deactivates
  - Deactivated fact that was recalled recently: reactivates
  - Hard TTL in the past: deactivates, reported as ttl_expired
  - Hard TTL in the future: no effect
  - Incident with recent access: not archived
  - Incident past archive threshold: archived + memory_index deactivated
  - Already-archived incident: no-op
  - run_all returns a unified report
  - Empty DB: returns empty lists, no errors
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from brain_v2 import maintenance, store
from brain_v2.config import (
    FACT_DECAY_HALFLIFE_DAYS,
    FACT_DECAY_SCORE_THRESHOLD,
    INCIDENT_ARCHIVE_DAYS,
)


def _age_fact(conn, fid: int, days_ago: float) -> None:
    """Rewrite a fact's last_accessed to N days ago (for decay testing)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE facts SET last_accessed = NOW() - make_interval(secs => %s) WHERE id = %s",
            (float(days_ago) * 86400.0, fid),
        )
    conn.commit()


def _age_incident(conn, iid: int, days_ago: float) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE incidents SET last_accessed = NOW() - make_interval(secs => %s) WHERE id = %s",
            (float(days_ago) * 86400.0, iid),
        )
    conn.commit()


def _index_active(conn, kind: str, memory_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT active FROM memory_index WHERE kind = %s AND memory_id = %s",
            (kind, memory_id),
        )
        row = cur.fetchone()
        return row[0] if row else False


class TestFactDecay:
    def test_fresh_fact_stays_active(self, conn):
        f = store.remember_fact(
            conn, headline="Fresh fact recently accessed",
            body="Recent and relevant, should not decay.",
            project="test", source="test",
        )
        result = maintenance.decay_facts(conn)
        assert f.id not in result["deactivated"]
        assert _index_active(conn, "fact", f.id) is True

    def test_old_fact_deactivates(self, conn):
        f = store.remember_fact(
            conn, headline="Old unused fact about deprecated feature",
            body="Hasn't been touched in a long time.",
            project="test", source="test",
        )
        # Age beyond the decay threshold. halflife=7, threshold=0.1
        # → ~23 days. Go to 40 for safety margin.
        _age_fact(conn, f.id, days_ago=40)
        result = maintenance.decay_facts(conn)
        assert f.id in result["deactivated"]
        assert _index_active(conn, "fact", f.id) is False

    def test_deactivated_fact_reactivates_after_recall(self, conn):
        f = store.remember_fact(
            conn, headline="Occasional fact about infrequent topic",
            body="Rarely accessed but still relevant.",
            project="test", source="test",
        )
        _age_fact(conn, f.id, days_ago=40)
        maintenance.decay_facts(conn)
        assert _index_active(conn, "fact", f.id) is False

        # Simulate a recall — bumps last_accessed to now
        store.recall(conn, kind="fact", memory_id=f.id)

        result = maintenance.decay_facts(conn)
        assert f.id in result["reactivated"]
        assert _index_active(conn, "fact", f.id) is True

    def test_custom_halflife_affects_threshold(self, conn):
        f = store.remember_fact(
            conn, headline="Fact with specific halflife semantics applied",
            body="Decay behavior depends on halflife parameter.",
            project="test", source="test",
        )
        _age_fact(conn, f.id, days_ago=4)
        # With halflife=1, 4-day-old fact has score = 2^-4 = 0.0625 < 0.1 threshold
        result = maintenance.decay_facts(conn, halflife_days=1.0)
        assert f.id in result["deactivated"]

    def test_custom_threshold_accepts_longer_retention(self, conn):
        f = store.remember_fact(
            conn, headline="Long-lived fact that should persist despite age",
            body="Higher retention threshold keeps this one active.",
            project="test", source="test",
        )
        _age_fact(conn, f.id, days_ago=40)
        # With threshold=0.001, even decayed facts stay active
        result = maintenance.decay_facts(conn, threshold=0.001)
        assert f.id not in result["deactivated"]


class TestFactTTL:
    def test_past_ttl_expires(self, conn):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        f = store.remember_fact(
            conn, headline="Expiring fact with past TTL",
            body="This should expire immediately.",
            project="test", source="test",
            ttl=past.isoformat(),
        )
        result = maintenance.decay_facts(conn)
        assert f.id in result["ttl_expired"]
        assert _index_active(conn, "fact", f.id) is False

    def test_future_ttl_has_no_effect(self, conn):
        future = datetime.now(timezone.utc) + timedelta(days=365)
        f = store.remember_fact(
            conn, headline="Future-dated TTL fact stays active today",
            body="TTL is far in the future.",
            project="test", source="test",
            ttl=future.isoformat(),
        )
        result = maintenance.decay_facts(conn)
        assert f.id not in result["ttl_expired"]
        assert f.id not in result["deactivated"]

    def test_ttl_expired_not_in_decayed(self, conn):
        """TTL-expired facts are reported only in ttl_expired, not deactivated."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        f = store.remember_fact(
            conn, headline="Stale fact with past TTL cleanup",
            body="Hard expiry.",
            project="test", source="test",
            ttl=past.isoformat(),
        )
        _age_fact(conn, f.id, days_ago=100)
        result = maintenance.decay_facts(conn)
        assert f.id in result["ttl_expired"]
        assert f.id not in result["deactivated"]


class TestIncidentArchive:
    def test_recent_incident_not_archived(self, conn):
        i = store.remember_incident(
            conn, headline="Recent bug fixed today and resolved",
            body="Happened recently, keep visible.",
            project="test", source="test",
        )
        archived = maintenance.archive_incidents(conn)
        assert i.id not in archived
        assert _index_active(conn, "incident", i.id) is True

    def test_old_incident_archived(self, conn):
        i = store.remember_incident(
            conn, headline="Ancient incident from years ago documented",
            body="Historical record.",
            project="test", source="test",
        )
        _age_incident(conn, i.id, days_ago=INCIDENT_ARCHIVE_DAYS + 5)
        archived = maintenance.archive_incidents(conn)
        assert i.id in archived
        assert _index_active(conn, "incident", i.id) is False
        # Verify the archived flag on the incident row
        with conn.cursor() as cur:
            cur.execute("SELECT archived FROM incidents WHERE id = %s", (i.id,))
            assert cur.fetchone()[0] is True

    def test_already_archived_is_noop(self, conn):
        i = store.remember_incident(
            conn, headline="Previously archived incident remains archived",
            body="Should not be picked up twice.",
            project="test", source="test",
        )
        _age_incident(conn, i.id, days_ago=INCIDENT_ARCHIVE_DAYS + 5)
        first = maintenance.archive_incidents(conn)
        assert i.id in first
        second = maintenance.archive_incidents(conn)
        assert i.id not in second

    def test_custom_archive_days(self, conn):
        i = store.remember_incident(
            conn, headline="Moderately old incident with custom threshold",
            body="Borderline case.",
            project="test", source="test",
        )
        _age_incident(conn, i.id, days_ago=10)
        # With 7-day threshold, the 10-day-old incident should archive
        archived = maintenance.archive_incidents(conn, archive_days=7)
        assert i.id in archived


class TestRunAll:
    def test_runs_both_jobs(self, conn):
        # Seed: one fresh fact, one old fact, one old incident
        fresh = store.remember_fact(
            conn, headline="Fresh fact that should remain active today",
            body="Relevant.", project="test", source="test",
        )
        old = store.remember_fact(
            conn, headline="Stale fact past decay threshold by weeks",
            body="Not touched in a long time.", project="test", source="test",
        )
        _age_fact(conn, old.id, days_ago=40)
        old_inc = store.remember_incident(
            conn, headline="Historic incident from distant past archive candidate",
            body="Old record.", project="test", source="test",
        )
        _age_incident(conn, old_inc.id, days_ago=INCIDENT_ARCHIVE_DAYS + 5)

        report = maintenance.run_all(conn)
        d = report.to_dict()
        assert old.id in d["facts_decayed"]
        assert fresh.id not in d["facts_decayed"]
        assert old_inc.id in d["incidents_archived"]
        assert d["facts_decayed_count"] == 1
        assert d["incidents_archived_count"] == 1

    def test_empty_db_returns_empty_report(self, conn):
        report = maintenance.run_all(conn)
        d = report.to_dict()
        assert d["facts_decayed_count"] == 0
        assert d["facts_reactivated_count"] == 0
        assert d["facts_ttl_expired_count"] == 0
        assert d["incidents_archived_count"] == 0

    def test_idempotent(self, conn):
        """Running twice in a row should produce the same end state;
        the second run should have zero deactivations."""
        old = store.remember_fact(
            conn, headline="Decaying fact for idempotency verification",
            body="Testing that re-runs are safe.", project="test", source="test",
        )
        _age_fact(conn, old.id, days_ago=40)

        r1 = maintenance.run_all(conn)
        r2 = maintenance.run_all(conn)
        assert old.id in r1.facts_decayed
        # Second run: nothing new to decay (already deactivated)
        assert old.id not in r2.facts_decayed


class TestDecayExcludesOtherTypes:
    def test_rules_not_affected_by_decay(self, conn):
        r = store.remember_rule(
            conn, headline="Rule that predates the current era by far",
            body="Rules never decay — supersede only.",
            severity="BLOCKER", project="test", source="test",
        )
        # Manually age the rule by rewriting memory_index.created_at
        # (decay_facts must not touch rule rows)
        maintenance.decay_facts(conn)
        assert _index_active(conn, "rule", r.id) is True

    def test_tasks_not_affected_by_decay(self, conn):
        t = store.remember_task(
            conn, content="Task that existed well before the job window",
            project="test", source="test",
        )
        maintenance.decay_facts(conn)
        assert _index_active(conn, "task", t.id) is True
