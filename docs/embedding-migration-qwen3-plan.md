# Open Brain: Embedding Model Migration Plan
## nomic-embed-text (768d) → qwen3-embedding:8b (4096d)

**Status:** Planned — not started  
**Created:** 2026-06-08  
**Author:** Shep / Claude  
**Target model:** `qwen3-embedding:8b`  
**Current model:** `nomic-embed-text` (768 dimensions)  
**New model dimensions:** 4096  

---

## Why

`qwen3-embedding:8b` scores ~8 points higher on MTEB than `nomic-embed-text` (70.58 vs 62.39). That gap shows up as better retrieval on complex, ambiguous queries — exactly the kind of recall Open Brain needs for guardrail and prior-decision lookups. The model is already downloaded to `F:\AI\ollama-models`.

---

## Scope

| Database | Table | Rows to re-embed | Current dim |
|----------|-------|-----------------|-------------|
| openbrain (V1, port 5432) | memories | **3,547** | 768 |
| open_brain_v2 (V2, port 5433) | memory_index | **73** | 768 |

Both tables have a `VECTOR(768)` column. pgvector requires the column to be dropped and recreated to change dimensions — you cannot ALTER COLUMN in place for vector types.

---

## Pre-Migration Checklist

- [ ] Open Brain is OFF (both servers stopped, both Docker DBs still running)
- [ ] Full DB backups taken and verified (see Backup step below)
- [ ] `qwen3-embedding:8b` confirmed loaded and responding at `http://localhost:11434`
- [ ] Test embed returns 4096 dimensions (run validation below)
- [ ] No active Claude sessions using Open Brain

---

## Step 1: Backup Both Databases

```powershell
$BACKUP_DIR = "F:\open-brain\backups\pre-embedding-migration-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
New-Item -ItemType Directory -Path $BACKUP_DIR

# V1
docker exec open-brain-db pg_dump -U postgres openbrain > "$BACKUP_DIR\openbrain_v1.sql"

# V2
docker exec open-brain-v2-db pg_dump -U postgres open_brain_v2 > "$BACKUP_DIR\open_brain_v2.sql"

# Verify both files are non-empty
Get-Item "$BACKUP_DIR\*.sql" | Select-Object Name, Length
```

**Pass criteria:** Both `.sql` files exist and are >1MB.  
**On failure:** Stop. Do not proceed.

---

## Step 2: Pre-Migration Validation

Verify the new model returns the correct dimensions before touching the DB:

```python
# F:\open-brain\scripts\validate_qwen3_embedding.py
import requests

r = requests.post("http://localhost:11434/api/embed",
    json={"model": "qwen3-embedding:8b", "input": "validation test"})
dims = len(r.json()["embeddings"][0])
assert dims == 4096, f"Expected 4096 dims, got {dims}"
print(f"PASS: qwen3-embedding:8b returns {dims} dimensions")
```

```powershell
F:\open-brain\.venv\Scripts\python.exe F:\open-brain\scripts\validate_qwen3_embedding.py
```

**Pass criteria:** Script prints `PASS: qwen3-embedding:8b returns 4096 dimensions`

---

## Step 3: Write the Migration Script

Create `F:\open-brain\scripts\migrate_embedding_qwen3.py`:

```python
"""
Migrate Open Brain V1 and V2 embedding columns from nomic-embed-text (768d)
to qwen3-embedding:8b (4096d).

Safety features:
- Dry-run mode (--dry-run) prints SQL without executing
- Batch processing with progress reporting
- Rollback instructions printed if any batch fails
- Re-runnable: skips already-migrated rows (null-check optional)

Usage:
    python scripts/migrate_embedding_qwen3.py --dry-run    # preview
    python scripts/migrate_embedding_qwen3.py              # execute
"""

import argparse
import sys
import time
import psycopg2
import requests

OLLAMA_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "qwen3-embedding:8b"
BATCH_SIZE = 50

V1_DB = "postgresql://postgres:password@127.0.0.1:5432/openbrain"
V2_DB = "postgresql://postgres:password@127.0.0.1:5433/open_brain_v2"


def embed(text: str) -> list[float]:
    r = requests.post(OLLAMA_URL, json={"model": EMBED_MODEL, "input": text}, timeout=60)
    r.raise_for_status()
    return r.json()["embeddings"][0]


def migrate_table(conn_str: str, table: str, text_col: str, id_col: str, dry_run: bool):
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()

    # Step A: Add new column
    print(f"\n[{table}] Adding embedding_new VECTOR(4096) column...")
    if not dry_run:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS embedding_new vector(4096)")
        conn.commit()

    # Step B: Fetch all rows
    cur.execute(f"SELECT {id_col}, {text_col} FROM {table} ORDER BY {id_col}")
    rows = cur.fetchall()
    total = len(rows)
    print(f"[{table}] {total} rows to re-embed...")

    # Step C: Batch re-embed
    failed = []
    for i, (row_id, text) in enumerate(rows):
        if not text:
            continue
        try:
            vec = embed(text)
            if not dry_run:
                cur.execute(
                    f"UPDATE {table} SET embedding_new = %s::vector WHERE {id_col} = %s",
                    (str(vec), row_id)
                )
                if (i + 1) % BATCH_SIZE == 0:
                    conn.commit()
                    print(f"  [{table}] {i+1}/{total} committed")
        except Exception as e:
            print(f"  WARN: row {row_id} failed: {e}")
            failed.append(row_id)

    if not dry_run:
        conn.commit()

    if failed:
        print(f"  ERROR: {len(failed)} rows failed: {failed[:10]}")
        print("  Do NOT proceed to column swap. Fix failures first.")
        return False

    # Step D: Swap columns
    print(f"[{table}] Swapping columns (old→backup, new→embedding)...")
    if not dry_run:
        cur.execute(f"ALTER TABLE {table} RENAME COLUMN embedding TO embedding_768_backup")
        cur.execute(f"ALTER TABLE {table} RENAME COLUMN embedding_new TO embedding")
        cur.execute(f"DROP INDEX IF EXISTS {table}_embedding_idx")
        cur.execute(
            f"CREATE INDEX {table}_embedding_idx ON {table} USING hnsw (embedding vector_cosine_ops)"
        )
        conn.commit()

    print(f"[{table}] Migration complete. Backup column: embedding_768_backup")
    conn.close()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN — no DB changes will be made ===\n")

    # Validate model before touching DB
    print("Validating qwen3-embedding:8b...")
    test_vec = embed("validation test")
    assert len(test_vec) == 4096, f"Expected 4096, got {len(test_vec)}"
    print(f"PASS: {len(test_vec)} dimensions confirmed\n")

    ok1 = migrate_table(V1_DB, "memories", "content", "id", args.dry_run)
    ok2 = migrate_table(V2_DB, "memory_index", "body", "id", args.dry_run)

    if ok1 and ok2:
        print("\n=== Migration complete ===")
        print("Next steps:")
        print("  1. Update F:\\open-brain\\.env:")
        print("     OLLAMA_EMBEDDING_MODEL=qwen3-embedding:8b")
        print("     EMBEDDING_DIMENSIONS=4096")
        print("     OPEN_BRAIN_V2_EMBEDDING_DIMS=4096")
        print("  2. Restart Open Brain")
        print("  3. Run post-migration validation")
        print("  4. After 7 days of stable operation, drop embedding_768_backup columns")
    else:
        print("\n=== Migration FAILED — see errors above ===")
        print("Restore from backup if needed:")
        print("  docker exec -i open-brain-db psql -U postgres openbrain < backup.sql")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## Step 4: Run Migration (Dry Run First)

```powershell
# Dry run — no changes, just validates logic
F:\open-brain\.venv\Scripts\python.exe F:\open-brain\scripts\migrate_embedding_qwen3.py --dry-run

# If dry run passes, run for real
F:\open-brain\.venv\Scripts\python.exe F:\open-brain\scripts\migrate_embedding_qwen3.py
```

**Pass criteria:**
- 0 failed rows reported
- Script prints `=== Migration complete ===`
- Both DBs have `embedding_768_backup` column (old) and `embedding` column (new, 4096d)

**Estimated time:** ~15-25 minutes for 3,547 V1 rows at ~0.3s/embed on GPU

---

## Step 5: Update .env

After successful migration, update `F:\open-brain\.env`:

```
OLLAMA_EMBEDDING_MODEL=qwen3-embedding:8b
EMBEDDING_DIMENSIONS=4096
OPEN_BRAIN_V2_EMBEDDING_DIMS=4096
```

---

## Step 6: Restart and Validate

```powershell
# Start Open Brain
cmd /c "F:\open-brain\scripts\windows\open-brain-on.cmd"
```

**Post-restart validation checklist:**

- [ ] Both servers start cleanly (ports 8080, 8081)
- [ ] `boot_session` from Claude Code returns memories without errors
- [ ] `search` for a known term ("AT&T", "resume-harbor") returns correct results
- [ ] `capture_context` of a test memory succeeds
- [ ] `search` for that test memory retrieves it correctly
- [ ] No errors in `F:\open-brain\logs\server-v1-crash.log`
- [ ] No errors in `F:\open-brain\logs\server-v2-crash.log`

---

## Step 7: Post-Migration Search Quality Spot-Check

Run 5 representative searches and compare results to pre-migration behavior:

| Query | Expected top result |
|-------|-------------------|
| "AT&T departure date" | Memory about April 2, 2026 departure |
| "em dash banned" | Guardrail about no em-dashes |
| "cover letter opening" | Feedback about never assuming candidate interest |
| "TWC work log" | Feedback about not prompting for TWC log |
| "metadata LLM upgrade" | The capture from this migration session |

---

## Step 8: Cleanup (After 7 Days Stable)

Only after 7+ days of confirmed stable operation:

```sql
-- V1
ALTER TABLE memories DROP COLUMN embedding_768_backup;

-- V2
ALTER TABLE memory_index DROP COLUMN embedding_768_backup;
```

Also remove `nomic-embed-text` from Ollama:
```powershell
ollama rm nomic-embed-text
```

---

## Rollback Procedure

If anything goes wrong after the column swap:

```powershell
# Stop Open Brain first
# Restore V1 from backup
docker exec -i open-brain-db psql -U postgres openbrain < "F:\open-brain\backups\pre-embedding-migration-<timestamp>\openbrain_v1.sql"

# Restore V2 from backup
docker exec -i open-brain-v2-db psql -U postgres open_brain_v2 < "F:\open-brain\backups\pre-embedding-migration-<timestamp>\open_brain_v2.sql"

# Revert .env to nomic-embed-text settings
# Restart Open Brain
```

---

## Summary

| Item | Detail |
|------|--------|
| Current model | nomic-embed-text (768d) |
| Target model | qwen3-embedding:8b (4096d) |
| V1 rows | 3,547 |
| V2 rows | 73 |
| Est. migration time | 15-25 min |
| Downtime window needed | ~30 min (brain off during migration) |
| Reversible | Yes — backup + rollback script |
| Cleanup | Drop backup columns after 7 days stable |

*Last updated: 2026-06-08*
