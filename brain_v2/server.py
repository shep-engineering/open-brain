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


_SESSION_ID = f"pid{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _ok(data) -> str:
    return json.dumps(data if isinstance(data, (dict, list, str, int, float, bool)) or data is None else data.to_dict(),
                      default=str)


def _err(msg: str, **extra) -> str:
    return json.dumps({"error": msg, **extra})


@mcp.tool()
def boot_session_v2(project: str = "", task: str = "", source: str = "",
                    handoff: str = "") -> str:
    """Boot v2 — returns headline-only payload.

    5 BLOCKER max. 5 PATTERN max (task-relevance ranked). 2,000 token cap.
    Bodies are NOT included. Use recall_v2(kind, memory_id) to fetch a body.
    WORKING CONTEXT is regenerated from `task` each call — not stored.
    """
    ensure_schema()
    try:
        with store.connect() as conn:
            payload = boot.build(conn, project=project, task=task, source=source, handoff=handoff)
    except Exception as exc:
        return _err(f"boot failed: {exc}")
    return _ok({"session_id": _SESSION_ID, **payload.to_dict()})


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
def supersede_rule_v2(old_id: int, new_headline: str, new_body: str,
                      reason: str, source: str = "", severity: str = "") -> str:
    """Supersede a rule. OLD rule moves to DEPRECATED severity. NEW
    rule replaces it in memory_index. This is the ONLY legal way to
    modify a rule — RULE bodies are immutable."""
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
