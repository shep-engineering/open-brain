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
    assert sl.normalize_host("WORKSTATION-A") == "workstation-a"
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
            (TEST_SOURCE, "__test__", "F:/case", dead, "WORKSTATION-A", "mixed case"),
        )
        row_id = cur.fetchone()[0]
    row = {"id": row_id, "host": "WORKSTATION-A", "pid": dead}
    ended = sl.probe_and_mark_ended(_get_conn(), [row], "workstation-a")
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


# ============================================================
# get_pid_create_time
# ============================================================

def test_get_pid_create_time_current_process_returns_float():
    ct = sl.get_pid_create_time(os.getpid())
    assert isinstance(ct, float)
    assert ct > 0


def test_get_pid_create_time_dead_pid_returns_none():
    assert sl.get_pid_create_time(_dead_pid()) is None


def test_get_pid_create_time_bad_input_returns_none():
    assert sl.get_pid_create_time(None) is None
    assert sl.get_pid_create_time("not-an-int") is None
    assert sl.get_pid_create_time(-1) is None
    assert sl.get_pid_create_time(0) is None


# ============================================================
# verify_pid_identity (pid-reuse-safe liveness)
# ============================================================

def test_verify_pid_identity_current_process_matches():
    """Same pid + same create_time -> alive."""
    my_pid = os.getpid()
    ct = sl.get_pid_create_time(my_pid)
    assert ct is not None
    assert sl.verify_pid_identity(my_pid, ct) is True


def test_verify_pid_identity_different_create_time_fails():
    """Same pid but stored create_time doesn't match current process's
    create_time -> treated as pid reuse -> NOT alive. Simulates the
    production scenario where pid 41712 was reused by ChatGPT.exe."""
    my_pid = os.getpid()
    # Fabricate a create_time far from any real process's start time.
    assert sl.verify_pid_identity(my_pid, 0.0) is False
    # Also tiny delta beyond tolerance should fail.
    real = sl.get_pid_create_time(my_pid)
    assert sl.verify_pid_identity(my_pid, real + 10.0) is False


def test_verify_pid_identity_within_tolerance_passes():
    """Sub-second jitter on create_time is absorbed by the 1s tolerance."""
    my_pid = os.getpid()
    real = sl.get_pid_create_time(my_pid)
    # 0.1s off should still match.
    assert sl.verify_pid_identity(my_pid, real + 0.1) is True
    assert sl.verify_pid_identity(my_pid, real - 0.1) is True


def test_verify_pid_identity_null_create_time_falls_back_to_pid_alive():
    """Legacy rows (pre-v0.23.1) have pid_create_time=None; verify
    should behave exactly like is_pid_alive for those."""
    my_pid = os.getpid()
    assert sl.verify_pid_identity(my_pid, None) is True
    assert sl.verify_pid_identity(_dead_pid(), None) is False


def test_verify_pid_identity_dead_pid_fails():
    assert sl.verify_pid_identity(_dead_pid(), 12345.0) is False


def test_verify_pid_identity_bad_input_fails():
    assert sl.verify_pid_identity(None, 12345.0) is False
    assert sl.verify_pid_identity("xyz", 12345.0) is False
    assert sl.verify_pid_identity(0, 12345.0) is False


# ============================================================
# Pid reuse end-to-end: probe reaps a row whose stored create_time
# no longer matches the live process at that pid.
# ============================================================

def test_probe_and_mark_ended_reaps_pid_reuse_impostor():
    """Register a row with our own (alive) pid, then clobber its stored
    pid_create_time with 0.0 to simulate the pid having been reused by
    a different process. Probe should mark the row ended."""
    host = socket.gethostname()
    r = db_register_session(TEST_SOURCE, "__test__", "F:/impostor",
                             os.getpid(), host, "will look like pid reuse")
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE active_sessions SET pid_create_time = 0.0 WHERE id = %s",
            (r["id"],),
        )
    # Fresh row dict carrying the fake create_time.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, pid, host, pid_create_time, heartbeat_at "
            "FROM active_sessions WHERE id = %s",
            (r["id"],),
        )
        row = {
            k: v for k, v in zip(
                ("id", "pid", "host", "pid_create_time", "heartbeat_at"),
                cur.fetchone(),
            )
        }
    ended = sl.probe_and_mark_ended(_get_conn(), [row], host)
    assert r["id"] in ended
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (r["id"],))
        assert cur.fetchone()[0] == "ended"


def test_probe_and_mark_ended_preserves_legacy_null_rows():
    """A row with NULL pid_create_time and an alive pid must NOT be
    reaped — legacy back-compat (pre-v0.23.1 rows predate the column).
    probe falls back to the pid-only check via is_pid_alive."""
    host = socket.gethostname()
    r = db_register_session(TEST_SOURCE, "__test__", "F:/legacy",
                             os.getpid(), host, "legacy-style")
    # Clobber to None to emulate a pre-migration row.
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE active_sessions SET pid_create_time = NULL WHERE id = %s",
            (r["id"],),
        )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, pid, host, pid_create_time, heartbeat_at "
            "FROM active_sessions WHERE id = %s",
            (r["id"],),
        )
        row = {
            k: v for k, v in zip(
                ("id", "pid", "host", "pid_create_time", "heartbeat_at"),
                cur.fetchone(),
            )
        }
    ended = sl.probe_and_mark_ended(_get_conn(), [row], host)
    assert ended == []
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                    (r["id"],))
        assert cur.fetchone()[0] == "active"


# ============================================================
# capture_identity_metadata (v0.23.2 / v2.1.2)
# ============================================================

def test_capture_identity_metadata_minimal():
    """No client_info + whatever psutil.Process().parent() returns →
    always gets recorded_at, and parent block if the parent is alive."""
    md = sl.capture_identity_metadata(client_info=None)
    assert "recorded_at" in md
    assert "client" not in md  # no client_info -> no client key
    # parent is best-effort; test process has a real parent (pytest), so
    # expect it to be populated. If it happens not to be, at least the
    # call didn't crash.
    if "parent" in md:
        assert isinstance(md["parent"].get("pid"), int)
        assert isinstance(md["parent"].get("name"), (str, type(None)))


def test_capture_identity_metadata_with_client_info():
    md = sl.capture_identity_metadata(
        client_info={"name": "claude-ai", "version": "1.0.90"},
    )
    assert md["client"] == {"name": "claude-ai", "version": "1.0.90"}
    assert "recorded_at" in md


def test_capture_identity_metadata_empty_client_info_omits_key():
    """If the dict is present but empty / has no useful fields, don't
    write an empty `client` block."""
    md = sl.capture_identity_metadata(client_info={})
    assert "client" not in md


def test_capture_identity_metadata_parent_exited(monkeypatch):
    """If the parent is None (exited), helper doesn't crash; parent block is omitted."""
    class _FakeProc:
        def parent(self_inner):
            return None
    monkeypatch.setattr(
        sl.psutil, "Process", lambda _pid=None: _FakeProc(),
    )
    md = sl.capture_identity_metadata(client_info=None)
    assert "parent" not in md
    assert "recorded_at" in md


def test_capture_identity_metadata_cmdline_truncated(monkeypatch):
    long_arg = "x" * 400
    class _FakeParent:
        pid = 4242
        def cmdline(self):
            return ["node.exe", long_arg]
        def name(self):
            return "node.exe"
    class _FakeProc:
        def parent(self_inner):
            return _FakeParent()
    monkeypatch.setattr(
        sl.psutil, "Process", lambda _pid=None: _FakeProc(),
    )
    md = sl.capture_identity_metadata(client_info=None)
    assert md["parent"]["pid"] == 4242
    assert md["parent"]["name"] == "node.exe"
    assert len(md["parent"]["cmdline_head"]) <= 200


def test_capture_identity_metadata_secret_scrubbed(monkeypatch):
    """Common credential patterns in the parent's cmdline get redacted
    before storage. Defensive belt-and-suspenders."""
    class _FakeParent:
        pid = 4242
        def cmdline(self):
            return [
                "node.exe", "cli.js",
                "--token=SECRET_A_VERYLONGTOKENVALUE123",
                "--api-key=SECRET_B_ANOTHER_VALUE",
                "Bearer", "AUTHSTRING_XYZ987",
                "sk-ABCDEFGHIJKLMNOPQRSTUVWX",
                "ghp_AAAAAAAAAAAAAAAAAAAAABBBBBB",
            ]
        def name(self):
            return "node.exe"
    class _FakeProc:
        def parent(self_inner):
            return _FakeParent()
    monkeypatch.setattr(
        sl.psutil, "Process", lambda _pid=None: _FakeProc(),
    )
    md = sl.capture_identity_metadata(client_info=None)
    head = md["parent"]["cmdline_head"]
    # Actual secret values should be gone; pattern prefix may remain.
    assert "SECRET_A_VERYLONGTOKENVALUE123" not in head
    assert "SECRET_B_ANOTHER_VALUE" not in head
    assert "AUTHSTRING_XYZ987" not in head
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in head
    assert "ghp_AAAAAAAAAAAAAAAAAAAAABBBBBB" not in head
    assert "<REDACTED>" in head


def test_scrub_cmdline_secrets_noop_on_clean_input():
    """Normal cmdlines without secrets pass through unchanged."""
    clean = "node.exe C:\\claude-code\\cli.js --stdio --project my-app"
    assert sl._scrub_cmdline_secrets(clean) == clean


# ============================================================
# sweep_host_stale (v0.24.0 — cross-host admin reaper)
# ============================================================

def _make_stale_row(age_seconds: int, host: str, pid_val: int = 0):
    """Register a row and backdate its heartbeat_at by age_seconds."""
    r = db_register_session(TEST_SOURCE, "__test__",
                             f"F:/xh-{age_seconds}",
                             pid_val or os.getpid(), host, "cross-host test")
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE active_sessions "
            "SET heartbeat_at = NOW() - make_interval(secs => %s) "
            "WHERE id = %s",
            (age_seconds, r["id"]),
        )
    return r


def test_sweep_host_stale_refuses_empty_host():
    result = sl.sweep_host_stale(_get_conn(), host="", max_age_seconds=60)
    assert result["success"] is False
    assert "host is required" in result["error"]


def test_sweep_host_stale_refuses_invalid_max_age():
    result = sl.sweep_host_stale(_get_conn(), host="someone-else",
                                  max_age_seconds=0)
    assert result["success"] is False
    result = sl.sweep_host_stale(_get_conn(), host="someone-else",
                                  max_age_seconds=-1)
    assert result["success"] is False


def test_sweep_host_stale_warns_on_local_host_but_proceeds():
    """v0.24.1: local-host sweeps proceed with a warning field attached.
    Refusal was too restrictive; dry_run=True default remains the
    load-bearing guardrail."""
    result = sl.sweep_host_stale(_get_conn(),
                                  host=socket.gethostname(),
                                  max_age_seconds=60,
                                  dry_run=True)
    assert result["success"] is True
    assert result["host"] == socket.gethostname().strip().lower()
    assert "warning" in result
    assert "LOCAL host" in result["warning"]


def test_sweep_host_stale_local_host_dry_run_does_not_write():
    """Local-host sweep with dry_run=True returns candidates without
    marking anything ended — same contract as remote-host dry_run."""
    host = socket.gethostname()
    stale = _make_stale_row(7200, host, pid_val=os.getpid())
    result = sl.sweep_host_stale(_get_conn(), host=host,
                                  max_age_seconds=3600, dry_run=True)
    assert result["success"] is True
    assert "warning" in result
    assert result["marked_ended"] == []
    assert _get_status(stale["id"]) == "active"


def test_sweep_host_stale_dry_run_returns_candidates_without_writing():
    remote = "remote-host-dry"
    stale = _make_stale_row(7200, remote)
    result = sl.sweep_host_stale(_get_conn(), host=remote,
                                  max_age_seconds=3600, dry_run=True)
    assert result["success"] is True
    assert result["dry_run"] is True
    ids = [c["id"] for c in result["candidates"]]
    assert stale["id"] in ids
    assert result["marked_ended"] == []
    assert _get_status(stale["id"]) == "active"


def test_sweep_host_stale_writes_when_dry_run_false():
    remote = "remote-host-write"
    stale = _make_stale_row(7200, remote)
    result = sl.sweep_host_stale(_get_conn(), host=remote,
                                  max_age_seconds=3600, dry_run=False)
    assert result["success"] is True
    assert result["dry_run"] is False
    assert stale["id"] in result["marked_ended"]
    assert _get_status(stale["id"]) == "ended"


def test_sweep_host_stale_ignores_fresh_rows():
    remote = "remote-host-fresh"
    fresh = _make_stale_row(10, remote)
    result = sl.sweep_host_stale(_get_conn(), host=remote,
                                  max_age_seconds=3600, dry_run=False)
    ids = [c["id"] for c in result["candidates"]]
    assert fresh["id"] not in ids
    assert result["marked_ended"] == []
    assert _get_status(fresh["id"]) == "active"


def test_sweep_host_stale_ignores_other_hosts():
    """Must only touch rows for the specified host."""
    target = "remote-target"
    bystander = "remote-bystander"
    target_row = _make_stale_row(7200, target)
    bystander_row = _make_stale_row(7200, bystander)
    result = sl.sweep_host_stale(_get_conn(), host=target,
                                  max_age_seconds=3600, dry_run=False)
    assert target_row["id"] in result["marked_ended"]
    assert bystander_row["id"] not in result["marked_ended"]
    assert _get_status(bystander_row["id"]) == "active"


def test_sweep_host_stale_case_insensitive_match():
    remote = "Remote-Mixed-Case"
    stale = _make_stale_row(7200, remote)
    # Normalize internally → lowercase; caller passes mixed case.
    result = sl.sweep_host_stale(_get_conn(), host="remote-MIXED-case",
                                  max_age_seconds=3600, dry_run=False)
    assert stale["id"] in result["marked_ended"]
