"""Tests for the belief-revision (supersession) feature.

Hit the test database (port 5434, db openbrain_test) per the project's
isolation conventions in conftest.py. Use deterministic fake embeddings
unless `-m ollama` is passed.

Run with: pytest tests/test_belief_revision.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import (
    _booted_sources,
    _record_search,
    db_get_memory,
    db_get_pinned,
    db_set_pinned,
    db_store_deduped,
    extract_metadata,
    get_embedding,
    list_recent,
    recall,
    remember,
    search,
    supersede,
    unsupersede,
)

TEST_PROJECT = "__test_belief_revision__"
TEST_SOURCE = "pytest-belief-revision"


def _make_memory(content: str, project: str = TEST_PROJECT,
                 type_override: str = "note") -> int:
    """Helper: create a memory and return its ID. Bypasses the MCP layer
    and uses db_store_deduped directly so we control exactly what's
    stored without compliance/source plumbing for setup."""
    embedding = get_embedding(content)
    metadata = extract_metadata(content)
    metadata["type"] = type_override
    metadata["source"] = TEST_SOURCE
    memory, _ = db_store_deduped(content, embedding, metadata, project)
    return memory["id"]


@pytest.fixture(autouse=True)
def cleanup_test_project():
    """Wipe all memories tagged __test_belief_revision__ before AND after
    each test so tests are isolated. Also satisfies the source-must-be-
    booted compliance gate by registering the test source manually."""
    import psycopg2
    from conftest import TEST_DATABASE_URL

    # Satisfy boot + search-first compliance gates so MCP tools we call
    # from tests don't get rejected with "must call boot_session first."
    _booted_sources.add(TEST_SOURCE)
    _record_search(TEST_SOURCE, TEST_PROJECT)

    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        # Clear FK references first by nulling superseded_by_id
        cur.execute("UPDATE memories SET superseded_by_id = NULL WHERE project = %s",
                    (TEST_PROJECT,))
        cur.execute("DELETE FROM memories WHERE project = %s", (TEST_PROJECT,))
    yield
    with conn.cursor() as cur:
        cur.execute("UPDATE memories SET superseded_by_id = NULL WHERE project = %s",
                    (TEST_PROJECT,))
        cur.execute("DELETE FROM memories WHERE project = %s", (TEST_PROJECT,))
    conn.close()


# ============================================================
# 1. Core supersede mechanics
# ============================================================

def test_supersede_basic():
    """supersede() returns both IDs; old.superseded_by_id == new.id."""
    old_id = _make_memory("Sky is green.")
    out = json.loads(supersede(old_id, "Sky is blue, not green.",
                                reason="Color was wrong",
                                source=TEST_SOURCE))
    assert out["success"] is True
    assert out["old_id"] == old_id
    assert out["new_id"] != old_id

    old = db_get_memory(old_id)
    assert old["superseded_by_id"] == out["new_id"]
    assert old["superseded_reason"] == "Color was wrong"
    assert old["superseded_at"] is not None


# ============================================================
# 2. Search filters superseded by default
# ============================================================

def test_supersede_filters_search():
    """search() default behavior excludes superseded memories."""
    old_id = _make_memory("Quantum widgets ship in 2019.",
                           type_override="note")
    supersede(old_id, "Quantum widgets ship in 2024 (delayed from 2019).",
              reason="Date was wrong", source=TEST_SOURCE)

    raw = search("quantum widgets", source=TEST_SOURCE,
                  project="__test_belief_revision__")
    if isinstance(raw, str) and raw.startswith("No memories"):
        results = []
    else:
        results = json.loads(raw)
    ids = [r["id"] for r in results]
    assert old_id not in ids, f"superseded {old_id} should be filtered, got ids={ids}"


def test_supersede_include_flag():
    """search(include_superseded=True) returns both old and new."""
    old_id = _make_memory("Cyrillic widgets are blue.")
    out = json.loads(supersede(old_id, "Cyrillic widgets are red.",
                                reason="Color updated", source=TEST_SOURCE))
    new_id = out["new_id"]
    raw = search("cyrillic widgets", source=TEST_SOURCE,
                  project="__test_belief_revision__",
                  include_superseded=True)
    results = json.loads(raw)
    ids = [r["id"] for r in results]
    assert old_id in ids
    assert new_id in ids


# ============================================================
# 3. Recall returns superseded with banner
# ============================================================

def test_recall_superseded_returns_banner():
    """recall(old_id) returns content + banner pointing at superseder."""
    old_id = _make_memory("Glargon flux is positive.")
    out = json.loads(supersede(old_id, "Glargon flux is negative.",
                                reason="Sign was wrong",
                                source=TEST_SOURCE))
    new_id = out["new_id"]

    recalled = json.loads(recall(old_id))
    assert recalled["content"] == "Glargon flux is positive."
    assert recalled["superseded_by_id"] == new_id
    assert "Sign was wrong" in recalled["banner"]
    assert f"recall({new_id})" in recalled["banner"]


def test_recall_active_no_banner():
    """recall(active_id) returns content with NO banner."""
    mid = _make_memory("Active fact, never superseded.")
    out = json.loads(recall(mid))
    assert "banner" not in out
    assert "superseded_by_id" not in out


# ============================================================
# 4. Chains blocked
# ============================================================

def test_supersede_chains_blocked():
    """Supersedeing an already-superseded memory returns an error."""
    a = _make_memory("Original belief A.")
    out = json.loads(supersede(a, "Corrected belief B.",
                                reason="A was wrong", source=TEST_SOURCE))
    b = out["new_id"]
    out2 = json.loads(supersede(a, "Different correction C.",
                                 reason="Trying to chain",
                                 source=TEST_SOURCE))
    assert out2["success"] is False
    assert "already superseded" in out2["error"].lower()
    assert str(b) in out2["error"]


# ============================================================
# 5. Required arg validation
# ============================================================

def test_supersede_requires_reason():
    """supersede with empty reason rejected."""
    mid = _make_memory("Reason-required test.")
    for empty in ("", "   "):
        out = json.loads(supersede(mid, "Replacement.", reason=empty,
                                    source=TEST_SOURCE))
        assert out["success"] is False
        assert "reason" in out["error"].lower()


def test_supersede_requires_source():
    """supersede with empty source rejected (matches remember/search)."""
    mid = _make_memory("Source-required test.")
    out = json.loads(supersede(mid, "Replacement.", reason="x",
                                source=""))
    assert out["success"] is False


def test_supersede_requires_new_content():
    mid = _make_memory("Content-required test.")
    out = json.loads(supersede(mid, "", reason="x", source=TEST_SOURCE))
    assert out["success"] is False
    assert "new_content" in out["error"].lower()


# ============================================================
# 6. Dedup ignores superseded
# ============================================================

def test_dedup_ignores_superseded():
    """Storing content similar to a SUPERSEDED memory does NOT
    false-match against it (would cause skipped writes for valid
    re-corrections)."""
    old_id = _make_memory("The CEO of Acme is Alice Smith.")
    supersede(old_id, "The CEO of Acme is Bob Jones (Alice resigned).",
              reason="CEO changed", source=TEST_SOURCE)

    # Re-store something near the OLD content. Without the dedup filter
    # for superseded, this would skip-vs-old. With the filter, it stores.
    out = json.loads(remember("The CEO of Acme is Alice Smith.",
                                source=TEST_SOURCE,
                                project="__test_belief_revision__"))
    # action should NOT be 'skipped' (which would mean dedup matched the
    # superseded memory). May be 'stored' or 'updated' against the new
    # active memory depending on similarity.
    assert out.get("action") != "skipped" or out["id"] != old_id


# ============================================================
# 7. Audit trail preserved
# ============================================================

def test_supersede_preserves_old_content():
    """The old memory's content is unchanged after supersession."""
    old_id = _make_memory("The original content stays.")
    supersede(old_id, "New replacement.", reason="r", source=TEST_SOURCE)
    old = db_get_memory(old_id)
    assert old["content"] == "The original content stays."


# ============================================================
# 8. unsupersede reverses
# ============================================================

def test_unsupersede_reverses():
    """unsupersede() clears the relation."""
    old_id = _make_memory("Reversible belief.")
    supersede(old_id, "Replacement.", reason="r", source=TEST_SOURCE)
    # Confirm it IS superseded
    assert db_get_memory(old_id)["superseded_by_id"] is not None
    out = json.loads(unsupersede(old_id, source=TEST_SOURCE))
    assert out["success"] is True
    assert db_get_memory(old_id)["superseded_by_id"] is None
    assert db_get_memory(old_id)["superseded_at"] is None
    assert db_get_memory(old_id)["superseded_reason"] is None


def test_unsupersede_does_not_delete_corrector():
    """The corrector memory survives unsupersede(); only the relation clears."""
    old_id = _make_memory("Original.")
    out = json.loads(supersede(old_id, "Corrected.", reason="r",
                                source=TEST_SOURCE))
    new_id = out["new_id"]
    unsupersede(old_id, source=TEST_SOURCE)
    assert db_get_memory(new_id) is not None
    assert db_get_memory(new_id)["content"] == "Corrected."


# ============================================================
# 9. Pinning inheritance (default off, opt-in)
# ============================================================

def test_supersede_does_not_inherit_pinned_by_default():
    """Without inherit_pinned, the new memory is NOT pinned even if old was."""
    old_id = _make_memory("Pinned guardrail.", type_override="guardrail")
    db_set_pinned(old_id, True)
    out = json.loads(supersede(old_id, "Updated guardrail.",
                                reason="r", source=TEST_SOURCE,
                                project="__test_belief_revision__"))
    new_id = out["new_id"]
    assert out["old_pinned"] is True
    assert out["new_pinned"] is False
    assert not db_get_memory(new_id).get("pinned")


def test_supersede_inherits_pinned_when_flagged():
    """With inherit_pinned=True, the new memory is pinned if old was."""
    old_id = _make_memory("Another pinned guardrail.", type_override="guardrail")
    db_set_pinned(old_id, True)
    out = json.loads(supersede(old_id, "Updated again.",
                                reason="r", source=TEST_SOURCE,
                                project="__test_belief_revision__",
                                inherit_pinned=True))
    new_id = out["new_id"]
    assert out["new_pinned"] is True
    assert db_get_memory(new_id).get("pinned") is True


# ============================================================
# 10. list_recent filters by default
# ============================================================

def test_list_recent_filters_superseded_by_default():
    """list_recent excludes superseded memories unless include_superseded=True."""
    old_id = _make_memory("Recent-test old fact.")
    out = json.loads(supersede(old_id, "Recent-test new fact.",
                                reason="r", source=TEST_SOURCE))
    new_id = out["new_id"]

    raw = list_recent(limit=100)
    items = json.loads(raw)
    ids = [m["id"] for m in items]
    assert old_id not in ids
    assert new_id in ids

    raw_inc = list_recent(limit=100, include_superseded=True)
    items_inc = json.loads(raw_inc)
    ids_inc = [m["id"] for m in items_inc]
    assert old_id in ids_inc


# ============================================================
# 11. supersede on missing memory rejected
# ============================================================

def test_supersede_on_missing_memory():
    out = json.loads(supersede(99999999, "x", reason="r", source=TEST_SOURCE))
    assert out["success"] is False
    assert "not found" in out["error"].lower()


def test_unsupersede_on_active_memory():
    """unsupersede() on a memory that wasn't superseded is rejected."""
    mid = _make_memory("Never superseded.")
    out = json.loads(unsupersede(mid, source=TEST_SOURCE))
    assert out["success"] is False
    assert "not superseded" in out["error"].lower()
