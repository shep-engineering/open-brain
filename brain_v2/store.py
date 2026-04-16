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

from .config import DATABASE_URL, DUPLICATE_COSINE_THRESHOLD
from .embedding import embed_to_pgvector
from .write_gate import WriteGateError, run_gate, check_kind


# ── Connection helper ───────────────────────────────────────────────
def connect():
    return psycopg2.connect(DATABASE_URL)


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
                  project: str = "", source: str = "", pinned: bool = False) -> Memory | DuplicateHit:
    """Insert a new RULE. If a >0.75-cosine duplicate exists, DOES NOT
    insert; returns DuplicateHit so caller can route to supersede.
    Refuses to merge."""
    embedding_vec = embed_to_pgvector(f"{headline}. {body}")
    dup = run_gate(conn, kind="rule", headline=headline, body=body,
                   severity=severity, embedding_vec=embedding_vec)
    if dup is not None:
        return DuplicateHit(kind="rule", existing_id=dup.memory_id,
                            similarity=dup.similarity, existing_headline=dup.headline)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rules (headline, body, severity, project, source)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (headline, body, severity, project, source),
        )
        rid, created = cur.fetchone()
        _index_insert(cur, kind="rule", memory_id=rid, project=project,
                      headline=headline, severity=severity,
                      embedding_vec=embedding_vec, pinned=pinned)
        _audit(cur, "INSERT", "rule", rid,
               {"headline": headline, "severity": severity, "project": project}, source)
    conn.commit()
    return Memory(kind="rule", id=rid, headline=headline, body=body, project=project,
                  severity=severity, created_at=str(created))


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
    params: list[Any] = []
    if kind:
        check_kind(kind)
        where.append("kind = %s")
        params.append(kind)
    if project is not None:
        where.append("(project = %s OR project = '')")
        params.append(project)
    params += [embedding_vec, embedding_vec, limit]
    sql = f"""
        SELECT kind, memory_id, headline, severity, project,
               1 - (embedding <=> %s::vector) AS similarity
        FROM memory_index
        WHERE {' AND '.join(where)}
        ORDER BY embedding <=> %s::vector ASC
        LIMIT %s
    """
    # params ordering: where params first, then embedding for SELECT, then embedding for ORDER, then limit
    # need to re-layout:
    final_params: list[Any] = [embedding_vec]  # SELECT similarity
    if kind:
        final_params.append(kind)
    if project is not None:
        final_params.append(project)
    final_params.append(embedding_vec)  # ORDER BY
    final_params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, final_params)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


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
