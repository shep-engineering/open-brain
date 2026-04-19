"""
Root-level conftest.py — forces all pytest tests to use the isolated test database.

SAFETY: This file MUST be loaded before any test file imports `server`.
pytest discovers conftest.py first and executes top-level code at collection time,
which means the DATABASE_URL override happens before server.py reads os.getenv().

Three layers of protection against accidentally hitting production:
  1. os.environ override (this file, top-level)
  2. Session fixture assertion (refuses to run if URL points to production)
  3. Connection singleton reset (forces _get_conn to reconnect with test URL)

xdist support:
  - Schema DDL runs once under a filelock; other workers wait and skip
  - Session teardown only truncates from the controller (no PYTEST_XDIST_WORKER)
"""

import hashlib
import os
import struct
import math
import tempfile

# ──────────────────────────────────────────────────────────────────────────────
# LAYER 1: Override DATABASE_URL before server.py is imported by any test file.
# This MUST be at the top level of conftest.py, NOT inside a fixture.
# ──────────────────────────────────────────────────────────────────────────────
TEST_DATABASE_URL = "postgresql://postgres:testpassword@localhost:5434/openbrain_test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest
import psycopg2


# ──────────────────────────────────────────────────────────────────────────────
# LAYER 2: Session-scoped safety assertion
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def safety_guard():
    """Hard-exit if DATABASE_URL somehow points to production."""
    import server
    url = server.DATABASE_URL
    if "openbrain_test" not in url or ":5434" not in url:
        pytest.exit(
            f"REFUSING TO RUN TESTS AGAINST PRODUCTION DATABASE.\n"
            f"  DATABASE_URL = {url}\n"
            f"  Expected: openbrain_test on port 5434\n"
            f"  Start test DB: docker compose -f docker-compose.test.yml up -d"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Schema initialization (session-scoped, runs once)
# Under xdist, each worker runs its own session. Use filelock so only one
# worker performs DDL; others wait and skip.
# ──────────────────────────────────────────────────────────────────────────────
_SCHEMA_LOCK = os.path.join(tempfile.gettempdir(), "open_brain_test_schema.lock")
_SCHEMA_DONE = os.path.join(tempfile.gettempdir(), "open_brain_test_schema.done")


def _create_schema_ddl():
    """Run all CREATE TABLE / INDEX / TRIGGER statements. Called once."""
    try:
        conn = psycopg2.connect(TEST_DATABASE_URL)
        conn.autocommit = True
    except psycopg2.OperationalError:
        pytest.skip(
            "Test database not running. Start with:\n"
            "  docker compose -f docker-compose.test.yml up -d"
        )
        return

    dims = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))

    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS memories (
                id            SERIAL      PRIMARY KEY,
                content       TEXT        NOT NULL,
                embedding     VECTOR({dims}),
                metadata      JSONB       NOT NULL DEFAULT '{{}}',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                project       TEXT        NOT NULL DEFAULT '',
                annotation    TEXT        NOT NULL DEFAULT '',
                access_count  INTEGER     NOT NULL DEFAULT 0,
                last_accessed TIMESTAMPTZ,
                upvotes       INTEGER     NOT NULL DEFAULT 0,
                downvotes     INTEGER     NOT NULL DEFAULT 0,
                pinned        BOOLEAN     NOT NULL DEFAULT FALSE
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS memories_embedding_hnsw_idx
            ON memories USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS memories_created_at_idx ON memories (created_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS memories_metadata_gin_idx ON memories USING gin (metadata)")
        cur.execute("CREATE INDEX IF NOT EXISTS memories_project_idx ON memories (project) WHERE project != ''")
        cur.execute("CREATE INDEX IF NOT EXISTS memories_last_accessed_idx ON memories (last_accessed ASC NULLS FIRST)")
        cur.execute("CREATE INDEX IF NOT EXISTS memories_pinned_project_idx ON memories (project) WHERE pinned = TRUE")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS memories_audit (
                audit_id    SERIAL      PRIMARY KEY,
                operation   TEXT        NOT NULL,
                memory_id   INTEGER     NOT NULL,
                content     TEXT,
                metadata    JSONB,
                project     TEXT,
                pinned      BOOLEAN,
                changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                changed_by  TEXT        NOT NULL DEFAULT ''
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS memories_audit_ts_idx ON memories_audit (changed_at DESC)")

        cur.execute("""
            CREATE OR REPLACE FUNCTION audit_memories()
            RETURNS trigger AS $body$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    INSERT INTO memories_audit (operation, memory_id, content, metadata, project, pinned)
                    VALUES ('DELETE', OLD.id, OLD.content, OLD.metadata, OLD.project, OLD.pinned);
                    RETURN OLD;
                ELSIF TG_OP = 'UPDATE' THEN
                    INSERT INTO memories_audit (operation, memory_id, content, metadata, project, pinned)
                    VALUES ('UPDATE', NEW.id, NEW.content, NEW.metadata, NEW.project, NEW.pinned);
                    RETURN NEW;
                ELSIF TG_OP = 'INSERT' THEN
                    INSERT INTO memories_audit (operation, memory_id, content, metadata, project, pinned)
                    VALUES ('INSERT', NEW.id, NEW.content, NEW.metadata, NEW.project, NEW.pinned);
                    RETURN NEW;
                END IF;
            END;
            $body$ LANGUAGE plpgsql
        """)
        cur.execute("""
            DO $body$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger WHERE tgname = 'memories_audit_trigger'
                ) THEN
                    CREATE TRIGGER memories_audit_trigger
                    AFTER INSERT OR UPDATE OR DELETE ON memories
                    FOR EACH ROW EXECUTE FUNCTION audit_memories();
                END IF;
            END $body$
        """)

    conn.close()


@pytest.fixture(scope="session", autouse=True)
def init_test_schema():
    """Create schema in the test database. Under xdist, serialized via filelock."""
    import server
    from filelock import FileLock

    # LAYER 3: Reset the singleton connection so _get_conn() reconnects with test URL
    server._conn = None

    # Schema DDL — run once, serialized across xdist workers
    with FileLock(_SCHEMA_LOCK):
        if not os.path.exists(_SCHEMA_DONE):
            _create_schema_ddl()
            # Run server.py idempotent migrations
            server.db_migrate_hybrid()
            server.db_migrate_bitemporal()
            server.db_migrate_uptime()
            with open(_SCHEMA_DONE, "w") as f:
                f.write("done")
        else:
            # Another worker did DDL. Still run migrations (idempotent, fast).
            server.db_migrate_hybrid()
            server.db_migrate_bitemporal()
            server.db_migrate_uptime()

    yield

    # Session teardown: truncate all test data.
    # Under xdist, only the controller (no PYTEST_XDIST_WORKER env var)
    # does the final wipe after all workers are done.
    try:
        if not os.environ.get("PYTEST_XDIST_WORKER"):
            cleanup_conn = psycopg2.connect(TEST_DATABASE_URL)
            cleanup_conn.autocommit = True
            with cleanup_conn.cursor() as cur:
                cur.execute("TRUNCATE memories, memories_audit, server_uptime RESTART IDENTITY CASCADE")
            cleanup_conn.close()
            # Clean up done marker for the next test run
            try:
                os.unlink(_SCHEMA_DONE)
            except FileNotFoundError:
                pass
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Fake embeddings (no Ollama required)
# ──────────────────────────────────────────────────────────────────────────────
def _fake_embedding(text: str) -> list[float]:
    """Deterministic 768-dim vector from SHA-256 hash.

    Different inputs produce different vectors (so cosine similarity varies),
    but no Ollama/OpenAI call is needed. Vectors are L2-normalized.
    """
    dims = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))
    h = hashlib.sha256(text.encode()).digest()
    # Repeat hash bytes to fill dims * 4 bytes (each float = 4 bytes)
    needed = dims * 4
    repeated = h * (needed // len(h) + 1)
    raw_floats = list(struct.unpack(f"<{dims}f", repeated[:needed]))
    # Replace NaN/Inf with small values
    raw_floats = [0.01 if (math.isnan(v) or math.isinf(v)) else v for v in raw_floats]
    # L2-normalize
    norm = math.sqrt(sum(x * x for x in raw_floats))
    if norm > 0:
        raw_floats = [x / norm for x in raw_floats]
    return raw_floats


@pytest.fixture(scope="session", autouse=True)
def fake_embeddings(request):
    """Monkey-patch server.get_embedding to use deterministic fake vectors.

    Skip this patch for tests marked with @pytest.mark.ollama.
    """
    import server
    original = server.get_embedding

    if not request.config.getoption("-m", default="") or "ollama" not in str(request.config.getoption("-m", default="")):
        server.get_embedding = _fake_embedding

    yield

    server.get_embedding = original


# ──────────────────────────────────────────────────────────────────────────────
# Reset connection singleton before each test module
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def reset_connection():
    """Ensure server._conn points to the test database for every test."""
    import server
    if server._conn is not None and server._conn.closed:
        server._conn = None
