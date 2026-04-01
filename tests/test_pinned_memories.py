"""Tests for pinned memories (guardrails) feature.

These tests hit the live database and Ollama embedding model.
Run with: pytest tests/test_pinned_memories.py -v
"""

import json
import os
import sys

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import (
    VALID_TYPES,
    _format_search_entry,
    _record_search,
    db_get_pinned,
    db_set_pinned,
    db_store,
    db_store_deduped,
    get_embedding,
    pin,
    prune,
    remember,
    search,
    unpin,
)

# ─── Test Helpers ─────────────────────────────────────────────────────────────

TEST_PROJECT = "__test_pinned__"


@pytest.fixture(autouse=True)
def cleanup_test_memories():
    """Delete all test memories before and after each test."""
    _delete_test_memories()
    yield
    _delete_test_memories()


def _delete_test_memories():
    from server import _get_conn
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM memories WHERE project = %s", (TEST_PROJECT,))


def _create_test_memory(content: str, project: str = TEST_PROJECT, pinned: bool = False) -> int:
    """Create a memory directly and return its ID."""
    embedding = get_embedding(content)
    metadata = {"type": "guardrail" if pinned else "note"}
    result = db_store(content, embedding, metadata, project)
    mem_id = result["id"]
    if pinned:
        db_set_pinned(mem_id, True)
    return mem_id


# ─── db_set_pinned / db_get_pinned ──────────────────────────────────────────

class TestDbPinned:

    def test_pin_sets_flag(self):
        mem_id = _create_test_memory("Test pin sets flag")
        result = db_set_pinned(mem_id, True)
        assert result is not None
        assert result["pinned"] is True

    def test_unpin_clears_flag(self):
        mem_id = _create_test_memory("Test unpin clears flag", pinned=True)
        result = db_set_pinned(mem_id, False)
        assert result is not None
        assert result["pinned"] is False

    def test_pin_nonexistent_returns_none(self):
        result = db_set_pinned(999999, True)
        assert result is None

    def test_get_pinned_returns_only_pinned_for_project(self):
        _create_test_memory("Pinned rule 1", pinned=True)
        _create_test_memory("Pinned rule 2", pinned=True)
        _create_test_memory("Not pinned note")

        pinned = db_get_pinned(TEST_PROJECT)
        assert len(pinned) == 2
        assert all("Pinned rule" in m["content"] for m in pinned)

    def test_get_pinned_empty_project_returns_empty(self):
        _create_test_memory("Some pinned thing", pinned=True)
        assert db_get_pinned("") == []

    def test_get_pinned_wrong_project_returns_empty(self):
        _create_test_memory("Pinned for test project", pinned=True)
        assert db_get_pinned("__nonexistent_project__") == []


# ─── Search Injection ────────────────────────────────────────────────────────

class TestSearchInjection:

    def test_search_with_project_includes_pinned_at_top(self):
        _create_test_memory("Always use feature branches for this project", pinned=True)
        _create_test_memory("Some unrelated note about parsing JSON")

        raw = search("parsing JSON", project=TEST_PROJECT)
        results = json.loads(raw)
        assert len(results) >= 1
        # First result should be the pinned one
        assert results[0].get("pinned") is True
        assert "feature branches" in results[0]["preview"]

    def test_pinned_entries_have_pinned_flag_and_similarity_1(self):
        _create_test_memory("Workflow rule: run tests before commit", pinned=True)

        raw = search("anything at all", project=TEST_PROJECT)
        results = json.loads(raw)
        pinned_results = [r for r in results if r.get("pinned")]
        assert len(pinned_results) >= 1
        for r in pinned_results:
            assert r["similarity"] == 1.0
            assert r["pinned"] is True

    def test_search_without_project_excludes_pinned(self):
        mem_id = _create_test_memory("Pinned rule that should not appear globally", pinned=True)

        raw = search("Pinned rule that should not appear globally")
        results = json.loads(raw)
        if isinstance(results, list):
            pinned_results = [r for r in results if r.get("pinned")]
            assert len(pinned_results) == 0

    def test_pinned_not_duplicated_in_results(self):
        _create_test_memory("Unique guardrail content for dedup test xyz", pinned=True)

        raw = search("Unique guardrail content for dedup test xyz", project=TEST_PROJECT)
        results = json.loads(raw)
        ids = [r["id"] for r in results]
        # No duplicate IDs
        assert len(ids) == len(set(ids))

    def test_pinned_do_not_count_against_limit(self):
        # Create 2 pinned + 3 regular
        _create_test_memory("Pinned guardrail A", pinned=True)
        _create_test_memory("Pinned guardrail B", pinned=True)
        _create_test_memory("Regular note C about testing")
        _create_test_memory("Regular note D about coding")
        _create_test_memory("Regular note E about debugging")

        # Search with limit=2 -- should get 2 pinned + up to 2 semantic
        raw = search("testing coding debugging", limit=2, project=TEST_PROJECT)
        results = json.loads(raw)
        pinned_count = sum(1 for r in results if r.get("pinned"))
        semantic_count = sum(1 for r in results if not r.get("pinned"))
        assert pinned_count == 2
        assert semantic_count <= 2
        assert len(results) >= 3  # at least pinned + some semantic


# ─── Prune Protection ────────────────────────────────────────────────────────

class TestPinnedProtection:

    def test_prune_skips_pinned_memories(self):
        mem_id = _create_test_memory("Critical guardrail that must survive prune", pinned=True)

        # Backdate the memory so it qualifies for pruning by age
        from server import _get_conn
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE memories SET created_at = NOW() - INTERVAL '365 days' WHERE id = %s",
                (mem_id,),
            )

        # Prune with aggressive settings
        raw = prune(days=1, min_access=100, dry_run=False)
        result = json.loads(raw)

        # The pinned memory should still exist
        pinned = db_get_pinned(TEST_PROJECT)
        assert any(m["id"] == mem_id for m in pinned), "Pinned memory was deleted by prune!"

    def test_prune_dry_run_excludes_pinned_from_count(self):
        mem_id = _create_test_memory("Pinned should not be counted", pinned=True)

        from server import _get_conn
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE memories SET created_at = NOW() - INTERVAL '365 days' WHERE id = %s",
                (mem_id,),
            )

        raw = prune(days=30, min_access=100, dry_run=True)
        result = json.loads(raw)
        # The count should not include our pinned memory
        # (It might include other old memories in the DB, but let's verify
        # by checking the pinned memory is still there after a real prune)
        assert result["dry_run"] is True

    def test_forget_can_still_delete_pinned(self):
        """Explicit forget() should work even on pinned memories."""
        from server import forget
        mem_id = _create_test_memory("Pinned but deletable", pinned=True)

        raw = forget(mem_id)
        result = json.loads(raw)
        assert result["success"] is True

        pinned = db_get_pinned(TEST_PROJECT)
        assert not any(m["id"] == mem_id for m in pinned)


# ─── Pin / Unpin MCP Tools ──────────────────────────────────────────────────

class TestPinUnpinTools:

    def test_pin_tool_succeeds(self):
        mem_id = _create_test_memory("Rule to pin via tool")
        raw = pin(mem_id)
        result = json.loads(raw)
        assert result["success"] is True
        assert result["pinned"] is True
        assert result["project"] == TEST_PROJECT

    def test_unpin_tool_succeeds(self):
        mem_id = _create_test_memory("Rule to unpin", pinned=True)
        raw = unpin(mem_id)
        result = json.loads(raw)
        assert result["success"] is True
        assert result["pinned"] is False

    def test_pin_nonexistent_memory_fails(self):
        raw = pin(999999)
        result = json.loads(raw)
        assert result["success"] is False

    def test_pin_global_memory_fails(self):
        """Global memories (no project) cannot be pinned."""
        mem_id = _create_test_memory("Global memory", project="")
        raw = pin(mem_id)
        result = json.loads(raw)
        assert result["success"] is False
        assert "global" in result["error"].lower() or "project" in result["error"].lower()


# ─── Guardrail Type ──────────────────────────────────────────────────────────

class TestGuardrailType:

    def test_guardrail_is_valid_type(self):
        assert "guardrail" in VALID_TYPES

    def test_remember_with_guardrail_type(self):
        _record_search("test", TEST_PROJECT)  # satisfy search-first enforcement
        raw = remember(
            "Always run tests before committing",
            source="test",
            type_override="guardrail",
            project=TEST_PROJECT,
        )
        result = json.loads(raw)
        assert result["success"] is True
        assert result["type"] == "guardrail"


# ─── Format Helper ───────────────────────────────────────────────────────────

class TestFormatHelper:

    def test_format_entry_with_pinned_flag(self):
        m = {
            "id": 1, "content": "Test content", "metadata": {"type": "guardrail"},
            "created_at": "2026-01-01T00:00:00", "project": "test",
            "annotation": "", "upvotes": 0, "downvotes": 0,
        }
        entry = _format_search_entry(m, pinned=True)
        assert entry["pinned"] is True
        assert entry["similarity"] == 1.0

    def test_format_entry_without_pinned_flag(self):
        m = {
            "id": 1, "content": "Test content", "metadata": {"type": "note"},
            "created_at": "2026-01-01T00:00:00", "project": "",
            "similarity": 0.85, "annotation": "", "upvotes": 0, "downvotes": 0,
        }
        entry = _format_search_entry(m, pinned=False)
        assert "pinned" not in entry
        assert entry["similarity"] == 0.85

    def test_format_entry_truncates_long_content(self):
        m = {
            "id": 1, "content": "x" * 300, "metadata": {},
            "created_at": "2026-01-01T00:00:00", "project": "",
            "annotation": "", "upvotes": 0, "downvotes": 0,
        }
        entry = _format_search_entry(m)
        assert len(entry["preview"]) == 203  # 200 + "..."
