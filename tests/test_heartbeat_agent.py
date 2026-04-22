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
import session_liveness as sl  # noqa: E402
from server import db_end_session, db_register_session, _get_conn  # noqa: E402

TEST_PROJECT = "__test_heartbeat_agent__"
TEST_SOURCE = "pytest-heartbeat-agent"


_ALL_TEST_SOURCES = (TEST_SOURCE, "pytest-boot-heal", "pytest-stale-warn")


@pytest.fixture(autouse=True)
def cleanup():
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM active_sessions WHERE source = ANY(%s)",
                    (list(_ALL_TEST_SOURCES),))
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM active_sessions WHERE source = ANY(%s)",
                    (list(_ALL_TEST_SOURCES),))
    for s in _ALL_TEST_SOURCES:
        server._active_session_ids.pop(s, None)
        if hasattr(server, "_pending_action_items"):
            server._pending_action_items.pop(s, None)


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

# ============================================================
# 6. Agent's host filter matches case-insensitively
# ============================================================

def test_probe_matches_host_case_insensitive():
    """Row stored with mixed-case host (e.g. legacy 'WORKSTATION-A') must still
    be picked up by an agent running on the same physical machine, even
    if socket.gethostname() returns a lowercase variant."""
    my_host = socket.gethostname()
    dead = _dead_pid()
    conn = _get_conn()
    # Insert row preserving uppercase host name by bypassing db_register_session
    # (which now normalizes to lowercase).
    upper_host = (my_host or "HOST").upper()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO active_sessions "
            "(source, project, cwd, pid, host, current_task, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'active') RETURNING id",
            (TEST_SOURCE, TEST_PROJECT, "F:/mixed-case",
             dead, upper_host, "legacy mixed-case row"),
        )
        row_id = cur.fetchone()[0]
    conn.commit()

    ha.probe_once(host_filter=my_host)

    assert _get_status(row_id) == "ended"


# ============================================================
# 7. boot_session self-heals dead same-host sibling rows
# ============================================================

def test_boot_session_marks_dead_sibling_ended():
    """A booting session should opportunistically mark dead same-host
    sibling rows as ended — even when the external heartbeat agent is
    not running. Belt-and-suspenders against agent downtime."""
    import json as _json
    my_host = socket.gethostname()
    dead = _dead_pid()
    # Pre-insert a dead sibling for this project on this host.
    sibling = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/dead-sibling",
                                    dead, my_host, "dead sibling")

    # Book a fresh session with a different source so exclude_session_id
    # drops our own row but keeps the sibling visible.
    raw = server.boot_session(source="pytest-boot-heal", project=TEST_PROJECT,
                               task="check opportunistic probe",
                               cwd="F:/booting", pid=os.getpid(),
                               host=my_host)
    try:
        payload = _json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        payload = {}
    assert isinstance(payload, dict)

    # Whether or not the section ships in the response (sibling got
    # removed mid-boot), the DB row must be 'ended' now.
    assert _get_status(sibling["id"]) == "ended"

    # If the section is present, the sibling must NOT appear in its content.
    sections = payload.get("context") or []
    for sec in sections:
        if sec.get("section") == "OTHER ACTIVE SESSIONS":
            content_ids = [s.get("id") for s in sec.get("content", [])]
            assert sibling["id"] not in content_ids
            # The self_healed_ended_ids field should include our sibling id.
            healed = sec.get("self_healed_ended_ids") or []
            assert sibling["id"] in healed
            break

    # Clean up the extra session we just registered.
    pytest_session_id = server._active_session_ids.get("pytest-boot-heal")
    if pytest_session_id:
        db_end_session(pytest_session_id)
        server._active_session_ids.pop("pytest-boot-heal", None)


# ============================================================
# 8. boot_session surfaces a warning when the registry is stale
# ============================================================

def test_boot_session_warning_surfaces_when_stale():
    """If the only same-project sibling row has an old heartbeat_at (and
    a still-live pid we shouldn't mark ended), boot_session's section
    must carry registry_trustworthy=False and a warning string."""
    import json as _json
    my_host = socket.gethostname()
    # Use a live pid (os.getpid) so probe_and_mark_ended leaves it alone,
    # then manually backdate its heartbeat_at to simulate a long-down
    # heartbeat agent.
    sibling = db_register_session(TEST_SOURCE, TEST_PROJECT,
                                    "F:/stale-sibling",
                                    os.getpid(), my_host, "alive but stale")
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE active_sessions "
            "SET heartbeat_at = now() - interval '2 hours' "
            "WHERE id = %s",
            (sibling["id"],),
        )
    conn.commit()

    raw = server.boot_session(source="pytest-stale-warn", project=TEST_PROJECT,
                               task="check staleness warning",
                               cwd="F:/booting2", pid=os.getpid(),
                               host=my_host)
    payload = _json.loads(raw) if isinstance(raw, str) else raw
    assert isinstance(payload, dict)

    sections = payload.get("context") or []
    oas = next((s for s in sections if s.get("section") == "OTHER ACTIVE SESSIONS"), None)
    assert oas is not None, "OTHER ACTIVE SESSIONS section expected"
    assert oas.get("registry_trustworthy") is False
    assert oas.get("registry_staleness_seconds") is not None
    assert oas["registry_staleness_seconds"] >= sl.STALENESS_WARN_SECONDS
    assert "warning" in oas

    # Clean up.
    pytest_session_id = server._active_session_ids.get("pytest-stale-warn")
    if pytest_session_id:
        db_end_session(pytest_session_id)
        server._active_session_ids.pop("pytest-stale-warn", None)


# ============================================================
# 9. Null-pid TTL sweep (Group C)
# ============================================================

def test_null_pid_row_ended_after_ttl():
    """A null-pid row older than NULL_PID_TTL_MINUTES must be marked
    ended by the janitorial sweep. These rows can't be probed by pid,
    so wall-clock is the only signal."""
    host = socket.gethostname()
    # Insert with started_at well past the 24h default TTL.
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO active_sessions "
            "(source, project, cwd, pid, host, current_task, status, started_at) "
            "VALUES (%s, %s, %s, NULL, %s, %s, 'active', now() - interval '2 days') "
            "RETURNING id",
            (TEST_SOURCE, TEST_PROJECT, "F:/stale-null", host, "stale null-pid"),
        )
        row_id = cur.fetchone()[0]
    conn.commit()

    ha.probe_once(host_filter=host)

    assert _get_status(row_id) == "ended"


def test_null_pid_row_survives_under_ttl():
    """A fresh null-pid row (started just now) must NOT be swept.
    Confirms the TTL gate."""
    host = socket.gethostname()
    r = db_register_session(TEST_SOURCE, TEST_PROJECT, "F:/fresh-null",
                             None, host, "fresh null-pid")

    ha.probe_once(host_filter=host)

    assert _get_status(r["id"]) == "active"


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
