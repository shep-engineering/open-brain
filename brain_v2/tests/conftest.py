"""Test fixtures for brain_v2.

SAFETY: URL override MUST happen before any brain_v2 import.
brain_v2/config.py reads OPEN_BRAIN_V2_DATABASE_URL at import time.

Three layers of protection against accidentally hitting production:
  1. os.environ override (this file, top-level, before any import)
  2. safety_guard_v2 fixture (hard-exit if URL points to production)
  3. Connection singleton reset (forces connect() to reconnect with test URL)
"""
from __future__ import annotations

import os
import sys

# ── LAYER 1: Override before any brain_v2 import ────────────────────────────
V2_TEST_DATABASE_URL = "postgresql://postgres:testpassword@localhost:5435/open_brain_v2_test"
os.environ["OPEN_BRAIN_V2_DATABASE_URL"] = V2_TEST_DATABASE_URL

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from brain_v2 import store
from brain_v2.schema import apply_schema


# ── LAYER 2: Hard exit if URL points to production ──────────────────────────
@pytest.fixture(scope="session", autouse=True)
def safety_guard_v2():
    """Refuse to run if connected to the production V2 database."""
    from brain_v2.config import DATABASE_URL
    if "open_brain_v2_test" not in DATABASE_URL or "5435" not in DATABASE_URL:
        pytest.exit(
            f"REFUSING TO RUN V2 TESTS AGAINST PRODUCTION DATABASE.\n"
            f"  DATABASE_URL = {DATABASE_URL}\n"
            f"  Expected: open_brain_v2_test on port 5435\n"
            f"  Start test DB: docker compose -f docker-compose.test.yml up -d"
        )

    # ── LAYER 3: Reset connection singleton to test DB ───────────────────────
    store._conn = None


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema(safety_guard_v2):
    """Schema setup. Explicit `safety_guard_v2` parameter makes the
    dependency unambiguous — we rely on the guard running first so
    we never apply schema to the production DB. Without the explicit
    param pytest picks up the ordering by definition order, which is
    brittle."""
    try:
        with store.connect() as conn:
            apply_schema(conn)
    except Exception:
        pytest.skip(
            "V2 test database not running. Start with:\n"
            "  docker compose -f docker-compose.test.yml up -d"
        )


@pytest.fixture
def conn():
    """Reusable connection with truncated tables.

    Does NOT close the shared connection — closing and reopening costs
    ~21s per test on Windows+Docker due to DNS/TCP overhead. Instead,
    we truncate tables and commit, yielding the same connection.
    Rollback after yield ensures no stale transaction state leaks.
    """
    c = store.connect()
    with c.cursor() as cur:
        cur.execute("TRUNCATE memory_index, rules, facts, incidents, tasks, "
                    "action_items, active_sessions, handoffs, maintenance_runs, "
                    "v2_audit, tool_events "
                    "RESTART IDENTITY CASCADE")
    c.commit()
    yield c
    c.rollback()  # clean up any uncommitted state without closing
