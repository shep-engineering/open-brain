"""Fact supersession — the correction primitive facts previously lacked.

Proves: after supersede_fact, search returns the NEW fact and NOT the stale one;
the old row is retained (audit) with superseded_by set; double-supersede is
refused; and — critically — decay does NOT reactivate a superseded fact (the
self-revert bug the PLAN gate caught).

Preconditions (NOT hermetic): Ollama qwen3-embedding:8b @ :11434 AND the 5435
open_brain_v2_test container up. If absent, conftest skips.
"""
from __future__ import annotations

import pytest

from brain_v2 import store
from brain_v2 import maintenance


def _seed(conn, headline, body, project="test"):
    m = store.remember_fact(conn, headline=headline, body=body, project=project,
                            source="test")
    assert hasattr(m, "id"), f"seed rejected as duplicate: {m}"
    return m


def test_supersede_fact_replaces_in_search(conn):
    old = _seed(conn, "The dev database runs Postgres 14",
                "As of setup the dev DB was Postgres 14 on the old host.")
    new = store.supersede_fact(
        conn, old_id=old.id,
        new_headline="The dev database runs Postgres 16 with pgvector",
        new_body="Migrated to Postgres 16 with the pgvector extension.",
        reason="version updated", source="test",
    )
    assert new.id != old.id
    assert new.supersedes == old.id

    # search for the topic must return the NEW fact and NOT the stale one.
    results = store.search_headlines(conn, query="what postgres version does the dev database run",
                                     kind="fact", limit=10)
    ids = [r["memory_id"] for r in results]
    assert new.id in ids, "new fact not retrievable after supersede"
    assert old.id not in ids, "STALE fact still returned after supersede"


def test_old_fact_retained_with_link(conn):
    old = _seed(conn, "Team uses Trello for tickets", "Tickets tracked in Trello.")
    new = store.supersede_fact(conn, old_id=old.id,
                               new_headline="Team uses Linear for tickets",
                               new_body="Tickets tracked in Linear now.",
                               reason="switched tools", source="test")
    with conn.cursor() as cur:
        cur.execute("SELECT superseded_by, supersede_reason FROM facts WHERE id = %s", (old.id,))
        sby, reason = cur.fetchone()
        assert sby == new.id
        assert reason == "switched tools"
        # old row still exists (audit), just inactive in the index
        cur.execute("SELECT active FROM memory_index WHERE kind='fact' AND memory_id=%s", (old.id,))
        assert cur.fetchone()[0] is False
        # audit row written
        cur.execute("SELECT count(*) FROM v2_audit WHERE operation='SUPERSEDE' AND kind='fact' AND memory_id=%s", (old.id,))
        assert cur.fetchone()[0] == 1


def test_double_supersede_refused(conn):
    old = _seed(conn, "Primary region is us-east-1", "Deployed in us-east-1.")
    store.supersede_fact(conn, old_id=old.id,
                         new_headline="Primary region is us-west-2",
                         new_body="Moved primary to us-west-2.",
                         reason="migration", source="test")
    with pytest.raises(ValueError, match="already superseded"):
        store.supersede_fact(conn, old_id=old.id,
                             new_headline="Primary region is eu-west-1",
                             new_body="Moved again.", reason="again", source="test")


def test_supersede_missing_fact_raises(conn):
    with pytest.raises(ValueError, match="not found"):
        store.supersede_fact(conn, old_id=999999, new_headline="x is y",
                             new_body="x is y now.", reason="r", source="test")


def test_capture_context_duplicate_surfaces_supersede_hint(conn):
    """The correction path must be REACHABLE from the normal capture workflow:
    when capture_context drops a near-duplicate fact, the result must tell the
    agent it can supersede the existing one (else the correction never happens)."""
    text = ("The staging deploy pipeline posts a validated check onto the dev "
            "SHA after the full deployed suite passes on stage.")
    first = store.capture_context(conn, context=text, source="test", project="test")
    # find the stored fact id
    stored = [r for r in first if r["action"] == "stored"]
    assert stored, f"expected the fact to store first time: {first}"

    # capture near-identical text again -> duplicate, must carry the hint
    second = store.capture_context(conn, context=text, source="test", project="test")
    dups = [r for r in second if r["action"] == "duplicate"]
    assert dups, f"expected a duplicate on re-capture: {second}"
    d = dups[0]
    assert "hint" in d, "duplicate result lacks the supersede hint"
    assert "supersede_fact_v2" in d["hint"]
    assert str(d["id"]) in d["hint"]
    assert d.get("dropped_headline"), "duplicate result should name the dropped chunk"


def test_decay_does_not_reactivate_superseded_fact(conn):
    """THE regression the PLAN gate caught: a just-superseded fact scores ~1.0
    (last_accessed NULL -> created_at=now), so a naive decay would flip its
    index entry back to active and the stale fact would reappear. It must NOT."""
    old = _seed(conn, "The API base url is api.v1.example.com",
                "v1 API endpoint.")
    store.supersede_fact(conn, old_id=old.id,
                         new_headline="The API base url is api.v2.example.com",
                         new_body="v2 API endpoint now.",
                         reason="v2 cutover", source="test")
    # sanity: old is inactive right after supersede
    with conn.cursor() as cur:
        cur.execute("SELECT active FROM memory_index WHERE kind='fact' AND memory_id=%s", (old.id,))
        assert cur.fetchone()[0] is False

    # run decay — with a huge halflife so a normal fact would score high and
    # a naive reactivate branch WOULD flip the superseded fact back on.
    maintenance.decay_facts(conn, halflife_days=3650.0, threshold=0.01)

    with conn.cursor() as cur:
        cur.execute("SELECT active FROM memory_index WHERE kind='fact' AND memory_id=%s", (old.id,))
        assert cur.fetchone()[0] is False, "decay REACTIVATED a superseded fact (self-revert bug)"

    # and it stays out of search
    results = store.search_headlines(conn, query="what is the API base url", kind="fact", limit=10)
    assert old.id not in [r["memory_id"] for r in results]
