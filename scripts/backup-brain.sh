#!/usr/bin/env bash
# Daily backup of the Open Brain PostgreSQL databases.
# Add to crontab: 0 3 * * * /mnt/f/open-brain/scripts/backup-brain.sh
#
# Backs up BOTH brains:
#   - v2 (open_brain_v2, container open-brain-v2-db) — the LIVE brain since the
#     v1 decommission (2026-07-09). This is the one that must never be missed.
#   - v1 (openbrain, container open-brain-db) — decommissioned but the container
#     still holds historical data; keep dumping it while it exists.
#
# Prior to 2026-09-12 this script dumped ONLY v1, so the live v2 brain had no
# automated backup. Fixed here.

set -u

BACKUP_DIR="/mnt/f/open-brain/backups"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
KEEP_DAYS=30

mkdir -p "$BACKUP_DIR"

# name|container|dbname  — v2 first so the live brain is prioritized.
TARGETS=(
  "v2|open-brain-v2-db|open_brain_v2"
  "v1|open-brain-db|openbrain"
)

overall_rc=0

for target in "${TARGETS[@]}"; do
  IFS='|' read -r name container db <<< "$target"
  out="$BACKUP_DIR/${db}-${TIMESTAMP}.sql"

  # Skip cleanly if the container isn't running (e.g. v1 after full removal),
  # so a missing decommissioned DB never fails the live-brain backup.
  if ! docker ps --format '{{.Names}}' | grep -qx "$container"; then
    echo "[backup] $TIMESTAMP: SKIP $name ($container not running)" >&2
    continue
  fi

  docker exec "$container" pg_dump -U postgres "$db" > "$out" 2>/dev/null
  rc=$?

  # Valid = pg_dump succeeded, file non-empty, AND carries the completion marker
  # (a truncated/interrupted dump lacks it — an unrestorable backup is not a
  # backup).
  if [ $rc -eq 0 ] && [ -s "$out" ] && grep -q "PostgreSQL database dump complete" "$out"; then
    echo "[backup] $TIMESTAMP: OK $name -> $(basename "$out") ($(wc -l < "$out") lines)"
    find "$BACKUP_DIR" -name "${db}-*.sql" -mtime +$KEEP_DAYS -delete 2>/dev/null
  else
    echo "[backup] $TIMESTAMP: FAILED $name (rc=$rc, incomplete or empty dump)" >&2
    rm -f "$out"
    overall_rc=1
  fi
done

exit $overall_rc
