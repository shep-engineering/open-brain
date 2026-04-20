#!/usr/bin/env python3
"""
Migration script: Add `ended_at TIMESTAMPTZ` to `active_sessions` (V1).

Run with:  python scripts/migrate_v7_active_sessions_ended_at.py

Closes the last real divergence between V1's and V2's active_sessions
schemas. V2 (brain_v2/schema.py:181) already has `ended_at`; V1
(scripts/migrate_v6_session_registry.py:75-87) didn't. Without this
migration a shared probe / sweeper written against the V2 convention
fails on V1 with "column ended_at of relation active_sessions does not
exist." See docs/planning/v14-registry-trust-extend-to-v2.md §2.

The v0.14.x sweep in scripts/heartbeat_agent.py worked around the gap
by writing `status='ended'` only. That worked but left V1's `ended_at`
unreadable and blocked the shared-module refactor. v0.23.0 lands this
migration so the probe SQL is uniform across both brains.

Schema change:

    ALTER TABLE active_sessions
      ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;

Existing rows retain NULL `ended_at` (no backfill — the transition time
is lost for historical rows; we don't fabricate it). New code going
forward sets `ended_at = NOW()` whenever it transitions a row from
active to ended.

Safe to re-run: ADD COLUMN IF NOT EXISTS is idempotent.

Reversibility (no data loss on memories):
    ALTER TABLE active_sessions DROP COLUMN IF EXISTS ended_at;
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")


def main() -> None:
    safe_url = DATABASE_URL.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
    print(f"\n  Open Brain -- v7 Migration (active_sessions.ended_at)")
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
                ALTER TABLE active_sessions
                    ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ
            """)
            print("  active_sessions.ended_at ready")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM active_sessions WHERE status = 'ended' AND ended_at IS NULL"
            )
            null_ended = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM active_sessions")
            total = cur.fetchone()[0]
            print(f"\n  After migration: {total} active_sessions rows total.")
            print(f"    {null_ended} ended rows retain NULL ended_at (no backfill — historical transition time lost).")
            print(f"    New code going forward will populate ended_at on every active->ended transition.")

        print("\n  v7 migration complete.")
    except Exception as e:
        print(f"\n  Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
