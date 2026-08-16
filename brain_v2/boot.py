"""Boot payload builder — Windsurf synthesis §4.3.

Hard constraints:
  - 5 BLOCKER headlines max
  - Top 5 PATTERN headlines by task-relevance
  - 2,000 token total cap (truncate TASKs before BLOCKERs if over)
  - WORKING CONTEXT regenerated from task args (NOT stored)
  - No bodies. No incident narratives. No merged archaeology.
    Agent fetches bodies on demand via recall(kind, id).

If BLOCKERs + ACTIVE TASKs exceed budget, truncate TASKs first.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import psycopg2.extras

from .config import (
    BOOT_BLOCKER_COUNT_CAP,
    BOOT_HANDOFF_TOKEN_CAP,
    BOOT_PATTERN_COUNT_CAP,
    BOOT_TASK_COUNT_CAP,
    BOOT_TOKEN_CAP,
)
from .embedding import embed_to_pgvector

# Shared with V1. Host normalization, pid probing, staleness signal.
import session_liveness


# Crude token estimate: 1 token ~= 4 chars. Matches OpenAI's average for
# English prose well enough for a cap check.
def _tok(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class BootPayload:
    blockers: list[dict] = field(default_factory=list)
    patterns: list[dict] = field(default_factory=list)
    active_tasks: list[dict] = field(default_factory=list)
    working_context: dict = field(default_factory=dict)
    pending_action_items: list[dict] = field(default_factory=list)
    other_active_sessions: list[dict] = field(default_factory=list)
    session_id: int | None = None
    handoff: str = ""
    handoff_source: dict | None = None
    token_estimate: int = 0
    truncated: list[str] = field(default_factory=list)
    # v2.1.0: registry trust signal. None + True for empty sibling set
    # (nothing to distrust). Populated when register=True.
    registry_staleness_seconds: int | None = None
    registry_trustworthy: bool = True
    self_healed_ended_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blockers": self.blockers,
            "patterns": self.patterns,
            "active_tasks": self.active_tasks,
            "working_context": self.working_context,
            "pending_action_items": self.pending_action_items,
            "writes_blocked": len(self.pending_action_items) > 0,
            "other_active_sessions": self.other_active_sessions,
            "session_id": self.session_id,
            "handoff": self.handoff,
            "handoff_source": self.handoff_source,
            "token_estimate": self.token_estimate,
            "truncated": self.truncated,
            "registry_staleness_seconds": self.registry_staleness_seconds,
            "registry_trustworthy": self.registry_trustworthy,
            "self_healed_ended_ids": self.self_healed_ended_ids,
            "caps": {
                # blocker_fill_count = max UNPROTECTED blockers that ride along;
                # protected (pinned/always_on) blockers are always shown on top of
                # this, up to blocker_protected_ceiling. So len(blockers) can exceed
                # blocker_fill_count but never blocker_protected_ceiling.
                "blocker_fill_count": BOOT_BLOCKER_COUNT_CAP,
                "blocker_protected_ceiling": BOOT_BLOCKER_COUNT_CAP * 2,
                "pattern_count": BOOT_PATTERN_COUNT_CAP,
                "task_count": BOOT_TASK_COUNT_CAP,
                "token_total": BOOT_TOKEN_CAP,
                "handoff_tokens": BOOT_HANDOFF_TOKEN_CAP,
            },
        }


def _fetch_blockers(cur, project: str) -> list[dict]:
    """Fetch BLOCKER headlines for the boot payload.

    BLOCKERs are the safety tier ("never force-push", "never touch the live DB").
    A critical safety rule must never be silently evicted by the count cap just
    because a project accumulated more than BOOT_BLOCKER_COUNT_CAP blockers. So
    the fetch is two-part:

      1. PROTECTED — every active blocker that is pinned OR tagged
         skill_trigger->>'always_on'. These ALWAYS appear, bounded only by a
         hard safety ceiling (2x the cap) so a "pin everything" mistake cannot
         blow the token budget.
      2. FILL — the remaining (unpinned, non-always_on) blockers, taking only
         the slots left under BOOT_BLOCKER_COUNT_CAP after the protected set.

    The count cap therefore limits how many *unprotected extras* ride along; it
    can never drop a protected blocker. Within each set, project-scoped rules
    sort before global, then most-recent first. Blockers are deliberately NOT
    ranked by task relevance: a safety rule is relevant regardless of the task,
    and cosine ranking would demote (and past the cap, evict) exactly the rules
    that must always show.
    """
    protected_ceiling = BOOT_BLOCKER_COUNT_CAP * 2

    def _is_protected() -> str:
        return (
            "(pinned = TRUE OR "
            "COALESCE((skill_trigger->>'always_on')::boolean, FALSE) = TRUE)"
        )

    # 1. Protected blockers — always included, bounded by the safety ceiling.
    cur.execute(
        f"""
        SELECT kind, memory_id, headline, project
        FROM memory_index
        WHERE active = TRUE
          AND severity = 'BLOCKER'
          AND (project = %s OR project = '')
          AND {_is_protected()}
        ORDER BY
          CASE WHEN project = %s THEN 0 ELSE 1 END,
          created_at DESC
        LIMIT %s
        """,
        (project, project, protected_ceiling),
    )
    protected = [dict(r) for r in cur.fetchall()]

    # 2. Fill remaining slots with unprotected blockers.
    fill_slots = BOOT_BLOCKER_COUNT_CAP - len(protected)
    fill: list[dict] = []
    if fill_slots > 0:
        cur.execute(
            f"""
            SELECT kind, memory_id, headline, project
            FROM memory_index
            WHERE active = TRUE
              AND severity = 'BLOCKER'
              AND (project = %s OR project = '')
              AND NOT {_is_protected()}
            ORDER BY
              CASE WHEN project = %s THEN 0 ELSE 1 END,
              created_at DESC
            LIMIT %s
            """,
            (project, project, fill_slots),
        )
        fill = [dict(r) for r in cur.fetchall()]

    return protected + fill


def _fetch_patterns(cur, project: str, task_embedding: str | None) -> list[dict]:
    """Top PATTERN rules ranked by task-relevance (cosine).
    Returns [] immediately if task_embedding is None (no PATTERNs exist)."""
    if task_embedding is None:
        return []
    cur.execute(
        """
        SELECT kind, memory_id, headline, project,
               1 - (embedding <=> %s::vector) AS similarity
        FROM memory_index
        WHERE active = TRUE
          AND severity = 'PATTERN'
          AND (project = %s OR project = '')
          AND (skill_trigger IS NULL
               OR COALESCE((skill_trigger->>'always_on')::boolean, FALSE) = TRUE)
        ORDER BY embedding <=> %s::vector ASC
        LIMIT %s
        """,
        (task_embedding, project, task_embedding, BOOT_PATTERN_COUNT_CAP),
    )
    return [dict(r) for r in cur.fetchall()]


def _fetch_active_tasks(cur, project: str) -> list[dict]:
    cur.execute(
        """
        SELECT id, content, priority, status
        FROM tasks
        WHERE status IN ('open', 'blocked')
          AND (project = %s OR project = '')
        ORDER BY
          CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
          created_at DESC
        LIMIT %s
        """,
        (project, BOOT_TASK_COUNT_CAP),
    )
    return [dict(r) for r in cur.fetchall()]


def _make_working_context(task: str, project: str, source: str) -> dict:
    return {
        "task": task,
        "project": project,
        "source": source,
        "note": "This block is ephemeral — regenerated from the task arg at boot. "
                "It is NOT stored as a memory.",
    }


def build(conn, *, project: str, task: str, source: str, handoff: str = "",
          cwd: str = "", pid: int | None = None, host: str = "",
          register: bool = True,
          metadata: dict | None = None) -> BootPayload:
    """Build the v2 boot payload. Embeds `task` ONCE for pattern
    retrieval. No per-section re-embedding. Caps applied in order.

    If register=True (default), a new active_sessions row is written
    and the returned payload includes session_id + other_active_sessions.
    Set register=False for read-only boot inspections (tests, tooling).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        blockers = _fetch_blockers(cur, project)

        # Skip Ollama embedding call when no PATTERNs exist in the DB
        cur.execute(
            "SELECT 1 FROM memory_index WHERE active = TRUE AND severity = 'PATTERN' "
            "AND (project = %s OR project = '') LIMIT 1",
            (project,)
        )
        has_patterns = cur.fetchone() is not None
        task_embedding = (
            embed_to_pgvector(task if task.strip() else (project or "open-brain"))
            if has_patterns else None
        )
        patterns = _fetch_patterns(cur, project, task_embedding)
        tasks = _fetch_active_tasks(cur, project)

    # Fetch pending action items (no embedding needed — direct table query)
    from . import store as _store
    pending_items = _store.get_pending_action_items(conn, project=project)

    # Session registry: register new session, find siblings, fetch latest handoff
    session_id: int | None = None
    siblings: list[dict] = []
    handoff_source: dict | None = None
    # v2.1.0: opportunistic self-heal + staleness signal. Fail-soft so a
    # probe error never kills boot. Windsurf flagged that the outer
    # try/except in boot_session_v2 is too broad — this inner guard
    # keeps the rest of the boot payload alive even if the probe fails.
    registry_staleness_seconds: int | None = None
    registry_trustworthy: bool = True
    self_healed_ended_ids: list[int] = []
    if register and source:
        session_id = _store.register_session(
            conn, source=source, project=project, cwd=cwd,
            pid=pid, host=host, current_task=task,
            metadata=metadata,
        )
        try:
            siblings = _store.list_active_sessions(
                conn, project=project, exclude_id=session_id,
            )
            # Opportunistic same-host pid probe. Caps probe to 20 rows
            # to bound boot latency. Dead rows flip to 'ended' inline;
            # we filter them out of the returned siblings list so the
            # caller sees a clean view.
            my_host = session_liveness.normalize_host(host)
            if my_host and siblings:
                ended = session_liveness.probe_and_mark_ended(
                    conn, siblings, my_host,
                )
                if ended:
                    ended_set = set(ended)
                    siblings = [
                        s for s in siblings
                        if s.get("id") not in ended_set
                    ]
                    self_healed_ended_ids = list(ended)
            # Staleness signal: inform the caller whether the registry
            # data is recent enough to trust.
            registry_staleness_seconds, registry_trustworthy = (
                session_liveness.compute_staleness(siblings)
            )
        except Exception:
            # Keep boot alive on any probe/list failure. Siblings may
            # be empty or partial; staleness stays at defaults.
            pass
        # Auto-populate handoff from latest if caller didn't supply one
        if not handoff:
            latest = _store.get_latest_handoff(
                conn, project=project, exclude_session_id=session_id,
            )
            if latest:
                handoff = latest["content"]
                handoff_source = {
                    "handoff_id": latest["id"],
                    "source": latest["source"],
                    "created_at": str(latest["created_at"]),
                }

    payload = BootPayload(
        blockers=blockers,
        patterns=patterns,
        active_tasks=tasks,
        working_context=_make_working_context(task, project, source),
        pending_action_items=pending_items,
        other_active_sessions=siblings,
        session_id=session_id,
        handoff=handoff[: BOOT_HANDOFF_TOKEN_CAP * 4],  # hard char cut
        handoff_source=handoff_source,
        registry_staleness_seconds=registry_staleness_seconds,
        registry_trustworthy=registry_trustworthy,
        self_healed_ended_ids=self_healed_ended_ids,
    )

    # Token accounting
    def _size(p: BootPayload) -> int:
        return (
            sum(_tok(b["headline"]) for b in p.blockers)
            + sum(_tok(b["headline"]) for b in p.patterns)
            + sum(_tok(t["content"]) for t in p.active_tasks)
            + _tok(p.working_context.get("task", ""))
            + _tok(p.handoff)
        )

    # Per §4.3: if over budget, truncate TASKs before BLOCKERs.
    while _size(payload) > BOOT_TOKEN_CAP and payload.active_tasks:
        dropped = payload.active_tasks.pop()
        payload.truncated.append(f"task:{dropped['id']}")
    # Second pass: truncate patterns if still over
    while _size(payload) > BOOT_TOKEN_CAP and payload.patterns:
        dropped = payload.patterns.pop()
        payload.truncated.append(f"pattern:{dropped['kind']}:{dropped['memory_id']}")
    # Last resort: truncate blockers only if literally unavoidable
    while _size(payload) > BOOT_TOKEN_CAP and len(payload.blockers) > 1:
        dropped = payload.blockers.pop()
        payload.truncated.append(f"blocker:{dropped['kind']}:{dropped['memory_id']}")

    payload.token_estimate = _size(payload)
    return payload
