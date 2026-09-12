"""brain_kg tests are self-contained: they create and tear down their OWN
throwaway KG database and never import the v1/v2 MCP `server` module.

The repo-root conftest.py forces DATABASE_URL to a 5434 `openbrain_test` stack
and has session-autouse fixtures (safety_guard, init_test_schema, ...) that
import `server` and skip/exit when that stack is absent. Those are irrelevant to
the KG tests and would otherwise skip them. We override each autouse fixture
with a no-op HERE so it applies only to this subtree — the parent stack is not
required to run brain_kg tests.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def safety_guard():  # override parent: no production-DB guard needed (own DB)
    yield


@pytest.fixture(scope="session", autouse=True)
def init_test_schema():  # override parent: KG tests build their own schema
    yield


@pytest.fixture(autouse=True)
def reset_connection():  # override parent's per-test fixture (imports server)
    yield
