#!/usr/bin/env python3
"""
Migration script: Add skill_trigger JSONB column for the skills layer.

Run with:  python scripts/migrate_v5_skills_layer.py

Adds ONE nullable JSONB column + ONE partial GIN index to the memories
table:

    skill_trigger  JSONB  DEFAULT NULL

Plus:
    idx_memories_skill_trigger  GIN partial index on skill_trigger
                                WHERE skill_trigger IS NOT NULL

Schema shape of skill_trigger when populated:

    {
      "name": "testing-discipline",          // globally unique
      "keywords": ["test", "pytest", "E2E"], // case-insensitive substring match
      "projects": [],                         // empty = all projects; populated = scoped
      "always_on": false                      // true = load at every boot regardless of triggers
    }

Why: Brain Harness Plan Phase 1. Shrinks boot_session payload by
letting most pinned guardrails become conditionally-loaded "skills"
rather than always-on. Skill-triggered memories surface only when:
  (a) boot: pinned + skill_trigger.always_on = true
  (b) search: any keyword substring-matches the query (and current
      project is in skill_trigger.projects OR projects is empty)
  (c) explicit: load_skill(name)

Safe to re-run -- ADD COLUMN IF NOT EXISTS and CREATE INDEX IF NOT
EXISTS are both idempotent.

Reversibility: the column + index can be dropped:
    DROP INDEX IF EXISTS idx_memories_skill_trigger;
    ALTER TABLE memories DROP COLUMN IF EXISTS skill_trigger;
No data loss -- existing rows had skill_trigger=NULL, which is the
default.

See docs/planning/SKILLS_LAYER_DESIGN.md for the full design.
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")


def main() -> None:
    safe_url = DATABASE_URL.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
    print(f"\n  Open Brain -- v5 Migration (skills layer)")
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
                    ALTER TABLE memories ADD COLUMN skill_trigger JSONB DEFAULT NULL;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """)
            print("  skill_trigger JSONB column ready")

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_skill_trigger
                    ON memories USING gin (skill_trigger)
                    WHERE skill_trigger IS NOT NULL
            """)
            print("  idx_memories_skill_trigger partial GIN index ready")

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM memories")
            total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM memories WHERE skill_trigger IS NOT NULL")
            with_trigger = cur.fetchone()[0]
            print(f"\n  After migration: {total} memories, {with_trigger} "
                  f"have skill_trigger (should be 0 on first run -- existing "
                  f"memories keep current behavior).")

        print("\n  v5 migration complete.")
    except Exception as e:
        print(f"\n  Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
