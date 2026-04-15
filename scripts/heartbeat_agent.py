#!/usr/bin/env python3
"""
Open Brain — Heartbeat Agent (v0.14.0+)

Probes `active_sessions.pid` for each row on this host and marks the row
'ended' when the owning process is gone.

This replaces the v0.13.0 TTL sweep (memory #4929 / #3719: timer-based
expiry is wrong — an agent doing long non-brain work silently aged out
and vanished from the registry). Liveness is now an EXTERNAL,
MULTI-SIGNAL check: process existence via psutil, not "did the agent
remember to ping us within N minutes."

Usage:
    python scripts/heartbeat_agent.py                       # default 60s interval, this host
    python scripts/heartbeat_agent.py --interval 30         # override interval
    python scripts/heartbeat_agent.py --host <name>         # filter to specific host
    python scripts/heartbeat_agent.py --once                # single pass then exit

Deploy:
    - Launch from dashboard.py alongside ollama / postgres.
    - Or register as a Windows scheduled task / systemd service.
    - One agent per host. Multi-host brains run one agent per machine.

Signals:
    SIGTERM / SIGINT → graceful shutdown between probe cycles.
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import time
from typing import Optional

import psutil
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")
DEFAULT_INTERVAL = int(os.getenv("OPEN_BRAIN_HEARTBEAT_INTERVAL", "60"))

_running = True


def _stop(_signum=None, _frame=None) -> None:
    global _running
    _running = False


def _fetch_local_active(host_filter: str) -> list[dict]:
    """Return active_sessions rows on this host that have a pid we can
    check. Rows with NULL pid are skipped — we can't prove they're dead."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, source, project, cwd, pid, host, current_task, "
                "       started_at, heartbeat_at "
                "FROM active_sessions "
                "WHERE status = 'active' "
                "  AND pid IS NOT NULL "
                "  AND (host = %s OR host IS NULL)",
                (host_filter,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _mark_ended(session_id: int, reason: str) -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE active_sessions SET status = 'ended' "
                "WHERE id = %s AND status = 'active'",
                (session_id,),
            )
        conn.commit()
    finally:
        conn.close()


def _bump_heartbeat(session_ids: list[int]) -> None:
    """Record confirmed-alive timestamp for rows that passed the probe.
    Cheap batch update."""
    if not session_ids:
        return
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE active_sessions SET heartbeat_at = now() "
                "WHERE id = ANY(%s) AND status = 'active'",
                (session_ids,),
            )
        conn.commit()
    finally:
        conn.close()


def probe_once(host_filter: str, verbose: bool = False) -> tuple[int, int]:
    """Run one probe cycle. Returns (alive_count, ended_count)."""
    try:
        rows = _fetch_local_active(host_filter)
    except Exception as e:
        print(f"[heartbeat-agent] DB read failed: {e}", file=sys.stderr)
        return (0, 0)

    alive_ids: list[int] = []
    ended = 0
    for r in rows:
        pid = r.get("pid")
        if pid is None:
            continue
        try:
            if psutil.pid_exists(int(pid)):
                alive_ids.append(r["id"])
                if verbose:
                    print(f"[heartbeat-agent] #{r['id']} pid={pid} source={r['source']} alive")
            else:
                _mark_ended(r["id"], reason="pid_gone")
                ended += 1
                if verbose:
                    print(f"[heartbeat-agent] #{r['id']} pid={pid} source={r['source']} → ENDED")
        except Exception as e:
            if verbose:
                print(f"[heartbeat-agent] probe #{r['id']} failed: {e}")

    _bump_heartbeat(alive_ids)
    return (len(alive_ids), ended)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open Brain heartbeat agent — external pid-based liveness prober."
    )
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                         help=f"Probe interval in seconds (default: {DEFAULT_INTERVAL}).")
    parser.add_argument("--host", default=socket.gethostname(),
                         help="Host filter — only probe rows where active_sessions.host matches (or is NULL). Defaults to this machine's hostname.")
    parser.add_argument("--once", action="store_true",
                         help="Run a single probe pass then exit (useful for testing).")
    parser.add_argument("-v", "--verbose", action="store_true",
                         help="Log every probe outcome.")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    print(f"[heartbeat-agent] starting — host={args.host}, interval={args.interval}s",
          file=sys.stderr)

    if args.once:
        alive, ended = probe_once(args.host, verbose=args.verbose)
        print(f"[heartbeat-agent] one-shot done: {alive} alive, {ended} ended")
        return 0

    while _running:
        try:
            alive, ended = probe_once(args.host, verbose=args.verbose)
            if args.verbose or ended:
                print(f"[heartbeat-agent] cycle: {alive} alive, {ended} ended",
                      file=sys.stderr)
        except Exception as e:
            print(f"[heartbeat-agent] cycle error: {e}", file=sys.stderr)
        # Sleep in short increments so SIGTERM is responsive.
        slept = 0
        while _running and slept < args.interval:
            time.sleep(1)
            slept += 1

    print("[heartbeat-agent] stopped.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
