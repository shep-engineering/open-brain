-- Open Brain V1 Embedding Diagnostic — run against openbrain DB (port 5432)
-- Get-Content scripts\diagnose_v1_embeddings.sql | docker exec -i open-brain-db psql -U postgres -d openbrain

\echo '=== V1: memories column types ==='
SELECT
    attname                         AS column_name,
    pg_catalog.format_type(atttypid, atttypmod) AS full_type
FROM pg_catalog.pg_attribute
WHERE attrelid = 'public.memories'::regclass
  AND attnum > 0
  AND NOT attisdropped
ORDER BY attnum;

\echo ''
\echo '=== V1: index definitions on memories ==='
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'memories' AND schemaname = 'public'
ORDER BY indexname;

\echo ''
\echo '=== V1: embedding dimension distribution ==='
SELECT
    vector_dims(embedding) AS dims,
    COUNT(*) AS row_count
FROM memories
WHERE embedding IS NOT NULL
GROUP BY vector_dims(embedding)
ORDER BY dims;

\echo ''
\echo '=== V1: embedding_768_backup column exists? ==='
SELECT COUNT(*) AS embedding_768_backup_exists
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'memories'
  AND column_name  = 'embedding_768_backup';
