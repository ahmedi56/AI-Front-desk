#!/bin/bash
# Proves a backup really works: restores it into a throw-away database and counts the rows.
# Usage: restore-test.sh /var/backups/frontdesk/frontdesk-XXXX.dump.enc
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="$1"; PASS_FILE="${BACKUP_PASSPHRASE_FILE:-/root/.frontdesk-backup-pass}"
docker compose exec -T db psql -U frontdesk_owner -d postgres -c "DROP DATABASE IF EXISTS restore_test;" -c "CREATE DATABASE restore_test;"
openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$PASS_FILE" < "$FILE" \
  | docker compose exec -T db pg_restore -U frontdesk_owner -d restore_test --no-owner
docker compose exec -T db psql -U frontdesk_owner -d restore_test -c \
  "SELECT (SELECT count(*) FROM businesses) AS businesses, (SELECT count(*) FROM conversations) AS conversations, (SELECT count(*) FROM appointments) AS appointments;"
docker compose exec -T db psql -U frontdesk_owner -d postgres -c "DROP DATABASE restore_test;"
echo "restore test finished: if you see the counts above, the backup is good."
