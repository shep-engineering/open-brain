"""Tests for the v0.12.0 skills-layer feature.

Hit the test database per conftest.py isolation. Use deterministic fake
embeddings unless `-m ollama` is passed.

Run with: pytest tests/test_skills_layer.py -v
"""

import json
import os
import sys

import psycopg2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import (
    _booted_sources,
    _record_search,
    db_get_memory,
    db_get_pinned,
    db_get_skill_by_name,
    db_get_skills_by_keywords,
    db_set_pinned,
    db_store,
    db_store_deduped,
    extract_metadata,
    get_embedding,
    load_skill,
    remember,
    search,
    supersede,
)

TEST_PROJECT = "__test_skills_layer__"
OTHER_PROJECT = "__test_skills_other__"
TEST_SOURCE = "pytest-skills-layer"


def _make_memory(content: str, project: str = TEST_PROJECT,
                 type_override: str = "note",
                 skill_trigger: dict | None = None,
                 pinned: bool = False) -> int:
    """Create a memory directly via db_store (no MCP compliance gates).
    Returns the memory id. If skill_trigger is provided, stores it as
    the JSONB column payload."""
    embedding = get_embedding(content)
    metadata = extract_metadata(content)
    metadata["type"] = type_override
    metadata["source"] = TEST_SOURCE
    mem = db_store(content, embedding, metadata, project,
                   skill_trigger=skill_trigger)
    if pinned:
        db_set_pinned(mem["id"], True)
    return mem["id"]


@pytest.fixture(autouse=True)
def cleanup_test_project():
    """Wipe test-tagged memories around each test + register test source
    with the boot/search compliance gates."""
    from conftest import TEST_DATABASE_URL

    _booted_sources.add(TEST_SOURCE)
    _record_search(TEST_SOURCE, TEST_PROJECT)
    _record_search(TEST_SOURCE, OTHER_PROJECT)

    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    for label in (TEST_PROJECT, OTHER_PROJECT):
        with conn.cursor() as cur:
            cur.execute("UPDATE memories SET superseded_by_id = NULL "
                        "WHERE project = %s", (label,))
            cur.execute("DELETE FROM memories WHERE project = %s", (label,))
    yield
    for label in (TEST_PROJECT, OTHER_PROJECT):
        with conn.cursor() as cur:
            cur.execute("UPDATE memories SET superseded_by_id = NULL "
                        "WHERE project = %s", (label,))
            cur.execute("DELETE FROM memories WHERE project = %s", (label,))
    conn.close()


# ============================================================
# 1. Schema sanity
# ============================================================

def test_skill_trigger_column_exists():
    """The v5 migration has been applied to the test DB."""
    from conftest import TEST_DATABASE_URL
    conn = psycopg2.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'memories' AND column_name = 'skill_trigger'
        """)
        row = cur.fetchone()
    conn.close()
    assert row is not None, "skill_trigger column missing — run migrate_v5"
    assert row[1] == "jsonb"


# ============================================================
# 2. remember() accepts skill_trigger and stores it
# ============================================================

def test_remember_with_skill_trigger_stores_jsonb():
    """remember(..., skill_trigger={...}) round-trips through the DB."""
    trig = {
        "name": "ollama-shutdown-graceful",
        "keywords": ["ollama", "shutdown", "graceful"],
        "projects": [],
        "always_on": False,
    }
    out = json.loads(remember(
        content="Send CTRL+BREAK to ollama on Windows for graceful shutdown.",
        source=TEST_SOURCE,
        project=TEST_PROJECT,
        skill_trigger=trig,
    ))
    assert out["success"] is True
    stored = db_get_memory(out["id"])
    assert stored["skill_trigger"] is not None
    assert stored["skill_trigger"]["name"] == "ollama-shutdown-graceful"
    assert "ollama" in stored["skill_trigger"]["keywords"]


# ============================================================
# 3. boot_session / db_get_pinned filters
# ============================================================

def test_boot_session_excludes_non_always_on_skills():
    """Pinned memory with skill_trigger and always_on=false is NOT
    returned by db_get_pinned — this is the core Phase 1 win."""
    _make_memory("Skill-only guardrail — keyword triggered.",
                 skill_trigger={
                     "name": "db-migrations",
                     "keywords": ["migration", "schema"],
                     "projects": [],
                     "always_on": False,
                 },
                 pinned=True)
    pinned_ids = [m["id"] for m in db_get_pinned(TEST_PROJECT)]
    assert len(pinned_ids) == 0, (
        f"expected 0 pinned at boot (skill-only should be excluded), "
        f"got {pinned_ids}"
    )


def test_boot_session_includes_always_on_skills():
    """Skill-tagged pinned memory with always_on=true IS returned
    at boot — the safety valve for rules that must fire every session."""
    mid = _make_memory("Always-on workflow rule.",
                       skill_trigger={
                           "name": "workflow-rules",
                           "keywords": ["git", "commit"],
                           "projects": [],
                           "always_on": True,
                       },
                       pinned=True)
    pinned_ids = [m["id"] for m in db_get_pinned(TEST_PROJECT)]
    assert mid in pinned_ids


def test_boot_session_includes_pinned_without_skill_trigger():
    """Pinned memory with skill_trigger=NULL loads at boot — backwards
    compatibility for existing guardrails."""
    mid = _make_memory("Classic pinned guardrail, no trigger.",
                       pinned=True)
    pinned_ids = [m["id"] for m in db_get_pinned(TEST_PROJECT)]
    assert mid in pinned_ids


# ============================================================
# 4. search() auto-matches skill triggers
# ============================================================

def test_search_auto_matches_skill_by_keyword():
    """A memory whose skill_trigger.keywords hits the query surfaces in
    search() with the via_skill_trigger flag, even without being pinned."""
    mid = _make_memory("Use CTRL+BREAK for ollama shutdown on Windows.",
                       skill_trigger={
                           "name": "ollama-shutdown",
                           "keywords": ["ollama", "shutdown"],
                           "projects": [],
                           "always_on": False,
                       })
    raw = search("how do I shut down ollama cleanly",
                 source=TEST_SOURCE, project=TEST_PROJECT)
    results = json.loads(raw) if not isinstance(raw, str) or not raw.startswith("No memories") else []
    hit = next((r for r in results if r["id"] == mid), None)
    assert hit is not None, f"skill should surface on keyword match, ids={[r['id'] for r in results]}"


def test_search_skill_match_flagged_in_response():
    """Surfaced skills carry via_skill_trigger == <skill name>."""
    mid = _make_memory("Database migration safety: backup first.",
                       skill_trigger={
                           "name": "db-safety",
                           "keywords": ["migration", "schema", "backup"],
                           "projects": [],
                           "always_on": False,
                       })
    raw = search("plan a schema migration",
                 source=TEST_SOURCE, project=TEST_PROJECT)
    results = json.loads(raw)
    hit = next(r for r in results if r["id"] == mid)
    assert hit.get("via_skill_trigger") == "db-safety"


def test_search_skill_match_respects_max():
    """OPEN_BRAIN_SKILL_TRIGGER_MAX caps surfaced skills per query."""
    import server as srv
    original = srv.SKILL_TRIGGER_MAX
    srv.SKILL_TRIGGER_MAX = 2
    try:
        for i in range(5):
            _make_memory(f"Skill {i} on bananas.",
                         skill_trigger={
                             "name": f"banana-{i}",
                             "keywords": ["banana"],
                             "projects": [],
                             "always_on": False,
                         })
        raw = search("banana facts", source=TEST_SOURCE,
                     project=TEST_PROJECT)
        results = json.loads(raw)
        flagged = [r for r in results if r.get("via_skill_trigger")]
        assert len(flagged) <= 2
    finally:
        srv.SKILL_TRIGGER_MAX = original


# ============================================================
# 5. load_skill MCP tool
# ============================================================

def test_load_skill_by_name_returns_content():
    """load_skill('name', source) returns the matching skill's content."""
    _make_memory("Ollama shutdown: CTRL+BREAK.",
                 skill_trigger={
                     "name": "ollama-shutdown-graceful",
                     "keywords": ["ollama"],
                     "projects": [],
                     "always_on": False,
                 })
    out = json.loads(load_skill(name="ollama-shutdown-graceful",
                                 source=TEST_SOURCE,
                                 project=TEST_PROJECT))
    assert out["success"] is True
    assert "CTRL+BREAK" in out["content"]
    assert out["skill_trigger"]["name"] == "ollama-shutdown-graceful"


def test_load_skill_unknown_name_404():
    """load_skill with an unknown name returns success=False gracefully."""
    out = json.loads(load_skill(name="no-such-skill",
                                 source=TEST_SOURCE,
                                 project=TEST_PROJECT))
    assert out["success"] is False
    assert "not found" in out["error"].lower()


def test_load_skill_requires_source():
    """Compliance gate: empty source is rejected."""
    out = json.loads(load_skill(name="anything", source="",
                                 project=TEST_PROJECT))
    assert out["success"] is False


def test_load_skill_respects_project_scope():
    """A skill with projects=[OTHER_PROJECT] is not loadable from
    TEST_PROJECT."""
    _make_memory("Scoped skill content.",
                 project=TEST_PROJECT,
                 skill_trigger={
                     "name": "other-only",
                     "keywords": ["other"],
                     "projects": [OTHER_PROJECT],
                     "always_on": False,
                 })
    out = json.loads(load_skill(name="other-only",
                                 source=TEST_SOURCE,
                                 project=TEST_PROJECT))
    assert out["success"] is False


# ============================================================
# 6. Belief-revision interaction
# ============================================================

def test_skill_trigger_on_superseded_memory_ignored():
    """A superseded skill-tagged memory does not surface via load_skill
    or search auto-match (active-only default per v0.11.0)."""
    old_id = _make_memory("Old skill content.",
                          skill_trigger={
                              "name": "deprecated-skill",
                              "keywords": ["deprecated"],
                              "projects": [],
                              "always_on": False,
                          })
    out = json.loads(supersede(old_id, "New content (non-skill).",
                                reason="Skill retired",
                                source=TEST_SOURCE))
    assert out["success"] is True

    # load_skill should not find it
    loaded = json.loads(load_skill(name="deprecated-skill",
                                    source=TEST_SOURCE,
                                    project=TEST_PROJECT))
    assert loaded["success"] is False

    # search auto-match should not surface it either
    matches = db_get_skills_by_keywords("deprecated thing",
                                         TEST_PROJECT, limit=5)
    assert all(m["id"] != old_id for m in matches)
