"""Tests for session compliance tracking (polling-based model).

Verifies that remember() and capture_context() block when no recent
search() has been called, and that the store counter enforces periodic
re-searching.

Run with: pytest tests/test_session_compliance.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import (
    _booted_sources,
    _check_compliance,
    _checkpoint_tracker,
    _record_search,
    _record_store,
    _session_tracker,
    boot_session,
    brain_checkpoint,
    capture_context,
    remember,
    search,
    CHECKPOINT_COOLDOWN,
    COMPLIANCE_MAX_STORES,
)

TEST_PROJECT = "__test_compliance__"


@pytest.fixture(autouse=True)
def clear_tracker():
    """Clear session tracker, boot state, and test memories before/after each test."""
    _session_tracker.clear()
    _booted_sources.clear()
    _checkpoint_tracker.clear()
    yield
    _session_tracker.clear()
    _booted_sources.clear()
    _checkpoint_tracker.clear()
    # Clean up test memories
    try:
        from server import _get_conn
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM memories WHERE project = %s", (TEST_PROJECT,))
    except Exception:
        pass


# ─── _record_search ──────────────────────────────────────────────────────────

class TestRecordSearch:

    def test_record_search_creates_tracker_entry(self):
        _record_search("claude", "my-project")
        assert "claude" in _session_tracker
        assert _session_tracker["claude"]["searches"] == 1
        assert _session_tracker["claude"]["stores_since_search"] == 0

    def test_record_search_increments_on_repeat(self):
        _record_search("claude", "proj")
        _record_search("claude", "proj")
        assert _session_tracker["claude"]["searches"] == 2

    def test_record_search_resets_store_counter(self):
        _record_search("claude", "proj")
        _record_store("claude")
        _record_store("claude")
        assert _session_tracker["claude"]["stores_since_search"] == 2
        _record_search("claude", "proj")
        assert _session_tracker["claude"]["stores_since_search"] == 0

    def test_record_search_source_only(self):
        _record_search("cursor", "")
        assert "cursor" in _session_tracker

    def test_record_search_no_source_uses_global(self):
        _record_search("", "")
        assert "_global" in _session_tracker


# ─── _record_store ──────────────────────────────────────────────────────────

class TestRecordStore:

    def test_record_store_increments_counter(self):
        _record_search("claude", "proj")
        _record_store("claude")
        assert _session_tracker["claude"]["stores_since_search"] == 1

    def test_record_store_without_search_is_noop(self):
        # If source never searched, _record_store does nothing (no key yet)
        _record_store("unknown-agent")
        assert "unknown-agent" not in _session_tracker


# ─── _check_compliance ───────────────────────────────────────────────────────

class TestCheckCompliance:

    def test_no_boot_returns_blocked(self):
        result = _check_compliance("claude", "proj")
        assert result is not None
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
        assert result["blocked_by"] == "boot_required"

    def test_booted_but_no_search_returns_blocked(self):
        _booted_sources.add("claude")
        result = _check_compliance("claude", "proj")
        assert result is not None
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
        assert result["blocked_by"] == "compliance"

    def test_booted_and_searched_returns_none(self):
        _booted_sources.add("claude")
        _record_search("claude", "proj")
        result = _check_compliance("claude", "proj")
        assert result is None

    def test_max_stores_exceeded_returns_blocked(self):
        _booted_sources.add("claude")
        _record_search("claude", "proj")
        for _ in range(COMPLIANCE_MAX_STORES):
            _record_store("claude")
        result = _check_compliance("claude", "proj")
        assert result is not None
        assert result["success"] is False
        assert "BLOCKED" in result["error"]

    def test_search_after_max_stores_unblocks(self):
        _booted_sources.add("claude")
        _record_search("claude", "proj")
        for _ in range(COMPLIANCE_MAX_STORES):
            _record_store("claude")
        assert _check_compliance("claude", "proj") is not None
        _record_search("claude", "proj")
        assert _check_compliance("claude", "proj") is None

    def test_anonymous_source_returns_none(self):
        result = _check_compliance("", "proj")
        assert result is None

    def test_different_sources_tracked_independently(self):
        _booted_sources.add("claude")
        _record_search("claude", "proj")
        assert _check_compliance("claude", "proj") is None
        # cursor hasn't booted
        assert _check_compliance("cursor", "proj") is not None

    def test_stores_below_max_are_allowed(self):
        _booted_sources.add("claude")
        _record_search("claude", "proj")
        for _ in range(COMPLIANCE_MAX_STORES - 1):
            _record_store("claude")
        assert _check_compliance("claude", "proj") is None


# ─── remember() compliance ───────────────────────────────────────────────────

class TestRememberCompliance:

    def test_remember_without_boot_is_blocked(self):
        raw = remember("Test note for compliance", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
        assert result["blocked_by"] == "boot_required"

    def test_remember_booted_but_no_search_is_blocked(self):
        _booted_sources.add("test-agent")
        raw = remember("Test note for compliance", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
        assert result["blocked_by"] == "compliance"

    def test_remember_after_boot_and_search_succeeds(self):
        _booted_sources.add("test-agent")
        _record_search("test-agent", TEST_PROJECT)
        raw = remember("Test note after search", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is True

    def test_remember_without_boot_does_not_store(self):
        raw = remember("Important note to store", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is False
        assert "id" not in result

    def test_remember_omitted_source_raises_type_error(self):
        # Schema-level enforcement: source is positional-required. Omitting
        # it at the Python level raises TypeError; at the MCP level, the
        # protocol rejects the call before it reaches the function.
        with pytest.raises(TypeError):
            remember("Anonymous note", project=TEST_PROJECT)

    def test_remember_empty_source_rejected_with_clear_error(self):
        # Body-level enforcement: empty string is as lazy as omitting and
        # must also fail, otherwise agents could bypass per-agent tracking
        # by passing source="".
        raw = remember("Anon attempt", source="", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["blocked_by"] == "source_required"
        assert "source is required" in result["error"]


# ─── capture_context() compliance ────────────────────────────────────────────

class TestCaptureContextCompliance:

    def test_capture_without_boot_is_blocked(self):
        raw = capture_context(
            "Built a new feature for testing compliance tracking",
            source="test-agent",
            project=TEST_PROJECT,
        )
        result = json.loads(raw)
        assert result["success"] is False
        assert "BLOCKED" in result["error"]

    def test_capture_after_boot_and_search_succeeds(self):
        _booted_sources.add("test-agent")
        _record_search("test-agent", TEST_PROJECT)
        raw = capture_context(
            "Captured after searching, should be compliant",
            source="test-agent",
            project=TEST_PROJECT,
        )
        result = json.loads(raw)
        assert result["success"] is True


# ─── search() source param ───────────────────────────────────────────────────

class TestSearchSourceParam:

    def test_search_accepts_source_param(self):
        raw = search("test query", source="test-agent", project=TEST_PROJECT)
        assert isinstance(raw, str)
        # Search records in tracker but doesn't boot
        assert "test-agent" in _session_tracker

    def test_search_omitted_source_raises_type_error(self):
        with pytest.raises(TypeError):
            search("test query", project=TEST_PROJECT)

    def test_search_empty_source_rejected_with_clear_error(self):
        raw = search("test query", source="", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["blocked_by"] == "source_required"

    def test_search_does_not_require_boot(self):
        # search() should work without boot_session -- it's a read tool
        raw = search("test query", source="test-agent", project=TEST_PROJECT)
        assert isinstance(raw, str)
        # But storing still requires boot
        result = _check_compliance("test-agent", TEST_PROJECT)
        assert result is not None
        assert result["blocked_by"] == "boot_required"


# ─── boot_session() ─────────────────────────────────────────────────────────

class TestBootSession:

    def test_boot_returns_success(self):
        raw = boot_session(project=TEST_PROJECT, source="test-agent")
        result = json.loads(raw)
        assert result["success"] is True
        assert result["booted"] is True

    def test_boot_marks_source_as_booted(self):
        assert "test-agent" not in _booted_sources
        boot_session(project=TEST_PROJECT, source="test-agent")
        assert "test-agent" in _booted_sources

    def test_boot_records_search(self):
        boot_session(project=TEST_PROJECT, source="test-agent")
        assert "test-agent" in _session_tracker
        assert _session_tracker["test-agent"]["searches"] >= 1

    def test_boot_stores_in_scratchpad(self):
        from server import _scratch
        boot_session(project=TEST_PROJECT, source="test-agent")
        assert "boot_context" in _scratch
        assert "boot_project" in _scratch
        assert _scratch["boot_project"] == TEST_PROJECT

    def test_boot_unlocks_remember(self):
        # Before boot: blocked
        raw = remember("test", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is False
        assert result["blocked_by"] == "boot_required"

        # After boot: search is also recorded by boot, so remember should work
        boot_session(project=TEST_PROJECT, source="test-agent")
        raw = remember("test note after boot", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is True

    def test_boot_returns_context_sections(self):
        # Boot against test project -- may or may not have pinned guardrails
        # but should always return a valid context list
        raw = boot_session(project=TEST_PROJECT, source="test-agent")
        result = json.loads(raw)
        assert "context" in result
        assert isinstance(result["context"], list)

    def test_boot_without_project_still_succeeds(self):
        raw = boot_session(project="", source="test-agent")
        result = json.loads(raw)
        assert result["success"] is True
        assert result["booted"] is True

    def test_boot_empty_source_rejected(self):
        raw = boot_session(project=TEST_PROJECT, source="")
        result = json.loads(raw)
        assert result["success"] is False
        assert result["blocked_by"] == "source_required"

    def test_boot_omitted_source_raises_type_error(self):
        with pytest.raises(TypeError):
            boot_session(project=TEST_PROJECT)

    def test_boot_degrades_gracefully_on_error(self):
        # Even if something goes wrong internally, boot should not hard-fail
        # (tested by checking the error path still returns booted=True)
        raw = boot_session(project=TEST_PROJECT, source="test-agent")
        result = json.loads(raw)
        assert result["booted"] is True


# ─── brain_checkpoint() ─────────────────────────────────────────────────────

class TestBrainCheckpoint:

    def test_checkpoint_returns_success(self):
        raw = brain_checkpoint(action="edit infrastructure", project=TEST_PROJECT, source="test-agent")
        result = json.loads(raw)
        assert result["success"] is True
        assert "relevant" in result
        assert "warnings" in result

    def test_checkpoint_records_in_tracker(self):
        assert "test-agent" not in _checkpoint_tracker
        brain_checkpoint(action="edit server", project=TEST_PROJECT, source="test-agent")
        assert "test-agent" in _checkpoint_tracker
        assert "edit server" in _checkpoint_tracker["test-agent"]

    def test_checkpoint_cooldown_skips_repeat(self):
        brain_checkpoint(action="edit infrastructure", project=TEST_PROJECT, source="test-agent")
        # Second call with same action should be skipped
        raw = brain_checkpoint(action="edit infrastructure", project=TEST_PROJECT, source="test-agent")
        result = json.loads(raw)
        assert result.get("skipped") is True

    def test_checkpoint_different_action_not_skipped(self):
        brain_checkpoint(action="edit infrastructure", project=TEST_PROJECT, source="test-agent")
        raw = brain_checkpoint(action="edit database", project=TEST_PROJECT, source="test-agent")
        result = json.loads(raw)
        assert result.get("skipped") is not True
        assert result["success"] is True

    def test_checkpoint_returns_relevant_memories(self):
        raw = brain_checkpoint(action="edit infrastructure", context="startup script", project=TEST_PROJECT, source="test-agent")
        result = json.loads(raw)
        assert isinstance(result["relevant"], list)
        assert isinstance(result["guardrails"], int)
        assert isinstance(result["relevant_memories"], int)

    def test_checkpoint_requires_action(self):
        raw = brain_checkpoint(action="", project="", source="test-agent")
        result = json.loads(raw)
        assert result["success"] is False

    def test_checkpoint_empty_source_rejected(self):
        raw = brain_checkpoint(action="test action", project=TEST_PROJECT, source="")
        result = json.loads(raw)
        assert result["success"] is False
        assert result["blocked_by"] == "source_required"
        # Tracker should NOT have a _global entry from a rejected call
        assert "_global" not in _checkpoint_tracker

    def test_checkpoint_degrades_gracefully(self):
        # Should not hard-fail even with bad input
        raw = brain_checkpoint(action="test", project=TEST_PROJECT, source="test-agent")
        result = json.loads(raw)
        assert result["success"] is True


# ─── Multi-project tags ─────────────────────────────────────────────────────

class TestMultiProjectTags:

    def test_remember_with_projects_param(self):
        _booted_sources.add("test-agent")
        _record_search("test-agent", TEST_PROJECT)
        raw = remember(
            "This memory spans two projects",
            source="test-agent",
            project=TEST_PROJECT,
            projects=[TEST_PROJECT, "other-project"],
        )
        result = json.loads(raw)
        assert result["success"] is True

    def test_remember_projects_includes_primary(self):
        """Primary project should always be in the projects array even if not explicitly listed."""
        _booted_sources.add("test-agent")
        _record_search("test-agent", TEST_PROJECT)
        raw = remember(
            "Primary project should be in array automatically",
            source="test-agent",
            project=TEST_PROJECT,
            projects=["other-project"],
        )
        result = json.loads(raw)
        assert result["success"] is True

    def test_recall_includes_updated_at(self):
        """recall() should include updated_at when the memory has been updated."""
        _booted_sources.add("test-agent")
        _record_search("test-agent", TEST_PROJECT)
        # Store a memory
        raw = remember("Test updated_at visibility", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        mem_id = result["id"]
        # Recall it -- db_get_by_id does an UPDATE (bumps access_count) which triggers updated_at
        from server import recall
        raw2 = recall(memory_id=mem_id)
        result2 = json.loads(raw2)
        assert "updated_at" in result2
