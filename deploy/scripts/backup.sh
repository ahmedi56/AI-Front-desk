#!/bin/bash
# Daily encrypted database backup. Run from cron (see docs/RUNBOOK.md).
# Needs: BACKUP_PASSPHRASE_FILE (a file only root can read) and a folder to write to.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT_DIR="${BACKUP_DIR:-/var/backups/frontdesk}"
PASS_FILE="${BACKUP_PASSPHRASE_FILE:-/root/.frontdesk-backup-pass}"
mkdir -p "$OUT_DIR"; chmod 700 "$OUT_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T db pg_dump -U frontdesk_owner --format=custom frontdesk \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$PASS_FILE" > "$OUT_DIR/frontdesk-$STAMP.dump.enc"
# keep 14 days locally. COPY THE FILES OFF THIS SERVER TOO (see docs/RUNBOOK.md).
find "$OUT_DIR" -name 'frontdesk-*.dump.enc' -mtime +14 -delete
echo "backup written: $OUT_DIR/frontdesk-$STAMP.dump.enc"
