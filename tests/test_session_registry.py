"""Tests for the v0.13.0 session-registry feature.

Hit the test database per conftest.py isolation. Use deterministic fake
embeddings unless `-m ollama` is passed.

Run with: pytest tests/test_session_registry.py -v
"""

import json
import os
import sys
import time

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import (
    _active_session_ids,
    _booted_sources,
    _record_search,
    boot_session,
    db_end_session,
    db_heartbeat_session,
    db_list_active_sessions,
    db_register_session,
    db_sweep_dead_sessions,
    db_update_active_task,
    end_session,
    list_active_sessions,
    update_active_task,
)

TEST_PROJECT = "__test_session_registry__"
OTHER_PROJECT = "__test_session_registry_other__"
TEST_SOURCE = "pytest-session-registry-a"
SIBLING_SOURCE = "pytest-session-registry-b"


@pytest.fixture(autouse=True)
def cleanup_sessions():
    """Wipe active_sessions rows for test sources + test projects around each test."""
    from conftest import TEST_DATABASE_URL

    _booted_sources.add(TEST_SOURCE)
    _booted_sources.add(SIBLING_SOURCE)
    _record_search(TEST_SOURCE, TEST_PROJECT)
    _record_search(SIBLING_SOURCE, TEST_PROJECT)

    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DELETE FROM active_sessions WHERE source IN (%s, %s)",
                    (TEST_SOURCE, SIBLING_SOURCE))
    _active_session_ids.pop(TEST_SOURCE, None)
    _active_session_ids.pop(SIBLING_SOURCE, None)
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM active_sessions WHERE source IN (%s, %s)",
                    (TEST_SOURCE, SIBLING_SOURCE))
    _active_session_ids.pop(TEST_SOURCE, None)
    _active_session_ids.pop(SIBLING_SOURCE, None)
    conn.close()


# ============================================================
# 1. Schema sanity
# ============================================================

def test_active_sessions_table_exists():
    """v6 migration applied to test DB."""
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'active_sessions' ORDER BY ordinal_position
        """)
        cols = [r[0] for r in cur.fetchall()]
    conn.close()
    assert cols, "active_sessions table missing — run migrate_v6"
    for expected in ("id", "source", "project", "cwd", "pid", "host",
                     "current_task", "started_at", "heartbeat_at",
                     "status", "metadata"):
        assert expected in cols, f"column {expected} missing"


# ============================================================
# 2. db_register_session
# ============================================================

def test_register_session_inserts_row():
    row = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/test",
                               1234, "host-a", "testing registry")
    assert row["id"] > 0
    assert row["source"] == TEST_SOURCE
    assert row["project"] == TEST_PROJECT
    assert row["cwd"] == "F:/test"
    assert row["current_task"] == "testing registry"
    assert row["status"] == "active"


def test_register_session_twice_creates_two_rows():
    """Two boots from the same source + cwd are allowed (user may run two
    Claude terminals in the same repo). Each gets its own row."""
    a = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/test", None, "h", "task A")
    b = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/test", None, "h", "task B")
    assert a["id"] != b["id"]


# ============================================================
# 3. boot_session returns OTHER_ACTIVE_SESSIONS
# ============================================================

def test_boot_session_registers_and_returns_session_id():
    out = json.loads(boot_session(source=TEST_SOURCE, project=TEST_PROJECT,
                                    task="pytest boot"))
    assert out["success"] is True
    assert out["session_id"] > 0
    assert _active_session_ids[TEST_SOURCE] == out["session_id"]


def test_boot_session_surfaces_sibling_session():
    """If a sibling session exists in the same project, the booting
    session must see it in the OTHER ACTIVE SESSIONS block."""
    sibling = db_register_session(SIBLING_SOURCE, TEST_PROJECT,
                                   "C:/sibling", 9999, "host-b",
                                   "sibling is doing stuff")
    out = json.loads(boot_session(source=TEST_SOURCE, project=TEST_PROJECT))
    sections = {s["section"]: s for s in out["context"]}
    assert "OTHER ACTIVE SESSIONS" in sections
    others = sections["OTHER ACTIVE SESSIONS"]["content"]
    ids = [s["id"] for s in others]
    assert sibling["id"] in ids
    assert out["session_id"] not in ids  # excluded self


def test_boot_session_excludes_other_projects():
    """Sibling sessions in other projects do not surface when boot
    filters by project."""
    db_register_session(SIBLING_SOURCE, OTHER_PROJECT, "C:/sib", None,
                         "h", "unrelated project")
    out = json.loads(boot_session(source=TEST_SOURCE, project=TEST_PROJECT))
    sections = {s["section"]: s for s in out["context"]}
    # either the section is missing entirely (no siblings in TEST_PROJECT)
    # or it exists without the OTHER_PROJECT entry
    if "OTHER ACTIVE SESSIONS" in sections:
        others = sections["OTHER ACTIVE SESSIONS"]["content"]
        projects = [s.get("project") for s in others]
        assert OTHER_PROJECT not in projects


# ============================================================
# 4. TTL sweep
# ============================================================

def test_sweep_dead_sessions_ends_stale_rows():
    """Rows with heartbeat older than TTL get status='ended' on sweep."""
    from conftest import TEST_DATABASE_URL
    row = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/test",
                               None, "h", "stale")
    # Manually age the heartbeat
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE active_sessions "
                    "SET heartbeat_at = now() - interval '10 minutes' "
                    "WHERE id = %s", (row["id"],))
    swept = db_sweep_dead_sessions(ttl_minutes=5)
    assert swept >= 1
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (row["id"],))
        status = cur.fetchone()[0]
    conn.close()
    assert status == "ended"


def test_sweep_leaves_fresh_sessions_active():
    row = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/test",
                               None, "h", "fresh")
    db_sweep_dead_sessions(ttl_minutes=5)
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (row["id"],))
        status = cur.fetchone()[0]
    conn.close()
    assert status == "active"


# ============================================================
# 5. Implicit heartbeat
# ============================================================

def test_heartbeat_bumps_timestamp():
    """db_heartbeat_session bumps heartbeat_at."""
    from conftest import TEST_DATABASE_URL
    row = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/test",
                               None, "h", "heartbeat test")
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE active_sessions "
                    "SET heartbeat_at = now() - interval '3 minutes' "
                    "WHERE id = %s", (row["id"],))
        cur.execute("SELECT heartbeat_at FROM active_sessions WHERE id = %s",
                    (row["id"],))
        before = cur.fetchone()[0]
    db_heartbeat_session(row["id"])
    with conn.cursor() as cur:
        cur.execute("SELECT heartbeat_at FROM active_sessions WHERE id = %s",
                    (row["id"],))
        after = cur.fetchone()[0]
    conn.close()
    assert after > before


def test_heartbeat_noop_on_ended_session():
    """A session with status='ended' is not resurrected by a heartbeat."""
    from conftest import TEST_DATABASE_URL
    row = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/test",
                               None, "h", "will end")
    db_end_session(row["id"])
    db_heartbeat_session(row["id"])
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (row["id"],))
        status = cur.fetchone()[0]
    conn.close()
    assert status == "ended"


# ============================================================
# 6. update_active_task MCP tool
# ============================================================

def test_update_active_task_updates_current_task():
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT, task="initial")
    out = json.loads(update_active_task(source=TEST_SOURCE,
                                          task="now doing X"))
    assert out["success"] is True
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT current_task FROM active_sessions WHERE id = %s",
                    (out["session_id"],))
        task = cur.fetchone()[0]
    conn.close()
    assert task == "now doing X"


def test_update_active_task_requires_boot_first():
    """Without a cached session id, update_active_task returns error."""
    _active_session_ids.pop(TEST_SOURCE, None)
    out = json.loads(update_active_task(source=TEST_SOURCE, task="orphaned"))
    assert out["success"] is False
    assert "boot_session" in out["error"].lower()


def test_update_active_task_requires_source():
    out = json.loads(update_active_task(source="", task="anything"))
    assert out["success"] is False


# ============================================================
# 7. list_active_sessions MCP tool
# ============================================================

def test_list_active_sessions_sees_sibling():
    sibling = db_register_session(SIBLING_SOURCE, TEST_PROJECT, "C:/sib",
                                   None, "h", "sibling task")
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT, task="myself")
    out = json.loads(list_active_sessions(source=TEST_SOURCE,
                                            project=TEST_PROJECT))
    assert out["success"] is True
    ids = [s["id"] for s in out["sessions"]]
    assert sibling["id"] in ids


def test_list_active_sessions_excludes_self_by_default():
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT, task="myself")
    my_id = _active_session_ids[TEST_SOURCE]
    out = json.loads(list_active_sessions(source=TEST_SOURCE,
                                            project=TEST_PROJECT))
    ids = [s["id"] for s in out["sessions"]]
    assert my_id not in ids


def test_list_active_sessions_can_include_self():
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT, task="myself")
    my_id = _active_session_ids[TEST_SOURCE]
    out = json.loads(list_active_sessions(source=TEST_SOURCE,
                                            project=TEST_PROJECT,
                                            exclude_self=False))
    ids = [s["id"] for s in out["sessions"]]
    assert my_id in ids


# ============================================================
# 8. end_session MCP tool
# ============================================================

def test_end_session_marks_ended():
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT, task="brief")
    my_id = _active_session_ids[TEST_SOURCE]
    out = json.loads(end_session(source=TEST_SOURCE))
    assert out["success"] is True
    assert out["was_active"] is True
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (my_id,))
        status = cur.fetchone()[0]
    conn.close()
    assert status == "ended"
    assert TEST_SOURCE not in _active_session_ids


def test_end_session_idempotent_on_already_ended():
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT, task="brief")
    sid = _active_session_ids[TEST_SOURCE]
    end_session(source=TEST_SOURCE)
    # Second call: source was popped, so no session to end
    out = json.loads(end_session(source=TEST_SOURCE, session_id=sid))
    # With explicit session_id: was_active is now False (already ended)
    assert out["success"] is True
    assert out["was_active"] is False
