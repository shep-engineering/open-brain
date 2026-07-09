"""Tests for boot_session pinned-guardrail rendering caps.

Regression coverage for the 2026-06-29 incident: 27 pinned guardrails
rendered at FULL content produced a ~60KB PINNED GUARDRAILS section and a
~75KB boot payload, which exceeded the MCP result token ceiling and
hard-failed boot_session. The cap (BOOT_GUARDRAIL_CHAR_CAP /
BOOT_GUARDRAIL_TOTAL_CAP / BOOT_GUARDRAIL_HEADLINE) bounds the section
while keeping full bodies available via recall(#id).

These tests hit the live database and Ollama embedding model.
Run with: pytest tests/test_boot_guardrail_cap.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server
from server import (
    BOOT_GUARDRAIL_CHAR_CAP,
    BOOT_GUARDRAIL_TOTAL_CAP,
    boot_session,
    db_set_pinned,
    db_store,
)

TEST_PROJECT = "__test_boot_cap__"
TEST_SOURCE = "test"


@pytest.fixture(autouse=True)
def stub_embedding(monkeypatch):
    """Force a correctly-sized dummy vector for every embedding call so the
    test never depends on Ollama or the live embedding-model dimension
    (which drifted 768->4096 in the qwen3 migration while the test schema
    stayed 768). boot_session() itself calls get_embedding for its
    search-backed sections, so this must patch the module attribute."""
    monkeypatch.setattr(server, "get_embedding",
                        lambda text: [0.0] * server.EMBEDDING_DIMS)


@pytest.fixture(autouse=True)
def cleanup_test_memories():
    _delete_test_memories()
    yield
    _delete_test_memories()


def _delete_test_memories():
    from server import _get_conn
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM memories WHERE project = %s", (TEST_PROJECT,))


def _create_pinned(content: str) -> int:
    # Rendering test only — similarity is irrelevant; use a dummy vector.
    embedding = server.get_embedding(content)
    result = db_store(content, embedding, {"type": "guardrail"}, TEST_PROJECT)
    db_set_pinned(result["id"], True)
    return result["id"]


def _guardrail_section(project: str) -> dict:
    raw = boot_session(source=TEST_SOURCE, project=project)
    payload = json.loads(raw)
    for s in payload["context"]:
        if s["section"] == "PINNED GUARDRAILS":
            return s
    return {}


class TestBootGuardrailCap:

    def test_large_guardrail_is_truncated_with_recall_pointer(self):
        big = "RULE. " + ("verylongbody " * 400)  # ~5KB, well over CHAR_CAP
        mem_id = _create_pinned(big)

        section = _guardrail_section(TEST_PROJECT)
        assert section, "PINNED GUARDRAILS section missing"
        entry = section["content"][0]
        # Entry preview is bounded, not the full 5KB body.
        assert len(entry) < BOOT_GUARDRAIL_CHAR_CAP + 200
        assert f"[recall #{mem_id} for full]" in entry
        assert mem_id in section.get("truncated", [])
        assert "note" in section

    def test_small_guardrail_is_not_truncated(self):
        small = "Always use feature branches; never commit to main."
        mem_id = _create_pinned(small)

        section = _guardrail_section(TEST_PROJECT)
        entry = section["content"][0]
        assert small in entry
        assert "[recall #" not in entry
        assert mem_id not in section.get("truncated", [])

    def test_many_large_guardrails_stay_under_total_budget(self):
        # 30 oversized guardrails — the failure shape from the incident.
        for i in range(30):
            _create_pinned(f"GUARDRAIL {i}. " + ("payload " * 500))

        section = _guardrail_section(TEST_PROJECT)
        section_bytes = len(json.dumps(section))
        # Section must be bounded near the total budget, not ~60KB.
        assert section_bytes < BOOT_GUARDRAIL_TOTAL_CAP + 4000, (
            f"section_bytes={section_bytes} exceeds budget"
        )
        assert section["count"] == 30          # all still reported
        assert len(section["content"]) == 30   # all still present (headline-only tail)
        assert len(section["truncated"]) >= 1

    def test_full_boot_payload_under_token_ceiling(self):
        # The whole point: full boot must stay well under the MCP token cap.
        for i in range(30):
            _create_pinned(f"GUARDRAIL {i}. " + ("payload " * 500))

        raw = boot_session(source=TEST_SOURCE, project=TEST_PROJECT)
        # ~4 chars/token heuristic; ceiling is ~25K tokens. Stay well under.
        assert len(raw) < 60000, f"boot payload {len(raw)} chars too large"
