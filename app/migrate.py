"""Create the tables and the database-level safety rules.

Run with the ADMIN database address (DATABASE_ADMIN_URL), not the app's limited one:

    python -m app.migrate

On Postgres this also:
  * turns on row level security, so a session for business A cannot read business B's rows
    even if our code has a bug;
  * adds a rule that makes double booking impossible at the database level;
  * gives the limited app role only the rights it needs.
"""
from __future__ import annotations

import re
import sys

from sqlalchemy import create_engine, inspect, text

from . import models  # noqa: F401  (loads the tables)
from .config import get_settings
from .db import Base
from .models import TENANT_TABLES

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def _pg_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def add_missing_columns(engine) -> list[str]:
    """New versions add new fields. create_all() makes new TABLES but never changes old ones, so we add each missing
    column here. Only columns that may be empty are added this way, so no existing row breaks and no data is lost."""
    added: list[str] = []
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as c:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {col["name"] for col in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                if not col.nullable:
                    raise RuntimeError(f"Column {table.name}.{col.name} is required and cannot be added automatically.")
                ddl_type = col.type.compile(dialect=engine.dialect)
                c.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}'))
                added.append(f"{table.name}.{col.name}")
    return added


def migrate(admin_url: str | None = None, app_role: str | None = None) -> None:
    s = get_settings()
    admin_url = admin_url or s.database_admin_url or s.database_url
    app_role = app_role or s.app_db_role
    if not _ROLE_RE.match(app_role):
        raise ValueError("bad role name")
    if admin_url.startswith("sqlite"):
        engine = create_engine(admin_url)
        Base.metadata.create_all(engine)
        for name in add_missing_columns(engine):
            print(f"Added new field: {name}")
        return

    engine = create_engine(_pg_url(admin_url))
    Base.metadata.create_all(engine)
    for name in add_missing_columns(engine):
        print(f"Added new field: {name}")
    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": app_role}).first()
        if not exists:
            raise RuntimeError(
                f"The database role '{app_role}' does not exist. Create it first "
                f"(see deploy/postgres-init.sql). The app must NOT connect as the database owner."
            )

        # businesses: a tenant session may only see its own business row
        c.execute(text("ALTER TABLE businesses ENABLE ROW LEVEL SECURITY"))
        c.execute(text("ALTER TABLE businesses FORCE ROW LEVEL SECURITY"))
        c.execute(text("DROP POLICY IF EXISTS tenant_isolation ON businesses"))
        c.execute(
            text(
                "CREATE POLICY tenant_isolation ON businesses "
                "USING (current_setting('app.bypass_rls', true) = 'on' "
                "       OR id = current_setting('app.business_id', true)) "
                "WITH CHECK (current_setting('app.bypass_rls', true) = 'on' "
                "            OR id = current_setting('app.business_id', true))"
            )
        )
        for table in TENANT_TABLES:
            c.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            c.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            c.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
            c.execute(
                text(
                    f"CREATE POLICY tenant_isolation ON {table} "
                    "USING (current_setting('app.bypass_rls', true) = 'on' "
                    "       OR business_id = current_setting('app.business_id', true)) "
                    "WITH CHECK (current_setting('app.bypass_rls', true) = 'on' "
                    "            OR business_id = current_setting('app.business_id', true))"
                )
            )

        # prospects (our own sales leads): no business may ever read them, only the platform (system session)
        c.execute(text("ALTER TABLE prospects ENABLE ROW LEVEL SECURITY"))
        c.execute(text("ALTER TABLE prospects FORCE ROW LEVEL SECURITY"))
        c.execute(text("DROP POLICY IF EXISTS platform_only ON prospects"))
        c.execute(
            text(
                "CREATE POLICY platform_only ON prospects "
                "USING (current_setting('app.bypass_rls', true) = 'on') "
                "WITH CHECK (current_setting('app.bypass_rls', true) = 'on')"
            )
        )

        # no two booked appointments of one business may overlap in time
        c.execute(text("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS no_double_booking"))
        c.execute(
            text(
                "ALTER TABLE appointments ADD CONSTRAINT no_double_booking "
                "EXCLUDE USING gist (business_id WITH =, tstzrange(starts_at, ends_at) WITH &&) "
                "WHERE (status = 'booked')"
            )
        )

        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {app_role}"))
        c.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_role}"))
        # audit log can only be added to, never edited or erased by the app
        c.execute(text(f"REVOKE UPDATE, DELETE ON audit_log FROM {app_role}"))


if __name__ == "__main__":
    try:
        migrate()
        print("Database is ready.")
    except Exception as exc:  # show a clear message, not a long traceback
        print(f"Migration failed: {exc}", file=sys.stderr)
        sys.exit(1)
