-- Open Brain V2 Embedding Diagnostic
-- Read-only. No writes. Run via:
--   Get-Content scripts\diagnose_v2_embeddings.sql | docker exec -i open-brain-v2-db psql -U postgres -d open_brain_v2
-- or:
--   docker exec -i open-brain-v2-db psql -U postgres -d open_brain_v2 < scripts\diagnose_v2_embeddings.sql

\echo '=== [1] COLUMN INVENTORY on memory_index ==='
SELECT
    column_name,
    udt_name      AS pg_type,
    character_maximum_length,
    is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'memory_index'
ORDER BY ordinal_position;

\echo ''
\echo '=== [2] DECLARED COLUMN TYPE (pg_catalog detail) ==='
SELECT
    attname                         AS column_name,
    pg_catalog.format_type(atttypid, atttypmod) AS full_type
FROM pg_catalog.pg_attribute
WHERE attrelid = 'public.memory_index'::regclass
  AND attnum > 0
  AND NOT attisdropped
ORDER BY attnum;

\echo ''
\echo '=== [3] INDEX DEFINITIONS on memory_index ==='
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'memory_index'
  AND schemaname = 'public'
ORDER BY indexname;

\echo ''
\echo '=== [4] TOTAL ROW COUNT ==='
SELECT COUNT(*) AS total_rows FROM memory_index;

\echo ''
\echo '=== [5] DIMENSION DISTRIBUTION (embedding column) ==='
SELECT
    vector_dims(embedding)  AS dims,
    severity,
    active,
    COUNT(*)                AS row_count
FROM memory_index
WHERE embedding IS NOT NULL
GROUP BY vector_dims(embedding), severity, active
ORDER BY dims NULLS FIRST, severity, active;

\echo ''
\echo '=== [6] NULL embedding rows (no vector stored) ==='
SELECT
    kind, severity, active,
    COUNT(*) AS null_embedding_count
FROM memory_index
WHERE embedding IS NULL
GROUP BY kind, severity, active
ORDER BY severity, active;

\echo ''
\echo '=== [7] ACTIVE PATTERN count (this triggers the failing boot query) ==='
SELECT COUNT(*) AS active_pattern_rows
FROM memory_index
WHERE active = TRUE
  AND severity = 'PATTERN';

\echo ''
\echo '=== [8] ACTIVE PATTERN sample (first 10 headlines) ==='
SELECT kind, memory_id, headline, project
FROM memory_index
WHERE active = TRUE
  AND severity = 'PATTERN'
ORDER BY created_at DESC
LIMIT 10;

\echo ''
\echo '=== [9] Does embedding_new column exist? (migration artifact) ==='
SELECT COUNT(*) AS embedding_new_exists
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'memory_index'
  AND column_name  = 'embedding_new';

\echo ''
\echo '=== [10] Does embedding_768_backup column exist? (successful migration sign) ==='
SELECT COUNT(*) AS embedding_768_backup_exists
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'memory_index'
  AND column_name  = 'embedding_768_backup';

\echo ''
\echo '--- V1 comparison: memories table type ---'
\echo '(run this query against the V1 DB: docker exec -i open-brain-db psql -U postgres -d openbrain)'
\echo 'SELECT attname, pg_catalog.format_type(atttypid, atttypmod)'
\echo 'FROM pg_catalog.pg_attribute'
\echo 'WHERE attrelid = '"'"'public.memories'"'"'::regclass AND attnum > 0 AND NOT attisdropped'
\echo 'ORDER BY attnum;'
