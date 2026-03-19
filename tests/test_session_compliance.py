"""Tests for session compliance tracking.

Verifies that remember() and capture_context() warn when no recent
search() has been called by the same source.

Run with: pytest tests/test_session_compliance.py -v
"""

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import (
    _check_compliance,
    _record_search,
    _session_tracker,
    capture_context,
    remember,
    search,
    COMPLIANCE_WINDOW,
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

    def test_record_search_stores_timestamp(self):
        _record_search("claude", "my-project")
        key = "claude:my-project"
        assert key in _session_tracker
        assert isinstance(_session_tracker[key], float)

    def test_record_search_updates_on_repeat(self):
        _record_search("claude", "proj")
        first = _session_tracker["claude:proj"]
        time.sleep(0.05)
        _record_search("claude", "proj")
        assert _session_tracker["claude:proj"] > first

    def test_record_search_source_only(self):
        _record_search("cursor", "")
        assert "cursor" in _session_tracker

    def test_record_search_project_only(self):
        _record_search("", "open-brain")
        assert "open-brain" in _session_tracker

    def test_record_search_neither(self):
        _record_search("", "")
        assert "_global" in _session_tracker


# ─── _check_compliance ───────────────────────────────────────────────────────

class TestCheckCompliance:

    def test_no_search_returns_warning(self):
        warning = _check_compliance("claude", "proj")
        assert warning is not None
        assert "No search()" in warning
        assert "claude" in warning

    def test_recent_search_returns_none(self):
        _record_search("claude", "proj")
        warning = _check_compliance("claude", "proj")
        assert warning is None

    def test_expired_search_returns_stale_warning(self):
        _record_search("claude", "proj")
        # Backdate the timestamp
        key = "claude:proj"
        _session_tracker[key] = time.time() - COMPLIANCE_WINDOW - 60
        warning = _check_compliance("claude", "proj")
        assert warning is not None
        assert "ago" in warning

    def test_anonymous_source_returns_none(self):
        # Can't track if no source is given
        warning = _check_compliance("", "proj")
        assert warning is None

    def test_different_sources_tracked_independently(self):
        _record_search("claude", "proj")
        # cursor never searched
        assert _check_compliance("claude", "proj") is None
        assert _check_compliance("cursor", "proj") is not None

    def test_different_projects_tracked_independently(self):
        _record_search("claude", "proj-a")
        assert _check_compliance("claude", "proj-a") is None
        assert _check_compliance("claude", "proj-b") is not None


# ─── remember() compliance ───────────────────────────────────────────────────

class TestRememberCompliance:

    def test_remember_without_prior_search_includes_warning(self):
        raw = remember("Test note for compliance", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is True
        assert "compliance_warning" in result
        assert "No search()" in result["compliance_warning"]

    def test_remember_after_search_has_no_warning(self):
        # Record a search first
        _record_search("test-agent", TEST_PROJECT)
        raw = remember("Test note after search", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is True
        assert "compliance_warning" not in result

    def test_remember_still_succeeds_with_warning(self):
        raw = remember("Important note to store", source="test-agent", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is True
        assert result["action"] in ("stored", "updated", "skipped")
        assert "id" in result
        # Warning present but didn't block
        assert "compliance_warning" in result

    def test_remember_no_source_no_warning(self):
        # Anonymous calls can't be tracked, so no warning
        raw = remember("Anonymous note", project=TEST_PROJECT)
        result = json.loads(raw)
        assert result["success"] is True
        assert "compliance_warning" not in result


# ─── capture_context() compliance ────────────────────────────────────────────

class TestCaptureContextCompliance:

    def test_capture_without_prior_search_includes_warning(self):
        raw = capture_context(
            "Built a new feature for testing compliance tracking",
            source="test-agent",
            project=TEST_PROJECT,
        )
        result = json.loads(raw)
        assert result["success"] is True
        assert "compliance_warning" in result
        assert "No search()" in result["compliance_warning"]

    def test_capture_after_search_has_no_warning(self):
        _record_search("test-agent", TEST_PROJECT)
        raw = capture_context(
            "Captured after searching, should be compliant",
            source="test-agent",
            project=TEST_PROJECT,
        )
        result = json.loads(raw)
        assert result["success"] is True
        assert "compliance_warning" not in result


# ─── search() source param ───────────────────────────────────────────────────

class TestSearchSourceParam:

    def test_search_accepts_source_param(self):
        raw = search("test query", source="test-agent", project=TEST_PROJECT)
        # Should not error
        assert isinstance(raw, str)
        # Should have recorded the search
        assert _check_compliance("test-agent", TEST_PROJECT) is None

    def test_search_without_source_still_works(self):
        raw = search("test query", project=TEST_PROJECT)
        assert isinstance(raw, str)
        # Global key should be recorded
        assert TEST_PROJECT in _session_tracker or "_global" in _session_tracker
