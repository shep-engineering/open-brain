"""Tests for the v0.14.0 external heartbeat agent.

Hits the test DB directly. Uses psutil.pid_exists via real pids (os.getpid()
for "alive" and a definitely-gone pid for "dead"). No subprocess spawning —
the agent module is imported and probe_once() is called in-process.

Run with: pytest tests/test_heartbeat_agent.py -v

Uses server._get_conn() for connection reuse — avoids the 21-second
Windows+Docker DNS/TCP overhead per psycopg2.connect() call.
"""
from __future__ import annotations

import os
import socket
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import heartbeat_agent as ha  # noqa: E402
import server  # noqa: E402
from server import db_end_session, db_register_session, _get_conn  # noqa: E402

TEST_PROJECT = "__test_heartbeat_agent__"
TEST_SOURCE = "pytest-heartbeat-agent"


@pytest.fixture(autouse=True)
def cleanup():
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM active_sessions WHERE source = %s",
                    (TEST_SOURCE,))
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM active_sessions WHERE source = %s",
                    (TEST_SOURCE,))


def _dead_pid() -> int:
    """Return a pid that's unlikely to belong to any process."""
    import psutil
    for candidate in (987654, 876543, 765432, 654321):
        if not psutil.pid_exists(candidate):
            return candidate
    pytest.skip("couldn't find a dead pid on this system")


def _get_status(session_id: int) -> str:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (session_id,))
        row = cur.fetchone()
        return row[0] if row else ""


# ============================================================
# 1. Agent marks dead-pid rows 'ended'
# ============================================================

def test_probe_marks_dead_pid_ended():
    host = socket.gethostname()
    dead = _dead_pid()
    r = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/dead",
                             dead, host, "going to die")

    alive, ended = ha.probe_once(host_filter=host)

    assert ended >= 1
    assert _get_status(r["id"]) == "ended"


# ============================================================
# 2. Agent leaves alive-pid rows active
# ============================================================

def test_probe_leaves_alive_pid_active():
    host = socket.gethostname()
    my_pid = os.getpid()
    r = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/alive",
                             my_pid, host, "I am alive")

    ha.probe_once(host_filter=host)

    assert _get_status(r["id"]) == "active"


# ============================================================
# 3. Agent skips rows on other hosts
# ============================================================

def test_probe_skips_other_hosts():
    """A row with host='other-machine' must NOT be touched by this
    machine's agent, even if its pid appears dead locally."""
    dead = _dead_pid()
    r = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/remote",
                             dead, "other-machine-xyz", "remote")

    ha.probe_once(host_filter=socket.gethostname())

    assert _get_status(r["id"]) == "active"


# ============================================================
# 4. Agent skips rows without pid
# ============================================================

def test_probe_skips_null_pid_rows():
    """A row with NULL pid can't be probed; agent leaves it alone."""
    host = socket.gethostname()
    r = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/nopid",
                             None, host, "no pid to probe")

    ha.probe_once(host_filter=host)

    assert _get_status(r["id"]) == "active"


# ============================================================
# 5. Agent bumps heartbeat_at on confirmed-alive rows
# ============================================================

def test_probe_bumps_heartbeat_on_alive_rows():
    host = socket.gethostname()
    r = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/alive",
                             os.getpid(), host, "alive")
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("UPDATE active_sessions "
                    "SET heartbeat_at = now() - interval '10 minutes' "
                    "WHERE id = %s", (r["id"],))
        cur.execute("SELECT heartbeat_at FROM active_sessions WHERE id = %s",
                    (r["id"],))
        before = cur.fetchone()[0]

    ha.probe_once(host_filter=host)

    with conn.cursor() as cur:
        cur.execute("SELECT heartbeat_at FROM active_sessions WHERE id = %s",
                    (r["id"],))
        after = cur.fetchone()[0]
    assert after > before
