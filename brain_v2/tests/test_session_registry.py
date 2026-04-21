"""Session registry + handoff tests.

Exercises:
  - register_session writes a row, returns id
  - list_active_sessions filters correctly
  - update_active_task bumps heartbeat
  - end_session transitions status, non-idempotent
  - (source, cwd, pid) supersede on reboot: old row ended, new one created
  - write_handoff / get_latest_handoff roundtrip
  - Handoff content capped at 2000 chars
  - Empty handoff content rejected
  - boot.build auto-populates handoff from latest for project
  - boot.build excludes caller's own session from handoff auto-load
  - boot.build includes siblings in other_active_sessions
  - boot.build excludes caller's own session from siblings
  - register=False suppresses session write (for read-only inspection)
"""
from __future__ import annotations

import time

import pytest

from brain_v2 import boot, store


class TestRegisterSession:
    def test_register_returns_id(self, conn):
        sid = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/open-brain", pid=12345, host="DAVE-PC",
        )
        assert isinstance(sid, int)
        assert sid > 0

    def test_register_writes_row(self, conn):
        sid = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/open-brain", pid=12345,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT status, source, project, pid FROM active_sessions WHERE id = %s", (sid,))
            row = cur.fetchone()
            assert row == ("active", "claude", "test", 12345)

    def test_supersede_same_pid_cwd_source(self, conn):
        sid1 = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/open-brain", pid=12345,
        )
        sid2 = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/open-brain", pid=12345,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM active_sessions WHERE id = %s", (sid1,))
            assert cur.fetchone()[0] == "ended"
            cur.execute("SELECT status FROM active_sessions WHERE id = %s", (sid2,))
            assert cur.fetchone()[0] == "active"

    def test_different_pid_not_superseded(self, conn):
        sid1 = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/open-brain", pid=111,
        )
        sid2 = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/open-brain", pid=222,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM active_sessions WHERE id IN (%s, %s) ORDER BY id",
                        (sid1, sid2))
            statuses = [r[0] for r in cur.fetchall()]
            assert statuses == ["active", "active"]


class TestListActiveSessions:
    def test_list_returns_active_only(self, conn):
        sid1 = store.register_session(conn, source="claude", project="p1", pid=1)
        sid2 = store.register_session(conn, source="windsurf", project="p1", pid=2)
        store.end_session(conn, session_id=sid1)
        live = store.list_active_sessions(conn, project="p1")
        ids = [s["id"] for s in live]
        assert sid2 in ids
        assert sid1 not in ids

    def test_exclude_id(self, conn):
        sid1 = store.register_session(conn, source="claude", project="p1", pid=1)
        sid2 = store.register_session(conn, source="windsurf", project="p1", pid=2)
        live = store.list_active_sessions(conn, project="p1", exclude_id=sid1)
        ids = [s["id"] for s in live]
        assert sid1 not in ids
        assert sid2 in ids

    def test_project_filter(self, conn):
        store.register_session(conn, source="claude", project="alpha", pid=1)
        store.register_session(conn, source="claude", project="beta", pid=2)
        alpha_only = store.list_active_sessions(conn, project="alpha")
        assert all(s["project"] == "alpha" for s in alpha_only)


class TestUpdateActiveTask:
    def test_update_task(self, conn):
        sid = store.register_session(conn, source="claude", project="test", pid=1)
        ok = store.update_active_task(conn, session_id=sid, task="new task description")
        assert ok is True
        with conn.cursor() as cur:
            cur.execute("SELECT current_task FROM active_sessions WHERE id = %s", (sid,))
            assert cur.fetchone()[0] == "new task description"

    def test_update_ended_session_returns_false(self, conn):
        sid = store.register_session(conn, source="claude", project="test", pid=1)
        store.end_session(conn, session_id=sid)
        ok = store.update_active_task(conn, session_id=sid, task="whatever")
        assert ok is False

    def test_update_bumps_heartbeat(self, conn):
        sid = store.register_session(conn, source="claude", project="test", pid=1)
        with conn.cursor() as cur:
            cur.execute("SELECT heartbeat_at FROM active_sessions WHERE id = %s", (sid,))
            before = cur.fetchone()[0]
        time.sleep(0.1)
        store.update_active_task(conn, session_id=sid, task="updated")
        with conn.cursor() as cur:
            cur.execute("SELECT heartbeat_at FROM active_sessions WHERE id = %s", (sid,))
            after = cur.fetchone()[0]
        assert after > before


class TestEndSession:
    def test_end_transitions_status(self, conn):
        sid = store.register_session(conn, source="claude", project="test", pid=1)
        changed = store.end_session(conn, session_id=sid, source="claude")
        assert changed is True
        with conn.cursor() as cur:
            cur.execute("SELECT status, ended_at FROM active_sessions WHERE id = %s", (sid,))
            row = cur.fetchone()
            assert row[0] == "ended"
            assert row[1] is not None

    def test_end_returns_false_if_already_ended(self, conn):
        sid = store.register_session(conn, source="claude", project="test", pid=1)
        store.end_session(conn, session_id=sid)
        changed = store.end_session(conn, session_id=sid)
        assert changed is False

    def test_end_returns_false_if_not_found(self, conn):
        changed = store.end_session(conn, session_id=99999)
        assert changed is False


class TestHandoff:
    def test_write_and_get_latest(self, conn):
        hid = store.write_handoff(
            conn, source="claude",
            content="Left off at test X, next session picks up at Y.",
            project="test",
        )
        assert isinstance(hid, int)
        latest = store.get_latest_handoff(conn, project="test")
        assert latest is not None
        assert latest["id"] == hid
        assert "test X" in latest["content"]

    def test_latest_returns_most_recent(self, conn):
        store.write_handoff(conn, source="claude", content="First handoff note.", project="test")
        time.sleep(0.1)
        hid2 = store.write_handoff(conn, source="claude", content="Second handoff note.", project="test")
        latest = store.get_latest_handoff(conn, project="test")
        assert latest["id"] == hid2

    def test_content_capped_at_2000(self, conn):
        huge = "x" * 5000
        hid = store.write_handoff(conn, source="claude", content=huge, project="test")
        with conn.cursor() as cur:
            cur.execute("SELECT length(content) FROM handoffs WHERE id = %s", (hid,))
            assert cur.fetchone()[0] == 2000

    def test_empty_rejected(self, conn):
        with pytest.raises(ValueError, match="empty"):
            store.write_handoff(conn, source="claude", content="   ", project="test")

    def test_exclude_session_id(self, conn):
        sid1 = store.register_session(conn, source="claude", project="test", pid=1)
        sid2 = store.register_session(conn, source="claude", project="test", pid=2)
        store.write_handoff(conn, source="claude", content="Handoff from session one.",
                            project="test", session_id=sid1)
        store.write_handoff(conn, source="claude", content="Handoff from session two.",
                            project="test", session_id=sid2)
        # Exclude session 2 — should get session 1's handoff even though it's older
        latest = store.get_latest_handoff(conn, project="test", exclude_session_id=sid2)
        assert latest is not None
        assert "session one" in latest["content"]

    def test_project_filter(self, conn):
        store.write_handoff(conn, source="claude", content="alpha handoff", project="alpha")
        store.write_handoff(conn, source="claude", content="beta handoff", project="beta")
        latest = store.get_latest_handoff(conn, project="alpha")
        assert "alpha" in latest["content"]


class TestBootIntegration:
    def test_boot_registers_session(self, conn):
        payload = boot.build(
            conn, project="test", task="doing a thing", source="claude",
            cwd="F:/open-brain", pid=5555, host="DAVE-PC",
        )
        assert payload.session_id is not None
        with conn.cursor() as cur:
            cur.execute("SELECT source, project, pid FROM active_sessions WHERE id = %s",
                        (payload.session_id,))
            row = cur.fetchone()
            assert row == ("claude", "test", 5555)

    def test_boot_excludes_self_from_siblings(self, conn):
        payload = boot.build(
            conn, project="test", task="solo", source="claude",
            cwd="F:/open-brain", pid=5555,
        )
        sibling_ids = [s["id"] for s in payload.other_active_sessions]
        assert payload.session_id not in sibling_ids

    def test_boot_lists_siblings(self, conn):
        store.register_session(conn, source="windsurf", project="test", pid=99)
        payload = boot.build(
            conn, project="test", task="joining", source="claude",
            cwd="F:/open-brain", pid=100,
        )
        sibling_sources = {s["source"] for s in payload.other_active_sessions}
        assert "windsurf" in sibling_sources

    def test_boot_auto_populates_handoff(self, conn):
        # Register a prior session and write its handoff
        prior_sid = store.register_session(conn, source="claude", project="test", pid=1)
        store.write_handoff(conn, source="claude",
                            content="Previous session left off at step 4.",
                            project="test", session_id=prior_sid)
        store.end_session(conn, session_id=prior_sid)
        # New session boot
        payload = boot.build(
            conn, project="test", task="continuing", source="claude",
            cwd="F:/open-brain", pid=2,
        )
        assert "step 4" in payload.handoff
        assert payload.handoff_source is not None

    def test_boot_reboot_picks_up_predecessor_handoff(self, conn):
        # Process reboots: predecessor's handoff should be picked up by
        # the new session — that's the whole point of continuity.
        sid = store.register_session(conn, source="claude", project="test", pid=1)
        store.write_handoff(conn, source="claude",
                            content="Predecessor handoff text for continuity.",
                            project="test", session_id=sid)
        # Reboot from same process (supersedes sid, creates new row)
        payload = boot.build(
            conn, project="test", task="reboot", source="claude",
            cwd="", pid=1,
        )
        assert payload.session_id != sid
        assert "Predecessor handoff" in payload.handoff
        assert payload.handoff_source is not None

    def test_boot_does_not_echo_within_same_session(self, conn):
        # Within a SINGLE session row: if this session writes a handoff
        # and then boot is called again mid-session with the same session_id
        # (not via reboot), it should not echo its own handoff.
        # We simulate this by calling get_latest_handoff with the exclude
        # set to the writer's session_id — exercises the filter directly.
        sid = store.register_session(conn, source="claude", project="test", pid=1)
        store.write_handoff(conn, source="claude",
                            content="In-session handoff that should be hidden from its writer.",
                            project="test", session_id=sid)
        latest = store.get_latest_handoff(conn, project="test", exclude_session_id=sid)
        assert latest is None

    def test_boot_explicit_handoff_wins_over_auto(self, conn):
        prior_sid = store.register_session(conn, source="claude", project="test", pid=1)
        store.write_handoff(conn, source="claude",
                            content="Stored handoff should be ignored when caller supplies one.",
                            project="test", session_id=prior_sid)
        store.end_session(conn, session_id=prior_sid)
        payload = boot.build(
            conn, project="test", task="go", source="claude",
            cwd="", pid=2, handoff="Explicit handoff text.",
        )
        assert payload.handoff == "Explicit handoff text."

    def test_register_false_skips_registration(self, conn):
        payload = boot.build(
            conn, project="test", task="dry-run", source="claude",
            cwd="", pid=1, register=False,
        )
        assert payload.session_id is None
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM active_sessions")
            assert cur.fetchone()[0] == 0


# ──────────────────────────────────────────────────────────────────────
# v2.1.0 — registry trust: host normalization, opportunistic probe,
# staleness signal. Mirrors V1's tests/test_session_liveness.py.
# ──────────────────────────────────────────────────────────────────────


import os
import socket


def _dead_pid() -> int:
    import psutil
    for candidate in (987654, 876543, 765432, 654321):
        if not psutil.pid_exists(candidate):
            return candidate
    pytest.skip("couldn't find a dead pid on this system")


class TestHostNormalization:
    def test_register_lowercases_host_on_insert(self, conn):
        sid = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/x", pid=1, host="DAVE-PC",
        )
        with conn.cursor() as cur:
            cur.execute("SELECT host FROM active_sessions WHERE id = %s", (sid,))
            assert cur.fetchone()[0] == "dave-pc"

    def test_register_empty_host_stays_empty_string(self, conn):
        """Schema is NOT NULL DEFAULT ''. normalize_host('') -> None,
        falls back to '' to satisfy the column. Regression guard."""
        sid = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/x", pid=1, host="",
        )
        with conn.cursor() as cur:
            cur.execute("SELECT host FROM active_sessions WHERE id = %s", (sid,))
            assert cur.fetchone()[0] == ""

    def test_register_strips_whitespace(self, conn):
        sid = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/x", pid=1, host="  Mixed-Host  ",
        )
        with conn.cursor() as cur:
            cur.execute("SELECT host FROM active_sessions WHERE id = %s", (sid,))
            assert cur.fetchone()[0] == "mixed-host"


class TestBootOpportunisticProbe:
    def test_boot_marks_dead_same_host_sibling_ended(self, conn):
        """A dead same-host sibling gets flipped to ended inline by boot."""
        my_host = socket.gethostname()
        dead = _dead_pid()
        sibling = store.register_session(
            conn, source="windsurf", project="healcheck",
            cwd="F:/dead", pid=dead, host=my_host,
        )
        # Separate session boots.
        payload = boot.build(
            conn, project="healcheck", task="check probe", source="claude",
            cwd="F:/booting", pid=os.getpid(), host=my_host,
        )
        # Dead sibling should be marked ended AND removed from siblings.
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                        (sibling,))
            assert cur.fetchone()[0] == "ended"
        sibling_ids = [s["id"] for s in payload.other_active_sessions]
        assert sibling not in sibling_ids
        assert sibling in payload.self_healed_ended_ids

    def test_boot_leaves_alive_sibling_active(self, conn):
        my_host = socket.gethostname()
        sibling = store.register_session(
            conn, source="windsurf", project="healcheck",
            cwd="F:/alive", pid=os.getpid(), host=my_host,
        )
        payload = boot.build(
            conn, project="healcheck", task="check probe", source="claude",
            cwd="F:/booting2", pid=os.getpid() + 1, host=my_host,
        )
        # Alive sibling stays active and appears in siblings list.
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                        (sibling,))
            assert cur.fetchone()[0] == "active"
        sibling_ids = [s["id"] for s in payload.other_active_sessions]
        assert sibling in sibling_ids
        assert sibling not in payload.self_healed_ended_ids

    def test_boot_skips_other_host_siblings(self, conn):
        """A row on another host must NOT be probed even if its pid
        is dead locally — cross-host pid space is unknown."""
        my_host = socket.gethostname()
        other_host = "some-other-machine-xyz"
        dead = _dead_pid()
        sibling = store.register_session(
            conn, source="windsurf", project="healcheck",
            cwd="F:/remote", pid=dead, host=other_host,
        )
        boot.build(
            conn, project="healcheck", task="t", source="claude",
            cwd="F:/booting3", pid=os.getpid(), host=my_host,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM active_sessions WHERE id = %s",
                        (sibling,))
            assert cur.fetchone()[0] == "active"


class TestRegistryStaleness:
    def test_fresh_siblings_trustworthy(self, conn):
        my_host = socket.gethostname()
        store.register_session(
            conn, source="windsurf", project="stalecheck",
            cwd="F:/x", pid=os.getpid(), host=my_host,
        )
        payload = boot.build(
            conn, project="stalecheck", task="t", source="claude",
            cwd="F:/y", pid=os.getpid() + 1, host=my_host,
        )
        assert payload.registry_trustworthy is True

    def test_stale_siblings_not_trustworthy(self, conn):
        """Sibling's heartbeat_at artificially pushed >10min into the
        past. compute_staleness reports seconds past threshold and
        flips trustworthy to False."""
        import session_liveness as sl
        my_host = socket.gethostname()
        # Use a live pid so the probe doesn't flip it to ended —
        # we want it still in the sibling list so staleness is computed.
        sibling = store.register_session(
            conn, source="windsurf", project="stalecheck",
            cwd="F:/stale", pid=os.getpid(), host=my_host,
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE active_sessions "
                "SET heartbeat_at = NOW() - interval '2 hours' "
                "WHERE id = %s",
                (sibling,),
            )
        conn.commit()

        payload = boot.build(
            conn, project="stalecheck", task="t", source="claude",
            cwd="F:/booting4", pid=os.getpid() + 1, host=my_host,
        )
        assert payload.registry_trustworthy is False
        assert payload.registry_staleness_seconds is not None
        assert payload.registry_staleness_seconds >= sl.STALENESS_WARN_SECONDS

    def test_empty_siblings_trustworthy_and_none(self, conn):
        """No siblings → (None, True). Nothing to distrust."""
        payload = boot.build(
            conn, project="noone-else", task="t", source="claude",
            cwd="F:/x", pid=os.getpid(), host=socket.gethostname(),
        )
        assert payload.other_active_sessions == []
        assert payload.registry_trustworthy is True
        assert payload.registry_staleness_seconds is None


# ──────────────────────────────────────────────────────────────────────
# v2.1.1 — PID reuse false-positive protection.
# pid_create_time stamped at register; probe verifies identity.
# ──────────────────────────────────────────────────────────────────────


class TestPidReuse:
    def test_register_stamps_pid_create_time(self, conn):
        """Fresh registrations should capture create_time for the pid."""
        sid = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/x", pid=os.getpid(), host=socket.gethostname(),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pid_create_time FROM active_sessions WHERE id = %s",
                (sid,),
            )
            ct = cur.fetchone()[0]
        assert ct is not None
        assert isinstance(ct, float)

    def test_register_dead_pid_leaves_create_time_null(self, conn):
        """A pid that doesn't exist at register time cannot have a
        create_time captured. The row gets NULL and falls back to the
        legacy pid-only probe (which correctly reaps it since the pid
        is dead)."""
        sid = store.register_session(
            conn, source="claude", project="test",
            cwd="F:/dead", pid=_dead_pid(), host=socket.gethostname(),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pid_create_time FROM active_sessions WHERE id = %s",
                (sid,),
            )
            assert cur.fetchone()[0] is None

    def test_boot_reaps_pid_reuse_impostor(self, conn):
        """Register with our own pid, then overwrite pid_create_time
        with 0.0 to simulate pid reuse. Next boot's opportunistic probe
        should mark the row ended."""
        my_host = socket.gethostname()
        sibling = store.register_session(
            conn, source="windsurf", project="pidreuse",
            cwd="F:/impostor", pid=os.getpid(), host=my_host,
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE active_sessions SET pid_create_time = 0.0 WHERE id = %s",
                (sibling,),
            )
        conn.commit()

        payload = boot.build(
            conn, project="pidreuse", task="reap impostor", source="claude",
            cwd="F:/booting", pid=os.getpid() + 1, host=my_host,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM active_sessions WHERE id = %s",
                (sibling,),
            )
            assert cur.fetchone()[0] == "ended"
        assert sibling in payload.self_healed_ended_ids

    def test_boot_preserves_legacy_null_create_time(self, conn):
        """Legacy rows (pre-v2.1.1) have NULL pid_create_time. As long
        as their pid is alive, they must NOT be reaped — pid-only
        fallback preserves back-compat."""
        my_host = socket.gethostname()
        sibling = store.register_session(
            conn, source="windsurf", project="legacy",
            cwd="F:/legacy", pid=os.getpid(), host=my_host,
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE active_sessions SET pid_create_time = NULL "
                "WHERE id = %s",
                (sibling,),
            )
        conn.commit()

        payload = boot.build(
            conn, project="legacy", task="don't reap legacy", source="claude",
            cwd="F:/booting2", pid=os.getpid() + 1, host=my_host,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM active_sessions WHERE id = %s",
                (sibling,),
            )
            assert cur.fetchone()[0] == "active"
        assert sibling not in payload.self_healed_ended_ids
        sibling_ids = [s["id"] for s in payload.other_active_sessions]
        assert sibling in sibling_ids
