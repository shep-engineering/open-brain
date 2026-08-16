"""Open Brain v2 MCP server.

FastMCP stdio server. Server name: open-brain-v2.
Separate DB (open_brain_v2 on port 5433), separate tool namespace.

Phase 1 tools:
    boot_session_v2       — headline-only payload, 5 BLOCKER cap, 2K token cap
    recall_v2             — fetch body by (kind, memory_id)
    search_v2             — headline-only search
    remember_rule_v2      — atomic rule write, routes to supersede on overlap
    remember_fact_v2      — atomic fact write
    remember_incident_v2  — incident write
    remember_task_v2      — task write
    supersede_rule_v2     — revise a rule (ONLY legal modification path)
    update_task_status_v2 — lifecycle transition on a task
"""
from __future__ import annotations

import json
import os
import sys
import uuid

# Ensure our package is importable when launched from an arbitrary cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP  # type: ignore
from mcp.server.fastmcp.server import Context  # type: ignore

from brain_v2 import boot, store, temporal_cache
from brain_v2.config import SERVER_NAME
from brain_v2.schema import apply_schema
from brain_v2.store import DuplicateHit
from brain_v2.write_gate import WriteGateError


mcp = FastMCP(SERVER_NAME)

# Auto-instrument every @mcp.tool() with timing + logging.
# This wraps every tool function so we get entry/exit logs with
# duration on every call — per the telemetry guardrail.
_original_mcp_tool = mcp.tool

def _instrumented_mcp_tool(*args, **kwargs):
    """Wraps mcp.tool() so the decorated function gets _instrument applied."""
    orig_decorator = _original_mcp_tool(*args, **kwargs)
    def wrapper(fn):
        return orig_decorator(_instrument(fn))
    return wrapper

mcp.tool = _instrumented_mcp_tool  # type: ignore[assignment]


import logging
import time as _time
from functools import wraps as _wraps
from pathlib import Path as _Path

from brain_v2.observability import obs
from brain_v2.config import SLOW_CALL_THRESHOLD_MS

# ── Structured logging: file + stderr ────────────────────────────────
_LOG_DIR = _Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "brain_v2.log"

_formatter = logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# stderr handler (for MCP client visibility)
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.INFO)
_stderr_handler.setFormatter(_formatter)

# file handler (persistent, for monitoring + debugging)
_file_handler = logging.FileHandler(str(_LOG_FILE), encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_formatter)

# Attach handlers to the brain_v2 namespace logger (NOT root — avoids
# conflict with v1's conftest.py which calls basicConfig first).
_ns_logger = logging.getLogger("brain_v2")
_ns_logger.setLevel(logging.DEBUG)
_ns_logger.addHandler(_stderr_handler)
_ns_logger.addHandler(_file_handler)
_ns_logger.propagate = False  # don't double-log via root

_log = logging.getLogger("brain_v2.server")
_log.info("server.py loading — pid=%d log_file=%s", os.getpid(), _LOG_FILE)

# Initialize observability layer (JSONL logging, metrics, toast alerts)
import brain_v2 as _brain_v2_pkg
obs.startup(version=_brain_v2_pkg.__version__)

# psycopg2 auto-instrumentation for DB query timing (silent fail if unavailable)
def _init_db_instrumentation():
    try:
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
        Psycopg2Instrumentor().instrument()
        _log.info("psycopg2 auto-instrumentation active")
    except ImportError:
        _log.debug("opentelemetry-instrumentation-psycopg2 not installed, skipping")
    except Exception as exc:
        _log.warning("psycopg2 instrumentation failed: %s", exc)

_init_db_instrumentation()

import atexit as _atexit

_SERVER_START_TIME = _time.time()
_SESSION_ID = f"pid{os.getpid()}-{uuid.uuid4().hex[:8]}"
_DB_SESSION_ID: int | None = None  # set by boot_session_v2
_TOOL_CALL_COUNT = 0

# Pre-session tool event buffer: events before boot_session_v2 flush to DB on boot
_BUFFER_FILE = _Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "logs" / "tool_events_buffer.jsonl"
_BUFFER_MAX_LINES     = 1000   # drop silently when full
_BUFFER_MAX_AGE_HOURS = 24     # discard stale rows on flush


def _instrument(fn):
    """Decorator: logs entry/exit + duration, records metrics + persistent telemetry."""
    @_wraps(fn)
    def wrapper(*args, **kwargs):
        global _TOOL_CALL_COUNT
        _TOOL_CALL_COUNT += 1
        call_id = _TOOL_CALL_COUNT
        name = fn.__name__
        _log.info("TOOL_ENTRY #%d %s args=%s", call_id, name,
                  {k: (str(v)[:80] if isinstance(v, str) else v)
                   for k, v in kwargs.items()} if kwargs else "(positional)")
        t0 = _time.perf_counter()
        try:
            result = fn(*args, **kwargs)
            elapsed_ms = (_time.perf_counter() - t0) * 1000
            _log.info("TOOL_EXIT  #%d %s %.1fms ok", call_id, name, elapsed_ms)
            obs.record_call(name, elapsed_ms, success=True)
            obs.record_slow_call(name, elapsed_ms, threshold_ms=SLOW_CALL_THRESHOLD_MS)
            _persist_tool_event(name, elapsed_ms, True, None, kwargs)
            return result
        except Exception as exc:
            elapsed_ms = (_time.perf_counter() - t0) * 1000
            _log.error("TOOL_EXIT  #%d %s %.1fms ERROR: %s", call_id, name, elapsed_ms, exc)
            obs.record_call(name, elapsed_ms, success=False,
                            error=f"{type(exc).__name__}: {exc}")
            _persist_tool_event(name, elapsed_ms, False, str(exc), kwargs)
            raise
    return wrapper


def _persist_tool_event(name: str, elapsed_ms: float, success: bool,
                        error_msg: str | None, kwargs: dict | None) -> None:
    """Write to DB if session active, else buffer to JSONL on disk. Never raises."""
    import json as _json
    from datetime import datetime, timezone

    event_id = str(uuid.uuid4())
    proj = (kwargs or {}).get("project", "")
    proj = proj if isinstance(proj, str) else ""
    src = (kwargs or {}).get("source", "")
    src = src if isinstance(src, str) else ""
    ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    if _DB_SESSION_ID is not None:
        try:
            store.log_tool_event(
                store.connect(), event_id=event_id, tool_name=name,
                session_id=_DB_SESSION_ID, project=proj, source=src,
                duration_ms=int(elapsed_ms), success=success,
                error_msg=error_msg[:500] if error_msg else None)
        except Exception:
            pass
    else:
        try:
            _BUFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(_BUFFER_FILE, encoding="utf-8") as bf:
                    n = sum(1 for _ in bf)
            except FileNotFoundError:
                n = 0
            if n >= _BUFFER_MAX_LINES:
                return
            with open(_BUFFER_FILE, "a", encoding="utf-8") as f:
                f.write(_json.dumps({
                    "event_id": event_id, "tool_name": name,
                    "project": proj, "source": src,
                    "duration_ms": int(elapsed_ms), "success": success,
                    "error_msg": error_msg[:500] if error_msg else None,
                    "occurred_at": ts,
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass


def _flush_tool_event_buffer(session_id: int) -> None:
    """Flush buffered pre-session tool events into the DB. Safe to call multiple times."""
    import json as _json
    from datetime import datetime, timezone, timedelta

    if not _BUFFER_FILE.exists():
        return
    try:
        lines = _BUFFER_FILE.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        _log.warning("_flush_tool_event_buffer: read failed: %s", exc)
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=_BUFFER_MAX_AGE_HOURS)
    inserted = skipped = 0
    try:
        conn = store.connect()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = _json.loads(line)
            except Exception:
                skipped += 1
                continue
            try:
                row_ts = datetime.fromisoformat(row.get("occurred_at", ""))
                if row_ts.tzinfo is None:
                    row_ts = row_ts.replace(tzinfo=timezone.utc)
                if row_ts < cutoff:
                    skipped += 1
                    continue
            except Exception:
                pass
            store.log_tool_event(
                conn,
                event_id=row.get("event_id", str(uuid.uuid4())),
                tool_name=row.get("tool_name", "unknown"),
                session_id=session_id,
                project=row.get("project", ""),
                source=row.get("source", ""),
                duration_ms=row.get("duration_ms"),
                success=row.get("success", True),
                error_msg=row.get("error_msg"),
                occurred_at=row.get("occurred_at"),
            )
            inserted += 1
        _BUFFER_FILE.unlink(missing_ok=True)
        _log.info("_flush_tool_event_buffer: flushed=%d skipped=%d", inserted, skipped)
    except Exception as exc:
        _log.warning("_flush_tool_event_buffer: flush failed: %s — buffer retained", exc)


def _check_write_gate(project: str) -> str | None:
    """Returns an error JSON string if writes are blocked by pending
    action items, else None."""
    try:
        conn = store.connect()
        pending = store.count_pending_action_items(conn, project=project)
        if pending > 0:
            return _err(
                f"BLOCKED: {pending} pending action item(s) must be acknowledged "
                f"before writes are allowed. Call acknowledge_action_item_v2 for each.",
                blocked_by="action_items_pending",
                pending_count=pending,
            )
    except Exception as exc:
        _log.warning("_check_write_gate: DB unreachable, failing open: %s", exc)
    return None


def _ok(data) -> str:
    return json.dumps(data if isinstance(data, (dict, list, str, int, float, bool)) or data is None else data.to_dict(),
                      default=str)


def _err(msg: str, **extra) -> str:
    return json.dumps({"error": msg, **extra})


@mcp.tool()
def health_v2() -> str:
    """Health check for brain_v2. Returns DB connectivity, table row
    counts, Ollama reachability, server uptime, current session info,
    and tool count. First thing to call when diagnosing issues."""
    checks: dict = {
        "server": {
            "pid": os.getpid(),
            "session_id": _SESSION_ID,
            "db_session_id": _DB_SESSION_ID,
            "uptime_seconds": round(_time.time() - _SERVER_START_TIME, 1),
            "tool_calls_total": _TOOL_CALL_COUNT,
            "log_file": str(_LOG_FILE),
        },
        "db": {"status": "unknown"},
        "ollama": {"status": "unknown"},
        "tables": {},
    }
    # DB connectivity + table counts
    try:
        with store.connect() as conn:
            with conn.cursor() as cur:
                for tbl in ("rules", "facts", "incidents", "tasks",
                            "memory_index", "action_items", "active_sessions",
                            "handoffs", "maintenance_runs", "v2_audit",
                            "tool_events"):
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                        checks["tables"][tbl] = cur.fetchone()[0]
                    except Exception:
                        checks["tables"][tbl] = "error"
        checks["db"]["status"] = "connected"
        _c = store.connect()
        checks["db"]["url_masked"] = _c.dsn.split("@")[-1] if hasattr(_c, "dsn") else "?"
    except Exception as exc:
        checks["db"]["status"] = f"unreachable: {exc}"

    # Ollama reachability — use socket-level connect test, not urlopen.
    # On Windows, urlopen(timeout=N) only bounds the read phase; the TCP
    # connect phase uses the OS default (~60-90s), making health_v2 block
    # for over a minute when Ollama is down.
    try:
        import socket as _socket
        import urllib.parse
        from brain_v2.config import OLLAMA_BASE_URL
        parsed = urllib.parse.urlparse(OLLAMA_BASE_URL)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11434
        with _socket.create_connection((host, port), timeout=5):
            checks["ollama"]["status"] = "reachable"
    except OSError as exc:
        checks["ollama"]["status"] = f"unreachable: {exc}"

    # Tool count
    tool_names = sorted([t.name for t in mcp._tool_manager.list_tools()])
    checks["tools"] = {"count": len(tool_names), "names": tool_names}

    return _ok(checks)


@mcp.tool()
def boot_session_v2(project: str = "", task: str = "", source: str = "",
                    handoff: str = "", cwd: str = "", pid: int = 0,
                    host: str = "", context: Context = None) -> str:
    """Boot v2 — returns headline-only payload.

    5 BLOCKER max. 5 PATTERN max (task-relevance ranked). 2,000 token cap.
    Bodies are NOT included. Use recall_v2(kind, memory_id) to fetch a body.
    WORKING CONTEXT is regenerated from `task` each call — not stored.

    Registers a row in active_sessions and returns other_active_sessions
    so the caller can surface sibling work before overlapping. If handoff
    is not supplied, the most recent handoff for the project (from a
    different session) is auto-loaded.

    Args:
        project: Project name.
        task:    Short description of what this session is doing.
        source:  Agent name (claude/windsurf/cursor/etc).
        handoff: Optional explicit handoff text. If empty, latest for
                 the project is used.
        cwd:     Absolute path of caller's working dir.
        pid:     Caller's pid. Defaults to server.py's pid.
        host:    Caller's hostname.
        context: FastMCP-injected handle to the MCP session. Used to
                 read `clientInfo` from the initialize handshake so the
                 registered row knows which app opened it (v2.1.2+).
    """
    global _DB_SESSION_ID
    ensure_schema()
    actual_pid = pid if pid else os.getpid()
    # v2.1.0: default host to this machine's hostname if caller omits,
    # mirroring V1's server.py:1835. Ensures the opportunistic probe in
    # boot.build has something non-empty to match against. Row #4 in
    # the production registry (empty host) is exactly the failure mode
    # this prevents.
    import socket as _socket
    actual_host = host if host else _socket.gethostname()
    # v2.1.2: capture clientInfo + parent process identity for the
    # metadata column, and explicitly end the source='auto' row from
    # server startup (it doesn't get superseded automatically — the
    # supersede rule matches on source+cwd+pid, and 'auto' != this
    # session's source). Without this end_session call, two rows
    # would coexist for the same pid until the next run.
    import session_liveness
    _client_info: dict | None = None
    if context is not None:
        try:
            cp = context.request_context.session.client_params
            if cp is not None and getattr(cp, "clientInfo", None) is not None:
                _client_info = {
                    "name": cp.clientInfo.name,
                    "version": cp.clientInfo.version,
                }
        except Exception:
            _client_info = None
    identity_md = session_liveness.capture_identity_metadata(_client_info)
    try:
        with store.connect() as conn:
            # End the auto-registered row (set at server startup by
            # _auto_register_session) before the real boot row lands.
            # See plan §5.6.
            if _DB_SESSION_ID is not None:
                try:
                    store.end_session(conn, session_id=_DB_SESSION_ID,
                                       source="auto")
                except Exception:
                    _log.warning("failed to end auto session row %s",
                                  _DB_SESSION_ID, exc_info=True)
            payload = boot.build(
                conn, project=project, task=task, source=source,
                handoff=handoff, cwd=cwd, pid=actual_pid, host=actual_host,
                metadata=identity_md,
            )
    except Exception as exc:
        _log.exception("boot_session_v2 failed")
        return _err(f"boot failed: {exc}")
    _DB_SESSION_ID = payload.session_id
    _flush_tool_event_buffer(_DB_SESSION_ID)
    _log.info("boot_session_v2: source=%s project=%s session_id=%s siblings=%d",
              source, project, payload.session_id, len(payload.other_active_sessions))
    return _ok({"worker_session_id": _SESSION_ID, **payload.to_dict()})


@mcp.tool()
def recall_v2(kind: str, memory_id: int) -> str:
    """Fetch the full body of a single memory by (kind, memory_id).

    If the memory has been forgotten, the body is still returned (audit
    semantics — "show me what used to be here") but a forgotten banner
    is included so the caller knows it is no longer current truth.
    """
    try:
        with store.connect() as conn:
            mem = store.recall(conn, kind=kind, memory_id=memory_id)
            # Surface forgotten banner if applicable
            forgotten_meta = None
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT forgotten_at, forgotten_reason, forgotten_by "
                    "FROM memory_index WHERE kind = %s AND memory_id = %s",
                    (kind, memory_id),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    forgotten_meta = {
                        "forgotten_at": str(row[0]),
                        "forgotten_reason": row[1],
                        "forgotten_by": row[2],
                        "banner": "This memory has been FORGOTTEN. Content is "
                                  "returned for audit only; do not treat it as "
                                  "current truth.",
                    }
    except Exception as exc:
        _log.exception("recall_v2 failed")
        return _err(f"recall failed: {exc}")
    if mem is None:
        return _err(f"not found: {kind} id={memory_id}")
    cache = temporal_cache.get(_SESSION_ID)
    cache.mark_retrieved(kind, memory_id, boost=1.0)
    if kind == "rule" and mem.supersedes:
        cache.apply_link_boost([("rule", mem.supersedes)])
    out = mem.to_dict()
    if forgotten_meta:
        out["forgotten"] = forgotten_meta
    return _ok(out)


@mcp.tool()
def search_v2(query: str, kind: str = "", project: str = "", limit: int = 10) -> str:
    """Semantic search over headlines. Bodies are NOT returned. Call
    recall_v2 for a specific id to fetch its body."""
    try:
        with store.connect() as conn:
            rows = store.search_headlines(
                conn, query=query,
                kind=kind or None,
                project=project if project != "" else None,
                limit=limit,
            )
    except Exception as exc:
        return _err(f"search failed: {exc}")
    cache = temporal_cache.get(_SESSION_ID)
    for r in rows:
        cache.mark_retrieved(r["kind"], r["memory_id"], boost=0.25)
        r["session_boost"] = cache.boost_for(r["kind"], r["memory_id"])
    return _ok({"results": rows, "count": len(rows)})


@mcp.tool()
def remember_rule_v2(headline: str, body: str, severity: str = "PATTERN",
                     project: str = "", source: str = "",
                     skill_trigger: dict | None = None) -> str:
    """Write a new RULE. Rejects duplicates (>0.75 cosine) — caller must
    route to supersede_rule_v2 instead. RULE bodies are immutable; the
    only legal modification path is supersede.

    skill_trigger: Optional dict tagging this rule as a skill. Shape:
        {"name": "<globally-unique-name>",
         "keywords": ["k1", "k2"],   # case-insensitive substring match
         "projects": [],              # empty = global; populated = scoped
         "always_on": false}          # true = load at every boot
      When set, the rule is returned by boot_session_v2 only if always_on
      is true; otherwise it loads on keyword match via search_v2 or
      explicitly via load_skill_v2.
    """
    blocked = _check_write_gate(project)
    if blocked:
        return blocked
    try:
        with store.connect() as conn:
            result = store.remember_rule(
                conn, headline=headline, body=body, severity=severity,
                project=project, source=source,
                skill_trigger=skill_trigger,
            )
    except WriteGateError as exc:
        return _err(f"write gate rejected: {exc}", step="write_gate")
    except Exception as exc:
        return _err(f"remember_rule failed: {exc}")
    if isinstance(result, DuplicateHit):
        return _ok(result.to_dict())
    return _ok(result.to_dict())


@mcp.tool()
def remember_fact_v2(headline: str, body: str, project: str = "",
                     tags: list[str] | None = None, ttl: str = "",
                     source: str = "") -> str:
    blocked = _check_write_gate(project)
    if blocked:
        return blocked
    try:
        with store.connect() as conn:
            result = store.remember_fact(
                conn, headline=headline, body=body, project=project,
                tags=tags or None, ttl=ttl or None, source=source,
            )
    except WriteGateError as exc:
        return _err(f"write gate rejected: {exc}", step="write_gate")
    except Exception as exc:
        return _err(f"remember_fact failed: {exc}")
    if isinstance(result, DuplicateHit):
        return _ok(result.to_dict())
    return _ok(result.to_dict())


@mcp.tool()
def remember_incident_v2(headline: str, body: str, project: str = "",
                         root_cause: str = "", resolution: str = "",
                         linked_rule_ids: list[int] | None = None,
                         source: str = "") -> str:
    blocked = _check_write_gate(project)
    if blocked:
        return blocked
    try:
        with store.connect() as conn:
            mem = store.remember_incident(
                conn, headline=headline, body=body, project=project,
                root_cause=root_cause or None,
                resolution=resolution or None,
                linked_rule_ids=linked_rule_ids or None,
                source=source,
            )
    except WriteGateError as exc:
        return _err(f"write gate rejected: {exc}", step="write_gate")
    except Exception as exc:
        return _err(f"remember_incident failed: {exc}")
    return _ok(mem.to_dict())


@mcp.tool()
def remember_task_v2(content: str, project: str = "", priority: str = "medium",
                     due_condition: str = "", source: str = "") -> str:
    blocked = _check_write_gate(project)
    if blocked:
        return blocked
    try:
        with store.connect() as conn:
            mem = store.remember_task(
                conn, content=content, project=project, priority=priority,
                due_condition=due_condition or None,
                created_session=_SESSION_ID, source=source,
            )
    except WriteGateError as exc:
        return _err(f"write gate rejected: {exc}", step="write_gate")
    except Exception as exc:
        return _err(f"remember_task failed: {exc}")
    return _ok(mem.to_dict())


@mcp.tool()
def capture_context_v2(context: str, source: str = "", project: str = "") -> str:
    """Auto-decompose raw text into typed atomic memories and store each.

    The primary tool for automatic brain capture. Agents should call this
    at natural checkpoints — after completing a task, when a decision is
    made, when something notable is learned. Do NOT wait for the user to
    ask.

    The brain decomposes the context into individual atomic memories
    (rule / fact / incident / task) using heuristic classification and
    stores each one separately through the typed write gate.

    Args:
        context: Raw text to capture — conversation excerpt, session
                 summary, decisions made, things learned. Can be long.
        source:  Which agent is capturing (e.g. 'claude', 'windsurf').
        project: Project this memory belongs to (e.g. 'open-brain').
    """
    ensure_schema()
    if not context or not context.strip():
        return _err("context is empty")
    blocked = _check_write_gate(project)
    if blocked:
        return blocked
    try:
        with store.connect() as conn:
            results = store.capture_context(
                conn, context=context, source=source, project=project,
            )
    except Exception as exc:
        return _err(f"capture_context failed: {exc}")
    stored_count = sum(1 for r in results if r["action"] == "stored")
    return _ok({
        "success": True,
        "memories_stored": stored_count,
        "total_chunks": len(results),
        "stored": results,
    })


@mcp.tool()
def supersede_rule_v2(old_id: int, new_headline: str, new_body: str,
                      reason: str, source: str = "", severity: str = "") -> str:
    """Supersede a rule. OLD rule moves to DEPRECATED severity. NEW
    rule replaces it in memory_index. This is the ONLY legal way to
    modify a rule — RULE bodies are immutable."""
    blocked = _check_write_gate("")  # supersede uses old rule's project
    if blocked:
        return blocked
    try:
        with store.connect() as conn:
            new = store.supersede_rule(
                conn, old_id=old_id, new_headline=new_headline,
                new_body=new_body, reason=reason, source=source,
                severity=severity or None,
            )
    except WriteGateError as exc:
        return _err(f"write gate rejected: {exc}", step="write_gate")
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"supersede failed: {exc}")
    return _ok(new.to_dict())


@mcp.tool()
def update_task_status_v2(task_id: int, status: str, source: str = "") -> str:
    try:
        with store.connect() as conn:
            store.update_task_status(conn, task_id=task_id, status=status, source=source)
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"update_task_status failed: {exc}")
    return _ok({"task_id": task_id, "status": status})


@mcp.tool()
def list_active_sessions_v2(project: str = "", exclude_self: bool = True) -> str:
    """List all currently-active sessions, optionally filtered by project.

    Args:
        project:      Filter to a specific project (empty = all projects).
        exclude_self: Hide this session from the result (default True).
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            exclude_id = _DB_SESSION_ID if exclude_self else None
            sessions = store.list_active_sessions(
                conn, project=project, exclude_id=exclude_id,
            )
    except Exception as exc:
        _log.exception("list_active_sessions_v2 failed")
        return _err(f"list failed: {exc}")
    # v2.1.0: emit registry trust signal so callers can tell whether
    # the heartbeat agent has been running recently enough to trust
    # the data. Empty set → (None, True) — nothing to distrust.
    import session_liveness as _sl
    staleness, trustworthy = _sl.compute_staleness(sessions)
    return _ok({
        "count": len(sessions),
        "sessions": sessions,
        "registry_staleness_seconds": staleness,
        "registry_trustworthy": trustworthy,
    })


@mcp.tool()
def sweep_host_v2(host: str, max_age_minutes: int = 60,
                  dry_run: bool = True) -> str:
    """Admin: reap stale active_sessions rows for a NON-local host.

    When a machine's heartbeat_agent stops or the machine is down, its
    active rows can't be reaped by the local probe (psutil only sees
    local processes). This tool lets an operator explicitly mark rows
    ended for a remote host whose sessions are staler than
    `max_age_minutes`.

    Preferred for same-host cleanup is still the heartbeat_agent
    (authoritative psutil check). Local-host sweeps are allowed
    (brain_v2 2.2.1+) and return a `warning` field; dry_run=True
    default remains the guardrail.

    Args:
        host:            Target hostname (case-insensitive). Local is
                         allowed with a warning.
        max_age_minutes: Minimum heartbeat staleness. Default 60.
        dry_run:         Default True. Returns candidates without writing.

    Returns dict with candidates[], marked_ended[] (empty when dry_run),
    and `warning` when sweeping the local host.
    (brain_v2 2.2.0; local-host warning 2.2.1)
    """
    ensure_schema()
    import session_liveness as _sl
    try:
        with store.connect() as conn:
            result = _sl.sweep_host_stale(
                conn, host=host,
                max_age_seconds=int(max_age_minutes) * 60,
                dry_run=bool(dry_run),
            )
    except Exception as exc:
        _log.exception("sweep_host_v2 failed")
        return _err(f"sweep failed: {exc}")
    return _ok(result)


@mcp.tool()
def update_active_task_v2(task: str, session_id: int = 0) -> str:
    """Update the current_task of a live session (+ bumps heartbeat_at).

    Args:
        task:       New task description.
        session_id: Optional — defaults to this server's session_id from
                    the most recent boot_session_v2 call.
    """
    ensure_schema()
    sid = session_id if session_id else _DB_SESSION_ID
    if not sid:
        return _err("no session_id available — call boot_session_v2 first "
                    "or pass session_id explicitly")
    try:
        with store.connect() as conn:
            ok = store.update_active_task(conn, session_id=sid, task=task)
    except Exception as exc:
        _log.exception("update_active_task_v2 failed")
        return _err(f"update failed: {exc}")
    if not ok:
        return _err(f"session_id={sid} not found or not active")
    return _ok({"success": True, "session_id": sid, "task": task})


@mcp.tool()
def end_session_v2(handoff: str = "", session_id: int = 0, source: str = "") -> str:
    """Cleanly end a session. Optionally writes a handoff note in the
    same call for the next session on the project to pick up.

    Args:
        handoff:    Optional handoff note (≤2000 chars) to leave for the
                    next session on this project.
        session_id: Optional — defaults to this server's session_id.
        source:     Optional — for audit.
    """
    global _DB_SESSION_ID
    ensure_schema()
    sid = session_id if session_id else _DB_SESSION_ID
    if not sid:
        return _err("no session_id available — call boot_session_v2 first "
                    "or pass session_id explicitly")
    handoff_id: int | None = None
    try:
        with store.connect() as conn:
            # Find source + project to attach to the handoff
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT source, project FROM active_sessions WHERE id = %s",
                    (sid,),
                )
                row = cur.fetchone()
            if handoff and handoff.strip():
                if row:
                    handoff_id = store.write_handoff(
                        conn, source=source or row[0], content=handoff,
                        project=row[1] or "", session_id=sid,
                    )
                else:
                    handoff_id = store.write_handoff(
                        conn, source=source or "unknown", content=handoff,
                        project="", session_id=sid,
                    )
            ended = store.end_session(conn, session_id=sid, source=source or (row[0] if row else ""))
    except Exception as exc:
        _log.exception("end_session_v2 failed")
        return _err(f"end_session failed: {exc}")
    if sid == _DB_SESSION_ID:
        _DB_SESSION_ID = None
    return _ok({
        "success": True,
        "session_id": sid,
        "ended": ended,
        "handoff_written": handoff_id,
    })


@mcp.tool()
def write_handoff_v2(content: str, source: str = "", project: str = "",
                     session_id: int = 0) -> str:
    """Write a handoff note without ending the session. Use when you
    want to leave a checkpoint mid-session. Content is hard-capped to
    2000 chars.
    """
    ensure_schema()
    if not content or not content.strip():
        return _err("handoff content is empty")
    sid = session_id if session_id else _DB_SESSION_ID
    try:
        with store.connect() as conn:
            hid = store.write_handoff(
                conn, source=source, content=content, project=project,
                session_id=sid,
            )
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        _log.exception("write_handoff_v2 failed")
        return _err(f"write_handoff failed: {exc}")
    return _ok({"success": True, "handoff_id": hid, "session_id": sid})


@mcp.tool()
def acknowledge_action_item_v2(item_id: int, decision: str,
                               source: str = "", reason: str = "") -> str:
    """Acknowledge a pending action item. Write tools are BLOCKED until
    all pending items are acknowledged.

    Args:
        item_id:  ID of the action item (from boot_session_v2's
                  pending_action_items list).
        decision: 'will_execute' | 'already_done' | 'not_relevant'.
        source:   Which agent is acknowledging.
        reason:   Required for 'already_done' and 'not_relevant'.
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            result = store.acknowledge_action_item(
                conn, item_id=item_id, decision=decision,
                source=source, reason=reason,
            )
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"acknowledge failed: {exc}")
    # Include remaining count so agent knows when writes unlock
    try:
        with store.connect() as conn:
            remaining = store.count_pending_action_items(conn)
    except Exception:
        remaining = -1
    result["remaining"] = remaining
    return _ok(result)


@mcp.tool()
def create_action_item_v2(source_kind: str, source_id: int, text: str,
                          project: str = "", kind: str = "task") -> str:
    """Create a new action item linked to a memory.

    Action items are surfaced at boot and block writes until acknowledged.
    Typically created automatically (e.g., from rules with action_items),
    but can also be created manually.

    Args:
        source_kind: The memory kind producing this item ('rule', 'fact',
                     'incident', 'task').
        source_id:   Memory id.
        text:        Item text.
        project:     Project scope (empty = global).
        kind:        'task' (default) or 'rule'. Rules cannot be
                     'already_done' at ack time — forcing agents to
                     commit or explicitly justify bypass. (v2.2.0+)
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            aid = store.create_action_item(
                conn, source_kind=source_kind, source_id=source_id,
                text=text, project=project, kind=kind,
            )
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"create_action_item failed: {exc}")
    return _ok({"success": True, "id": aid, "text": text, "kind": kind})


@mcp.tool()
def run_maintenance_v2() -> str:
    """Run all v2 maintenance jobs once: fact decay + incident archive.

    Returns counts and affected IDs for audit. Safe to call repeatedly
    (idempotent — rerunning will no-op if nothing has changed).

    Jobs:
      - Fact decay: Ebbinghaus score = 2^(-Δdays/halflife). Facts below
        threshold are deactivated; facts that recovered above threshold
        are reactivated. Hard-TTL facts whose ttl is past are expired.
      - Incident archive: incidents with no access in the last
        INCIDENT_ARCHIVE_DAYS (default 90) are flagged archived and
        their memory_index row deactivates.
    """
    ensure_schema()
    try:
        from brain_v2 import maintenance
        with store.connect() as conn:
            report = maintenance.run_all(conn)
    except Exception as exc:
        _log.exception("run_maintenance_v2 failed")
        return _err(f"maintenance failed: {exc}")
    return _ok({"success": True, **report.to_dict()})


@mcp.tool()
def run_maintenance_if_due_v2(hours: float = 24.0) -> str:
    """Run maintenance ONLY IF the last successful run was more than
    `hours` ago. Otherwise returns a skipped report immediately.

    Safe to fire on every boot via a PostToolUse hook. See
    brain_v2/MAINTENANCE_SCHEDULING.md for the hook config example.

    Args:
        hours: Rate-limit window. Default 24h. Any call inside this
               window is a no-op and returns {skipped: true}.
    """
    ensure_schema()
    try:
        from brain_v2 import maintenance
        with store.connect() as conn:
            report = maintenance.run_if_due(conn, hours=hours, source="mcp")
    except Exception as exc:
        _log.exception("run_maintenance_if_due_v2 failed")
        return _err(f"maintenance failed: {exc}")
    return _ok({"success": True, **report.to_dict()})


@mcp.tool()
def decay_facts_v2() -> str:
    """Run only the fact-decay job. Returns deactivated + reactivated +
    ttl_expired id lists. See run_maintenance_v2 for details."""
    ensure_schema()
    try:
        from brain_v2 import maintenance
        with store.connect() as conn:
            result = maintenance.decay_facts(conn)
    except Exception as exc:
        _log.exception("decay_facts_v2 failed")
        return _err(f"decay_facts failed: {exc}")
    return _ok({"success": True, **result,
                "deactivated_count": len(result["deactivated"]),
                "reactivated_count": len(result["reactivated"]),
                "ttl_expired_count": len(result["ttl_expired"])})


@mcp.tool()
def archive_incidents_v2() -> str:
    """Run only the incident-archive job. Returns list of archived ids.
    See run_maintenance_v2 for details."""
    ensure_schema()
    try:
        from brain_v2 import maintenance
        with store.connect() as conn:
            archived = maintenance.archive_incidents(conn)
    except Exception as exc:
        _log.exception("archive_incidents_v2 failed")
        return _err(f"archive_incidents failed: {exc}")
    return _ok({"success": True, "archived": archived, "archived_count": len(archived)})


@mcp.tool()
def forget_v2(kind: str, memory_id: int, reason: str = "", source: str = "") -> str:
    """Soft-delete a memory. Deactivates its memory_index row and
    records who forgot it + why. Body is preserved for audit; recall
    still works but returns a forgotten banner.

    Idempotent: forgetting an already-forgotten memory is a no-op.

    Args:
        kind:      'rule' | 'fact' | 'incident' | 'task'
        memory_id: the id within that kind.
        reason:    why this memory is being forgotten (strongly recommended).
        source:    which agent initiated the forget.
    """
    ensure_schema()
    blocked = _check_write_gate("")
    if blocked:
        return blocked
    try:
        with store.connect() as conn:
            result = store.forget(
                conn, kind=kind, memory_id=memory_id,
                reason=reason, source=source,
            )
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        _log.exception("forget_v2 failed")
        return _err(f"forget failed: {exc}")
    return _ok({"success": True, **result})


@mcp.tool()
def stats_v2(project: str = "") -> str:
    """Return corpus statistics for v2.

    Includes per-kind totals (active/forgotten), rule severity breakdown,
    task status breakdown, incident archive counts, pending action items,
    and active session count.

    Args:
        project: scope to a project (empty = global stats).
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            data = store.stats(conn, project=project)
    except Exception as exc:
        _log.exception("stats_v2 failed")
        return _err(f"stats failed: {exc}")
    return _ok(data)


@mcp.tool()
def list_recent_v2(limit: int = 20, days: int = 0, kind: str = "",
                   project: str = "", include_forgotten: bool = False) -> str:
    """List recent memories (headline-only), newest first.

    Args:
        limit:             max rows (default 20, hard cap 200).
        days:              only rows created in the last N days (0 = no limit).
        kind:              filter by memory kind ('' = all).
        project:           filter by project ('' = no filter; empty-string
                           project memories are global).
        include_forgotten: include forgotten rows (default False).
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            rows = store.list_recent(
                conn,
                limit=limit, days=days,
                kind=kind or None,
                project=project if project != "" else None,
                include_forgotten=include_forgotten,
            )
    except Exception as exc:
        _log.exception("list_recent_v2 failed")
        return _err(f"list_recent failed: {exc}")
    return _ok({"count": len(rows), "rows": rows})


# ── In-process ephemeral state (scratchpad + checkpoint cooldown) ───
# Mirrors v1's `_scratch` dict and `_checkpoint_tracker`. Cleared on
# server restart — NOT persisted to the DB (intentional, per v1).
_scratch: dict[str, str] = {}
_checkpoint_tracker: dict[str, dict[str, float]] = {}

# Default cooldown between identical brain_checkpoint calls (seconds).
# v1 uses COMPLIANCE_WINDOW from env; matching that default.
CHECKPOINT_COOLDOWN = int(os.getenv("OPEN_BRAIN_V2_CHECKPOINT_COOLDOWN", "300"))


@mcp.tool()
def annotate_v2(kind: str, memory_id: int, note: str = "",
                clear: bool = False) -> str:
    """Attach a persistent note to an existing memory, or clear it.

    Modes (mirrors v1 exactly):
      - clear=True: remove the annotation.
      - clear=False and note="": read-only, returns current annotation.
      - clear=False and note="...": set the annotation.

    Args:
        kind:      'rule' | 'fact' | 'incident' | 'task'
        memory_id: id within that kind.
        note:      the annotation text (ignored if clear=True).
        clear:     True to remove the annotation.
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            result = store.annotate(
                conn, kind=kind, memory_id=memory_id,
                note=(note if note or clear else None), clear=clear,
            )
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        _log.exception("annotate_v2 failed")
        return _err(f"annotate failed: {exc}")
    return _ok({"success": True, **result})


@mcp.tool()
def rate_v2(kind: str, memory_id: int, direction: str) -> str:
    """Rate a memory as useful (up) or not useful (down). Bumps the
    persistent counter on memory_index.

    Args:
        kind:      'rule' | 'fact' | 'incident' | 'task'
        memory_id: id within that kind.
        direction: 'up' or 'down'.
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            result = store.rate(conn, kind=kind, memory_id=memory_id,
                                direction=direction)
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        _log.exception("rate_v2 failed")
        return _err(f"rate failed: {exc}")
    return _ok({"success": True, **result})


@mcp.tool()
def pin_v2(kind: str, memory_id: int) -> str:
    """Pin a memory so it surfaces prominently in boot + search for its
    project. Global memories (empty project) cannot be pinned — call
    will return an error explaining how to re-store with a project.

    Args:
        kind:      'rule' | 'fact' | 'incident' | 'task'
        memory_id: id within that kind.
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            result = store.set_pinned(conn, kind=kind, memory_id=memory_id,
                                       pinned=True)
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        _log.exception("pin_v2 failed")
        return _err(f"pin failed: {exc}")
    return _ok({"success": True, **result})


@mcp.tool()
def unpin_v2(kind: str, memory_id: int) -> str:
    """Remove the pin from a memory. No-op on global memories (which
    cannot be pinned in the first place).

    Args:
        kind:      'rule' | 'fact' | 'incident' | 'task'
        memory_id: id within that kind.
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            result = store.set_pinned(conn, kind=kind, memory_id=memory_id,
                                       pinned=False)
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        _log.exception("unpin_v2 failed")
        return _err(f"unpin failed: {exc}")
    return _ok({"success": True, **result})


@mcp.tool()
def scratch_set_v2(key: str, value: str) -> str:
    """Store a value in session scratchpad (ephemeral in-memory dict).
    Cleared on server restart. Use remember_* for persistent storage.
    """
    _scratch[key] = value
    return _ok({"success": True, "key": key, "stored": True})


@mcp.tool()
def scratch_get_v2(key: str) -> str:
    """Read a value from the session scratchpad. Returns found=False
    with value=null if the key isn't set."""
    value = _scratch.get(key)
    return _ok({"key": key, "value": value, "found": value is not None})


@mcp.tool()
def scratch_list_v2() -> str:
    """List all current scratchpad key/value pairs."""
    return _ok({"count": len(_scratch), "entries": dict(_scratch)})


@mcp.tool()
def brain_checkpoint_v2(action: str, source: str, context: str = "",
                        project: str = "") -> str:
    """Check the brain before a risky action.

    Surfaces pinned (BLOCKER-severity) rules for the project plus
    task-relevant PATTERN rules ranked by cosine similarity to the
    action+context. Rate-limited per-source per-action via an
    in-process cooldown (default 5 minutes, env-configurable).

    Args:
        action:  what you're about to do.
        source:  REQUIRED. Which agent is calling.
        context: additional context about the specific change.
        project: project scope (empty = global).
    """
    ensure_schema()
    if not source:
        return _err("source is required for brain_checkpoint_v2",
                    blocked_by="source_required")
    if not action:
        return _err("action is required")

    # Cooldown
    tracker = _checkpoint_tracker.setdefault(source, {})
    last = tracker.get(action)
    now = _time.time()
    if last and (now - last) < CHECKPOINT_COOLDOWN:
        return _ok({
            "success": True,
            "skipped": True,
            "reason": f"Already checked '{action}' {int(now - last)}s ago. "
                      f"Cooldown is {CHECKPOINT_COOLDOWN}s.",
        })

    query = f"{action} {context} {project}".strip()
    try:
        with store.connect() as conn:
            # Pinned + BLOCKER rules (scoped to project or global)
            with conn.cursor() as cur:
                proj_filter = "project = %s OR project = ''" if project else "TRUE"
                params: list = [project] if project else []
                cur.execute(
                    f"""
                    SELECT kind, memory_id, headline, severity, project, pinned
                    FROM memory_index
                    WHERE active = TRUE
                      AND (severity = 'BLOCKER' OR pinned = TRUE)
                      AND kind = 'rule'
                      AND ({proj_filter})
                    ORDER BY pinned DESC, created_at DESC
                    LIMIT 10
                    """,
                    params,
                )
                guardrails = [dict(zip(("kind", "memory_id", "headline",
                                         "severity", "project", "pinned"), r))
                              for r in cur.fetchall()]

            # Task-relevant PATTERN rules + facts
            relevant = store.search_headlines(
                conn, query=query,
                project=project if project != "" else None,
                limit=5,
            )

        tracker[action] = now
    except Exception as exc:
        _log.exception("brain_checkpoint_v2 failed")
        return _err(f"checkpoint failed: {exc}")

    return _ok({
        "success": True,
        "action": action,
        "context": context,
        "project": project,
        "guardrails_count": len(guardrails),
        "relevant_count": len(relevant),
        "guardrails": guardrails,
        "relevant": relevant,
        "message": f"Checkpoint complete. {len(guardrails)} guardrails, "
                   f"{len(relevant)} relevant. Review before proceeding.",
    })


@mcp.tool()
def forget_many_v2(kinds: list[str], memory_ids: list[int],
                   reason: str = "", source: str = "") -> str:
    """Batch soft-delete. Accepts parallel lists: kinds[i] + memory_ids[i]
    identify one memory each. Both lists must have the same length.

    Partial success allowed — failing one item does not abort the batch.
    Returns a summary with newly-forgotten, already-forgotten, and
    not-found lists plus counts.

    Args:
        kinds:      list of memory kinds, same length as memory_ids.
        memory_ids: list of memory ids within their respective kinds.
        reason:     applied to every item.
        source:     which agent initiated the forget.
    """
    ensure_schema()
    blocked = _check_write_gate("")
    if blocked:
        return blocked
    if len(kinds) != len(memory_ids):
        return _err(
            f"kinds and memory_ids must have the same length "
            f"({len(kinds)} vs {len(memory_ids)})"
        )
    items = [{"kind": k, "memory_id": mid}
             for k, mid in zip(kinds, memory_ids)]
    try:
        with store.connect() as conn:
            result = store.forget_many(
                conn, items=items, reason=reason, source=source,
            )
    except Exception as exc:
        _log.exception("forget_many_v2 failed")
        return _err(f"forget_many failed: {exc}")
    return _ok({"success": True, **result})


@mcp.tool()
def unsupersede_v2(old_id: int, source: str, keep_corrector: bool = False) -> str:
    """Reverse a rule supersession. Clears superseded_by on the original rule,
    restores its severity, and reactivates it. By DEFAULT the corrector rule is
    retired too, restoring a clean single-active state (a superseded rule and its
    corrector are never both active). Pass keep_corrector=True to leave both
    active. Refuses to unsupersede a mid-chain rule (unsupersede the head first).

    Args:
        old_id: id of the rule whose supersession to reverse.
        source: REQUIRED. Which agent is unsupersedeing.
        keep_corrector: if True, leave the corrector active (both-active). Default False.
    """
    ensure_schema()
    if not source:
        return _err("source is required for unsupersede_v2",
                    blocked_by="source_required")
    try:
        with store.connect() as conn:
            result = store.unsupersede_rule(
                conn, old_id=old_id, source=source,
                keep_corrector=keep_corrector,
            )
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        _log.exception("unsupersede_v2 failed")
        return _err(f"unsupersede failed: {exc}")
    return _ok({"success": True, **result})


@mcp.tool()
def brain_startup_reminder_v2() -> str:
    """Return the v2 startup reminder as a structured system message.

    Clients should display this prominently at session start. It
    describes the v2 mandatory workflow and the tools an agent must
    call first.

    Mirrors v1's brain_startup_reminder, adjusted for v2 tool names.
    """
    return _ok({
        "type": "system_message",
        "level": "mandatory",
        "title": "Open Brain v2: Boot-First Enforcement Active",
        "message": (
            "MANDATORY WORKFLOW: You must call boot_session_v2 FIRST "
            "before any other v2 tool.\n\n"
            "Why? Open Brain v2 enforces a 'boot first' policy so every "
            "session starts with full project context:\n"
            "  - Pinned BLOCKER-severity rules for this project\n"
            "  - Task-relevant PATTERN rules (top 5 by semantic match)\n"
            "  - Pending action items that BLOCK writes until acknowledged\n"
            "  - Other active sessions (so you don't overlap sibling work)\n"
            "  - Auto-loaded handoff from the last session on this project\n\n"
            "What happens if you skip the boot?\n"
            "  - Writes (remember_*, capture_context_v2, supersede_rule_v2) "
            "may block on pending action items you never saw\n"
            "  - Your session is not registered in active_sessions, so "
            "sibling sessions can't see you\n"
            "  - You lose the handoff continuity from the previous session\n\n"
            "How to comply:\n"
            "  1. Call boot_session_v2(project, task, source, cwd, pid, host).\n"
            "  2. If it returns pending_action_items, acknowledge each via "
            "acknowledge_action_item_v2 BEFORE writing.\n"
            "  3. If other_active_sessions shows a sibling on the same "
            "project, surface it to the user before overlapping their work.\n"
            "  4. At session end, call end_session_v2(handoff=...) so the "
            "next session picks up where you left off.\n\n"
            "This is a hard requirement, not a suggestion."
        ),
        "action": "display_at_session_start",
    })


@mcp.tool()
def prune_v2(days: int = 90, min_access: int = 0,
             dry_run: bool = True) -> str:
    """Permanently delete stale memories with v1 safeguards.

    SAFETY: hard floor days>=30 (OPEN_BRAIN_V2_PRUNE_MIN_DAYS),
    hard cap 50 rows/call (OPEN_BRAIN_V2_PRUNE_MAX_DELETE), dry_run
    defaults to True. Pinned memories are NEVER pruned. Rules are
    NEVER pruned (immutable in v2 design).

    Targets (must be older than `days`):
      - Facts with access_count <= min_access
      - Incidents with archived = TRUE
      - Tasks with status in ('done', 'stale')

    Use dry_run=True to preview; set False to execute. Every deletion
    is audited via v2_audit BEFORE the row is removed.

    Args:
        days:       age threshold in days (minimum 30).
        min_access: facts with access_count > this are NOT pruned.
        dry_run:    True (default) previews only.
    """
    ensure_schema()
    # Note: prune is HARD DELETE — do NOT gate on pending action items
    # because pruning stale inactive rows is an administrative cleanup.
    try:
        with store.connect() as conn:
            result = store.prune(
                conn, days=days, min_access=min_access, dry_run=dry_run,
            )
    except ValueError as exc:
        return _err(str(exc), blocked_by="prune_safeguard")
    except Exception as exc:
        _log.exception("prune_v2 failed")
        return _err(f"prune failed: {exc}")
    return _ok({"success": True, **result})


@mcp.tool()
def load_skill_v2(name: str, source: str = "", project: str = "") -> str:
    """Load a specific skill by its trigger name.

    Skills are rules with skill_trigger.name set. Call this when you're
    about to start work on a topic you know has a named skill. Returns
    the skill content in the same shape as search_v2.

    Only active (non-superseded) skills are returned. A skill with
    projects populated is only loadable from one of those projects;
    a skill with projects = [] is global.

    Args:
        name:    The skill name from its skill_trigger.name field.
        source:  REQUIRED agent identifier.
        project: Current project scope.
    """
    ensure_schema()
    if not name:
        return _err("name is required")
    try:
        conn = store.connect()
        skill = store.get_skill_by_name(conn, name=name,
                                         project_filter=project or None)
    except Exception as exc:
        _log.exception("load_skill_v2 failed")
        return _err(f"load_skill failed: {exc}")
    if skill is None:
        return _err(f"skill '{name}' not found or out of scope for project '{project}'",
                    blocked_by="not_found")
    return _ok(skill)


@mcp.tool()
def metrics_v2() -> str:
    """Return per-tool call counts, error rates, and latency percentiles
    for the current server process lifetime.

    Useful for diagnosing which tools are slow or failing frequently.
    """
    return _ok(obs.get_metrics())


@mcp.tool()
def recent_errors_v2(n: int = 20) -> str:
    """Return the last N error events from the in-memory log ring buffer.
    Useful for diagnosing recent failures without reading log files.
    """
    return _ok({"errors": obs.get_recent_errors(n=n)})


# ─── MCP Prompts ───────────────────────────────────────────────────────────────

@mcp.prompt(
    name="open-brain-v2-startup",
    title="Open Brain V2: Boot Session",
    description="Mandatory V2 startup sequence — call this before any other action in a new session.",
)
def open_brain_v2_startup_prompt(project: str = "open-brain") -> list[dict]:
    """Return the mandatory V2 boot sequence as an injectable MCP prompt."""
    return [
        {
            "role": "user",
            "content": (
                f"MANDATORY SESSION START (Open Brain V2) — execute NOW before any other action:\n\n"
                f"1. Call `boot_session_v2` with project=\"{project}\", "
                f"task=\"<first user prompt>\", source=\"claude\"\n"
                f"   Returns: typed rules, active facts, incidents, pending tasks, "
                f"other active sessions, and action items.\n\n"
                f"2. Also call V1 boot: `open-brain_boot_session` project=\"{project}\" source=\"claude\"\n"
                f"   Returns: guardrails, correction history, pinned rules.\n\n"
                f"3. Search for task context and user preferences.\n\n"
                f"4. Acknowledge all pending action_items — write tools are BLOCKED until done.\n\n"
                f"Do NOT skip these steps."
            ),
        }
    ]


_schema_applied = False


def ensure_schema() -> None:
    global _schema_applied
    if _schema_applied:
        return
    try:
        with store.connect() as conn:
            apply_schema(conn)
        _schema_applied = True
    except Exception as exc:
        import sys
        print(f"[open-brain-v2] schema check deferred (DB not ready): {exc}",
              file=sys.stderr)


def _auto_register_session() -> None:
    """Auto-register a session at server startup so telemetry flows
    from the first tool call. boot_session_v2 upgrades this session
    with project/task/handoff when the agent calls it explicitly."""
    global _DB_SESSION_ID
    ensure_schema()
    if _DB_SESSION_ID is not None:
        return
    try:
        import socket
        import session_liveness
        # v2.1.2: capture parent process identity (no clientInfo available
        # at startup — the MCP initialize handshake happens later when the
        # client calls its first tool). Half-identified row: enough to
        # see "this server was launched by node.exe / cursor.exe / etc."
        auto_md = session_liveness.capture_identity_metadata(client_info=None)
        conn = store.connect()
        _DB_SESSION_ID = store.register_session(
            conn, source="auto", project="",
            cwd=os.getcwd(), pid=os.getpid(),
            host=socket.gethostname(),
            current_task="server started, awaiting boot_session_v2",
            metadata=auto_md,
        )
        _flush_tool_event_buffer(_DB_SESSION_ID)
        _log.info("auto-registered session id=%d (pid=%d)", _DB_SESSION_ID, os.getpid())
    except Exception as exc:
        _log.warning("auto-register session failed (DB not ready): %s", exc)


def _on_shutdown():
    if _DB_SESSION_ID is not None:
        _flush_tool_event_buffer(_DB_SESSION_ID)
    obs.shutdown()

_atexit.register(_on_shutdown)


HTTP_PORT = int(os.getenv("OPEN_BRAIN_V2_PORT", "8081"))
HTTP_HOST = os.getenv("OPEN_BRAIN_V2_HOST", "0.0.0.0")


def _run_stdio() -> None:
    mcp.run(transport="stdio")


def _run_http(host: str, port: int) -> None:
    import uvicorn
    app = mcp.streamable_http_app()
    print(f"Open Brain v2 HTTP server: http://{host}:{port}/mcp", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _run_both(host: str, port: int) -> None:
    import threading
    t = threading.Thread(target=_run_http, args=(host, port), daemon=True)
    t.start()
    _run_stdio()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Open Brain v2 MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "both"],
        default="stdio",
        help="Transport: stdio (editors), http (persistent HTTP), both (stdio+http)",
    )
    parser.add_argument(
        "--port", type=int, default=HTTP_PORT,
        help=f"HTTP port (default: {HTTP_PORT}, env: OPEN_BRAIN_V2_PORT)",
    )
    parser.add_argument(
        "--host", default=HTTP_HOST,
        help=f"HTTP host (default: {HTTP_HOST}, env: OPEN_BRAIN_V2_HOST)",
    )
    args = parser.parse_args()

    if args.transport in ("http", "both"):
        # Don't block HTTP startup on session registration
        import threading as _threading
        _threading.Thread(target=_auto_register_session, daemon=True).start()
        if args.transport == "http":
            _run_http(args.host, args.port)
        else:
            _run_both(args.host, args.port)
    else:
        _auto_register_session()
        _run_stdio()
