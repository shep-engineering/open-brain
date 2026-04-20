"""Tests for session_liveness.py — pure-function and DB-touching helpers.

Run with: pytest tests/test_session_liveness.py -v

The DB-backed test (probe_and_mark_ended) uses server._get_conn() and the
test DB (conftest.py forces DATABASE_URL to the openbrain_test database).
"""
from __future__ import annotations

import os
import socket
import sys
from datetime import datetime, timedelta, timezone

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

import session_liveness as sl  # noqa: E402
import server  # noqa: E402
from server import db_register_session, _get_conn  # noqa: E402

TEST_SOURCE = "pytest-session-liveness"


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


# ============================================================
# normalize_host
# ============================================================

def test_normalize_host_strips_and_lowercases():
    assert sl.normalize_host("DAVE-PC") == "dave-pc"
    assert sl.normalize_host("  HostName  ") == "hostname"


def test_normalize_host_empty_returns_none():
    assert sl.normalize_host("") is None
    assert sl.normalize_host(None) is None
    assert sl.normalize_host("   ") is None


# ============================================================
# is_pid_alive
# ============================================================

def test_is_pid_alive_current_process():
    assert sl.is_pid_alive(os.getpid()) is True


def test_is_pid_alive_dead_pid():
    import psutil
    for candidate in (987654, 876543, 765432, 654321):
        if not psutil.pid_exists(candidate):
            assert sl.is_pid_alive(candidate) is False
            return
    pytest.skip("couldn't find a dead pid on this system")


def test_is_pid_alive_bad_input_returns_false():
    assert sl.is_pid_alive(None) is False
    assert sl.is_pid_alive("not-an-int") is False
    assert sl.is_pid_alive(-1) is False
    assert sl.is_pid_alive(0) is False


# ============================================================
# compute_staleness
# ============================================================

def test_compute_staleness_empty_rows():
    seconds, trustworthy = sl.compute_staleness([])
    assert seconds is None
    assert trustworthy is True


def test_compute_staleness_fresh_row_is_trustworthy():
    now = datetime.now(timezone.utc)
    rows = [{"heartbeat_at": now - timedelta(seconds=5)}]
    seconds, trustworthy = sl.compute_staleness(rows)
    assert seconds is not None and seconds < 60
    assert trustworthy is True


def test_compute_staleness_stale_row_is_not_trustworthy():
    now = datetime.now(timezone.utc)
    rows = [{"heartbeat_at": now - timedelta(hours=1)}]
    seconds, trustworthy = sl.compute_staleness(rows)
    assert seconds is not None and seconds > sl.STALENESS_WARN_SECONDS
    assert trustworthy is False


def test_compute_staleness_uses_max_across_rows():
    """Freshest row in the set drives the decision — a fresh bump on any
    one row means the agent ran recently."""
    now = datetime.now(timezone.utc)
    rows = [
        {"heartbeat_at": now - timedelta(hours=1)},   # stale
        {"heartbeat_at": now - timedelta(seconds=5)}, # fresh
    ]
    seconds, trustworthy = sl.compute_staleness(rows)
    assert trustworthy is True
    assert seconds is not None and seconds < 60


def test_compute_staleness_naive_datetime_assumed_utc():
    """Naive datetimes should not raise — they're coerced to UTC."""
    naive = datetime.utcnow() - timedelta(seconds=5)
    rows = [{"heartbeat_at": naive}]
    seconds, trustworthy = sl.compute_staleness(rows)
    assert trustworthy is True


def test_compute_staleness_iso_string_accepted():
    recent_iso = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    rows = [{"heartbeat_at": recent_iso}]
    seconds, trustworthy = sl.compute_staleness(rows)
    assert trustworthy is True


# ============================================================
# probe_and_mark_ended (DB-backed)
# ============================================================

def _dead_pid() -> int:
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


def test_probe_and_mark_ended_flips_dead_same_host_row():
    host = socket.gethostname()
    dead = _dead_pid()
    r = db_register_session(TEST_SOURCE, "__test__", "F:/dead",
                             dead, host, "dead one")
    ended = sl.probe_and_mark_ended(_get_conn(), [r], host)
    assert r["id"] in ended
    assert _get_status(r["id"]) == "ended"


def test_probe_and_mark_ended_leaves_alive_row_active():
    host = socket.gethostname()
    r = db_register_session(TEST_SOURCE, "__test__", "F:/alive",
                             os.getpid(), host, "alive one")
    ended = sl.probe_and_mark_ended(_get_conn(), [r], host)
    assert r["id"] not in ended
    assert _get_status(r["id"]) == "active"


def test_probe_and_mark_ended_ignores_other_hosts():
    """A row on another host must not be probed even if its pid is dead
    locally — we don't know anything about the other host's pid space."""
    dead = _dead_pid()
    r = db_register_session(TEST_SOURCE, "__test__", "F:/elsewhere",
                             dead, "some-other-host", "elsewhere")
    ended = sl.probe_and_mark_ended(_get_conn(), [r], socket.gethostname())
    assert ended == []
    assert _get_status(r["id"]) == "active"


def test_probe_and_mark_ended_case_insensitive_host_match():
    """Row stored with mixed-case host still matches when my_host is
    lowercase — normalize_host is applied on both sides."""
    dead = _dead_pid()
    # Insert directly to preserve mixed case (db_register_session now normalizes).
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO active_sessions "
            "(source, project, cwd, pid, host, current_task, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'active') RETURNING id",
            (TEST_SOURCE, "__test__", "F:/case", dead, "DAVE-PC", "mixed case"),
        )
        row_id = cur.fetchone()[0]
    row = {"id": row_id, "host": "DAVE-PC", "pid": dead}
    ended = sl.probe_and_mark_ended(_get_conn(), [row], "dave-pc")
    assert row_id in ended
    assert _get_status(row_id) == "ended"


def test_probe_and_mark_ended_respects_cap():
    """Only up to `cap` same-host rows are probed per call."""
    host = socket.gethostname()
    dead = _dead_pid()
    rows = []
    for i in range(5):
        r = db_register_session(TEST_SOURCE, "__test__", f"F:/dead-{i}",
                                 dead, host, "dead")
        rows.append(r)
    ended = sl.probe_and_mark_ended(_get_conn(), rows, host, cap=2)
    assert len(ended) == 2
