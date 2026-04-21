"""Tests for the session-registry feature (v0.13.0, reworked v0.14.0).

v0.14.0 replaced TTL-based expiry with explicit signoff + external
heartbeat-agent pid probes (memory #4929 / #3719 — timer-based expiry is
wrong). These tests reflect that model:

- No TTL sweep tests (the function is gone).
- No implicit-heartbeat-on-brain-tool-calls tests.
- Supersede-on-reboot test (same source+cwd+pid ends the prior row).
- Signoff test (_signoff_all_sessions ends every cached session id).
- Heartbeat-agent tests live in test_heartbeat_agent.py (separate file).

Run with: pytest tests/test_session_registry.py -v
"""

import json
import os
import socket
import sys

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import (
    _active_session_ids,
    _booted_sources,
    _record_search,
    _signoff_all_sessions,
    boot_session,
    db_end_session,
    db_heartbeat_session,
    db_list_active_sessions,
    db_register_session,
    db_supersede_previous_session,
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
    """Wipe active_sessions rows for test sources around each test."""
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
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'active_sessions' ORDER BY ordinal_position
        """)
        cols = [r[0] for r in cur.fetchall()]
    conn.close()
    for expected in ("id", "source", "project", "cwd", "pid", "host",
                     "current_task", "started_at", "heartbeat_at",
                     "status", "metadata"):
        assert expected in cols


# ============================================================
# 2. db_register_session
# ============================================================

def test_register_inserts_row():
    row = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/test",
                               1234, "host-a", "testing registry")
    assert row["id"] > 0
    assert row["source"] == TEST_SOURCE
    assert row["project"] == TEST_PROJECT
    assert row["pid"] == 1234
    assert row["status"] == "active"


# ============================================================
# 3. boot_session registers + defaults pid/host
# ============================================================

def test_boot_registers_with_defaulted_pid_and_host():
    """When caller doesn't pass pid/host, server defaults to its own
    os.getpid() and socket.gethostname() so the heartbeat agent has
    something probe-able."""
    out = json.loads(boot_session(source=TEST_SOURCE, project=TEST_PROJECT,
                                    task="pytest boot"))
    assert out["success"] is True
    assert out["session_id"] > 0
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT pid, host FROM active_sessions WHERE id = %s",
                    (out["session_id"],))
        pid, host = cur.fetchone()
    conn.close()
    assert pid == os.getpid()
    # v0.14.x normalizes host to lowercase on insert. The raw
    # socket.gethostname() on Windows is typically uppercase (e.g.
    # 'DAVE-PC'); the stored form is 'dave-pc'.
    assert host == socket.gethostname().lower()


class _FakeClientInfo:
    def __init__(self, name, version):
        self.name = name
        self.version = version


class _FakeClientParams:
    def __init__(self, client_info):
        self.clientInfo = client_info


class _FakeSession:
    def __init__(self, client_params):
        self.client_params = client_params


class _FakeRequestContext:
    def __init__(self, session):
        self.session = session


class _FakeContext:
    """Minimal duck-typed stand-in for FastMCP's Context. Mirrors the
    attribute path `context.request_context.session.client_params.clientInfo`."""
    def __init__(self, client_info):
        cp = _FakeClientParams(client_info)
        self.request_context = _FakeRequestContext(_FakeSession(cp))


def test_boot_session_stores_identity_metadata():
    """v0.23.2: boot_session called with an MCP context stores a
    metadata JSONB containing client.{name, version} from the
    initialize handshake + parent process identity."""
    ctx = _FakeContext(_FakeClientInfo("claude-ai", "1.0.90"))
    out = json.loads(boot_session(
        source=TEST_SOURCE, project=TEST_PROJECT, task="pytest identity",
        context=ctx,
    ))
    assert out["success"] is True
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT metadata FROM active_sessions WHERE id = %s",
                    (out["session_id"],))
        (md,) = cur.fetchone()
    conn.close()
    assert md is not None
    assert md.get("client") == {"name": "claude-ai", "version": "1.0.90"}
    assert "recorded_at" in md
    # Parent is best-effort; pytest has a parent so we expect it to be captured.
    if "parent" in md:
        assert isinstance(md["parent"].get("pid"), int)


def test_boot_session_without_context_stores_parent_only():
    """If no Context is injected (e.g. direct function call in a test,
    or an MCP client that didn't send clientInfo), boot_session still
    stores metadata — just without the `client` block."""
    out = json.loads(boot_session(source=TEST_SOURCE, project=TEST_PROJECT,
                                    task="pytest no-context"))
    assert out["success"] is True
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT metadata FROM active_sessions WHERE id = %s",
                    (out["session_id"],))
        (md,) = cur.fetchone()
    conn.close()
    assert md is not None
    assert "client" not in md
    assert "recorded_at" in md


def test_boot_surfaces_sibling_in_same_project():
    sibling = db_register_session(SIBLING_SOURCE, TEST_PROJECT,
                                   "C:/sibling", 9999, "host-b",
                                   "sibling is doing stuff")
    out = json.loads(boot_session(source=TEST_SOURCE, project=TEST_PROJECT))
    sections = {s["section"]: s for s in out["context"]}
    assert "OTHER ACTIVE SESSIONS" in sections
    ids = [s["id"] for s in sections["OTHER ACTIVE SESSIONS"]["content"]]
    assert sibling["id"] in ids


def test_boot_filters_other_projects():
    db_register_session(SIBLING_SOURCE, OTHER_PROJECT, "C:/sib", 8888,
                         "h", "unrelated project")
    out = json.loads(boot_session(source=TEST_SOURCE, project=TEST_PROJECT))
    sections = {s["section"]: s for s in out["context"]}
    if "OTHER ACTIVE SESSIONS" in sections:
        projects = [s.get("project") for s in sections["OTHER ACTIVE SESSIONS"]["content"]]
        assert OTHER_PROJECT not in projects


# ============================================================
# 4. Supersede-on-reboot (v0.14.0)
# ============================================================

def test_supersede_previous_session_by_source_cwd_pid():
    """A new boot from same (source, cwd, pid) marks the prior row 'ended'."""
    first = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/same",
                                  4242, "h", "first")
    count = db_supersede_previous_session(TEST_SOURCE, "F:/same", 4242)
    assert count >= 1
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (first["id"],))
        status = cur.fetchone()[0]
    conn.close()
    assert status == "ended"


def test_supersede_does_not_touch_different_pid():
    first = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/same",
                                  1111, "h", "first")
    count = db_supersede_previous_session(TEST_SOURCE, "F:/same", 2222)
    assert count == 0
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (first["id"],))
        assert cur.fetchone()[0] == "active"
    conn.close()


# ============================================================
# 5. Signoff (atexit / signal path)
# ============================================================

def test_signoff_all_sessions_ends_cached_ids():
    """_signoff_all_sessions() marks every cached session_id 'ended' and
    clears the cache. This is the atexit / signal-handler path."""
    r = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/x",
                             7777, "h", "pre-signoff")
    _active_session_ids[TEST_SOURCE] = r["id"]

    _signoff_all_sessions()

    assert TEST_SOURCE not in _active_session_ids
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (r["id"],))
        assert cur.fetchone()[0] == "ended"
    conn.close()


# ============================================================
# 6. update_active_task / end_session MCP tools
# ============================================================

def test_update_active_task_updates_task_field():
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT, task="initial")
    out = json.loads(update_active_task(source=TEST_SOURCE,
                                          task="pivoted to X"))
    assert out["success"] is True
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT current_task FROM active_sessions WHERE id = %s",
                    (out["session_id"],))
        assert cur.fetchone()[0] == "pivoted to X"
    conn.close()


def test_update_active_task_requires_boot():
    _active_session_ids.pop(TEST_SOURCE, None)
    out = json.loads(update_active_task(source=TEST_SOURCE, task="orphan"))
    assert out["success"] is False


def test_update_active_task_requires_source():
    out = json.loads(update_active_task(source="", task="x"))
    assert out["success"] is False


def test_end_session_marks_ended_and_clears_cache():
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT, task="brief")
    sid = _active_session_ids[TEST_SOURCE]
    out = json.loads(end_session(source=TEST_SOURCE))
    assert out["success"] is True
    assert out["was_active"] is True
    assert TEST_SOURCE not in _active_session_ids
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (sid,))
        assert cur.fetchone()[0] == "ended"
    conn.close()


# ============================================================
# 7. list_active_sessions cross-project visibility
# ============================================================

def test_list_active_sessions_empty_project_returns_all():
    """project='' means no filter — returns rows across all projects.
    This is the fix for the 'sibling in different project is invisible'
    complaint."""
    db_register_session(SIBLING_SOURCE, OTHER_PROJECT, "C:/sib",
                         5555, "h", "other proj")
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT)
    out = json.loads(list_active_sessions(source=TEST_SOURCE, project=""))
    projects = {r["project"] for r in out["sessions"]}
    # The sibling's row is in OTHER_PROJECT; this caller is in TEST_PROJECT.
    # With exclude_self=True default, we get the sibling but not ourselves.
    assert OTHER_PROJECT in projects


def test_list_active_sessions_project_filter_scopes():
    db_register_session(SIBLING_SOURCE, OTHER_PROJECT, "C:/sib",
                         6666, "h", "other proj")
    boot_session(source=TEST_SOURCE, project=TEST_PROJECT)
    out = json.loads(list_active_sessions(source=TEST_SOURCE,
                                            project=TEST_PROJECT))
    projects = {r["project"] for r in out["sessions"]}
    assert OTHER_PROJECT not in projects


# ============================================================
# 8. db_heartbeat_session (used by the external agent)
# ============================================================

def test_heartbeat_bumps_timestamp_only_when_active():
    """db_heartbeat_session updates heartbeat_at on active rows.
    Ended rows are not resurrected."""
    r = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/x",
                             3333, "h", "fresh")
    db_end_session(r["id"])
    db_heartbeat_session(r["id"])  # should be no-op on ended row
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (r["id"],))
        assert cur.fetchone()[0] == "ended"
    conn.close()
