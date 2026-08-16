"""Typed memory store operations for Open Brain v2.

One function per (kind, verb) pairing. Every write:
    1. Runs through write_gate.run_gate()
    2. Inserts into the kind-specific table
    3. Inserts the headline/embedding projection into memory_index
    4. Logs to v2_audit

Recall does NOT re-embed. Search does NOT re-embed for cached queries.
This is deliberate — it's the primary defense against the Ollama-thrash
failure mode documented in infra-cost-addendum.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.extensions

import logging

from .config import (
    DATABASE_URL,
    DUPLICATE_COSINE_THRESHOLD,
    CONSOLIDATION_COSINE,
    CONSOLIDATION_MAX_RULES,
)
from .embedding import embed_to_pgvector
from .write_gate import WriteGateError, run_gate, run_gate_with_neighbors, check_kind

# Shared with V1 (repo-root module imported by both brains). Used for
# host normalization at session-registry insert and by the boot-time
# opportunistic probe in brain_v2/boot.py.
import session_liveness

log = logging.getLogger("brain_v2.store")


# ── Connection helper ───────────────────────────────────────────────
_conn: psycopg2.extensions.connection | None = None


def connect() -> psycopg2.extensions.connection:
    """Return a reusable connection (same pattern as v1's _get_conn).

    psycopg2.connect() to the v2 container takes ~21 seconds due to
    DNS/TCP resolution overhead on Windows + Docker. Opening a fresh
    connection per tool call was the root cause of every v2 tool taking
    42-65 seconds (one connect in the write gate + one in the tool body).
    """
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(DATABASE_URL)
        # autocommit stays False (psycopg2 default) — atomicity preserved.
        # Multi-statement writes (INSERT rule + INSERT memory_index + INSERT
        # v2_audit) must be atomic; conn.commit() at the end of each store
        # function is the transaction boundary.
    else:
        # The connection is a process-wide singleton reused across every tool
        # call. If a prior tool raised mid-transaction and its caller did not
        # roll back, the connection is left INTRANS (or INERROR after a failed
        # statement), which would poison the NEXT tool with "current transaction
        # is aborted". Reset any lingering transaction so each tool starts clean.
        # (INTRANS = 2, INERROR = 3; TransactionStatus.IDLE = 0.)
        try:
            status = _conn.get_transaction_status()
            if status != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
                _conn.rollback()
        except psycopg2.Error:
            # Connection is unusable — drop it so the next call reconnects.
            try:
                _conn.close()
            except psycopg2.Error:
                pass
            _conn = psycopg2.connect(DATABASE_URL)
    return _conn


# ── Data shapes ─────────────────────────────────────────────────────
@dataclass
class Memory:
    kind: str
    id: int
    headline: str
    body: str
    project: str
    severity: str | None = None
    supersedes: int | None = None
    superseded_by: int | None = None
    created_at: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["extra"] is None:
            d.pop("extra")
        return d


# ── Duplicate hit (return value; not an exception) ───────────────────
@dataclass
class DuplicateHit:
    kind: str
    existing_id: int
    similarity: float
    existing_headline: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "duplicate": True,
            "kind": self.kind,
            "existing_id": self.existing_id,
            "similarity": round(self.similarity, 4),
            "existing_headline": self.existing_headline,
            "hint": f"route to supersede(old_id={self.existing_id}, ...) instead of creating a parallel {self.kind}",
            "threshold": DUPLICATE_COSINE_THRESHOLD,
        }


# ── Shared helpers ──────────────────────────────────────────────────
def _audit(cur, operation: str, kind: str, memory_id: int, snapshot: dict, source: str) -> None:
    cur.execute(
        "INSERT INTO v2_audit (operation, kind, memory_id, snapshot, source) "
        "VALUES (%s, %s, %s, %s::jsonb, %s)",
        (operation, kind, memory_id, json.dumps(snapshot, default=str), source),
    )


def _index_insert(cur, *, kind: str, memory_id: int, project: str, headline: str,
                  severity: str | None, embedding_vec: str, pinned: bool = False) -> None:
    cur.execute(
        """
        INSERT INTO memory_index (kind, memory_id, project, headline, severity, embedding, pinned, active)
        VALUES (%s, %s, %s, %s, %s, %s::vector, %s, TRUE)
        """,
        (kind, memory_id, project, headline, severity, embedding_vec, pinned),
    )


def _index_deactivate(cur, kind: str, memory_id: int) -> None:
    cur.execute(
        "UPDATE memory_index SET active = FALSE WHERE kind = %s AND memory_id = %s",
        (kind, memory_id),
    )


# ── RULE ─────────────────────────────────────────────────────────────
def remember_rule(conn, *, headline: str, body: str, severity: str = "PATTERN",
                  project: str = "", source: str = "",
                  pinned: bool = False,
                  skill_trigger: dict | None = None) -> Memory | DuplicateHit:
    """Insert a new RULE. If a >0.75-cosine duplicate exists, DOES NOT
    insert; returns DuplicateHit so caller can route to supersede.
    Refuses to merge.

    skill_trigger: optional dict tagging this rule as a skill. Shape:
        {"name": "<unique-name>", "keywords": ["k1", "k2"],
         "projects": [], "always_on": false}
    """
    embedding_vec = embed_to_pgvector(f"{headline}. {body}")
    # Single scan derives BOTH the duplicate check and the "similar existing
    # rule" hint (no re-embed, no second scan).
    gate = run_gate_with_neighbors(
        conn, kind="rule", headline=headline, body=body,
        severity=severity, embedding_vec=embedding_vec, project=project)
    if gate.duplicate is not None:
        return DuplicateHit(kind="rule", existing_id=gate.duplicate.memory_id,
                            similarity=gate.duplicate.similarity,
                            existing_headline=gate.duplicate.headline)

    st_json = json.dumps(skill_trigger) if skill_trigger else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rules (headline, body, severity, project, source, skill_trigger)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id, created_at
            """,
            (headline, body, severity, project, source, st_json),
        )
        rid, created = cur.fetchone()
        _index_insert(cur, kind="rule", memory_id=rid, project=project,
                      headline=headline, severity=severity,
                      embedding_vec=embedding_vec, pinned=pinned)
        # Store skill_trigger on memory_index for fast boot-time filtering
        if st_json:
            cur.execute(
                "UPDATE memory_index SET skill_trigger = %s::jsonb "
                "WHERE kind = 'rule' AND memory_id = %s",
                (st_json, rid),
            )
        _audit(cur, "INSERT", "rule", rid,
               {"headline": headline, "severity": severity, "project": project,
                "skill_trigger": skill_trigger}, source)
    conn.commit()
    extra = None
    if gate.neighbors:
        extra = {"similar_existing": [
            {"id": n.memory_id, "headline": n.headline, "severity": n.severity,
             "similarity": round(n.similarity, 4)}
            for n in gate.neighbors
        ], "hint": ("This rule is close to existing rule(s) about the same topic. "
                    "If they conflict, supersede the wrong one; if this is a "
                    "refinement, supersede instead of adding a parallel rule.")}
    return Memory(kind="rule", id=rid, headline=headline, body=body, project=project,
                  severity=severity, created_at=str(created), extra=extra)


def supersede_rule(conn, *, old_id: int, new_headline: str, new_body: str,
                   reason: str, source: str = "", severity: str | None = None) -> Memory:
    """Supersede old_id with a new RULE. Old rule's severity becomes
    DEPRECATED, superseded_by points at the new rule. memory_index
    entry for old_id goes inactive. This is the ONLY legal way to
    modify a rule."""
    with conn.cursor() as cur:
        cur.execute("SELECT severity, project, superseded_by FROM rules WHERE id = %s", (old_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"rule id={old_id} not found")
        old_severity, project, already = row
        if already is not None:
            raise ValueError(f"rule id={old_id} already superseded by {already}; "
                             f"supersede the latest in the chain")

    new_severity = severity or (old_severity if old_severity != "DEPRECATED" else "PATTERN")
    embedding_vec = embed_to_pgvector(f"{new_headline}. {new_body}")
    # Gate checks type + headline + atomicity; skip duplicate detection
    # because by definition we're revising an overlap.
    run_gate(conn, kind="rule", headline=new_headline, body=new_body,
             severity=new_severity, embedding_vec=None)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rules (headline, body, severity, project, source, supersedes, supersede_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (new_headline, new_body, new_severity, project, source, old_id, reason),
        )
        new_id, created = cur.fetchone()
        cur.execute(
            "UPDATE rules SET superseded_by = %s, severity = 'DEPRECATED' WHERE id = %s",
            (new_id, old_id),
        )
        _index_deactivate(cur, "rule", old_id)
        _index_insert(cur, kind="rule", memory_id=new_id, project=project,
                      headline=new_headline, severity=new_severity,
                      embedding_vec=embedding_vec)
        _audit(cur, "SUPERSEDE", "rule", old_id,
               {"superseded_by": new_id, "reason": reason}, source)
    conn.commit()
    return Memory(kind="rule", id=new_id, headline=new_headline, body=new_body,
                  project=project, severity=new_severity, supersedes=old_id,
                  created_at=str(created))


# ── FACT ─────────────────────────────────────────────────────────────
def remember_fact(conn, *, headline: str, body: str, project: str = "",
                  tags: list[str] | None = None, ttl: str | None = None,
                  source: str = "") -> Memory | DuplicateHit:
    embedding_vec = embed_to_pgvector(f"{headline}. {body}")
    dup = run_gate(conn, kind="fact", headline=headline, body=body,
                   embedding_vec=embedding_vec)
    if dup is not None:
        return DuplicateHit(kind="fact", existing_id=dup.memory_id,
                            similarity=dup.similarity, existing_headline=dup.headline)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO facts (headline, body, project, tags, ttl, source)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (headline, body, project, tags or [], ttl, source),
        )
        fid, created = cur.fetchone()
        _index_insert(cur, kind="fact", memory_id=fid, project=project,
                      headline=headline, severity=None, embedding_vec=embedding_vec)
        _audit(cur, "INSERT", "fact", fid,
               {"headline": headline, "project": project}, source)
    conn.commit()
    return Memory(kind="fact", id=fid, headline=headline, body=body,
                  project=project, created_at=str(created))


# ── INCIDENT ─────────────────────────────────────────────────────────
def remember_incident(conn, *, headline: str, body: str, project: str = "",
                      root_cause: str | None = None, resolution: str | None = None,
                      linked_rule_ids: list[int] | None = None,
                      source: str = "") -> Memory:
    embedding_vec = embed_to_pgvector(f"{headline}. {body}")
    # duplicate check intentionally skipped for incidents (embedding_vec=None):
    # the same event can legitimately recur; callers track by linked_rule_ids,
    # not by semantic similarity.
    run_gate(conn, kind="incident", headline=headline, body=body, embedding_vec=None)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO incidents (headline, body, project, root_cause, resolution, linked_rule_ids, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, occurred_at
            """,
            (headline, body, project, root_cause, resolution, linked_rule_ids or [], source),
        )
        iid, occurred = cur.fetchone()
        _index_insert(cur, kind="incident", memory_id=iid, project=project,
                      headline=headline, severity=None, embedding_vec=embedding_vec)
        _audit(cur, "INSERT", "incident", iid,
               {"headline": headline, "project": project}, source)
    conn.commit()
    return Memory(kind="incident", id=iid, headline=headline, body=body,
                  project=project, created_at=str(occurred))


# ── TASK ─────────────────────────────────────────────────────────────
def remember_task(conn, *, content: str, project: str = "",
                  priority: str = "medium", due_condition: str | None = None,
                  created_session: str | None = None,
                  source: str = "") -> Memory:
    headline = content.split("\n", 1)[0][:120]
    # Tasks are short-lived obligations, not rules — the atomicity check
    # for "multiple guardrails in one body" doesn't apply, but length + headline do.
    from .write_gate import check_headline
    check_headline(headline)
    embedding_vec = embed_to_pgvector(content)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tasks (content, project, priority, due_condition, created_session)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (content, project, priority, due_condition, created_session),
        )
        tid, created = cur.fetchone()
        _index_insert(cur, kind="task", memory_id=tid, project=project,
                      headline=headline, severity=None, embedding_vec=embedding_vec)
        _audit(cur, "INSERT", "task", tid,
               {"content": content, "project": project, "priority": priority}, source)
    conn.commit()
    return Memory(kind="task", id=tid, headline=headline, body=content,
                  project=project, created_at=str(created))


def update_task_status(conn, *, task_id: int, status: str, source: str = "") -> None:
    if status not in ("open", "blocked", "done", "stale"):
        raise ValueError(f"invalid status {status!r}")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = %s, updated_at = NOW() WHERE id = %s",
            (status, task_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"task id={task_id} not found")
        if status in ("done", "stale"):
            _index_deactivate(cur, "task", task_id)
        _audit(cur, "UPDATE", "task", task_id, {"status": status}, source)
    conn.commit()


# ── RECALL ───────────────────────────────────────────────────────────
def recall(conn, *, kind: str, memory_id: int) -> Memory | None:
    check_kind(kind)
    table_map = {"rule": "rules", "fact": "facts", "incident": "incidents", "task": "tasks"}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM {table_map[kind]} WHERE id = %s", (memory_id,))
        row = cur.fetchone()
        if not row:
            return None
        if kind in ("fact", "incident"):
            cur.execute(
                f"UPDATE {table_map[kind]} SET last_accessed = NOW()"
                + (", access_count = access_count + 1" if kind == "fact" else "")
                + " WHERE id = %s",
                (memory_id,),
            )
    conn.commit()
    if kind == "task":
        return Memory(kind=kind, id=row["id"], headline=row["content"][:120],
                      body=row["content"], project=row["project"],
                      created_at=str(row["created_at"]),
                      extra={"status": row["status"], "priority": row["priority"]})
    return Memory(
        kind=kind, id=row["id"], headline=row["headline"], body=row["body"],
        project=row["project"],
        severity=row.get("severity"),
        supersedes=row.get("supersedes"),
        superseded_by=row.get("superseded_by"),
        created_at=str(row.get("created_at") or row.get("occurred_at")),
    )


# ── SEARCH ───────────────────────────────────────────────────────────
def search_headlines(conn, *, query: str, kind: str | None = None,
                     project: str | None = None, limit: int = 10) -> list[dict]:
    """Return headline-only results. Bodies are NOT loaded — caller
    uses recall(id) if they need a body. This is a direct implementation
    of the infra-cost addendum's advice: boot/search should not
    materialize bodies by default."""
    embedding_vec = embed_to_pgvector(query)
    where = ["active = TRUE"]
    params: list[Any] = [embedding_vec]  # for SELECT similarity
    if kind:
        check_kind(kind)
        where.append("kind = %s")
        params.append(kind)
    if project is not None:
        where.append("(project = %s OR project = '')")
        params.append(project)
    params.append(embedding_vec)  # for ORDER BY (same value; pgvector CSE handles it)
    params.append(limit)
    sql = f"""
        SELECT kind, memory_id, headline, severity, project,
               1 - (embedding <=> %s::vector) AS similarity
        FROM memory_index
        WHERE {' AND '.join(where)}
        ORDER BY embedding <=> %s::vector ASC
        LIMIT %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    results = [dict(r) for r in rows]

    # Merge skill keyword matches (cheap string match, no Ollama call)
    from .config import SKILL_TRIGGER_MAX
    skills = get_skills_by_keywords(conn, query=query,
                                     project_filter=project, limit=SKILL_TRIGGER_MAX)
    # Annotate cosine results that also matched by keyword, add new ones
    seen_ids = {r["memory_id"]: i for i, r in enumerate(results)}
    for skill in skills:
        idx = seen_ids.get(skill["memory_id"])
        if idx is not None:
            # Already in results from cosine — annotate with skill trigger info
            results[idx]["via_skill_trigger"] = skill["via_skill_trigger"]
            results[idx]["skill_trigger"] = skill.get("skill_trigger")
        else:
            results.append(skill)
            seen_ids[skill["memory_id"]] = len(results) - 1
    return results[:limit]


# ── SKILLS LAYER ────────────────────────────────────────────────────
def get_skills_by_keywords(conn, query: str, project_filter: str | None,
                            limit: int = 5) -> list[dict]:
    """Return rules whose skill_trigger keywords match the query string.

    Keyword matching is case-insensitive substring (not embedding similarity).
    Respects project scope: empty projects array = global, populated = scoped.
    Only active (non-superseded) skills are returned.
    """
    query_lower = query.lower()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.id, r.headline, r.body, r.project, r.skill_trigger, "
            "       r.created_at, mi.memory_id, mi.kind, mi.pinned "
            "FROM rules r JOIN memory_index mi ON mi.kind = 'rule' AND mi.memory_id = r.id "
            "WHERE r.skill_trigger IS NOT NULL "
            "  AND mi.active = TRUE "
            "ORDER BY r.created_at ASC"
        )
        matches = []
        for row in cur.fetchall():
            trig = row[4] or {}
            keywords = [kw for kw in (trig.get("keywords") or []) if kw]
            if not keywords or not any(kw.lower() in query_lower for kw in keywords):
                continue
            scope = trig.get("projects") or []
            if scope and (not project_filter or project_filter not in scope):
                continue
            d = {"kind": "rule", "memory_id": row[6], "headline": row[1],
                 "project": row[3], "skill_trigger": trig,
                 "via_skill_trigger": trig.get("name", "unknown")}
            matches.append(d)
            if len(matches) >= limit:
                break
    return matches


def get_skill_by_name(conn, name: str,
                       project_filter: str | None = None) -> dict | None:
    """Fetch a specific skill by its trigger name.

    Returns None if not found or out of scope for the given project.
    Only active (non-superseded) skills are returned.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT r.id, r.headline, r.body, r.project, r.severity, "
            "       r.skill_trigger, r.created_at "
            "FROM rules r JOIN memory_index mi ON mi.kind = 'rule' AND mi.memory_id = r.id "
            "WHERE r.skill_trigger->>'name' = %s "
            "  AND mi.active = TRUE "
            "LIMIT 1",
            (name,),
        )
        row = cur.fetchone()
    if not row:
        return None
    trig = row["skill_trigger"] or {}
    scope = trig.get("projects") or []
    if scope and (not project_filter or project_filter not in scope):
        return None
    return {
        "kind": "rule",
        "memory_id": row["id"],
        "headline": row["headline"],
        "body": row["body"],
        "project": row["project"],
        "severity": row["severity"],
        "skill_trigger": trig,
        "via_skill_trigger": trig.get("name", "unknown"),
        "created_at": str(row["created_at"]),
    }


# ── SESSION REGISTRY ─────────────────────────────────────────────────
def register_session(conn, *, source: str, project: str = "", cwd: str = "",
                     pid: int | None = None, host: str = "",
                     current_task: str = "",
                     metadata: dict | None = None) -> int:
    """Register a new active session. If a row with the same
    (source, cwd, pid) is already active, it is ENDED first (supersede
    on reboot — process lifecycle is authoritative, no TTL).
    """
    # v0.23.0 / V2 2.1.0: normalize host at insert so `lower(host)=...`
    # matches legacy rows written in mixed case.
    # v2.1.2: defense-in-depth — if caller passed empty/whitespace/None
    # host, default to this machine's hostname rather than letting the
    # empty string land (empty-host rows are unprobeable by the agent
    # and operationally useless). The boot tools already default host
    # at the tool layer; this catches any direct register_session caller
    # that bypasses the tool (tests, scripts, future code paths).
    # Chosen over hard-rejection: raising ValueError on empty host would
    # be a breaking API change for existing store.register_session callers
    # in tests; a defensive default achieves the same data outcome
    # (no empty-host rows land) while keeping the patch bump valid semver.
    normalized_host = session_liveness.normalize_host(host)
    if not normalized_host:
        import socket as _socket
        normalized_host = session_liveness.normalize_host(_socket.gethostname()) or ""
    # v2.1.1: capture create_time for pid-reuse-safe identity check at
    # probe time. None for null-pid / permission-denied / race — the
    # probe falls back to the legacy pid-only check in that case.
    pid_create_time = session_liveness.get_pid_create_time(pid) if pid else None
    with conn.cursor() as cur:
        # End any prior active session from the same process
        if pid is not None:
            cur.execute(
                """
                UPDATE active_sessions
                SET status = 'ended', ended_at = NOW()
                WHERE status = 'active'
                  AND source = %s
                  AND cwd = %s
                  AND pid = %s
                """,
                (source, cwd, pid),
            )
            if cur.rowcount:
                log.info("register_session: superseded %d prior session(s) for source=%s pid=%s",
                         cur.rowcount, source, pid)

        cur.execute(
            """
            INSERT INTO active_sessions
                (source, project, cwd, pid, host, current_task, metadata, pid_create_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (source, project, cwd, pid, normalized_host, current_task or None,
             json.dumps(metadata) if metadata else None, pid_create_time),
        )
        session_id = cur.fetchone()[0]
    conn.commit()
    log.info("register_session: source=%s project=%s pid=%s → session_id=%d",
             source, project, pid, session_id)
    return session_id


def end_session(conn, *, session_id: int, source: str = "") -> bool:
    """Mark a session as ended. Returns True if transitioned, False if
    already ended or not found."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE active_sessions
            SET status = 'ended', ended_at = NOW()
            WHERE id = %s AND status = 'active'
            """,
            (session_id,),
        )
        changed = cur.rowcount > 0
        if changed:
            _audit(cur, "END", "session", session_id, {"source": source}, source)
    conn.commit()
    if changed:
        log.info("end_session: id=%d ended by source=%s", session_id, source)
    return changed


def update_active_task(conn, *, session_id: int, task: str) -> bool:
    """Update current_task + bump heartbeat_at for a live session."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE active_sessions
            SET current_task = %s, heartbeat_at = NOW()
            WHERE id = %s AND status = 'active'
            """,
            (task or None, session_id),
        )
        changed = cur.rowcount > 0
    conn.commit()
    return changed


def list_active_sessions(conn, *, project: str = "", exclude_id: int | None = None) -> list[dict]:
    """Return all live sessions, optionally filtered to a project and
    excluding a given id (typically the caller's own session)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        sql = """
            SELECT id, source, project, cwd, pid, host, current_task,
                   started_at, heartbeat_at, status, pid_create_time, metadata
            FROM active_sessions
            WHERE status = 'active'
        """
        params: list[Any] = []
        if project:
            sql += " AND project = %s"
            params.append(project)
        if exclude_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_id)
        sql += " ORDER BY started_at DESC"
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def write_handoff(conn, *, source: str, content: str, project: str = "",
                  session_id: int | None = None) -> int:
    """Write a handoff note. Returns the handoff id. Content is hard-
    capped to 2000 chars on write to prevent boot payload blowup."""
    content = content.strip()[:2000]
    if not content:
        raise ValueError("handoff content is empty")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO handoffs (session_id, source, project, content)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (session_id, source, project, content),
        )
        hid = cur.fetchone()[0]
        _audit(cur, "INSERT", "handoff", hid,
               {"source": source, "project": project, "content_preview": content[:100]}, source)
    conn.commit()
    log.info("write_handoff: id=%d source=%s project=%s (%d chars)",
             hid, source, project, len(content))
    return hid


def get_latest_handoff(conn, *, project: str = "",
                       exclude_session_id: int | None = None) -> dict | None:
    """Return the most recent handoff for a project, optionally excluding
    one session's own handoffs (so boot doesn't echo back the current
    session's last handoff)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        sql = """
            SELECT id, session_id, source, project, content, created_at
            FROM handoffs
            WHERE (project = %s OR project = '')
        """
        params: list[Any] = [project]
        if exclude_session_id is not None:
            sql += " AND (session_id IS NULL OR session_id <> %s)"
            params.append(exclude_session_id)
        sql += " ORDER BY created_at DESC LIMIT 1"
        cur.execute(sql, params)
        row = cur.fetchone()
    return dict(row) if row else None


# ── ACTION ITEMS ─────────────────────────────────────────────────────
def create_action_item(conn, *, source_kind: str, source_id: int,
                       text: str, project: str = "",
                       kind: str = "task") -> int:
    """Create a pending action item linked to a memory.

    kind ∈ {'task', 'rule'}:
      - 'task' (default): one-shot obligation; 'already_done' is valid.
      - 'rule':           ongoing discipline; cannot be 'already_done'
                          (rules don't complete). Agents must use
                          'not_relevant' + reason to bypass — audited.
    (v2.2.0+)
    """
    if kind not in ("task", "rule"):
        raise ValueError(f"kind must be 'task' or 'rule', got {kind!r}")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO action_items (source_kind, source_id, text, project, kind)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (source_kind, source_id, text, project, kind),
        )
        aid = cur.fetchone()[0]
        _audit(cur, "INSERT", "action_item", aid,
               {"source_kind": source_kind, "source_id": source_id,
                "text": text[:100], "kind": kind}, "system")
    conn.commit()
    log.info("action_item created id=%d kind=%s for %s:%d: %s",
             aid, kind, source_kind, source_id, text[:60])
    return aid


def get_pending_action_items(conn, *, project: str = "") -> list[dict]:
    """Return all pending (unacknowledged) action items for a project.
    Each row includes `kind` (v2.2.0+)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, source_kind, source_id, text, project, kind, created_at
            FROM action_items
            WHERE status = 'pending'
              AND (project = %s OR project = '')
            ORDER BY created_at ASC
            """,
            (project,),
        )
        return [dict(r) for r in cur.fetchall()]


def acknowledge_action_item(conn, *, item_id: int, decision: str,
                            source: str = "", reason: str = "") -> dict:
    """Acknowledge a pending action item. Returns the updated item.

    decision must be one of: 'will_execute', 'already_done', 'not_relevant'.
    reason is required for 'already_done' and 'not_relevant'.

    v2.2.0: rule-kind items reject 'already_done' (rules don't complete).
    Forces agents to either commit ('will_execute') or explicitly bypass
    with a justification ('not_relevant' + reason) — auditable.
    """
    valid_decisions = ("will_execute", "already_done", "not_relevant")
    if decision not in valid_decisions:
        raise ValueError(f"decision must be one of {valid_decisions}, got {decision!r}")
    if decision in ("already_done", "not_relevant") and not reason:
        raise ValueError(f"reason is required for decision={decision!r}")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, status, text, kind FROM action_items WHERE id = %s", (item_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"action_item id={item_id} not found")
        if row["status"] != "pending":
            # Idempotent: already acked
            return {"success": True, "id": item_id, "status": row["status"],
                    "text": row["text"], "kind": row["kind"],
                    "already_acked": True}

        # v2.2.0 — block the already_done loophole for ongoing rules.
        if row["kind"] == "rule" and decision == "already_done":
            raise ValueError(
                "rule-kind action items cannot be 'already_done' — rules "
                "don't complete. Use 'will_execute' to commit, or "
                "'not_relevant' + reason to explicitly bypass (audited)."
            )

        cur.execute(
            """
            UPDATE action_items
            SET status = %s, ack_reason = %s, ack_source = %s, ack_at = NOW()
            WHERE id = %s
            """,
            (decision, reason, source, item_id),
        )
        _audit(cur, "ACK", "action_item", item_id,
               {"decision": decision, "reason": reason, "kind": row["kind"]}, source)
    conn.commit()
    log.info("action_item id=%d kind=%s acknowledged: %s (source=%s)",
             item_id, row["kind"], decision, source)
    return {"success": True, "id": item_id, "status": decision,
            "text": row["text"], "kind": row["kind"],
            "already_acked": False}


def count_pending_action_items(conn, *, project: str = "") -> int:
    """Return count of pending action items for a project."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM action_items WHERE status = 'pending' "
            "AND (project = %s OR project = '')",
            (project,),
        )
        return cur.fetchone()[0]


# ── CAPTURE CONTEXT ──────────────────────────────────────────────────
def capture_context(conn, *, context: str, source: str = "",
                    project: str = "") -> list[dict]:
    """Auto-decompose raw text into typed atomic memories and store each.

    Returns a list of dicts, one per stored memory, with keys:
        kind, id, headline, action ('stored' | 'duplicate')

    Uses heuristic classification (no metadata LLM) per
    infra-cost-addendum: the metadata LLM stays out of the write path.
    Each chunk routes through the typed remember_* functions which run
    the full write gate.
    """
    from .decompose import decompose

    chunks = decompose(context)
    results: list[dict] = []

    for chunk in chunks:
        try:
            if chunk.kind == "rule":
                mem = remember_rule(
                    conn, headline=chunk.headline, body=chunk.text,
                    severity=chunk.severity or "PATTERN",
                    project=project, source=source,
                )
            elif chunk.kind == "incident":
                mem = remember_incident(
                    conn, headline=chunk.headline, body=chunk.text,
                    project=project, source=source,
                )
            elif chunk.kind == "task":
                mem = remember_task(
                    conn, content=chunk.text,
                    project=project, source=source,
                )
            else:  # fact
                mem = remember_fact(
                    conn, headline=chunk.headline, body=chunk.text,
                    project=project, source=source,
                )

            if isinstance(mem, DuplicateHit):
                results.append({
                    "kind": chunk.kind,
                    "id": mem.existing_id,
                    "headline": mem.existing_headline,
                    "action": "duplicate",
                    "similarity": round(mem.similarity, 4),
                })
                log.info("capture_context: duplicate %s (id=%d, sim=%.3f): %s",
                         chunk.kind, mem.existing_id, mem.similarity, chunk.headline[:60])
            else:
                results.append({
                    "kind": mem.kind,
                    "id": mem.id,
                    "headline": mem.headline,
                    "action": "stored",
                })
                log.info("capture_context: stored %s id=%d: %s",
                         mem.kind, mem.id, mem.headline[:60])

        except WriteGateError as exc:
            log.warning("capture_context: write gate rejected chunk (%s): %s",
                        chunk.kind, exc)
            results.append({
                "kind": chunk.kind,
                "headline": chunk.headline,
                "action": "rejected",
                "reason": str(exc),
            })
        except Exception as exc:
            log.error("capture_context: failed to store chunk (%s): %s",
                      chunk.kind, exc)
            results.append({
                "kind": chunk.kind,
                "headline": chunk.headline,
                "action": "error",
                "reason": str(exc),
            })

    log.info("capture_context: %d chunks processed, %d stored, %d duplicates, %d rejected/errors",
             len(results),
             sum(1 for r in results if r["action"] == "stored"),
             sum(1 for r in results if r["action"] == "duplicate"),
             sum(1 for r in results if r["action"] in ("rejected", "error")))
    return results


# ── ANNOTATE ─────────────────────────────────────────────────────────
def annotate(conn, *, kind: str, memory_id: int,
             note: str | None = None, clear: bool = False) -> dict[str, Any]:
    """Attach or clear a persistent note on a memory. Mirrors v1's
    annotate semantics exactly.

    Modes:
      - clear=True:  remove the annotation
      - note=None + clear=False: read-only, return current annotation
      - note="..." + clear=False: set the annotation

    Raises ValueError if the memory is not found in memory_index.
    """
    check_kind(kind)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT annotation, headline FROM memory_index "
            "WHERE kind = %s AND memory_id = %s",
            (kind, memory_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"{kind} id={memory_id} not found")

        if clear:
            cur.execute(
                "UPDATE memory_index SET annotation = NULL "
                "WHERE kind = %s AND memory_id = %s",
                (kind, memory_id),
            )
            _audit(cur, "ANNOTATE_CLEAR", kind, memory_id, {}, "")
            conn.commit()
            log.info("annotate: cleared %s id=%d", kind, memory_id)
            return {"kind": kind, "memory_id": memory_id, "annotation": None,
                    "cleared": True}

        if note is None:
            # Read-only
            return {"kind": kind, "memory_id": memory_id,
                    "annotation": row["annotation"] or "",
                    "headline": row["headline"]}

        # Set
        cur.execute(
            "UPDATE memory_index SET annotation = %s "
            "WHERE kind = %s AND memory_id = %s",
            (note, kind, memory_id),
        )
        _audit(cur, "ANNOTATE_SET", kind, memory_id,
               {"note_preview": note[:100]}, "")
    conn.commit()
    log.info("annotate: set %s id=%d (%d chars)", kind, memory_id, len(note))
    return {"kind": kind, "memory_id": memory_id, "annotation": note,
            "cleared": False}


# ── RATE ─────────────────────────────────────────────────────────────
def rate(conn, *, kind: str, memory_id: int, direction: str) -> dict[str, Any]:
    """Upvote or downvote a memory. Mirrors v1's rate semantics.

    Args:
        direction: 'up' or 'down' (any other value raises ValueError).

    Returns {kind, memory_id, upvotes, downvotes, score}.
    Raises ValueError if the memory is not found.
    """
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    check_kind(kind)

    col = "upvotes" if direction == "up" else "downvotes"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"UPDATE memory_index SET {col} = {col} + 1 "
            "WHERE kind = %s AND memory_id = %s "
            "RETURNING upvotes, downvotes",
            (kind, memory_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"{kind} id={memory_id} not found")
        _audit(cur, "RATE", kind, memory_id, {"direction": direction}, "")
    conn.commit()
    log.info("rate: %s id=%d %s (now %d/%d)",
             kind, memory_id, direction, row["upvotes"], row["downvotes"])
    return {
        "kind": kind,
        "memory_id": memory_id,
        "upvotes": row["upvotes"],
        "downvotes": row["downvotes"],
        "score": row["upvotes"] - row["downvotes"],
    }


# ── PIN / UNPIN ──────────────────────────────────────────────────────
def set_pinned(conn, *, kind: str, memory_id: int, pinned: bool) -> dict[str, Any]:
    """Pin or unpin a memory. Mirrors v1's semantics:
      - Global memories (empty project) CANNOT be pinned; attempting to
        pin raises ValueError without state change.
      - Unpinning a global memory is always a no-op success.
    """
    check_kind(kind)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT project, pinned, headline FROM memory_index "
            "WHERE kind = %s AND memory_id = %s",
            (kind, memory_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"{kind} id={memory_id} not found")

        if pinned and not (row["project"] or "").strip():
            raise ValueError(
                "Cannot pin a global memory. Pinning only works for "
                "project-scoped memories. Re-store this memory with a "
                "project parameter first."
            )

        cur.execute(
            "UPDATE memory_index SET pinned = %s "
            "WHERE kind = %s AND memory_id = %s",
            (pinned, kind, memory_id),
        )
        _audit(cur, "PIN" if pinned else "UNPIN", kind, memory_id,
               {"project": row["project"]}, "")
    conn.commit()
    log.info("set_pinned: %s id=%d pinned=%s", kind, memory_id, pinned)
    return {
        "kind": kind,
        "memory_id": memory_id,
        "pinned": pinned,
        "project": row["project"],
        "headline": row["headline"],
    }


# ── FORGET ───────────────────────────────────────────────────────────
def forget(conn, *, kind: str, memory_id: int, reason: str = "",
           source: str = "") -> dict[str, Any]:
    """Soft-delete a memory. Deactivates the memory_index row and
    records who forgot it + why. Body is preserved in the typed table
    for audit + recall (with a forgotten banner).

    Idempotent: forgetting an already-forgotten memory is a no-op.
    Returns {kind, memory_id, already_forgotten: bool, forgotten_at}.
    """
    check_kind(kind)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT active, forgotten_at FROM memory_index "
            "WHERE kind = %s AND memory_id = %s",
            (kind, memory_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"{kind} id={memory_id} not found in memory_index")
        if row["forgotten_at"] is not None:
            return {
                "kind": kind, "memory_id": memory_id,
                "already_forgotten": True,
                "forgotten_at": str(row["forgotten_at"]),
            }

        cur.execute(
            """
            UPDATE memory_index
            SET active = FALSE,
                forgotten_at = NOW(),
                forgotten_reason = %s,
                forgotten_by = %s
            WHERE kind = %s AND memory_id = %s
            RETURNING forgotten_at
            """,
            (reason or None, source or None, kind, memory_id),
        )
        forgotten_at = cur.fetchone()["forgotten_at"]
        _audit(cur, "FORGET", kind, memory_id,
               {"reason": reason, "source": source}, source)
    conn.commit()
    log.info("forget: %s id=%d by=%s reason=%s", kind, memory_id, source, reason[:60])
    return {
        "kind": kind, "memory_id": memory_id,
        "already_forgotten": False,
        "forgotten_at": str(forgotten_at),
    }


# ── FORGET MANY ──────────────────────────────────────────────────────
def forget_many(conn, *, items: list[dict], reason: str = "",
                source: str = "") -> dict[str, Any]:
    """Batch soft-delete. `items` is a list of {kind, memory_id} dicts.

    Returns a summary:
      {
        "forgotten": [(kind, memory_id), ...],         # newly forgotten
        "already_forgotten": [(kind, memory_id), ...], # idempotent hits
        "not_found": [(kind, memory_id), ...],         # invalid targets
        "forgotten_count": int,
        "total_requested": int,
      }

    Partial success allowed: failing one item does NOT abort the batch.
    """
    forgotten: list[tuple[str, int]] = []
    already: list[tuple[str, int]] = []
    not_found: list[tuple[str, int]] = []

    for item in items or []:
        kind = (item or {}).get("kind")
        memory_id = (item or {}).get("memory_id")
        if not kind or memory_id is None:
            continue
        try:
            result = forget(conn, kind=kind, memory_id=int(memory_id),
                            reason=reason, source=source)
            if result.get("already_forgotten"):
                already.append((kind, int(memory_id)))
            else:
                forgotten.append((kind, int(memory_id)))
        except ValueError:
            not_found.append((kind, int(memory_id)))
        except Exception as exc:
            log.error("forget_many: unexpected error on %s:%s — %s",
                      kind, memory_id, exc)
            not_found.append((kind, int(memory_id)))

    return {
        "forgotten": forgotten,
        "already_forgotten": already,
        "not_found": not_found,
        "forgotten_count": len(forgotten),
        "total_requested": len(items or []),
    }


# ── UNSUPERSEDE ──────────────────────────────────────────────────────
def unsupersede_rule(conn, *, old_id: int, source: str = "",
                     keep_corrector: bool = False) -> dict[str, Any]:
    """Reverse a supersession on a rule. Clears superseded_by on the original
    rule, restores its severity, and reactivates its memory_index row.

    Invariant restored: a superseded rule and its corrector are never both
    active. supersede_rule is a two-part act (retire old + activate new), so a
    true reverse must undo BOTH halves. By default (keep_corrector=False) the
    corrector is retired (its memory_index row deactivated in the SAME
    transaction — symmetric with how supersede retires the old rule, and atomic:
    no separate commit). Pass keep_corrector=True to leave the corrector active
    (both-active), e.g. when re-evaluating.

    Only the ACTIVE HEAD of a chain can be unsuperseded: if the corrector is
    itself superseded (mid-chain), this raises — undoing a mid-chain supersession
    cannot produce a single clean state.

    Returns {old_id, former_corrector, corrector_retired, restored_severity, note}.
    Raises ValueError if the rule was not superseded, or the corrector is mid-chain.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT superseded_by, severity FROM rules WHERE id = %s",
            (old_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"rule id={old_id} not found")
        if row["superseded_by"] is None:
            raise ValueError(
                f"rule id={old_id} was not superseded; nothing to undo."
            )
        former_corrector = row["superseded_by"]

        # The corrector must be the active head (not itself superseded), or the
        # undo can't yield a single clean state (would leave the original AND the
        # chain tail both active). Refuse mid-chain undo.
        cur.execute(
            "SELECT severity, superseded_by FROM rules WHERE id = %s",
            (former_corrector,),
        )
        corr_row = cur.fetchone()
        if corr_row and corr_row["superseded_by"] is not None and not keep_corrector:
            raise ValueError(
                f"rule id={old_id}'s corrector ({former_corrector}) is itself "
                f"superseded (mid-chain); unsupersede the chain head first."
            )

        restored_severity = "PATTERN"
        if corr_row and corr_row["severity"] in ("BLOCKER", "PATTERN"):
            restored_severity = corr_row["severity"]

        # Clear supersession + restore severity on the original
        cur.execute(
            """
            UPDATE rules
            SET superseded_by = NULL,
                supersede_reason = NULL,
                severity = %s
            WHERE id = %s
            """,
            (restored_severity, old_id),
        )
        # Reactivate the original's memory_index row
        cur.execute(
            """
            UPDATE memory_index
            SET active = TRUE, severity = %s
            WHERE kind = 'rule' AND memory_id = %s
            """,
            (restored_severity, old_id),
        )

        corrector_retired = False
        if not keep_corrector:
            # Retire the corrector INLINE (same transaction, no separate commit)
            # — mirrors supersede_rule's _index_deactivate of the old rule.
            _index_deactivate(cur, "rule", former_corrector)
            corrector_retired = True

        _audit(cur, "UNSUPERSEDE", "rule", old_id,
               {"former_corrector": former_corrector,
                "restored_severity": restored_severity,
                "corrector_retired": corrector_retired},
               source)
    conn.commit()
    log.info("unsupersede_rule: id=%d former_corrector=%d severity=%s retired=%s",
             old_id, former_corrector, restored_severity, corrector_retired)
    note = (f"Original {old_id} is active; corrector {former_corrector} retired "
            f"(single-active state restored)." if corrector_retired
            else f"Both {old_id} and corrector {former_corrector} are active "
                 f"(keep_corrector=True).")
    return {
        "old_id": old_id,
        "former_corrector": former_corrector,
        "corrector_retired": corrector_retired,
        "restored_severity": restored_severity,
        "note": note,
    }


# ── PRUNE ────────────────────────────────────────────────────────────
def prune(conn, *, days: int, min_access: int = 0,
          dry_run: bool = True) -> dict[str, Any]:
    """HARD delete of stale memories with v1 safeguards.

    Criteria:
      - memory_index.created_at < NOW() - INTERVAL '{days} days'
      - memory_index.pinned = FALSE
      - kind != 'rule' (rules are immutable in v2 design)
      - For facts: access_count <= min_access
      - For incidents: archived = TRUE (must be maintenance-archived first)
      - For tasks: status IN ('done', 'stale')

    Safeguards:
      - days must be >= PRUNE_MIN_DAYS (default 30). ValueError if below.
      - Hard cap of PRUNE_MAX_DELETE (default 50) rows per call.
      - dry_run defaults to True. Must be explicitly False to actually delete.

    Returns {dry_run, eligible_count, deleted, deleted_ids: {kind:[ids]}}.
    """
    from .config import PRUNE_MIN_DAYS, PRUNE_MAX_DELETE

    if days < PRUNE_MIN_DAYS:
        raise ValueError(
            f"Refusing to prune: days={days} is below the hard minimum "
            f"of {PRUNE_MIN_DAYS}. This safeguard prevents accidental "
            "mass deletion."
        )

    # Build candidate list from memory_index joined against typed tables.
    # Using a single SELECT across LEFT JOINs keeps the caps honest.
    sql = """
        SELECT mi.kind, mi.memory_id
        FROM memory_index mi
        LEFT JOIN facts     f ON mi.kind = 'fact'     AND mi.memory_id = f.id
        LEFT JOIN incidents i ON mi.kind = 'incident' AND mi.memory_id = i.id
        LEFT JOIN tasks     t ON mi.kind = 'task'     AND mi.memory_id = t.id
        WHERE mi.created_at < NOW() - make_interval(days => %s)
          AND mi.pinned = FALSE
          AND mi.kind != 'rule'
          AND (
                (mi.kind = 'fact'     AND f.access_count <= %s)
             OR (mi.kind = 'incident' AND i.archived = TRUE)
             OR (mi.kind = 'task'     AND t.status IN ('done', 'stale'))
          )
        ORDER BY mi.created_at ASC
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (int(days), int(min_access), PRUNE_MAX_DELETE))
        candidates = cur.fetchall()

    eligible_count = len(candidates)
    deleted_ids: dict[str, list[int]] = {"fact": [], "incident": [], "task": []}

    if not dry_run and eligible_count == 0:
        # Real execution but nothing to delete — return the
        # consistent "deleted=0" shape instead of the dry-run shape.
        return {
            "dry_run": False,
            "deleted": 0,
            "deleted_ids": deleted_ids,
            "max_per_call": PRUNE_MAX_DELETE,
            "message": "Nothing to prune — no memories matched the criteria.",
        }

    if dry_run:
        # Count how many more there'd be if we lifted the cap (for the
        # operator to know how many calls they'd need). COUNT(*) instead
        # of fetchall() to avoid loading all rows into Python memory.
        count_sql = """
            SELECT COUNT(*)
            FROM memory_index mi
            LEFT JOIN facts     f ON mi.kind = 'fact'     AND mi.memory_id = f.id
            LEFT JOIN incidents i ON mi.kind = 'incident' AND mi.memory_id = i.id
            LEFT JOIN tasks     t ON mi.kind = 'task'     AND mi.memory_id = t.id
            WHERE mi.created_at < NOW() - make_interval(days => %s)
              AND mi.pinned = FALSE
              AND mi.kind != 'rule'
              AND (
                    (mi.kind = 'fact'     AND f.access_count <= %s)
                 OR (mi.kind = 'incident' AND i.archived = TRUE)
                 OR (mi.kind = 'task'     AND t.status IN ('done', 'stale'))
              )
        """
        with conn.cursor() as cur:
            cur.execute(count_sql, (int(days), int(min_access)))
            would_total = cur.fetchone()[0]
        result = {
            "dry_run": True,
            "would_delete_this_call": eligible_count,
            "would_delete_total": would_total,
            "max_per_call": PRUNE_MAX_DELETE,
            "criteria": f"older than {days} days, pinned=False, kind in "
                        f"(fact[access<={min_access}], incident[archived], "
                        "task[done|stale])",
            "message": f"Would delete {eligible_count} of {would_total} "
                       f"eligible memories this call (cap {PRUNE_MAX_DELETE}). "
                       "Set dry_run=False to execute.",
        }
        return result

    # Real delete. Audit BEFORE the row goes away.
    with conn.cursor() as cur:
        for kind, memory_id in candidates:
            _audit(cur, "PRUNE", kind, memory_id,
                   {"days": days, "min_access": min_access}, "")
            deleted_ids[kind].append(memory_id)

        # Delete from memory_index first (FK reference safety)
        for kind, memory_id in candidates:
            cur.execute(
                "DELETE FROM memory_index WHERE kind = %s AND memory_id = %s",
                (kind, memory_id),
            )
        # Delete from typed tables
        for kind in ("fact", "incident", "task"):
            if deleted_ids[kind]:
                tbl = {"fact": "facts", "incident": "incidents", "task": "tasks"}[kind]
                cur.execute(
                    f"DELETE FROM {tbl} WHERE id = ANY(%s)",
                    (deleted_ids[kind],),
                )
    conn.commit()
    total_deleted = sum(len(v) for v in deleted_ids.values())
    log.warning("prune: HARD DELETED %d memories (days=%d, min_access=%d): %s",
                total_deleted, days, min_access, deleted_ids)
    return {
        "dry_run": False,
        "deleted": total_deleted,
        "deleted_ids": deleted_ids,
        "max_per_call": PRUNE_MAX_DELETE,
        "message": f"Pruned {total_deleted} stale memories (max "
                   f"{PRUNE_MAX_DELETE} per call).",
    }


# ── STATS ────────────────────────────────────────────────────────────
def stats(conn, *, project: str = "") -> dict[str, Any]:
    """Return corpus stats.

    If project is non-empty, stats are scoped to that project (plus
    global/empty-project rows). If project is empty, returns global stats.
    """
    proj_filter = "project = %s OR project = ''" if project else "TRUE"
    params = (project,) if project else ()

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Totals by kind (active + all)
        cur.execute(
            f"""
            SELECT kind,
                   COUNT(*)                                 AS total,
                   SUM(CASE WHEN active THEN 1 ELSE 0 END)  AS active,
                   SUM(CASE WHEN forgotten_at IS NOT NULL THEN 1 ELSE 0 END) AS forgotten
            FROM memory_index
            WHERE {proj_filter}
            GROUP BY kind
            ORDER BY kind
            """,
            params,
        )
        by_kind = {r["kind"]: {
            "total": int(r["total"]),
            "active": int(r["active"] or 0),
            "forgotten": int(r["forgotten"] or 0),
        } for r in cur.fetchall()}

        # Rule severity breakdown (active only)
        cur.execute(
            f"""
            SELECT severity, COUNT(*) AS n
            FROM memory_index
            WHERE kind = 'rule' AND active AND ({proj_filter})
            GROUP BY severity
            """,
            params,
        )
        rule_by_severity = {r["severity"]: int(r["n"]) for r in cur.fetchall()}

        # Task status breakdown
        if project:
            cur.execute(
                "SELECT status, COUNT(*) AS n FROM tasks "
                "WHERE project = %s OR project = '' GROUP BY status",
                params,
            )
        else:
            cur.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
        task_by_status = {r["status"]: int(r["n"]) for r in cur.fetchall()}

        # Incident archive
        if project:
            cur.execute(
                "SELECT archived, COUNT(*) AS n FROM incidents "
                "WHERE project = %s OR project = '' GROUP BY archived",
                params,
            )
        else:
            cur.execute("SELECT archived, COUNT(*) AS n FROM incidents GROUP BY archived")
        inc = {r["archived"]: int(r["n"]) for r in cur.fetchall()}

        # Pending action items
        if project:
            cur.execute(
                "SELECT COUNT(*) AS n FROM action_items "
                "WHERE status = 'pending' AND (project = %s OR project = '')",
                params,
            )
        else:
            cur.execute("SELECT COUNT(*) AS n FROM action_items WHERE status = 'pending'")
        pending_action_items = int(cur.fetchone()["n"])

        # Active sessions (project-scoped if requested)
        if project:
            cur.execute(
                "SELECT COUNT(*) AS n FROM active_sessions "
                "WHERE status = 'active' AND project = %s",
                params,
            )
        else:
            cur.execute("SELECT COUNT(*) AS n FROM active_sessions WHERE status = 'active'")
        active_sessions = int(cur.fetchone()["n"])

        # Correction stickiness (report-only signal).
        # A rule LINEAGE superseded repeatedly = a correction that keeps needing
        # revision, i.e. it is not settling. We measure lineage depth by walking
        # rules.supersedes from each chain HEAD (superseded_by IS NULL) back to its
        # root. supersede_rule refuses to re-supersede a superseded rule
        # (chain guard), so chains are linear; a depth cap guards against any
        # corrupted pointer. This is a signal to inspect, NOT a verdict — a rule
        # may be revised repeatedly for good reasons.
        # Caveat: rules.supersedes/superseded_by are ON DELETE SET NULL, so a
        # HARD-deleted ancestor (not the sanctioned supersede path) truncates or
        # splits a lineage and UNDER-reports its depth. Accept the undercount —
        # this is advisory, and hard-deleting rules is not a normal path.
        # "revisions" counts VERSIONS in the lineage (N versions = N-1 supersessions).
        rule_proj = "r.project = %s OR r.project = ''" if project else "TRUE"
        cur.execute(
            f"""
            WITH RECURSIVE chain AS (
                -- heads: current (non-superseded) rules
                SELECT r.id AS head_id, r.id AS cur_id, r.headline, r.project,
                       1 AS depth
                FROM rules r
                WHERE r.superseded_by IS NULL AND ({rule_proj})
              UNION ALL
                SELECT c.head_id, r.supersedes AS cur_id, c.headline, c.project,
                       c.depth + 1
                FROM chain c
                JOIN rules r ON r.id = c.cur_id
                WHERE r.supersedes IS NOT NULL
                  AND c.depth < 50        -- hard depth cap / cycle guard
            )
            SELECT head_id, headline, MAX(depth) AS revisions
            FROM chain
            GROUP BY head_id, headline
            HAVING MAX(depth) >= 2          -- superseded at least once
            ORDER BY revisions DESC
            LIMIT 10
            """,
            params,
        )
        revised = [
            {"head_rule_id": int(r["head_id"]), "headline": r["headline"],
             "revisions": int(r["revisions"])}
            for r in cur.fetchall()
        ]
        correction_stickiness = {
            "revised_lineages": revised,      # top rules that keep being revised
            "revised_lineage_count": len(revised),
        }

    return {
        "project": project or "(global)",
        "by_kind": by_kind,
        "rules_by_severity": rule_by_severity,
        "tasks_by_status": task_by_status,
        "incidents_archived": int(inc.get(True, 0)),
        "incidents_active": int(inc.get(False, 0)),
        "pending_action_items": pending_action_items,
        "active_sessions": active_sessions,
        "correction_stickiness": correction_stickiness,
    }


# ── CONSOLIDATION CANDIDATES ─────────────────────────────────────────
def _maximal_cliques(adj: dict[int, set[int]], min_size: int = 2,
                     call_cap: int = 200_000) -> list[set[int]]:
    """All maximal cliques (size >= min_size) of an undirected graph, via
    Bron-Kerbosch with pivoting. Bounded by a recursion-breadth cap so a
    pathologically dense graph degrades gracefully instead of hanging.

    Extracted for direct unit testing (greedy grow-from-node is INCOMPLETE — it
    silently misses ~1/3 of maximal cliques; see brain rule 669)."""
    cliques: list[set[int]] = []
    calls = [0]

    def _bk(R: set[int], P: set[int], X: set[int]) -> None:
        calls[0] += 1
        if calls[0] > call_cap:
            return
        if not P and not X:
            if len(R) >= min_size:
                cliques.append(set(R))
            return
        pivot = max(P | X, key=lambda u: len(adj.get(u, set()) & P))
        for v in list(P - adj.get(pivot, set())):
            nv = adj.get(v, set())
            _bk(R | {v}, P & nv, X & nv)
            P = P - {v}
            X = X | {v}

    _bk(set(), set(adj.keys()), set())
    return cliques


def consolidation_candidates(conn, *, project: str | None = None,
                             threshold: float = CONSOLIDATION_COSINE,
                             limit: int = 20) -> dict[str, Any]:
    """Read-only. Find CLIQUES of active rules that are mutually highly similar
    (every pair >= threshold) — candidates the agent should REVIEW and possibly
    consolidate by superseding them into one canonical rule. It does NOT merge
    anything.

    Cliques, not connected-components: every member is directly similar to every
    other member, so "these are the same rule" is defensible. (Connected
    components could chain unrelated rules through a hub — unsafe for a merge
    suggestion.) Each cluster surfaces headline + severity + pinned per member so
    the agent can judge; cosine is an imperfect proxy (a short opposite body may
    not move the combined headline+body embedding much), so the agent must read
    the bodies (via recall) before superseding, and should not merge across
    differing severity or retire a pinned rule blindly.

    Scope: same project + global when `project` is given; all active rules when
    `project` is None. Bounded: refuses above CONSOLIDATION_MAX_RULES active rules
    (the pairwise self-join is O(N^2) with no vector index).

    Returns {clusters: [{rule_ids, members:[{id,headline,severity,pinned}],
    size, max_similarity, min_similarity}], cluster_count, active_rules_scanned,
    note}. Raises ValueError if too many active rules to scan safely.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Count active rules in scope; refuse an unbounded O(N^2) scan.
        if project is not None:
            cur.execute(
                "SELECT COUNT(*) AS n FROM memory_index "
                "WHERE kind='rule' AND active AND embedding IS NOT NULL "
                "AND (project=%s OR project='')",
                (project,),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) AS n FROM memory_index "
                "WHERE kind='rule' AND active AND embedding IS NOT NULL"
            )
        n_rules = int(cur.fetchone()["n"])
        if n_rules > CONSOLIDATION_MAX_RULES:
            raise ValueError(
                f"{n_rules} active rules exceeds CONSOLIDATION_MAX_RULES "
                f"({CONSOLIDATION_MAX_RULES}); the O(N^2) scan would be too slow. "
                f"Scope to a project or raise the limit deliberately."
            )

        # Pairwise self-join. Distance computed ONCE in the CTE; NULL embeddings
        # excluded; a.memory_id < b.memory_id dedups pairs and drops self-pairs.
        proj_pred = ""
        params: list = [threshold]
        if project is not None:
            proj_pred = ("AND (a.project=%s OR a.project='') "
                         "AND (b.project=%s OR b.project='')")
            params = [project, project, threshold]
        cur.execute(
            f"""
            WITH pairs AS (
                SELECT a.memory_id AS a_id, b.memory_id AS b_id,
                       1 - (a.embedding <=> b.embedding) AS sim
                FROM memory_index a
                JOIN memory_index b
                  ON a.kind='rule' AND b.kind='rule'
                 AND a.active AND b.active
                 AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
                 AND a.memory_id < b.memory_id
                 {proj_pred}
            )
            SELECT a_id, b_id, sim FROM pairs WHERE sim >= %s
            """,
            tuple(params),
        )
        pair_rows = cur.fetchall()

        # Build the similarity graph (adjacency + pair-sim lookup).
        adj: dict[int, set[int]] = {}
        pair_sim: dict[tuple[int, int], float] = {}
        for r in pair_rows:
            a, b, s = int(r["a_id"]), int(r["b_id"]), float(r["sim"])
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
            pair_sim[(min(a, b), max(a, b))] = s

        # All MAXIMAL cliques (>=2 members). Bron-Kerbosch, not greedy (greedy is
        # incomplete — see brain rule 669). N bounded by CONSOLIDATION_MAX_RULES.
        clusters = _maximal_cliques(adj, min_size=2)

        # Fetch member metadata (headline, severity, pinned) for all clustered ids.
        all_ids = sorted({i for c in clusters for i in c})
        meta: dict[int, dict] = {}
        if all_ids:
            cur.execute(
                "SELECT memory_id, headline, severity, pinned FROM memory_index "
                "WHERE kind='rule' AND memory_id = ANY(%s)",
                (all_ids,),
            )
            for r in cur.fetchall():
                meta[int(r["memory_id"])] = {
                    "id": int(r["memory_id"]), "headline": r["headline"],
                    "severity": r["severity"], "pinned": r["pinned"],
                }

    # Build output; each clique's sims are all its member pairs.
    out = []
    for c in clusters:
        ids = sorted(c)
        sims = [pair_sim[(min(a, b), max(a, b))]
                for i, a in enumerate(ids) for b in ids[i + 1:]]
        out.append({
            "rule_ids": ids,
            "members": [meta[i] for i in ids if i in meta],
            "size": len(ids),
            "max_similarity": round(max(sims), 4) if sims else None,
            "min_similarity": round(min(sims), 4) if sims else None,
        })
    # Tightest (highest min_similarity) first — safest to merge.
    out.sort(key=lambda x: (x["min_similarity"] or 0), reverse=True)
    out = out[:limit]

    return {
        "clusters": out,
        "cluster_count": len(out),
        "active_rules_scanned": n_rules,
        "note": ("Each cluster is a set of active rules that are ALL mutually "
                 "similar (candidates to consolidate). Before superseding, recall "
                 "and READ the bodies — cosine is an imperfect proxy and may group "
                 "rules that encode distinct nuances or opposite polarity. Do not "
                 "merge across differing severity, and do not retire a pinned rule "
                 "without cause."),
    }


# ── LIST RECENT ──────────────────────────────────────────────────────
def list_recent(conn, *, limit: int = 20, days: int = 0,
                kind: str | None = None, project: str | None = None,
                include_forgotten: bool = False) -> list[dict]:
    """Return recent memory_index entries (headline-only), newest first.

    Args:
        limit: max rows to return (default 20, hard-cap 200 server-side).
        days: only include rows created in the last N days (0 = no limit).
        kind: filter by memory kind (rule/fact/incident/task).
        project: filter by project (empty-string project means global).
        include_forgotten: include forgotten rows (default False).
    """
    limit = max(1, min(int(limit), 200))
    where = ["1=1"]
    params: list[Any] = []
    if not include_forgotten:
        where.append("forgotten_at IS NULL")
    if kind:
        check_kind(kind)
        where.append("kind = %s")
        params.append(kind)
    if project is not None:
        where.append("(project = %s OR project = '')")
        params.append(project)
    if days and days > 0:
        where.append("created_at >= NOW() - make_interval(days => %s)")
        params.append(int(days))
    params.append(limit)

    sql = f"""
        SELECT kind, memory_id, project, headline, severity, active,
               forgotten_at, created_at
        FROM memory_index
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
        LIMIT %s
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    # Normalize datetimes to ISO strings for JSON transport
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = str(d["created_at"])
        if d.get("forgotten_at"):
            d["forgotten_at"] = str(d["forgotten_at"])
        out.append(d)
    return out


# ── TOOL EVENTS (observability) ─────────────────────────────────────
def log_tool_event(
    conn,
    *,
    event_id: str,
    tool_name: str,
    session_id: int,
    project: str = "",
    source: str = "",
    duration_ms: int | None = None,
    success: bool = True,
    error_msg: str | None = None,
    occurred_at: str | None = None,
) -> None:
    """Persist one tool_events row. ON CONFLICT DO NOTHING makes re-flush safe. Never raises."""
    try:
        cols = "(event_id, session_id, tool_name, project, source, duration_ms, success, error_msg"
        vals: list[Any] = [event_id, session_id, tool_name, project or "", source or "",
                           duration_ms, success, error_msg]
        if occurred_at:
            cols += ", occurred_at"
            vals.append(occurred_at)
        cols += ")"
        placeholders = ", ".join(["%s"] * len(vals))
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO tool_events {cols} VALUES ({placeholders}) "
                f"ON CONFLICT (event_id) DO NOTHING",
                vals,
            )
        conn.commit()
    except Exception as exc:
        log.warning("log_tool_event: failed to persist: %s", exc)
