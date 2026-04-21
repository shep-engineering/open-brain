#!/usr/bin/env python3
"""
Migration script: Add `pid_create_time DOUBLE PRECISION` to
`active_sessions` (V1).

Run with:  python scripts/migrate_v8_active_sessions_pid_create_time.py

Fixes the PID reuse false-positive: the v0.23.0 probe would see a pid
exists and bump heartbeat_at even if the OS had reassigned that pid to
an unrelated process (e.g. DAVE-PC had pid 41712 registered by a Claude
MCP server, the OS later gave that pid to ChatGPT.exe, and the probe
happily reported "alive" for a session row that was long dead).

With this column populated, the probe also verifies that the current
process's create_time matches what was stored at registration.
Mismatch -> different process -> row marked ended.

Column:

    ALTER TABLE active_sessions
      ADD COLUMN IF NOT EXISTS pid_create_time DOUBLE PRECISION;

Nullable. Legacy rows (pre-v0.23.1) retain NULL and the probe falls
back to the old pid-only check for them (back-compat).

New rows populate it via `session_liveness.get_pid_create_time(pid)`
which wraps `psutil.Process(pid).create_time()` with None on error.

Safe to re-run: ADD COLUMN IF NOT EXISTS is idempotent.

Reversibility (no data loss on memories):
    ALTER TABLE active_sessions DROP COLUMN IF EXISTS pid_create_time;
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")


def main() -> None:
    safe_url = DATABASE_URL.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
    print(f"\n  Open Brain -- v8 Migration (active_sessions.pid_create_time)")
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
                    ADD COLUMN IF NOT EXISTS pid_create_time DOUBLE PRECISION
            """)
            print("  active_sessions.pid_create_time ready")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM active_sessions "
                "WHERE status = 'active' AND pid IS NOT NULL "
                "  AND pid_create_time IS NULL"
            )
            null_active = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM active_sessions")
            total = cur.fetchone()[0]
            print(f"\n  After migration: {total} active_sessions rows total.")
            print(f"    {null_active} active rows have pid but no pid_create_time (legacy).")
            print(f"    Legacy rows retain the old pid-only probe behavior (back-compat).")
            print(f"    New registrations populate pid_create_time automatically.")

        print("\n  v8 migration complete.")
    except Exception as e:
        print(f"\n  Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
