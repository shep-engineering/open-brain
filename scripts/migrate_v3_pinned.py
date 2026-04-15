#!/usr/bin/env python3
"""
Migration script: Add pinned column for guardrail memories.

Run with:  python scripts/migrate_v3_pinned.py

Pinned memories are always returned at the top of search results for their
project, regardless of semantic similarity. Use this for workflow rules,
conventions, and guardrails that agents must always see.

Safe to re-run -- all statements use IF NOT EXISTS or are idempotent.
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")


def main() -> None:
    safe_url = DATABASE_URL.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
    print(f"\n  Open Brain -- v3 Migration (pinned guardrails)")
    print(f"    DB: {safe_url}\n")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
    except psycopg2.OperationalError as e:
        print(f"  Cannot connect to PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            # 1. Add pinned boolean column
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT FALSE;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            print("  pinned column ready")

            # 2. Partial index for fast pinned lookups per project
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_pinned_project_idx
                ON memories (project) WHERE pinned = TRUE
            """)
            print("  pinned project index ready")

            # Verify
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'memories'
                ORDER BY ordinal_position
            """)
            cols = [r[0] for r in cur.fetchall()]

        print(f"\n  Migration complete. Columns: {', '.join(cols)}")
        print()

    except Exception as e:
        print(f"  Migration failed: {e}\n")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
