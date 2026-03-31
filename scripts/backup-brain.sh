#!/usr/bin/env bash
# Daily backup of Open Brain PostgreSQL database
# Add to crontab: 0 3 * * * /mnt/f/open-brain/scripts/backup-brain.sh

BACKUP_DIR="/mnt/f/open-brain/backups"
CONTAINER="open-brain-db"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
KEEP_DAYS=30

mkdir -p "$BACKUP_DIR"

# Dump via docker exec
docker exec "$CONTAINER" pg_dump -U postgres openbrain \
  > "$BACKUP_DIR/brain-$TIMESTAMP.sql" 2>/dev/null

if [ $? -eq 0 ] && [ -s "$BACKUP_DIR/brain-$TIMESTAMP.sql" ]; then
    echo "[backup] $TIMESTAMP: OK ($(wc -l < "$BACKUP_DIR/brain-$TIMESTAMP.sql") lines)"
    # Prune old backups
    find "$BACKUP_DIR" -name "brain-*.sql" -mtime +$KEEP_DAYS -delete 2>/dev/null
else
    echo "[backup] $TIMESTAMP: FAILED" >&2
    rm -f "$BACKUP_DIR/brain-$TIMESTAMP.sql"
    exit 1
fi
