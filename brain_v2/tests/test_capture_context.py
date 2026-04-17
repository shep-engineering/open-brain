"""Tests for capture_context_v2 — auto-decompose + typed store.

Exercises:
  - Multi-paragraph context decomposes into multiple typed memories
  - Rule/fact/incident/task classification via heuristics
  - Duplicate detection (same content → DuplicateHit, not double-store)
  - Write gate enforcement on decomposed chunks (headline ≤15 words, atomicity)
  - Single-paragraph fallback (stored as one memory, not dropped)
  - Empty context rejected
  - Results include kind, id, headline, action for each chunk
"""
from __future__ import annotations

import pytest

from brain_v2 import store
from brain_v2.decompose import Chunk, decompose


class TestDecompose:
    def test_splits_on_double_newline(self):
        raw = "First paragraph about a fact.\n\nSecond paragraph about another fact."
        chunks = decompose(raw)
        assert len(chunks) == 2

    def test_classifies_rule_by_keywords(self):
        raw = "GUARDRAIL: Never commit directly to main branch under any circumstances."
        chunks = decompose(raw)
        assert len(chunks) == 1
        assert chunks[0].kind == "rule"

    def test_classifies_blocker_severity(self):
        raw = "This is a non-negotiable blocker: never deploy on Fridays without rollback plan."
        chunks = decompose(raw)
        assert chunks[0].kind == "rule"
        assert chunks[0].severity == "BLOCKER"

    def test_classifies_incident_by_keywords(self):
        raw = "Bug found in the auth module. The session token was not being refreshed on 401 responses, causing a crash."
        chunks = decompose(raw)
        assert chunks[0].kind == "incident"

    def test_classifies_task_by_keywords(self):
        raw = "TODO: need to implement the retry logic for failed webhook deliveries before next release."
        chunks = decompose(raw)
        assert chunks[0].kind == "task"

    def test_classifies_fact_by_default(self):
        raw = "The project uses PostgreSQL 16 with pgvector for embedding storage on port 5433."
        chunks = decompose(raw)
        assert chunks[0].kind == "fact"

    def test_generates_headline_from_first_sentence(self):
        raw = "Database backups run daily at 3am UTC. The retention policy keeps 30 days of snapshots on S3."
        chunks = decompose(raw)
        assert "backups" in chunks[0].headline.lower() or "database" in chunks[0].headline.lower()
        assert len(chunks[0].headline.split()) <= 15

    def test_skips_very_short_chunks(self):
        raw = "OK.\n\nThe project uses React 18 with TypeScript for the frontend dashboard."
        chunks = decompose(raw)
        # "OK." is <5 words, should be skipped
        assert all("OK" not in c.text for c in chunks)

    def test_fallback_for_single_paragraph(self):
        raw = "The v2 container runs on port 5433 with a separate database named open_brain_v2."
        chunks = decompose(raw)
        assert len(chunks) == 1

    def test_mixed_types_in_one_context(self):
        raw = (
            "GUARDRAIL: Always run tests before pushing code to any remote branch.\n\n"
            "Bug found today: the boot payload was exceeding the 2K token cap because "
            "task content was not being truncated before token estimation.\n\n"
            "The project switched from SQLite to PostgreSQL in March 2026 for vector support.\n\n"
            "TODO: need to add monitoring to the v2 MCP server startup path."
        )
        chunks = decompose(raw)
        kinds = {c.kind for c in chunks}
        assert "rule" in kinds
        assert "incident" in kinds
        assert "fact" in kinds
        assert "task" in kinds


class TestCaptureContextStore:
    def test_stores_multiple_typed_memories(self, conn):
        raw = (
            "GUARDRAIL: Never touch the v1 database during v2 development.\n\n"
            "The v2 brain runs on a separate Postgres container on port 5433."
        )
        results = store.capture_context(conn, context=raw, source="test", project="test")
        stored = [r for r in results if r["action"] == "stored"]
        assert len(stored) >= 2
        kinds = {r["kind"] for r in stored}
        assert "rule" in kinds
        assert "fact" in kinds

    def test_each_stored_memory_has_id_and_headline(self, conn):
        raw = "The deployment pipeline runs on GitHub Actions with a 10-minute timeout per job."
        results = store.capture_context(conn, context=raw, source="test", project="test")
        for r in results:
            assert "kind" in r
            assert "headline" in r
            assert "action" in r
            if r["action"] == "stored":
                assert "id" in r
                assert isinstance(r["id"], int)

    def test_duplicate_detection_across_captures(self, conn):
        raw = "The project uses nomic-embed-text as the embedding model for all vector operations."
        # First capture
        r1 = store.capture_context(conn, context=raw, source="test", project="test")
        assert any(r["action"] == "stored" for r in r1)
        # Second capture of same content
        r2 = store.capture_context(conn, context=raw, source="test", project="test")
        assert any(r["action"] == "duplicate" for r in r2)

    def test_stored_memories_retrievable_via_recall(self, conn):
        raw = "Decision: we will use separate Docker containers for v1 and v2 databases to ensure physical isolation."
        results = store.capture_context(conn, context=raw, source="test", project="test")
        stored = [r for r in results if r["action"] == "stored"]
        assert stored
        mem = store.recall(conn, kind=stored[0]["kind"], memory_id=stored[0]["id"])
        assert mem is not None
        assert "isolation" in mem.body.lower() or "separate" in mem.body.lower()

    def test_stored_memories_searchable(self, conn):
        raw = "The Ollama embedding model nomic-embed-text produces 768-dimensional vectors for semantic search."
        results = store.capture_context(conn, context=raw, source="test", project="test")
        stored = [r for r in results if r["action"] == "stored"]
        assert stored
        search_results = store.search_headlines(conn, query="embedding dimensions", project="test")
        assert any(r["memory_id"] == stored[0]["id"] for r in search_results)

    def test_rule_stored_in_rules_table(self, conn):
        raw = "GUARDRAIL: All new services must have structured logging from day one."
        results = store.capture_context(conn, context=raw, source="test", project="test")
        stored = [r for r in results if r["action"] == "stored" and r["kind"] == "rule"]
        assert stored
        with conn.cursor() as cur:
            cur.execute("SELECT id, headline, severity FROM rules WHERE id = %s", (stored[0]["id"],))
            row = cur.fetchone()
            assert row is not None
            assert row[2] in ("BLOCKER", "PATTERN")

    def test_incident_stored_in_incidents_table(self, conn):
        raw = "Bug: the MCP server crashed on startup because ensure_schema had no error handling when the DB was unreachable."
        results = store.capture_context(conn, context=raw, source="test", project="test")
        stored = [r for r in results if r["action"] == "stored" and r["kind"] == "incident"]
        assert stored
        with conn.cursor() as cur:
            cur.execute("SELECT id, headline FROM incidents WHERE id = %s", (stored[0]["id"],))
            assert cur.fetchone() is not None

    def test_task_stored_in_tasks_table(self, conn):
        raw = "TODO: need to add the acknowledge_action_item_v2 tool to close the P0 gap."
        results = store.capture_context(conn, context=raw, source="test", project="test")
        stored = [r for r in results if r["action"] == "stored" and r["kind"] == "task"]
        assert stored
        with conn.cursor() as cur:
            cur.execute("SELECT id, content FROM tasks WHERE id = %s", (stored[0]["id"],))
            assert cur.fetchone() is not None
