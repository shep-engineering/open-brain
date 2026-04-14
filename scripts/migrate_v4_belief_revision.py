#!/usr/bin/env python3
"""
Migration script: Add belief-revision (supersession) columns + indexes.

Run with:  python scripts/migrate_v4_belief_revision.py

Adds three nullable columns + two partial indexes to the memories table:

    superseded_by_id  INTEGER REFERENCES memories(id) ON DELETE SET NULL
    superseded_at     TIMESTAMPTZ
    superseded_reason TEXT

Plus:
    idx_memories_active        — partial index on (id) WHERE superseded_by_id IS NULL
    idx_memories_superseded_by — partial index on (superseded_by_id) WHERE superseded_by_id IS NOT NULL

Why: lets agents mark a memory as corrected by a newer one. Search/recall
filter to active memories by default; the audit trail of past beliefs
stays intact. Canonical motivating case: memory #3663 currently asserts
two contradicting facts about whether the ON script starts the MCP server.

Safe to re-run -- all statements use IF NOT EXISTS or DO/EXCEPTION blocks.

Reversibility: all three columns are nullable additions; drop with
    ALTER TABLE memories DROP COLUMN superseded_by_id;
    ALTER TABLE memories DROP COLUMN superseded_at;
    ALTER TABLE memories DROP COLUMN superseded_reason;
to roll back without data loss (existing rows are unaffected).

See docs/planning/BELIEF_REVISION_DESIGN.md for the full design.
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")


def main() -> None:
    safe_url = DATABASE_URL.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
    print(f"\n  Open Brain -- v4 Migration (belief revision / supersession)")
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
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN superseded_by_id INTEGER
                        REFERENCES memories(id) ON DELETE SET NULL;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            print("  superseded_by_id column ready")

            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN superseded_at TIMESTAMPTZ;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            print("  superseded_at column ready")

            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN superseded_reason TEXT;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            print("  superseded_reason column ready")

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_active
                    ON memories (id)
                    WHERE superseded_by_id IS NULL
            """)
            print("  idx_memories_active partial index ready")

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_superseded_by
                    ON memories (superseded_by_id)
                    WHERE superseded_by_id IS NOT NULL
            """)
            print("  idx_memories_superseded_by partial index ready")

        # Sanity check: no existing rows broken
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM memories")
            total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM memories WHERE superseded_by_id IS NULL")
            active = cur.fetchone()[0]
            print(f"\n  After migration: {total} memories, {active} active "
                  f"({total - active} superseded — should be 0 on first run)")

        print("\n  v4 migration complete.")
    except Exception as e:
        print(f"\n  Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
