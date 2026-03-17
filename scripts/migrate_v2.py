#!/usr/bin/env python3
"""
Migration script: Add project scoping, annotations, access tracking, and rating columns.

Run with:  python scripts/migrate_v2.py

Inspired by Context Hub (chub) patterns:
  - Project scoping: filter memories by project context
  - Annotations: attach notes to existing memories (like chub's annotate command)
  - Access tracking: know which memories are actually useful
  - Rating: up/down quality signals (like chub's feedback system)

Safe to re-run — all statements use IF NOT EXISTS or are idempotent.
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")


def main() -> None:
    safe_url = DATABASE_URL.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
    print(f"\n  Open Brain — v2 Migration")
    print(f"    DB: {safe_url}\n")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
    except psycopg2.OperationalError as e:
        print(f"  Cannot connect to PostgreSQL: {e}")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            # 1. Add project column for scoping memories
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN project TEXT NOT NULL DEFAULT '';
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            print("  project column ready")

            # 2. Add annotation column for attaching notes to memories
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN annotation TEXT NOT NULL DEFAULT '';
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            print("  annotation column ready")

            # 3. Add access tracking columns
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN last_accessed TIMESTAMPTZ;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            print("  access_count + last_accessed columns ready")

            # 4. Add rating columns (up/down counts like chub's feedback)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN upvotes INTEGER NOT NULL DEFAULT 0;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE memories ADD COLUMN downvotes INTEGER NOT NULL DEFAULT 0;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            print("  upvotes + downvotes columns ready")

            # 5. Index on project for fast filtering
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_project_idx ON memories (project)
                WHERE project != ''
            """)
            print("  project index ready")

            # 6. Index on last_accessed for pruning queries
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_last_accessed_idx
                ON memories (last_accessed ASC NULLS FIRST)
            """)
            print("  last_accessed index ready")

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
