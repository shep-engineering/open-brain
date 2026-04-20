"""Session-liveness helpers shared by server.py and scripts/heartbeat_agent.py.

The Open Brain session registry (`active_sessions`) is kept honest by an
external heartbeat agent that pid-probes each row. When the agent stops
running, rows with `status='active'` silently go stale — the registry
lies about liveness. This module centralizes the three cheap checks that
let both the agent and the MCP server detect and recover from that:

    is_pid_alive(pid)                -> bool
    normalize_host(h)                -> str | None
    compute_staleness(rows)          -> (seconds:int|None, trustworthy:bool)
    probe_and_mark_ended(conn, rows, my_host, cap=20) -> list[int]

Design notes:
  - `compute_staleness` looks at the freshest `heartbeat_at` across the
    rows, not per-row. A healthy agent bumps *all* rows on its cycle, so
    the max is a good "agent recently ran" signal. If every row is old,
    the agent is probably down.
  - `probe_and_mark_ended` is the belt-and-suspenders path: `boot_session`
    can call it to self-heal obvious dead rows on its own host, so the
    registry is correct at the moment the caller reads it — without
    waiting for the agent's next cycle.
  - Host comparison is case-insensitive after `normalize_host`. Raw
    `socket.gethostname()` varies across OSes (Windows NetBIOS vs DNS),
    so we lowercase consistently on insert and probe.

This module intentionally reuses any passed-in psycopg2 connection rather
than opening its own (per the #5117 guardrail: new DB code in open-brain
must reuse connections).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import psutil

STALENESS_WARN_SECONDS: int = int(
    os.getenv("OPEN_BRAIN_REGISTRY_STALENESS_WARN", "600")
)

NULL_PID_TTL_MINUTES: int = int(
    os.getenv("OPEN_BRAIN_SESSION_TTL_MINUTES", "1440")
)

_OPPORTUNISTIC_PROBE_CAP = 20


def is_pid_alive(pid) -> bool:
    """True if a process with this pid currently exists on this host.

    Safely handles None / non-int / negative input by returning False.
    """
    if pid is None:
        return False
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    try:
        return psutil.pid_exists(n)
    except Exception:
        return False


def normalize_host(h) -> str | None:
    """Strip + lowercase a hostname. Empty/None/whitespace → None."""
    if h is None:
        return None
    try:
        s = str(h).strip().lower()
    except Exception:
        return None
    return s or None


def _as_utc(dt) -> datetime | None:
    """Coerce a datetime to UTC tz-aware. Returns None for bad input.

    Naive datetimes are assumed to already be UTC (they come from
    TIMESTAMP WITH TIME ZONE columns; naive is the exception, not the
    rule). Strings (ISO) are parsed if possible.
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_staleness(rows) -> tuple[int | None, bool]:
    """Return (staleness_seconds, trustworthy) for a list of session rows.

    Staleness = now - max(heartbeat_at across rows), in integer seconds.
    Trustworthy iff staleness < STALENESS_WARN_SECONDS (default 600s).

    Empty rows → (None, True). Nothing to distrust; caller should treat
    as "registry is empty, nothing stale."
    """
    if not rows:
        return (None, True)
    freshest: datetime | None = None
    for r in rows:
        hb = _as_utc(r.get("heartbeat_at") if isinstance(r, dict) else None)
        if hb is None:
            continue
        if freshest is None or hb > freshest:
            freshest = hb
    if freshest is None:
        return (None, False)
    now = datetime.now(timezone.utc)
    seconds = int(max(0, (now - freshest).total_seconds()))
    trustworthy = seconds < STALENESS_WARN_SECONDS
    return (seconds, trustworthy)


def probe_and_mark_ended(conn, rows, my_host, cap: int = _OPPORTUNISTIC_PROBE_CAP) -> list[int]:
    """Opportunistically mark dead same-host session rows as ended.

    For each row whose host matches `my_host` (case-insensitive) and has
    a pid, probe the pid. If it's gone, UPDATE status='ended' on the DB
    and drop the id from the caller's view.

    Returns the list of ids that were just marked ended.

    Caps at `cap` rows to bound boot latency. Silently swallows per-row
    failures — this is a best-effort self-heal, not a guarantee.
    """
    if not rows:
        return []
    target = normalize_host(my_host)
    if target is None:
        return []

    ended_ids: list[int] = []
    checked = 0
    for r in rows:
        if checked >= cap:
            break
        if not isinstance(r, dict):
            continue
        row_host = normalize_host(r.get("host"))
        if row_host is None or row_host != target:
            continue
        pid = r.get("pid")
        if pid is None:
            continue
        checked += 1
        if is_pid_alive(pid):
            continue
        row_id = r.get("id")
        if row_id is None:
            continue
        try:
            with conn.cursor() as cur:
                # Writes `ended_at = NOW()` in addition to flipping status.
                # V2 has had this column since brain_v2/schema.py:181.
                # V1 got it in migration v7 (scripts/migrate_v7_active_sessions_ended_at.py).
                # Uniform SQL across both brains — a shared probe can't
                # work without schema parity on this column.
                cur.execute(
                    "UPDATE active_sessions "
                    "SET status = 'ended', ended_at = NOW() "
                    "WHERE id = %s AND status = 'active'",
                    (row_id,),
                )
            try:
                conn.commit()
            except Exception:
                pass
            ended_ids.append(int(row_id))
        except Exception:
            continue
    return ended_ids
