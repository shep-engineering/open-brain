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

# Make repo root importable so we can share session_liveness with server.py.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

load_dotenv(os.path.join(_REPO_ROOT, ".env"))

import session_liveness  # noqa: E402  (sys.path must be set up first)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")
DEFAULT_INTERVAL = int(os.getenv("OPEN_BRAIN_HEARTBEAT_INTERVAL", "60"))

# v0.23.0: this agent probes BOTH V1 (openbrain, :5432) and V2
# (open_brain_v2, :5433) in a single process. Both schemas agree on
# every column probe_once reads (`id, source, pid, host, status,
# started_at, heartbeat_at`) and on the columns it writes
# (`status, ended_at` — V1 gained `ended_at` in migration v7). Per-URL
# errors are isolated, so if V2 is down the V1 probe keeps working.
#
# Coupling V1's heartbeat agent to V2's schema is provisional. If V2
# later adopts a different liveness model (e.g. in-process lifecycle
# integration with the brain_v2 MCP server exit handler), cleanly
# split back into two agents by copying this file, swapping the
# DATABASE_URL, and removing the other URL from OPEN_BRAIN_PROBE_URLS.


def _default_v2_url_from_v1(v1_url: str) -> str:
    """Derive the V2 DB URL from V1's by swapping the default port +
    DB name. Used only when no explicit DATABASE_URL_V2 or
    OPEN_BRAIN_PROBE_URLS is set. Non-destructive: if the input
    doesn't contain the default V1 markers, return it unchanged (the
    caller will dedupe)."""
    candidate = v1_url
    candidate = candidate.replace(":5432/", ":5433/")
    candidate = candidate.replace("/openbrain", "/open_brain_v2")
    return candidate


def _resolve_probe_urls() -> list[str]:
    """Return the list of DB URLs this agent should probe each cycle.

    Precedence:
      1. OPEN_BRAIN_PROBE_URLS env — comma-separated list, authoritative.
      2. DATABASE_URL + DATABASE_URL_V2 if both set.
      3. DATABASE_URL + derived V2 URL (swap :5432 -> :5433, openbrain -> open_brain_v2).

    Deduplicates while preserving order.
    """
    raw = os.getenv("OPEN_BRAIN_PROBE_URLS", "")
    if raw.strip():
        urls = [u.strip() for u in raw.split(",") if u.strip()]
    else:
        urls = [DATABASE_URL]
        v2 = os.getenv("DATABASE_URL_V2", "").strip()
        if not v2:
            v2 = _default_v2_url_from_v1(DATABASE_URL)
        if v2 and v2 != DATABASE_URL:
            urls.append(v2)
    # Dedupe, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


_running = True


def _stop(_signum=None, _frame=None) -> None:
    global _running
    _running = False


def _fetch_local_active(host_filter: str, url: str = None) -> list[dict]:
    """Return active_sessions rows on this host that have a pid we can
    check. Rows with NULL pid are skipped — we can't prove they're dead.

    Host matching is case-insensitive via `lower(host)`. Legacy rows
    written before normalize_host was wired in (mixed-case like
    'DAVE-PC') still match correctly.

    `url` defaults to `DATABASE_URL` for test back-compat. Pass an
    explicit url in multi-brain deployments.
    """
    normalized = session_liveness.normalize_host(host_filter) or ""
    conn = psycopg2.connect(url or DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, source, project, cwd, pid, host, current_task, "
                "       started_at, heartbeat_at, pid_create_time "
                "FROM active_sessions "
                "WHERE status = 'active' "
                "  AND pid IS NOT NULL "
                "  AND (lower(host) = %s OR host IS NULL)",
                (normalized,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _mark_ended(session_id: int, reason: str, url: str = None) -> None:
    conn = psycopg2.connect(url or DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE active_sessions "
                "SET status = 'ended', ended_at = NOW() "
                "WHERE id = %s AND status = 'active'",
                (session_id,),
            )
        conn.commit()
    finally:
        conn.close()


def _bump_heartbeat(session_ids: list[int], url: str = None) -> None:
    """Record confirmed-alive timestamp for rows that passed the probe.
    Cheap batch update."""
    if not session_ids:
        return
    conn = psycopg2.connect(url or DATABASE_URL)
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


def _sweep_null_pid_stale(ttl_minutes: int, url: str = None) -> int:
    """Age-based sweeper for rows we CANNOT probe (pid IS NULL).

    The pid-probe model (memory #4929 / #3719) deliberately refuses to
    expire probeable rows on a timer — a long-idle agent would silently
    vanish. But null-pid rows have no liveness signal at all; they can
    only be reaped by wall clock. This is the ONLY TTL path, and it is
    gated on `pid IS NULL` precisely so probeable rows keep the strict
    probe-based semantics.

    Returns the number of rows swept.
    """
    if ttl_minutes <= 0:
        return 0
    conn = psycopg2.connect(url or DATABASE_URL)
    try:
        with conn.cursor() as cur:
            # v0.23.0: V1 gained the `ended_at` column via migration v7,
            # so we now set it uniformly with V2. Matches
            # session_liveness.probe_and_mark_ended.
            cur.execute(
                "UPDATE active_sessions "
                "SET status = 'ended', ended_at = NOW() "
                "WHERE status = 'active' "
                "  AND pid IS NULL "
                "  AND started_at < NOW() - make_interval(mins => %s)",
                (ttl_minutes,),
            )
            swept = cur.rowcount or 0
        conn.commit()
        return swept
    finally:
        conn.close()


def probe_once(host_filter: str, verbose: bool = False,
               url: str = None) -> tuple[int, int]:
    """Run one probe cycle against a single DB URL.
    Returns (alive_count, ended_count).

    `url` defaults to `DATABASE_URL` for back-compat with single-brain
    callers and existing tests. For multi-brain deployments the main
    loop iterates `_resolve_probe_urls()` and passes each in turn.
    """
    try:
        rows = _fetch_local_active(host_filter, url=url)
    except Exception as e:
        print(f"[heartbeat-agent] DB read failed ({url or DATABASE_URL}): {e}",
              file=sys.stderr)
        return (0, 0)

    alive_ids: list[int] = []
    ended = 0
    for r in rows:
        pid = r.get("pid")
        if pid is None:
            continue
        try:
            # v0.23.1: identity check (pid + create_time) instead of
            # pid-only. Legacy rows with NULL pid_create_time fall back
            # to pid-only via verify_pid_identity's None branch.
            if session_liveness.verify_pid_identity(pid, r.get("pid_create_time")):
                alive_ids.append(r["id"])
                if verbose:
                    print(f"[heartbeat-agent] #{r['id']} pid={pid} source={r['source']} alive")
            else:
                _mark_ended(r["id"], reason="pid_gone_or_reused", url=url)
                ended += 1
                if verbose:
                    print(f"[heartbeat-agent] #{r['id']} pid={pid} source={r['source']} -> ENDED")
        except Exception as e:
            if verbose:
                print(f"[heartbeat-agent] probe #{r['id']} failed: {e}")

    _bump_heartbeat(alive_ids, url=url)

    # Separate janitorial path for rows we can't probe by pid. Gated on
    # pid IS NULL so probeable rows keep the strict probe-based model.
    try:
        swept = _sweep_null_pid_stale(
            session_liveness.NULL_PID_TTL_MINUTES, url=url
        )
        if swept and verbose:
            print(f"[heartbeat-agent] null-pid TTL sweep: {swept} row(s) marked ended "
                  f"(TTL={session_liveness.NULL_PID_TTL_MINUTES}m)")
    except Exception as e:
        if verbose:
            print(f"[heartbeat-agent] null-pid sweep failed: {e}")

    return (len(alive_ids), ended)


def probe_all_urls(host_filter: str, urls: list[str],
                    verbose: bool = False) -> tuple[int, int]:
    """Probe each URL in `urls` once with per-URL isolation. Returns
    (total_alive, total_ended) across all URLs. A failure in one URL
    (connect timeout, missing table, etc.) does NOT stop probing of
    the others — each is wrapped individually."""
    total_alive = 0
    total_ended = 0
    for url in urls:
        try:
            alive, ended = probe_once(host_filter, verbose=verbose, url=url)
            total_alive += alive
            total_ended += ended
            if verbose:
                _safe_url = url.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
                print(f"[heartbeat-agent] {_safe_url}: {alive} alive, {ended} ended")
        except Exception as e:
            print(f"[heartbeat-agent] url {url!r} probe failed: {e}", file=sys.stderr)
    return (total_alive, total_ended)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open Brain heartbeat agent — external pid-based liveness prober."
    )
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                         help=f"Probe interval in seconds (default: {DEFAULT_INTERVAL}).")
    parser.add_argument("--host",
                         default=session_liveness.normalize_host(socket.gethostname()),
                         help="Host filter — only probe rows where active_sessions.host matches (or is NULL). Defaults to this machine's hostname (lowercased).")
    parser.add_argument("--once", action="store_true",
                         help="Run a single probe pass then exit (useful for testing).")
    parser.add_argument("-v", "--verbose", action="store_true",
                         help="Log every probe outcome.")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    urls = _resolve_probe_urls()
    _safe_urls = [
        u.replace("://", "://<credentials>@", 1).split("@", 1)[-1] for u in urls
    ]
    print(f"[heartbeat-agent] starting -- host={args.host}, interval={args.interval}s, "
          f"urls={_safe_urls}",
          file=sys.stderr)

    if args.once:
        alive, ended = probe_all_urls(args.host, urls, verbose=args.verbose)
        print(f"[heartbeat-agent] one-shot done: {alive} alive, {ended} ended "
              f"across {len(urls)} URL(s)")
        return 0

    while _running:
        try:
            alive, ended = probe_all_urls(args.host, urls, verbose=args.verbose)
            if args.verbose or ended:
                print(f"[heartbeat-agent] cycle: {alive} alive, {ended} ended "
                      f"across {len(urls)} URL(s)",
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
