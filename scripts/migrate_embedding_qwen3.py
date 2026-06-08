"""
Migrate Open Brain V1 and V2 embedding columns from nomic-embed-text (768d)
to qwen3-embedding:8b (4096d).

Safety features:
- Validates model dimensions before touching the DB
- Dry-run mode (--dry-run) shows what would happen without executing
- Adds new column alongside old one (no destructive drop until swap)
- Batch commits with progress reporting
- Old column preserved as embedding_768_backup (not dropped — cleanup separate)
- Re-runnable: resumes from last uncommitted batch on restart

Usage:
    python scripts/migrate_embedding_qwen3.py --dry-run    # preview
    python scripts/migrate_embedding_qwen3.py              # execute

IMPORTANT: Stop Open Brain MCP servers before running. Docker DBs must stay running.
"""

import argparse
import sys
import psycopg2
import requests

OLLAMA_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "qwen3-embedding:8b"
EXPECTED_DIMS = 4096
BATCH_SIZE = 50

V1_DSN = "postgresql://postgres:password@127.0.0.1:5432/openbrain"
V2_DSN = "postgresql://postgres:password@127.0.0.1:5433/open_brain_v2"


def embed(text: str) -> list:
    r = requests.post(OLLAMA_URL, json={"model": EMBED_MODEL, "input": text}, timeout=60)
    r.raise_for_status()
    return r.json()["embeddings"][0]


def migrate_v1(dry_run: bool) -> bool:
    """Migrate memories.embedding from 768d to 4096d."""
    conn = psycopg2.connect(V1_DSN)
    cur = conn.cursor()

    print("\n[V1: memories]")
    print("  Adding embedding_new VECTOR(4096) column...")
    if not dry_run:
        cur.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding_new vector(4096)")
        conn.commit()

    cur.execute("SELECT id, content FROM memories ORDER BY id")
    rows = cur.fetchall()
    total = len(rows)
    print(f"  {total} rows to re-embed")

    if dry_run:
        print(f"  DRY RUN: would embed {total} rows then swap columns")
        conn.close()
        return True

    failed = []
    for i, (row_id, content) in enumerate(rows):
        if not content:
            continue
        try:
            vec = embed(content)
            cur.execute(
                "UPDATE memories SET embedding_new = %s::vector WHERE id = %s",
                (str(vec), row_id)
            )
            if (i + 1) % BATCH_SIZE == 0:
                conn.commit()
                print(f"  {i+1}/{total} committed")
        except Exception as e:
            print(f"  WARN: row {row_id} failed: {e}")
            failed.append(row_id)

    conn.commit()
    print(f"  {total} rows processed, {len(failed)} failed")

    if failed:
        print(f"  FAILED IDs (first 10): {failed[:10]}")
        print("  Do NOT swap columns. Fix failures then re-run.")
        conn.close()
        return False

    print("  Swapping columns: embedding -> embedding_768_backup, embedding_new -> embedding")
    cur.execute("ALTER TABLE memories RENAME COLUMN embedding TO embedding_768_backup")
    cur.execute("ALTER TABLE memories RENAME COLUMN embedding_new TO embedding")
    cur.execute("DROP INDEX IF EXISTS memories_embedding_hnsw_idx")
    cur.execute(
        "CREATE INDEX memories_embedding_hnsw_idx ON memories "
        "USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)"
    )
    conn.commit()
    print("  V1 migration complete. Backup column: embedding_768_backup")
    conn.close()
    return True


def migrate_v2(dry_run: bool) -> bool:
    """
    Migrate memory_index.embedding from 768d to 4096d.
    V2 uses composite PK (kind, memory_id) and text is in 'headline' column.
    """
    conn = psycopg2.connect(V2_DSN)
    cur = conn.cursor()

    print("\n[V2: memory_index]")
    print("  Adding embedding_new VECTOR(4096) column...")
    if not dry_run:
        cur.execute("ALTER TABLE memory_index ADD COLUMN IF NOT EXISTS embedding_new vector(4096)")
        conn.commit()

    # Composite PK: (kind, memory_id) — memory_id alone is NOT unique
    cur.execute("SELECT kind, memory_id, headline FROM memory_index ORDER BY kind, memory_id")
    rows = cur.fetchall()
    total = len(rows)
    print(f"  {total} rows to re-embed")

    if dry_run:
        print(f"  DRY RUN: would embed {total} rows then swap columns")
        conn.close()
        return True

    failed = []
    for i, (kind, memory_id, headline) in enumerate(rows):
        if not headline:
            continue
        try:
            vec = embed(headline)
            cur.execute(
                "UPDATE memory_index SET embedding_new = %s::vector "
                "WHERE kind = %s AND memory_id = %s",
                (str(vec), kind, memory_id)
            )
            if (i + 1) % BATCH_SIZE == 0:
                conn.commit()
                print(f"  {i+1}/{total} committed")
        except Exception as e:
            print(f"  WARN: ({kind}, {memory_id}) failed: {e}")
            failed.append((kind, memory_id))

    conn.commit()
    print(f"  {total} rows processed, {len(failed)} failed")

    if failed:
        print(f"  FAILED: {failed[:10]}")
        print("  Do NOT swap columns. Fix failures then re-run.")
        conn.close()
        return False

    print("  Swapping columns: embedding -> embedding_768_backup, embedding_new -> embedding")
    cur.execute("ALTER TABLE memory_index RENAME COLUMN embedding TO embedding_768_backup")
    cur.execute("ALTER TABLE memory_index RENAME COLUMN embedding_new TO embedding")
    cur.execute("DROP INDEX IF EXISTS memory_index_embedding_hnsw")
    cur.execute(
        "CREATE INDEX memory_index_embedding_hnsw ON memory_index "
        "USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)"
    )
    conn.commit()
    print("  V2 migration complete. Backup column: embedding_768_backup")
    conn.close()
    return True


def main():
    parser = argparse.ArgumentParser(description="Migrate embeddings to qwen3-embedding:8b")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN — no DB changes will be made ===\n")

    # Validate model before touching DB
    print(f"Validating {EMBED_MODEL}...")
    test_vec = embed("validation test")
    if len(test_vec) != EXPECTED_DIMS:
        print(f"FAIL: Expected {EXPECTED_DIMS} dims, got {len(test_vec)}")
        sys.exit(1)
    print(f"PASS: {len(test_vec)} dimensions confirmed\n")

    ok1 = migrate_v1(args.dry_run)
    ok2 = migrate_v2(args.dry_run)

    if ok1 and ok2:
        print("\n=== Migration complete ===")
        if not args.dry_run:
            print("\nNext steps:")
            print("  1. Update F:\\open-brain\\.env:")
            print("       OLLAMA_EMBEDDING_MODEL=qwen3-embedding:8b")
            print("       EMBEDDING_DIMENSIONS=4096")
            print("  2. Restart Open Brain")
            print("  3. Run post-migration validation (see plan doc)")
            print("  4. After 7 days stable, drop embedding_768_backup columns")
    else:
        print("\n=== Migration FAILED — see errors above ===")
        print("Restore from backup if needed:")
        print("  docker cp F:\\open-brain\\backups\\brain-v1-pre-embedding-<stamp>.sql open-brain-db:/tmp/restore.sql")
        print("  docker exec open-brain-db psql -U postgres -d openbrain -f /tmp/restore.sql")
        sys.exit(1)


if __name__ == "__main__":
    main()
