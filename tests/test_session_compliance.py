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
    _check_compliance,
    _record_search,
    _record_store,
    _session_tracker,
    capture_context,
    remember,
    search,
    COMPLIANCE_MAX_STORES,
)

TEST_PROJECT = "__test_compliance__"


@pytest.fixture(autouse=True)
def clear_tracker():
    """Clear session tracker and test memories before/after each test."""
    _session_tracker.clear()
    yield
    _session_tracker.clear()
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

    def test_no_search_returns_blocked(self):
        result = _check_compliance("claude", "proj")
        assert result is not None
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
        assert result["blocked_by"] == "compliance"

    def test_recent_search_returns_none(self):
        _record_search("claude", "proj")
        result = _check_compliance("claude", "proj")
        assert result is None

    def test_max_stores_exceeded_returns_blocked(self):
        _record_search("claude", "proj")
        # Simulate COMPLIANCE_MAX_STORES stores without searching
        for _ in range(COMPLIANCE_MAX_STORES):
            _record_store("claude")
        result = _check_compliance("claude", "proj")
        assert result is not None
        assert result["success"] is False
        assert "BLOCKED" in result["error"]

    def test_search_after_max_stores_unblocks(self):
        _record_search("claude", "proj")
        for _ in range(COMPLIANCE_MAX_STORES):
            _record_store("claude")
        # Blocked now
        assert _check_compliance("claude", "proj") is not None
        # Search again — should unblock
        _record_search("claude", "proj")
        assert _check_compliance("claude", "proj") is None

    def test_anonymous_source_returns_none(self):
        result = _check_compliance("", "proj")
        assert result is None

    def test_different_sources_tracked_independently(self):
        _record_search("claude", "proj")
        assert _check_compliance("claude", "proj") is None
        assert _check_compliance("cursor", "proj") is not None

    def test_stores_below_max_are_allowed(self):
        _record_search("claude", "proj")
        for _ in range(COMPLIANCE_MAX_STORES - 1):
            _record_store("claude")
        assert _check_compliance("claude", "proj") is None


# ─── remember() compliance ───────────────────────────────────────────────────

class TestRememberCompliance:

    def test_remember_without_prior_search_is_blocked(self):
        raw = remember("Test note for compliance", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
        assert result["blocked_by"] == "compliance"

    def test_remember_after_search_succeeds(self):
        _record_search("test-agent", TEST_PROJECT)
        raw = remember("Test note after search", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is True

    def test_remember_without_search_does_not_store(self):
        raw = remember("Important note to store", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is False
        assert "id" not in result

    def test_remember_no_source_not_blocked(self):
        raw = remember("Anonymous note", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is True


# ─── capture_context() compliance ────────────────────────────────────────────

class TestCaptureContextCompliance:

    def test_capture_without_prior_search_is_blocked(self):
        raw = capture_context(
            "Built a new feature for testing compliance tracking",
            source="test-agent",
            project=TEST_PROJECT,
        )
        result = json.loads(raw)
        assert result["success"] is False
        assert "BLOCKED" in result["error"]

    def test_capture_after_search_succeeds(self):
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
        assert _check_compliance("test-agent", TEST_PROJECT) is None

    def test_search_without_source_still_works(self):
        raw = search("test query", project=TEST_PROJECT)
        assert isinstance(raw, str)
        assert "_global" in _session_tracker
