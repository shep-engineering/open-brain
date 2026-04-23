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
import re
from datetime import datetime, timezone

import psutil


# v0.23.2 / v2.1.2: redact common secret patterns from a captured parent
# cmdline before storing in session metadata. Defensive belt-and-suspenders —
# MCP clients (Claude Code, Cursor, Windsurf) don't carry tokens in argv in
# practice, but we'd rather not find out the hard way. Keep the list
# short and conservative; overly aggressive redaction would mangle normal
# cmdlines.
_SECRET_PATTERNS = [
    # --token=VALUE, --api-key=VALUE, --key=VALUE, --password=VALUE, --secret=VALUE
    re.compile(r"(--(?:token|api[-_]?key|key|password|secret|auth)=)\S+",
               re.IGNORECASE),
    # Bearer <token>
    re.compile(r"(Bearer\s+)\S+", re.IGNORECASE),
    # OpenAI-style sk-... keys (sk- followed by 20+ alnum)
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    # GitHub personal access tokens
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
]
_SECRET_REDACTED = "<REDACTED>"


def _scrub_cmdline_secrets(s: str) -> str:
    """Redact common credential patterns from a command-line string.
    Returns the input unchanged if no matches. Never raises."""
    if not s:
        return s
    out = s
    for pat in _SECRET_PATTERNS:
        # For patterns with a prefix capture group, preserve the prefix + REDACTED.
        # For bare patterns, replace the whole match.
        if pat.groups:
            out = pat.sub(rf"\1{_SECRET_REDACTED}", out)
        else:
            out = pat.sub(_SECRET_REDACTED, out)
    return out


def capture_identity_metadata(client_info: dict | None = None) -> dict:
    """Return a JSON-serializable identity dict for a register_session call.

    Captures:
      - client.{name, version} from the MCP initialize handshake's clientInfo
        (caller extracts from context.request_context.session.client_params;
        passes dict or None).
      - parent.{pid, name, cmdline_head} — OS-level parent process of the
        current server.py subprocess (the MCP client on stdio transport).
        cmdline_head is the first 200 chars of the parent's argv, with
        common credential patterns redacted.
      - recorded_at — ISO UTC timestamp.

    Never raises — any field that can't be gathered is omitted gracefully.
    Intended for the `metadata` JSONB column on active_sessions.
    """
    md: dict = {}
    if client_info:
        name = client_info.get("name")
        version = client_info.get("version")
        if name or version:
            md["client"] = {"name": name, "version": version}
    try:
        me = psutil.Process(os.getpid())
        parent = me.parent()
        if parent is not None:
            try:
                cmdline_list = parent.cmdline()
                cmdline_raw = " ".join(cmdline_list)[:200]
                cmdline_head = _scrub_cmdline_secrets(cmdline_raw)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                cmdline_head = None
            try:
                parent_name = parent.name()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                parent_name = None
            md["parent"] = {
                "pid": parent.pid,
                "name": parent_name,
                "cmdline_head": cmdline_head,
            }
    except Exception:
        # psutil.NoSuchProcess / AccessDenied / anything else — never raise.
        pass
    md["recorded_at"] = datetime.now(timezone.utc).isoformat()
    return md

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

    NOTE: This is the pid-only check. It CANNOT distinguish the
    original process from a new process that the OS later assigned the
    same pid (classic pid-reuse false-positive). For sibling-session
    liveness, prefer `verify_pid_identity` which also checks
    create_time. This function stays available for callers that
    legitimately only need a bare existence check.
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


# Identity tolerance: psutil.Process.create_time() returns a deterministic
# float epoch for a given process, but floats can carry sub-ms jitter
# across calls. 1s is generous for identity match (create_time won't
# legitimately shift) and far smaller than any realistic pid-reuse
# window on modern OSes.
_CREATE_TIME_TOLERANCE_SECONDS = 1.0


def get_pid_create_time(pid) -> float | None:
    """Return `psutil.Process(pid).create_time()` (epoch seconds) or
    None if the pid doesn't exist / permission denied / bad input.

    Called at session registration to stamp the row with the process's
    creation time. The probe compares the stored value against the
    current process's create_time at the same pid — mismatch means the
    OS reassigned the pid (pid reuse), so the original session is dead.
    """
    if pid is None:
        return None
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    try:
        return psutil.Process(n).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    except Exception:
        return None


def verify_pid_identity(pid, stored_create_time) -> bool:
    """True iff the pid exists AND its current process's create_time
    matches `stored_create_time` (within tolerance).

    - If `stored_create_time` is None, falls back to `is_pid_alive(pid)`.
      This preserves legacy behavior for rows written pre-v0.23.1 (no
      create_time captured) so they're not spuriously reaped.
    - On `NoSuchProcess` / `AccessDenied` / bad input, returns False.
      AccessDenied on a probe means "can't verify" which is safer to
      treat as dead than to trust — same-user processes shouldn't
      produce this.
    """
    if stored_create_time is None:
        return is_pid_alive(pid)
    if pid is None:
        return False
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    try:
        current = psutil.Process(n).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    except Exception:
        return False
    try:
        return abs(float(current) - float(stored_create_time)) < _CREATE_TIME_TOLERANCE_SECONDS
    except (TypeError, ValueError):
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


def sweep_host_stale(conn, host, max_age_seconds: int,
                     dry_run: bool = True) -> dict:
    """Admin: mark cross-host sessions ended based on heartbeat staleness.

    Same-host rows are kept honest by `probe_and_mark_ended` (psutil-based,
    authoritative). Rows on other hosts can't be pid-probed from here — if
    the heartbeat_agent on that host stops (crash, sleep, uninstall), its
    rows never clean up and pollute every caller's OTHER ACTIVE SESSIONS.

    This function gives an operator an explicit, opt-in reaper: select
    active rows for `host` whose latest `heartbeat_at` is older than
    `max_age_seconds`, and (unless dry_run) mark them ended. Staleness-
    based, not identity-based — the caller commits to "that host's
    sessions are dead if they haven't bumped heartbeat in this long."

    Args:
        conn:            psycopg2 connection (V1 openbrain or V2 open_brain_v2).
        host:            Target hostname. Normalized case-insensitively.
        max_age_seconds: Rows with heartbeat_at > now() - this many seconds
                         are candidates. Must be positive; typical 3600+.
        dry_run:         If True (default), return candidates without writing.
                         Flip to False to actually mark ended.

    Returns:
        {
          "success": True,
          "host": "<normalized>",
          "max_age_seconds": N,
          "dry_run": bool,
          "candidates":   [ {id, pid, heartbeat_age_seconds, source, project}, ... ],
          "marked_ended": [ id, ... ],   # [] when dry_run
        }

    Safety:
      - NEVER sweeps if host is empty/None.
      - Local-host sweeps proceed with a WARNING attached to the result
        (v0.24.1+). Same-host rows are normally reaped by the
        heartbeat_agent (psutil-based, authoritative); this tool uses
        staleness only and could reap your own live session if the
        operator mis-sets max_age_minutes. `dry_run=True` default
        remains the load-bearing guardrail.
      - Cap at 200 rows per call to bound blast radius; repeat the call
        if you need more swept.
    """
    target = normalize_host(host)
    if target is None:
        return {
            "success": False,
            "error": "host is required and must be non-empty",
        }
    if not isinstance(max_age_seconds, int) or max_age_seconds <= 0:
        return {
            "success": False,
            "error": "max_age_seconds must be a positive int",
        }
    try:
        import socket as _socket
        local = normalize_host(_socket.gethostname())
    except Exception:
        local = None
    warning: str | None = None
    if local is not None and target == local:
        warning = (
            f"sweep_host_stale was called against the LOCAL host "
            f"({local!r}). Same-host rows are normally reaped by the "
            f"heartbeat_agent (authoritative psutil check). This tool "
            f"uses staleness only and could end your own live session "
            f"if max_age_minutes is too aggressive. Verify the "
            f"candidates list before flipping dry_run=False."
        )

    cap = 200
    candidates: list[dict] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, pid, source, project, "
                "       EXTRACT(EPOCH FROM (NOW() - heartbeat_at))::int "
                "         AS heartbeat_age_seconds "
                "  FROM active_sessions "
                " WHERE status = 'active' "
                "   AND lower(host) = %s "
                "   AND heartbeat_at < (NOW() - make_interval(secs => %s)) "
                " ORDER BY heartbeat_at ASC "
                " LIMIT %s",
                (target, max_age_seconds, cap),
            )
            rows = cur.fetchall() or []
            colnames = [d[0] for d in cur.description]
            for r in rows:
                if isinstance(r, dict):
                    candidates.append({
                        "id":  r.get("id"),
                        "pid": r.get("pid"),
                        "source": r.get("source"),
                        "project": r.get("project"),
                        "heartbeat_age_seconds": r.get("heartbeat_age_seconds"),
                    })
                else:
                    row = dict(zip(colnames, r))
                    candidates.append({
                        "id":  row.get("id"),
                        "pid": row.get("pid"),
                        "source": row.get("source"),
                        "project": row.get("project"),
                        "heartbeat_age_seconds": row.get("heartbeat_age_seconds"),
                    })
    except Exception as exc:
        return {
            "success": False,
            "error":   f"query failed: {exc}",
            "host":    target,
        }

    marked_ended: list[int] = []
    if not dry_run and candidates:
        ids = [c["id"] for c in candidates if c.get("id") is not None]
        if ids:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE active_sessions "
                        "   SET status = 'ended', ended_at = NOW() "
                        " WHERE id = ANY(%s) "
                        "   AND status = 'active'",
                        (ids,),
                    )
                try:
                    conn.commit()
                except Exception:
                    pass
                marked_ended = ids
            except Exception as exc:
                return {
                    "success": False,
                    "error":   f"update failed: {exc}",
                    "host":    target,
                    "candidates": candidates,
                }

    result = {
        "success":         True,
        "host":            target,
        "max_age_seconds": max_age_seconds,
        "dry_run":         dry_run,
        "candidates":      candidates,
        "marked_ended":    marked_ended,
    }
    if warning:
        result["warning"] = warning
    return result


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
        # v2.1.1: identity check includes stored create_time when
        # present. Legacy rows (pid_create_time=None) fall back to
        # pid-only is_pid_alive via verify_pid_identity's None branch.
        if verify_pid_identity(pid, r.get("pid_create_time")):
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
