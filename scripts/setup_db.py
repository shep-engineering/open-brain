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
                    id         SERIAL      PRIMARY KEY,
                    content    TEXT        NOT NULL,
                    embedding  VECTOR({DIMENSIONS}),
                    metadata   JSONB       NOT NULL DEFAULT '{{}}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
            print("✓  Supporting indexes created")

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
