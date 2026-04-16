"""Test fixtures for brain_v2.

Tests run against the real v2 Postgres container (port 5433) and live
Ollama embedding model. No mocks — per project guardrail #3347
("smoke tests ≠ feature tested"). Each test truncates its working
tables in setup to start from a known clean state.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from brain_v2 import store
from brain_v2.schema import apply_schema


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema():
    with store.connect() as conn:
        apply_schema(conn)


@pytest.fixture
def conn():
    """Fresh connection with truncated tables."""
    c = store.connect()
    with c.cursor() as cur:
        cur.execute("TRUNCATE memory_index, rules, facts, incidents, tasks, action_items, v2_audit "
                    "RESTART IDENTITY CASCADE")
    c.commit()
    yield c
    c.close()
