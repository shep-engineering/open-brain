#!/usr/bin/env python3
"""
One-time database setup script.

Run with:  python scripts/setup_db.py

Creates or upgrades the v1 database schema to the current shape
expected by server.py. Safe to re-run -- all statements are idempotent.
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")
DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))


def ensure_current_schema(conn: psycopg2.extensions.connection, dimensions: int) -> None:
    """Create or upgrade the v1 schema in-place."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        print("OK  pgvector extension enabled")

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS memories (
                id                   SERIAL      PRIMARY KEY,
                content              TEXT        NOT NULL,
                embedding            VECTOR({dimensions}),
                metadata             JSONB       NOT NULL DEFAULT '{{}}',
                created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                project              TEXT        NOT NULL DEFAULT '',
                annotation           TEXT        NOT NULL DEFAULT '',
                access_count         INTEGER     NOT NULL DEFAULT 0,
                last_accessed        TIMESTAMPTZ,
                upvotes              INTEGER     NOT NULL DEFAULT 0,
                downvotes            INTEGER     NOT NULL DEFAULT 0,
                pinned               BOOLEAN     NOT NULL DEFAULT FALSE,
                updated_at           TIMESTAMPTZ DEFAULT NULL,
                projects             TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
                valid_time           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                transaction_time     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                superseded_by_id     INTEGER     REFERENCES memories(id) ON DELETE SET NULL,
                superseded_at        TIMESTAMPTZ,
                superseded_reason    TEXT,
                skill_trigger        JSONB       DEFAULT NULL,
                last_accessed_uptime FLOAT
            )
        """)
        print(f"OK  memories table ready ({dimensions}-dim vectors)")

        # Upgrade older installs in place.
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS project TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS annotation TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS access_count INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_accessed TIMESTAMPTZ")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS upvotes INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS downvotes INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS projects TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS valid_time TIMESTAMPTZ")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS transaction_time TIMESTAMPTZ")
        cur.execute("UPDATE memories SET valid_time = created_at WHERE valid_time IS NULL")
        cur.execute("UPDATE memories SET transaction_time = created_at WHERE transaction_time IS NULL")
        cur.execute("ALTER TABLE memories ALTER COLUMN valid_time SET NOT NULL")
        cur.execute("ALTER TABLE memories ALTER COLUMN valid_time SET DEFAULT NOW()")
        cur.execute("ALTER TABLE memories ALTER COLUMN transaction_time SET NOT NULL")
        cur.execute("ALTER TABLE memories ALTER COLUMN transaction_time SET DEFAULT NOW()")
        cur.execute("""
            DO $$ BEGIN
                ALTER TABLE memories ADD COLUMN skill_trigger JSONB DEFAULT NULL;
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)
        cur.execute("""
            DO $$ BEGIN
                ALTER TABLE memories ADD COLUMN superseded_by_id INTEGER
                    REFERENCES memories(id) ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS superseded_reason TEXT")
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_accessed_uptime FLOAT")

        cur.execute("""
            CREATE OR REPLACE FUNCTION set_updated_at()
            RETURNS trigger AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        cur.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger WHERE tgname = 'memories_set_updated_at'
                ) THEN
                    CREATE TRIGGER memories_set_updated_at
                    BEFORE UPDATE ON memories
                    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
                END IF;
            END $$;
        """)

        # pgvector's hnsw/ivfflat indexes only support up to 2000 dimensions
        # for the `vector` type. The qwen3-embedding:8b migration (4096d,
        # 2026-06-08) exceeds that, so production runs without an ANN index
        # (sequential cosine scan). Creating it unconditionally fails a fresh
        # high-dim install ("column cannot have more than 2000 dimensions for
        # hnsw index"), so guard on the limit.
        HNSW_MAX_DIMS = 2000
        if dimensions <= HNSW_MAX_DIMS:
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_embedding_hnsw_idx
                ON memories USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
        else:
            print(f"SKIP hnsw index: {dimensions}d exceeds pgvector's "
                  f"{HNSW_MAX_DIMS}-dim limit; using sequential scan "
                  f"(matches production qwen3 4096d).")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS memories_created_at_idx
            ON memories (created_at DESC)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS memories_metadata_gin_idx
            ON memories USING gin (metadata)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS memories_project_idx
            ON memories (project) WHERE project != ''
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS memories_projects_gin_idx
            ON memories USING gin (projects)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS memories_last_accessed_idx
            ON memories (last_accessed ASC NULLS FIRST)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS memories_pinned_project_idx
            ON memories (project) WHERE pinned = TRUE
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_active
            ON memories (id)
            WHERE superseded_by_id IS NULL
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_superseded_by
            ON memories (superseded_by_id)
            WHERE superseded_by_id IS NOT NULL
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_skill_trigger
            ON memories USING gin (skill_trigger)
            WHERE skill_trigger IS NOT NULL
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_valid_time
            ON memories (valid_time)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_transaction_time
            ON memories (transaction_time)
        """)
        print("OK  supporting indexes created")

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
        cur.execute("""
            CREATE INDEX IF NOT EXISTS memories_audit_ts_idx
            ON memories_audit (changed_at DESC)
        """)
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
        print("OK  audit log table and trigger created")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS active_sessions (
                id              BIGSERIAL PRIMARY KEY,
                source          TEXT        NOT NULL,
                project         TEXT,
                cwd             TEXT,
                pid             INTEGER,
                host            TEXT,
                current_task    TEXT,
                started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                heartbeat_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ended_at        TIMESTAMPTZ,
                status          TEXT        NOT NULL DEFAULT 'active',
                metadata        JSONB       DEFAULT NULL,
                pid_create_time DOUBLE PRECISION
            )
        """)
        cur.execute("ALTER TABLE active_sessions ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ")
        cur.execute("ALTER TABLE active_sessions ADD COLUMN IF NOT EXISTS pid_create_time DOUBLE PRECISION")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_active_sessions_status_heartbeat
            ON active_sessions (status, heartbeat_at)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_active_sessions_project_status
            ON active_sessions (project, status)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_active_sessions_source_cwd_status
            ON active_sessions (source, cwd, status)
        """)
        print("OK  active_sessions table ready")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS server_uptime (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                total_seconds FLOAT NOT NULL DEFAULT 0.0,
                last_heartbeat TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            INSERT INTO server_uptime (id, total_seconds)
            VALUES (1, 0.0)
            ON CONFLICT DO NOTHING
        """)
        print("OK  server_uptime table ready")


def main() -> None:
    safe_url = DATABASE_URL.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
    print("\nOpen Brain - Database Setup")
    print(f"    DB  : {safe_url}")
    print(f"    Dims: {DIMENSIONS}\n")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
    except psycopg2.OperationalError as e:
        print(f"Cannot connect to PostgreSQL: {e}")
        print("Start it with:  docker compose up -d\n")
        sys.exit(1)

    try:
        ensure_current_schema(conn, DIMENSIONS)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memories")
            total = cur.fetchone()[0]  # type: ignore[index]

        print(f"\nSetup complete - {total} memories in database")
        print("\nNext steps:")
        print("  1. Pull the embedding model:  ollama pull nomic-embed-text")
        print("  2. Install Python deps:       pip install -r requirements.txt")
        print("  3. Add to your MCP client     (see README.md)")
        print()

    except psycopg2.errors.UndefinedFile:  # type: ignore[attr-defined]
        print("pgvector extension not found.")
        print("Use the pgvector Docker image:  pgvector/pgvector:pg16\n")
        sys.exit(1)
    except Exception as e:
        print(f"Setup failed: {e}\n")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
