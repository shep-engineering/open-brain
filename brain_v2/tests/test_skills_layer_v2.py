"""Skills layer tests for brain_v2.

Ported from v1's tests/test_skills_layer.py, adapted for v2's typed
schema (skills are rules with skill_trigger JSONB).
"""
from __future__ import annotations

import pytest

from brain_v2 import store
from brain_v2.boot import build as boot_build


class TestSkillTriggerStorage:
    def test_skill_trigger_column_exists(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'rules' AND column_name = 'skill_trigger'"
            )
            assert cur.fetchone() is not None

    def test_remember_rule_stores_skill_trigger(self, conn):
        trigger = {
            "name": "test-skill",
            "keywords": ["ollama", "shutdown"],
            "projects": [],
            "always_on": False,
        }
        mem = store.remember_rule(
            conn, headline="Test skill storage",
            body="This is a test skill body.",
            severity="PATTERN", project="test", source="test",
            skill_trigger=trigger,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT skill_trigger FROM rules WHERE id = %s", (mem.id,))
            stored = cur.fetchone()[0]
        assert stored is not None
        assert stored["name"] == "test-skill"
        assert "ollama" in stored["keywords"]


class TestBootSkillFiltering:
    def test_boot_excludes_always_on_false(self, conn):
        store.remember_rule(
            conn, headline="Should not appear at boot",
            body="This skill has always_on false.",
            severity="PATTERN", project="test", source="test",
            skill_trigger={
                "name": "hidden-skill",
                "keywords": ["hidden"],
                "projects": [],
                "always_on": False,
            },
        )
        payload = boot_build(conn, project="test", task="general work",
                             source="test", register=False)
        pattern_headlines = [p["headline"] for p in payload.patterns]
        assert "Should not appear at boot" not in pattern_headlines

    def test_boot_includes_always_on_true(self, conn):
        store.remember_rule(
            conn, headline="Always on skill present",
            body="This skill has always_on true.",
            severity="PATTERN", project="test", source="test",
            skill_trigger={
                "name": "always-skill",
                "keywords": ["always"],
                "projects": [],
                "always_on": True,
            },
        )
        payload = boot_build(conn, project="test", task="general work",
                             source="test", register=False)
        pattern_headlines = [p["headline"] for p in payload.patterns]
        assert "Always on skill present" in pattern_headlines

    def test_boot_includes_null_skill_trigger(self, conn):
        store.remember_rule(
            conn, headline="Regular pattern rule",
            body="This rule has no skill_trigger.",
            severity="PATTERN", project="test", source="test",
        )
        payload = boot_build(conn, project="test", task="regular work",
                             source="test", register=False)
        pattern_headlines = [p["headline"] for p in payload.patterns]
        assert "Regular pattern rule" in pattern_headlines


class TestSearchSkillKeywords:
    def test_search_surfaces_skill_by_keyword(self, conn):
        store.remember_rule(
            conn, headline="Ollama graceful shutdown procedure",
            body="Send CTRL+BREAK to ollama on Windows.",
            severity="PATTERN", project="test", source="test",
            skill_trigger={
                "name": "ollama-shutdown",
                "keywords": ["ollama", "shutdown", "graceful"],
                "projects": [],
                "always_on": False,
            },
        )
        results = store.search_headlines(
            conn, query="how do I shut down ollama cleanly",
            project="test", limit=10,
        )
        skill_results = [r for r in results if r.get("via_skill_trigger")]
        assert len(skill_results) >= 1
        assert skill_results[0]["via_skill_trigger"] == "ollama-shutdown"

    def test_skill_trigger_max_caps_results(self, conn):
        # Use highly distinct headlines/bodies to avoid duplicate detection
        topics = [
            ("Database backup procedures", "Always run pg_dump before schema changes."),
            ("Python virtual environment setup", "Use python -m venv to create isolated environments."),
            ("Docker container networking", "Bridge networks connect containers on the same host."),
            ("Git branch naming conventions", "Use feat/ fix/ docs/ chore/ prefixes consistently."),
            ("Ollama model management tips", "Use ollama list to see installed models."),
            ("Windows service configuration", "Use sc.exe to manage Windows services."),
            ("PostgreSQL index tuning guide", "Partial indexes reduce storage for filtered queries."),
            ("API rate limiting strategies", "Token bucket algorithm provides burst tolerance."),
        ]
        for i, (headline, body) in enumerate(topics):
            store.remember_rule(
                conn, headline=headline, body=body,
                severity="PATTERN", project="test", source="test",
                skill_trigger={
                    "name": f"cap-test-skill-{i}",
                    "keywords": ["bulkcaptest"],
                    "projects": [],
                    "always_on": False,
                },
            )
        from brain_v2.config import SKILL_TRIGGER_MAX
        skills = store.get_skills_by_keywords(
            conn, query="bulkcaptest query", project_filter="test",
            limit=SKILL_TRIGGER_MAX,
        )
        assert len(skills) == SKILL_TRIGGER_MAX
        assert SKILL_TRIGGER_MAX < len(topics)  # cap must be below inserted count


class TestLoadSkill:
    def test_load_by_name(self, conn):
        store.remember_rule(
            conn, headline="Named skill for load test",
            body="Body of the named skill.",
            severity="PATTERN", project="test", source="test",
            skill_trigger={
                "name": "load-test-skill",
                "keywords": ["loadtest"],
                "projects": [],
                "always_on": False,
            },
        )
        skill = store.get_skill_by_name(conn, name="load-test-skill")
        assert skill is not None
        assert skill["headline"] == "Named skill for load test"
        assert skill["body"] == "Body of the named skill."
        assert skill["via_skill_trigger"] == "load-test-skill"

    def test_load_unknown_returns_none(self, conn):
        skill = store.get_skill_by_name(conn, name="nonexistent-skill")
        assert skill is None

    def test_project_scoped_skill_rejected_for_wrong_project(self, conn):
        store.remember_rule(
            conn, headline="Scoped skill",
            body="Only for project-a.",
            severity="PATTERN", project="project-a", source="test",
            skill_trigger={
                "name": "scoped-skill",
                "keywords": ["scoped"],
                "projects": ["project-a"],
                "always_on": False,
            },
        )
        assert store.get_skill_by_name(conn, name="scoped-skill",
                                        project_filter="project-a") is not None
        assert store.get_skill_by_name(conn, name="scoped-skill",
                                        project_filter="project-b") is None

    def test_superseded_skill_not_returned(self, conn):
        original = store.remember_rule(
            conn, headline="Original skill",
            body="Will be superseded.",
            severity="PATTERN", project="test", source="test",
            skill_trigger={
                "name": "supersede-test-skill",
                "keywords": ["supertest"],
                "projects": [],
                "always_on": False,
            },
        )
        store.supersede_rule(
            conn, old_id=original.id,
            new_headline="Revised skill",
            new_body="Supersedes the original.",
            reason="testing supersede",
            source="test",
        )
        skill = store.get_skill_by_name(conn, name="supersede-test-skill")
        assert skill is None
