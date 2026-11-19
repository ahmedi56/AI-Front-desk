#!/bin/sh
# Runs once, the first time the database is created.
# Creates the LIMITED role the app uses. It cannot create tables and it cannot skip the row security rules.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE frontdesk_app LOGIN PASSWORD '${APP_DB_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO frontdesk_app;
SQL
