#!/usr/bin/env python3
"""
Migration script: Add active_sessions table for the session-registry layer.

Run with:  python scripts/migrate_v6_session_registry.py

Creates a new table tracking live MCP-client sessions so booting
sessions can SEE other sessions working in the same project / cwd.
Closes the parallel-session blind spot that caused the Netflix SRE
vs. CI/CD prep mix-up (memory #3719). Design doc:
docs/planning/SESSION_REGISTRY_DESIGN.md

Schema:

    CREATE TABLE active_sessions (
        id             BIGSERIAL PRIMARY KEY,
        source         TEXT NOT NULL,
        project        TEXT,
        cwd            TEXT,
        pid            INTEGER,
        host           TEXT,
        current_task   TEXT,
        started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        heartbeat_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        status         TEXT NOT NULL DEFAULT 'active',
        metadata       JSONB DEFAULT NULL
    );

Indexes:
    idx_active_sessions_status_heartbeat   -- TTL sweep path
    idx_active_sessions_project_status     -- cross-session lookups
    idx_active_sessions_source_cwd_status  -- dedupe + implicit heartbeat

TTL rule (enforced in server.py, not schema): rows with status='active'
AND heartbeat_at < now() - interval '5 minutes' are promoted to
status='ended' on the next boot_session / list_active_sessions call.
5min matches the Anthropic prompt-cache TTL.

Safe to re-run -- CREATE TABLE / INDEX IF NOT EXISTS are idempotent.

Reversibility (no data loss on memories):
    DROP INDEX IF EXISTS idx_active_sessions_source_cwd_status;
    DROP INDEX IF EXISTS idx_active_sessions_project_status;
    DROP INDEX IF EXISTS idx_active_sessions_status_heartbeat;
    DROP TABLE IF EXISTS active_sessions;
Only active_sessions rows are lost (ephemeral by design -- dead
sessions are swept out anyway).
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")


def main() -> None:
    safe_url = DATABASE_URL.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
    print(f"\n  Open Brain -- v6 Migration (session registry)")
    print(f"    DB: {safe_url}\n")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
    except psycopg2.OperationalError as e:
        print(f"  Cannot connect to PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS active_sessions (
                    id             BIGSERIAL PRIMARY KEY,
                    source         TEXT NOT NULL,
                    project        TEXT,
                    cwd            TEXT,
                    pid            INTEGER,
                    host           TEXT,
                    current_task   TEXT,
                    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                    heartbeat_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    status         TEXT NOT NULL DEFAULT 'active',
                    metadata       JSONB DEFAULT NULL
                )
            """)
            print("  active_sessions table ready")

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_active_sessions_status_heartbeat
                    ON active_sessions (status, heartbeat_at)
            """)
            print("  idx_active_sessions_status_heartbeat ready")

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_active_sessions_project_status
                    ON active_sessions (project, status)
            """)
            print("  idx_active_sessions_project_status ready")

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_active_sessions_source_cwd_status
                    ON active_sessions (source, cwd, status)
            """)
            print("  idx_active_sessions_source_cwd_status ready")

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM active_sessions")
            total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM active_sessions WHERE status = 'active'")
            live = cur.fetchone()[0]
            print(f"\n  After migration: {total} active_sessions rows, "
                  f"{live} marked active (should be 0 on first run).")

        print("\n  v6 migration complete.")
    except Exception as e:
        print(f"\n  Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
