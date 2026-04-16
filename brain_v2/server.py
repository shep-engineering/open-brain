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

from brain_v2 import boot, store, temporal_cache
from brain_v2.config import SERVER_NAME
from brain_v2.schema import apply_schema
from brain_v2.store import DuplicateHit
from brain_v2.write_gate import WriteGateError


mcp = FastMCP(SERVER_NAME)


import logging

logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(levelname)s %(message)s",
    stream=sys.stderr,
)
_log = logging.getLogger("brain_v2.server")

_SESSION_ID = f"pid{os.getpid()}-{uuid.uuid4().hex[:8]}"
_DB_SESSION_ID: int | None = None  # set by boot_session_v2


def _check_write_gate(project: str) -> str | None:
    """Returns an error JSON string if writes are blocked by pending
    action items, else None."""
    try:
        with store.connect() as conn:
            pending = store.count_pending_action_items(conn, project=project)
        if pending > 0:
            return _err(
                f"BLOCKED: {pending} pending action item(s) must be acknowledged "
                f"before writes are allowed. Call acknowledge_action_item_v2 for each.",
                blocked_by="action_items_pending",
                pending_count=pending,
            )
    except Exception:
        pass  # DB down — don't block on a failed check
    return None


def _ok(data) -> str:
    return json.dumps(data if isinstance(data, (dict, list, str, int, float, bool)) or data is None else data.to_dict(),
                      default=str)


def _err(msg: str, **extra) -> str:
    return json.dumps({"error": msg, **extra})


@mcp.tool()
def boot_session_v2(project: str = "", task: str = "", source: str = "",
                    handoff: str = "", cwd: str = "", pid: int = 0,
                    host: str = "") -> str:
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
    """
    global _DB_SESSION_ID
    ensure_schema()
    actual_pid = pid if pid else os.getpid()
    try:
        with store.connect() as conn:
            payload = boot.build(
                conn, project=project, task=task, source=source,
                handoff=handoff, cwd=cwd, pid=actual_pid, host=host,
            )
    except Exception as exc:
        _log.exception("boot_session_v2 failed")
        return _err(f"boot failed: {exc}")
    _DB_SESSION_ID = payload.session_id
    _log.info("boot_session_v2: source=%s project=%s session_id=%s siblings=%d",
              source, project, payload.session_id, len(payload.other_active_sessions))
    return _ok({"worker_session_id": _SESSION_ID, **payload.to_dict()})


@mcp.tool()
def recall_v2(kind: str, memory_id: int) -> str:
    """Fetch the full body of a single memory by (kind, memory_id)."""
    try:
        with store.connect() as conn:
            mem = store.recall(conn, kind=kind, memory_id=memory_id)
    except Exception as exc:
        return _err(f"recall failed: {exc}")
    if mem is None:
        return _err(f"not found: {kind} id={memory_id}")
    cache = temporal_cache.get(_SESSION_ID)
    cache.mark_retrieved(kind, memory_id, boost=1.0)
    if kind == "rule" and mem.supersedes:
        cache.apply_link_boost([("rule", mem.supersedes)])
    return _ok(mem.to_dict())


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
                     project: str = "", source: str = "") -> str:
    """Write a new RULE. Rejects duplicates (>0.75 cosine) — caller must
    route to supersede_rule_v2 instead. RULE bodies are immutable; the
    only legal modification path is supersede."""
    blocked = _check_write_gate(project)
    if blocked:
        return blocked
    try:
        with store.connect() as conn:
            result = store.remember_rule(
                conn, headline=headline, body=body, severity=severity,
                project=project, source=source,
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
    return _ok({"count": len(sessions), "sessions": sessions})


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
                          project: str = "") -> str:
    """Create a new action item linked to a memory.

    Action items are surfaced at boot and block writes until acknowledged.
    Typically created automatically (e.g., from rules with action_items),
    but can also be created manually.
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            aid = store.create_action_item(
                conn, source_kind=source_kind, source_id=source_id,
                text=text, project=project,
            )
    except Exception as exc:
        return _err(f"create_action_item failed: {exc}")
    return _ok({"success": True, "id": aid, "text": text})


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


if __name__ == "__main__":
    ensure_schema()  # best-effort at startup; retried on first tool call if needed
    mcp.run()
