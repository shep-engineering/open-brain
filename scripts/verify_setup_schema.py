#!/usr/bin/env python3
"""
Verify that setup_db.py landed the current v1 schema.

Run with:
    python scripts/verify_setup_schema.py

Uses DATABASE_URL from .env / environment and exits non-zero if required
tables, columns, or key indexes are missing.
"""
from __future__ import annotations

import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")

REQUIRED_TABLES = {
    "memories",
    "memories_audit",
    "active_sessions",
    "server_uptime",
}

REQUIRED_MEMORY_COLUMNS = {
    "project",
    "annotation",
    "access_count",
    "last_accessed",
    "upvotes",
    "downvotes",
    "pinned",
    "updated_at",
    "projects",
    "valid_time",
    "transaction_time",
    "superseded_by_id",
    "superseded_at",
    "superseded_reason",
    "skill_trigger",
    "last_accessed_uptime",
}

REQUIRED_ACTIVE_SESSION_COLUMNS = {
    "source",
    "project",
    "cwd",
    "pid",
    "host",
    "current_task",
    "heartbeat_at",
    "ended_at",
    "status",
    "metadata",
    "pid_create_time",
}

REQUIRED_INDEXES = {
    "memories_embedding_hnsw_idx",
    "memories_project_idx",
    "memories_projects_gin_idx",
    "memories_pinned_project_idx",
    "idx_memories_active",
    "idx_memories_superseded_by",
    "idx_memories_skill_trigger",
    "idx_memories_valid_time",
    "idx_memories_transaction_time",
    "idx_active_sessions_status_heartbeat",
    "idx_active_sessions_project_status",
    "idx_active_sessions_source_cwd_status",
}


def _safe_url(url: str) -> str:
    return url.replace("://", "://<credentials>@", 1).split("@", 1)[-1]


def _fetch_columns(cur, table_name: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    )
    return {row[0] for row in cur.fetchall()}


def main() -> int:
    print(f"Verifying Open Brain schema on {_safe_url(DATABASE_URL)}")
    try:
        conn = psycopg2.connect(DATABASE_URL)
    except psycopg2.OperationalError as exc:
        print(f"FAIL: cannot connect to PostgreSQL: {exc}")
        return 1

    missing: list[str] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                """
            )
            tables = {row[0] for row in cur.fetchall()}
            for table in sorted(REQUIRED_TABLES - tables):
                missing.append(f"missing table: {table}")

            memory_cols = _fetch_columns(cur, "memories")
            for col in sorted(REQUIRED_MEMORY_COLUMNS - memory_cols):
                missing.append(f"memories missing column: {col}")

            active_cols = _fetch_columns(cur, "active_sessions")
            for col in sorted(REQUIRED_ACTIVE_SESSION_COLUMNS - active_cols):
                missing.append(f"active_sessions missing column: {col}")

            cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            indexes = {row[0] for row in cur.fetchall()}
            for idx in sorted(REQUIRED_INDEXES - indexes):
                missing.append(f"missing index: {idx}")

            cur.execute("SELECT COUNT(*) FROM server_uptime")
            uptime_rows = cur.fetchone()[0]
            if uptime_rows < 1:
                missing.append("server_uptime missing seed row")

    finally:
        conn.close()

    if missing:
        print("FAIL: schema verification failed")
        for item in missing:
            print(f"  - {item}")
        print("\nRun `python scripts/setup_db.py` and re-check.")
        return 1

    print("OK: schema matches current v1 expectations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
