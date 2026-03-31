#!/usr/bin/env python3
"""
One-time database setup script.

Run with:  python scripts/setup_db.py

Creates the memories table, pgvector extension, and all required indexes.
Safe to re-run — all statements use IF NOT EXISTS.
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")
DIMENSIONS   = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))

def main() -> None:
    safe_url = DATABASE_URL.replace("://", "://<credentials>@", 1).split("@", 1)[-1]
    print(f"\n🧠  Open Brain — Database Setup")
    print(f"    DB  : {safe_url}")
    print(f"    Dims: {DIMENSIONS}\n")

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
    except psycopg2.OperationalError as e:
        print(f"❌  Cannot connect to PostgreSQL: {e}")
        print("    Start it with:  docker compose up -d\n")
        sys.exit(1)

    try:
        with conn.cursor() as cur:

            # 1. pgvector extension
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            print("✓  pgvector extension enabled")

            # 2. memories table
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS memories (
                    id            SERIAL      PRIMARY KEY,
                    content       TEXT        NOT NULL,
                    embedding     VECTOR({DIMENSIONS}),
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
            print(f"✓  memories table ready  ({DIMENSIONS}-dim vectors)")

            # 3. HNSW index — fast approximate nearest-neighbour search
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_embedding_hnsw_idx
                ON memories USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
            print("✓  HNSW vector index created")

            # 4. B-tree index for time-range queries
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_created_at_idx
                ON memories (created_at DESC)
            """)

            # 5. GIN index for JSONB metadata filtering
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_metadata_gin_idx
                ON memories USING gin (metadata)
            """)

            # 6. Project filter index
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_project_idx
                ON memories (project) WHERE project != ''
            """)

            # 7. Last-accessed index for pruning
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_last_accessed_idx
                ON memories (last_accessed ASC NULLS FIRST)
            """)

            # 8. Pinned memories index (partial -- only pinned rows)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS memories_pinned_project_idx
                ON memories (project) WHERE pinned = TRUE
            """)
            print("✓  Supporting indexes created")

            # 9. Audit log — append-only transaction log of all changes
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

            # 10. Audit trigger — logs INSERT, UPDATE, DELETE with full row data
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
            print("✓  Audit log table and trigger created")

            # Verify
            cur.execute("SELECT COUNT(*) FROM memories")
            total = cur.fetchone()[0]  # type: ignore[index]

        print(f"\n✅  Setup complete — {total} memories in database")
        print("\nNext steps:")
        print("  1. Pull the embedding model:  ollama pull nomic-embed-text")
        print("  2. Install Python deps:        pip install -r requirements.txt")
        print("  3. Add to your MCP client     (see README.md)")
        print()

    except psycopg2.errors.UndefinedFile:  # type: ignore[attr-defined]
        print("❌  pgvector extension not found.")
        print("    Use the pgvector Docker image:  pgvector/pgvector:pg16\n")
        sys.exit(1)
    except Exception as e:
        print(f"❌  Setup failed: {e}\n")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
