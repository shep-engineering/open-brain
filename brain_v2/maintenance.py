"""Background maintenance jobs for v2 — fact decay + incident archive.

Per Windsurf synthesis §4.6 + §6 Phase 3:
  - RULE: no decay. Supersede only.
  - FACT: Ebbinghaus access-based decay (score halves every 7 days
          without access). Hard TTL for time-sensitive facts.
  - INCIDENT: soft archive after 90 days no-access. Still searchable.
  - TASK: lifecycle state only (no decay).

Both jobs are inline SQL — no external scheduler required. Callers
invoke them explicitly via MCP tool or on a cron schedule.

Decay does NOT delete. It deactivates the `memory_index` row so the
memory stops appearing in boot/search. The body is preserved for
`recall()` and audit. Un-decay happens automatically: when a deactivated
fact is recalled, its `access_count`/`last_accessed` gets bumped and a
later maintenance run will compute a fresh score above the threshold
and reactivate the index row.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Any

from .config import (
    FACT_DECAY_HALFLIFE_DAYS,
    FACT_DECAY_SCORE_THRESHOLD,
    INCIDENT_ARCHIVE_DAYS,
)

log = logging.getLogger("brain_v2.maintenance")


@dataclass
class MaintenanceReport:
    facts_decayed: list[int] = field(default_factory=list)
    facts_reactivated: list[int] = field(default_factory=list)
    facts_ttl_expired: list[int] = field(default_factory=list)
    incidents_archived: list[int] = field(default_factory=list)
    skipped: bool = False
    skipped_reason: str = ""
    last_run_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["facts_decayed_count"] = len(self.facts_decayed)
        d["facts_reactivated_count"] = len(self.facts_reactivated)
        d["facts_ttl_expired_count"] = len(self.facts_ttl_expired)
        d["incidents_archived_count"] = len(self.incidents_archived)
        return d


def decay_facts(conn, *,
                halflife_days: float = FACT_DECAY_HALFLIFE_DAYS,
                threshold: float = FACT_DECAY_SCORE_THRESHOLD) -> dict[str, list[int]]:
    """Apply Ebbinghaus decay to FACT memories.

    Score formula: `score = POWER(2, -Δdays / halflife_days)`
    where Δdays is days since `last_accessed` (or `created_at` if never
    accessed).

    Facts with score < threshold are deactivated in memory_index.
    Facts with score >= threshold that were previously deactivated by
    this job are REACTIVATED (so recalls restore visibility).

    Facts with a `ttl` column set and past are ALWAYS deactivated and
    reported separately (they do not reactivate).

    Returns {deactivated: [ids], reactivated: [ids], ttl_expired: [ids]}.
    """
    with conn.cursor() as cur:
        # Step 1: hard TTL expiry — any fact with ttl < now is deactivated
        cur.execute(
            """
            UPDATE memory_index mi
            SET active = FALSE
            FROM facts f
            WHERE mi.kind = 'fact'
              AND mi.memory_id = f.id
              AND f.ttl IS NOT NULL
              AND f.ttl < NOW()
              AND mi.active = TRUE
            RETURNING mi.memory_id
            """
        )
        ttl_expired = [r[0] for r in cur.fetchall()]

        # Step 2: compute current score for every fact, sort into
        # decayed / reactivated buckets relative to memory_index.active.
        cur.execute(
            """
            SELECT f.id,
                   POWER(2, -EXTRACT(EPOCH FROM (NOW() - COALESCE(f.last_accessed, f.created_at)))
                         / (%s * 86400.0)) AS score,
                   mi.active,
                   f.ttl IS NOT NULL AND f.ttl < NOW() AS ttl_expired
            FROM facts f
            JOIN memory_index mi ON mi.kind = 'fact' AND mi.memory_id = f.id
            """,
            (halflife_days,),
        )
        rows = cur.fetchall()

        to_deactivate: list[int] = []
        to_reactivate: list[int] = []
        for fid, score, active, ttl_expired_flag in rows:
            if ttl_expired_flag:
                continue  # already handled in step 1
            if active and score < threshold:
                to_deactivate.append(fid)
            elif not active and score >= threshold:
                to_reactivate.append(fid)

        if to_deactivate:
            cur.execute(
                "UPDATE memory_index SET active = FALSE "
                "WHERE kind = 'fact' AND memory_id = ANY(%s)",
                (to_deactivate,),
            )
        if to_reactivate:
            cur.execute(
                "UPDATE memory_index SET active = TRUE "
                "WHERE kind = 'fact' AND memory_id = ANY(%s)",
                (to_reactivate,),
            )
    conn.commit()
    log.info("decay_facts: deactivated=%d reactivated=%d ttl_expired=%d "
             "(halflife=%.1fd threshold=%.3f)",
             len(to_deactivate), len(to_reactivate), len(ttl_expired),
             halflife_days, threshold)
    return {
        "deactivated": to_deactivate,
        "reactivated": to_reactivate,
        "ttl_expired": ttl_expired,
    }


def archive_incidents(conn, *, archive_days: int = INCIDENT_ARCHIVE_DAYS) -> list[int]:
    """Soft-archive incidents with no access in the last `archive_days`.

    Uses `last_accessed` if set, else `occurred_at`. Incidents already
    flagged `archived=TRUE` are skipped. Archival: sets `archived=TRUE`
    on the incident row AND deactivates the corresponding memory_index
    row (so archived incidents don't appear in search/boot by default).

    Returns the list of archived incident ids.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE incidents i
            SET archived = TRUE
            WHERE archived = FALSE
              AND COALESCE(i.last_accessed, i.occurred_at)
                  < NOW() - make_interval(days => %s)
            RETURNING id
            """,
            (int(archive_days),),
        )
        archived_ids = [r[0] for r in cur.fetchall()]

        if archived_ids:
            cur.execute(
                "UPDATE memory_index SET active = FALSE "
                "WHERE kind = 'incident' AND memory_id = ANY(%s)",
                (archived_ids,),
            )
    conn.commit()
    log.info("archive_incidents: archived=%d (threshold=%d days)",
             len(archived_ids), archive_days)
    return archived_ids


def _record_run_start(conn, source: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO maintenance_runs (source) VALUES (%s) RETURNING id",
            (source,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def _record_run_finish(conn, run_id: int, report: MaintenanceReport) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE maintenance_runs SET finished_at = NOW(), report = %s::jsonb "
            "WHERE id = %s",
            (json.dumps(report.to_dict(), default=str), run_id),
        )
    conn.commit()


def _get_last_run_at(conn) -> str | None:
    """Return the most recent maintenance_runs.started_at as ISO string,
    or None if no runs have happened yet."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT started_at FROM maintenance_runs "
            "WHERE finished_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def run_all(conn, *, source: str = "") -> MaintenanceReport:
    """Run every maintenance job once. Returns a single report.

    Also records the run in `maintenance_runs` so rate-limited callers
    (see `run_if_due`) can short-circuit subsequent invocations.
    """
    run_id = _record_run_start(conn, source)
    decay = decay_facts(conn)
    archived = archive_incidents(conn)
    report = MaintenanceReport(
        facts_decayed=decay["deactivated"],
        facts_reactivated=decay["reactivated"],
        facts_ttl_expired=decay["ttl_expired"],
        incidents_archived=archived,
    )
    _record_run_finish(conn, run_id, report)
    log.info("run_all: %s", report.to_dict())
    return report


def run_if_due(conn, *, hours: float = 24.0, source: str = "") -> MaintenanceReport:
    """Run maintenance ONLY IF the last successful run was more than
    `hours` ago. Otherwise return a skipped report.

    Safe to call on every boot. Designed for Claude Code PostToolUse
    hooks per `brain_v2/MAINTENANCE_SCHEDULING.md`.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT started_at,
                   EXTRACT(EPOCH FROM (NOW() - started_at)) / 3600.0 AS hours_ago
            FROM maintenance_runs
            WHERE finished_at IS NOT NULL
            ORDER BY started_at DESC LIMIT 1
            """
        )
        row = cur.fetchone()

    if row and row[1] < hours:
        last_run_at, hours_ago = row
        log.info("run_if_due: skipping (last run %.2fh ago, window=%.2fh)",
                 hours_ago, hours)
        return MaintenanceReport(
            skipped=True,
            skipped_reason=f"last run {float(hours_ago):.2f}h ago; window={hours}h",
            last_run_at=str(last_run_at),
        )

    return run_all(conn, source=source)
