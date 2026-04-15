"""Tests for the v0.14.0 action-item compliance gate.

Hit the test database per conftest.py isolation. Deterministic fake
embeddings unless `-m ollama` is passed.

Run with: pytest tests/test_action_item_gate.py -v
"""

import json
import os
import sys

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server as srv
from server import (
    ACTION_ITEM_AUDIT_LOG_PATH,
    _active_session_ids,
    _booted_sources,
    _extract_action_items_from_memory,
    _pending_action_items,
    _populate_pending_action_items,
    _record_search,
    acknowledge_action_item,
    boot_session,
    capture_context,
    db_set_pinned,
    db_store,
    extract_metadata,
    get_embedding,
    remember,
    search,
)

TEST_PROJECT = "__test_action_item_gate__"
TEST_SOURCE = "pytest-action-item-gate"


def _make_memory(content: str, action_items: list[str] | None = None,
                  created_at: str | None = None,
                  project: str = TEST_PROJECT,
                  type_override: str = "note") -> int:
    """Create a memory directly via db_store with optional action_items
    injected into metadata. Bypasses MCP compliance gates."""
    embedding = get_embedding(content)
    metadata = extract_metadata(content)
    metadata["type"] = type_override
    metadata["source"] = TEST_SOURCE
    if action_items is not None:
        metadata["action_items"] = action_items
    mem = db_store(content, embedding, metadata, project)
    # Override created_at if needed (tests want recent/old control)
    if created_at:
        from conftest import TEST_DATABASE_URL
        conn = psycopg2.connect(TEST_DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("UPDATE memories SET created_at = %s WHERE id = %s",
                        (created_at, mem["id"]))
        conn.close()
    return mem["id"]


@pytest.fixture(autouse=True)
def cleanup():
    """Wipe test-project memories + reset per-source state around each test."""
    from conftest import TEST_DATABASE_URL

    _booted_sources.add(TEST_SOURCE)
    _record_search(TEST_SOURCE, TEST_PROJECT)
    _pending_action_items.pop(TEST_SOURCE, None)
    _active_session_ids.pop(TEST_SOURCE, None)

    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE memories SET superseded_by_id = NULL WHERE project = %s",
                    (TEST_PROJECT,))
        cur.execute("DELETE FROM memories WHERE project = %s", (TEST_PROJECT,))
        cur.execute("DELETE FROM active_sessions WHERE source = %s", (TEST_SOURCE,))
    yield
    with conn.cursor() as cur:
        cur.execute("UPDATE memories SET superseded_by_id = NULL WHERE project = %s",
                    (TEST_PROJECT,))
        cur.execute("DELETE FROM memories WHERE project = %s", (TEST_PROJECT,))
        cur.execute("DELETE FROM active_sessions WHERE source = %s", (TEST_SOURCE,))
    _pending_action_items.pop(TEST_SOURCE, None)
    _active_session_ids.pop(TEST_SOURCE, None)
    conn.close()


# ============================================================
# 1. Extraction helper
# ============================================================

def test_extract_action_items_returns_entries():
    """_extract_action_items_from_memory pulls items out of metadata."""
    mem = {"id": 42, "metadata": {"action_items": ["do X", "do Y"]}}
    out = _extract_action_items_from_memory(mem, origin="recent_history")
    assert len(out) == 2
    assert out[0] == {"memory_id": 42, "index": 0,
                       "text": "do X", "origin": "recent_history"}
    assert out[1]["index"] == 1


def test_extract_skips_empty_and_non_string():
    mem = {"id": 1, "metadata": {"action_items": ["ok", "", None, "  ", "fine"]}}
    out = _extract_action_items_from_memory(mem, origin="known_issues")
    texts = [e["text"] for e in out]
    assert texts == ["ok", "fine"]


def test_extract_handles_string_metadata():
    """metadata might come back as a JSON string from some code paths."""
    mem = {"id": 1, "metadata": json.dumps({"action_items": ["a"]})}
    out = _extract_action_items_from_memory(mem, origin="recent_history")
    assert len(out) == 1


# ============================================================
# 2. Populate pending + dedupe + cap
# ============================================================

def test_populate_dedupes_across_origins():
    """Same action_item text surfaced in two memories counts once."""
    mems = {
        "recent_history": [{"id": 1, "metadata": {"action_items": ["same task"]}}],
        "known_issues":   [{"id": 2, "metadata": {"action_items": ["same task"]}}],
    }
    out = _populate_pending_action_items(TEST_SOURCE, mems)
    assert len(out) == 1
    assert out[0]["memory_id"] == 1  # recent_history processed first


def test_populate_respects_cap():
    """Beyond ACTION_ITEM_GATE_MAX, keep the most-recent (highest id)."""
    original = srv.ACTION_ITEM_GATE_MAX
    srv.ACTION_ITEM_GATE_MAX = 3
    try:
        mems = {
            "recent_history": [
                {"id": i, "metadata": {"action_items": [f"item {i}"]}}
                for i in range(1, 8)  # 7 items
            ],
            "known_issues": [],
        }
        out = _populate_pending_action_items(TEST_SOURCE, mems)
        assert len(out) == 3
        ids = sorted([e["memory_id"] for e in out])
        assert ids == [5, 6, 7]  # most-recent kept
    finally:
        srv.ACTION_ITEM_GATE_MAX = original


# ============================================================
# 3. boot_session extraction integration
# ============================================================

def test_boot_surfaces_action_items_from_recent_history():
    """A recent memory with action_items appears in the boot response's
    pending_action_items list."""
    _make_memory("Recent work note.",
                  action_items=["Update the flashcard app for correct role"])
    out = json.loads(boot_session(source=TEST_SOURCE, project=TEST_PROJECT))
    texts = [p["text"] for p in out.get("pending_action_items", [])]
    assert "Update the flashcard app for correct role" in texts


def test_boot_no_action_items_no_gate_section():
    """Memory without action_items → no ACTION ITEMS PENDING section,
    no pending list."""
    _make_memory("Just a plain memory.")
    out = json.loads(boot_session(source=TEST_SOURCE, project=TEST_PROJECT))
    sections = {s["section"] for s in out["context"]}
    assert "ACTION ITEMS PENDING" not in sections
    assert out.get("pending_action_items") == []


# ============================================================
# 4. Gate blocks writes when pending
# ============================================================

def test_remember_blocked_when_pending():
    """remember() returns blocked_by='action_items_pending' when pending
    list is non-empty."""
    _make_memory("Task to flag.", action_items=["Handle task X"])
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT)
    # Confirm pending is populated
    assert len(_pending_action_items.get(TEST_SOURCE) or []) == 1
    out = json.loads(remember(
        content="Attempted write while pending.",
        source=TEST_SOURCE,
        project=TEST_PROJECT,
    ))
    assert out["success"] is False
    assert out.get("blocked_by") == "action_items_pending"
    assert "pending" in out


def test_search_allowed_when_pending():
    """search() is NOT in the write set and stays usable."""
    _make_memory("Pending work.", action_items=["Do a thing"])
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT)
    raw = search(query="pending work", source=TEST_SOURCE,
                  project=TEST_PROJECT)
    # Success = either a results list or the graceful "No memories" string
    assert isinstance(raw, str)


# ============================================================
# 5. Acknowledge clears pending
# ============================================================

def test_acknowledge_clears_one_item_preserves_others():
    mid1 = _make_memory("Memory A.", action_items=["Task A"])
    mid2 = _make_memory("Memory B.", action_items=["Task B"])
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT)
    assert len(_pending_action_items[TEST_SOURCE]) == 2

    out = json.loads(acknowledge_action_item(
        source=TEST_SOURCE, memory_id=mid1, text="Task A",
        decision="will_execute",
    ))
    assert out["success"] is True
    assert out["remaining"] == 1
    # Still blocked because Task B remains
    w = json.loads(remember(content="still blocked",
                             source=TEST_SOURCE, project=TEST_PROJECT))
    assert w.get("blocked_by") == "action_items_pending"


def test_acknowledge_all_unblocks_writes():
    mid = _make_memory("One task.", action_items=["Single task"])
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT)
    acknowledge_action_item(source=TEST_SOURCE, memory_id=mid,
                             text="Single task",
                             decision="will_execute")
    assert _pending_action_items[TEST_SOURCE] == []
    out = json.loads(remember(content="now unblocked",
                                source=TEST_SOURCE, project=TEST_PROJECT))
    # Either success or a different blocker (e.g. dedup), but NOT action_items_pending
    assert out.get("blocked_by") != "action_items_pending"


# ============================================================
# 6. Acknowledge validation
# ============================================================

def test_acknowledge_requires_reason_for_not_relevant():
    out = json.loads(acknowledge_action_item(
        source=TEST_SOURCE, memory_id=1, text="anything",
        decision="not_relevant", reason="",
    ))
    assert out["success"] is False
    assert "reason" in out["error"]


def test_acknowledge_requires_reason_for_already_done():
    out = json.loads(acknowledge_action_item(
        source=TEST_SOURCE, memory_id=1, text="anything",
        decision="already_done", reason="",
    ))
    assert out["success"] is False


def test_acknowledge_rejects_unknown_decision():
    out = json.loads(acknowledge_action_item(
        source=TEST_SOURCE, memory_id=1, text="x",
        decision="maybe",
    ))
    assert out["success"] is False


def test_acknowledge_idempotent_on_unknown_item():
    """Acking an item that's not pending returns success with unchanged
    remaining count."""
    out = json.loads(acknowledge_action_item(
        source=TEST_SOURCE, memory_id=9999, text="never pending",
        decision="will_execute",
    ))
    assert out["success"] is True
    assert out["removed"] == 0
