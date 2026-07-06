"""
V2 embedding column repair — step 2 of the v2 embedding fix.

Prerequisites:
  1. Run scripts/diagnose_v2_embeddings.sql first and share results.
  2. Run scripts/fix_v2_mcp_config.ps1 first and restart Claude Code.
  3. Open Brain servers MUST BE STOPPED (Docker DBs stay running).
  4. qwen3-embedding:8b must be loaded in Ollama.

This script handles the following cases detected by the diagnostic:

CASE A — Successful prior migration (embedding = vector(4096)):
  - Column is already 4096d. Nothing to ALTER.
  - Find rows with NULL embedding (writes that failed post-migration) and re-embed.
  - Verify no rows have wrong-dimension data.

CASE B — Failed prior migration (embedding = vector(768), embedding_new = vector(4096)):
  - ALTER TABLE memory_index ALTER COLUMN embedding TYPE vector(4096)
    USING embedding_new (the already-correct data from the migration run)
  - Drop the now-redundant embedding_new column.
  - Re-embed any rows where embedding_new was NULL (missed by prior migration).
  - Rename orphan backup: embedding_768_backup doesn't exist in this case
    (migration rolled back before it could be created).

CASE C — Clean 768d state (no migration artifacts):
  - Full re-embed: ALTER + re-embed all rows from scratch.
  - This is the slowest path (~1 min for typical V2 row count).

Usage:
    python scripts/fix_v2_embedding_column.py --diagnose   # detect case, no changes
    python scripts/fix_v2_embedding_column.py --apply       # execute the fix
"""
import argparse
import sys
import json
import psycopg2
import requests

OLLAMA_URL  = "http://localhost:11434/api/embed"
EMBED_MODEL = "qwen3-embedding:8b"
EXPECTED_DIMS = 4096
BATCH_SIZE    = 50

V2_DSN = "postgresql://postgres:password@127.0.0.1:5433/open_brain_v2"


def embed(text: str) -> list:
    r = requests.post(OLLAMA_URL, json={"model": EMBED_MODEL, "input": text}, timeout=60)
    r.raise_for_status()
    return r.json()["embeddings"][0]


def validate_model() -> bool:
    print(f"Validating {EMBED_MODEL}...")
    try:
        vec = embed("validation test")
        if len(vec) != EXPECTED_DIMS:
            print(f"FAIL: expected {EXPECTED_DIMS} dims, got {len(vec)}")
            return False
        print(f"PASS: {EMBED_MODEL} returns {len(vec)} dimensions")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def column_exists(cur, table: str, col: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
        (table, col),
    )
    return cur.fetchone() is not None


def column_type(cur, table: str, col: str) -> str | None:
    cur.execute(
        "SELECT pg_catalog.format_type(atttypid, atttypmod) "
        "FROM pg_catalog.pg_attribute "
        "WHERE attrelid = %s::regclass AND attname = %s AND NOT attisdropped",
        (f"public.{table}", col),
    )
    row = cur.fetchone()
    return row[0] if row else None


def detect_case(cur) -> str:
    embed_type = column_type(cur, "memory_index", "embedding")
    has_new    = column_exists(cur, "memory_index", "embedding_new")
    has_backup = column_exists(cur, "memory_index", "embedding_768_backup")

    print(f"  memory_index.embedding type : {embed_type}")
    print(f"  embedding_new exists        : {has_new}")
    print(f"  embedding_768_backup exists : {has_backup}")

    if embed_type and "4096" in embed_type:
        return "A"  # already 4096d — just repair NULLs
    elif embed_type and "768" in embed_type and has_new:
        return "B"  # failed migration — embedding_new has the good data
    else:
        return "C"  # clean 768d — full re-embed needed


def count_null_embeddings(cur) -> int:
    cur.execute("SELECT COUNT(*) FROM memory_index WHERE embedding IS NULL")
    return cur.fetchone()[0]


def count_rows_by_dim(cur) -> dict:
    cur.execute(
        """
        SELECT vector_dims(embedding) AS dims, COUNT(*) AS n
        FROM memory_index
        WHERE embedding IS NOT NULL
        GROUP BY dims ORDER BY dims
        """
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def reembed_nulls(conn, cur, dry_run: bool) -> int:
    """Re-embed rows where embedding IS NULL — these were written post-migration
    with the wrong model and their INSERT into memory_index failed silently."""
    cur.execute(
        "SELECT kind, memory_id, headline FROM memory_index WHERE embedding IS NULL"
    )
    rows = cur.fetchall()
    if not rows:
        print("  No NULL-embedding rows. Nothing to re-embed.")
        return 0
    print(f"  {len(rows)} rows with NULL embedding will be re-embedded.")
    if dry_run:
        print("  DRY RUN — skipping.")
        return 0
    failed = []
    for i, (kind, memory_id, headline) in enumerate(rows):
        if not headline:
            continue
        try:
            vec = embed(headline)
            cur.execute(
                "UPDATE memory_index SET embedding = %s::vector "
                "WHERE kind = %s AND memory_id = %s",
                ("[" + ",".join(f"{v:.8f}" for v in vec) + "]", kind, memory_id),
            )
            if (i + 1) % BATCH_SIZE == 0:
                conn.commit()
                print(f"  {i+1}/{len(rows)} committed")
        except Exception as e:
            print(f"  WARN ({kind}, {memory_id}): {e}")
            failed.append((kind, memory_id))
    conn.commit()
    print(f"  Re-embedded {len(rows) - len(failed)}, failed {len(failed)}")
    return len(failed)


def apply_case_a(conn, cur, dry_run: bool):
    print("\n[CASE A] Column already vector(4096). Repairing NULL-embedding rows only.")
    dims = count_rows_by_dim(cur)
    print(f"  Current dimension distribution: {dims}")
    nulls = count_null_embeddings(cur)
    print(f"  NULL embedding rows: {nulls}")
    reembed_nulls(conn, cur, dry_run)


def apply_case_b(conn, cur, dry_run: bool):
    print("\n[CASE B] Column is vector(768) but embedding_new (vector(4096)) exists.")
    print("  Plan: ALTER embedding TYPE vector(4096) USING embedding_new, then drop embedding_new.")

    cur.execute(
        "SELECT COUNT(*) FROM memory_index WHERE embedding_new IS NULL AND embedding IS NOT NULL"
    )
    rows_missing_new = cur.fetchone()[0]
    print(f"  Rows where embedding_new IS NULL (need fresh re-embed): {rows_missing_new}")

    if dry_run:
        print("  DRY RUN — no changes.")
        return

    # Backup: write a JSON snapshot of affected rows before any change.
    cur.execute("SELECT kind, memory_id, headline FROM memory_index WHERE embedding_new IS NULL")
    missing = cur.fetchall()

    print("  Step B1: ALTER COLUMN embedding TYPE vector(4096) USING embedding_new...")
    cur.execute(
        "ALTER TABLE memory_index ALTER COLUMN embedding TYPE vector(4096) "
        "USING embedding_new"
    )
    conn.commit()
    print("  Step B1 done.")

    print("  Step B2: DROP COLUMN embedding_new...")
    cur.execute("ALTER TABLE memory_index DROP COLUMN embedding_new")
    conn.commit()
    print("  Step B2 done.")

    # Re-embed rows that had NULL embedding_new (now NULL embedding after USING).
    if missing:
        print(f"  Step B3: Re-embedding {len(missing)} rows that had NULL embedding_new...")
        reembed_nulls(conn, cur, dry_run)

    dims = count_rows_by_dim(cur)
    print(f"  Final dimension distribution: {dims}")


def apply_case_c(conn, cur, dry_run: bool):
    print("\n[CASE C] Column is vector(768) — full ALTER + re-embed needed.")
    cur.execute("SELECT COUNT(*) FROM memory_index")
    total = cur.fetchone()[0]
    print(f"  Total rows: {total}")

    if dry_run:
        print("  DRY RUN — no changes.")
        return

    print("  Step C1: Adding embedding_new column vector(4096)...")
    cur.execute(
        "ALTER TABLE memory_index ADD COLUMN IF NOT EXISTS embedding_new vector(4096)"
    )
    conn.commit()

    print(f"  Step C2: Re-embedding {total} rows with {EMBED_MODEL}...")
    cur.execute("SELECT kind, memory_id, headline FROM memory_index ORDER BY kind, memory_id")
    rows = cur.fetchall()
    failed = []
    for i, (kind, memory_id, headline) in enumerate(rows):
        if not headline:
            continue
        try:
            vec = embed(headline)
            cur.execute(
                "UPDATE memory_index SET embedding_new = %s::vector "
                "WHERE kind = %s AND memory_id = %s",
                ("[" + ",".join(f"{v:.8f}" for v in vec) + "]", kind, memory_id),
            )
            if (i + 1) % BATCH_SIZE == 0:
                conn.commit()
                print(f"  {i+1}/{total} committed")
        except Exception as e:
            print(f"  WARN ({kind}, {memory_id}): {e}")
            failed.append((kind, memory_id))

    conn.commit()
    print(f"  {total - len(failed)} rows re-embedded, {len(failed)} failed")

    if failed:
        print(f"  FAILED (first 10): {failed[:10]}")
        print("  Do NOT swap columns. Fix failures then re-run.")
        conn.close()
        sys.exit(1)

    print("  Step C3: Swapping columns (embedding → embedding_768_backup, embedding_new → embedding)...")
    cur.execute("ALTER TABLE memory_index RENAME COLUMN embedding TO embedding_768_backup")
    cur.execute("ALTER TABLE memory_index RENAME COLUMN embedding_new TO embedding")
    conn.commit()
    print("  Column swap done. (No HNSW index — pgvector HNSW max 2000d, column is 4096d.)")

    dims = count_rows_by_dim(cur)
    print(f"  Final dimension distribution: {dims}")


def main():
    parser = argparse.ArgumentParser(description="Fix V2 embedding column")
    parser.add_argument("--diagnose", action="store_true", help="Detect case only, no changes")
    parser.add_argument("--apply",    action="store_true", help="Apply the fix")
    args = parser.parse_args()

    if not args.diagnose and not args.apply:
        parser.print_help()
        sys.exit(1)

    dry_run = not args.apply

    if not validate_model():
        print("Model validation failed. Is qwen3-embedding:8b loaded in Ollama?")
        sys.exit(1)

    conn = psycopg2.connect(V2_DSN)
    conn.autocommit = False
    cur = conn.cursor()

    print("\n=== Detecting case... ===")
    case = detect_case(cur)
    print(f"\n>>> DETECTED: CASE {case}")

    if case == "A":
        apply_case_a(conn, cur, dry_run)
    elif case == "B":
        apply_case_b(conn, cur, dry_run)
    elif case == "C":
        apply_case_c(conn, cur, dry_run)

    if dry_run:
        print("\nDRY RUN complete — no changes made. Re-run with --apply to execute.")
    else:
        print("\nFix complete.")
        print("Next: update brain_v2/config.py defaults + restart Open Brain.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
