"""Database setup.

Two kinds of sessions:

* tenant_session(business_id): can only see rows of ONE business. On Postgres this is
  enforced by the database itself (row level security), not only by our code.
* system_session(): can see all rows. Used only to find which business a phone number
  belongs to, for the background worker, and for platform admin pages.

If a session has neither, it sees nothing (fail closed).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import get_settings


class UTCDateTime(TypeDecorator):
    """Always stores and returns timezone-aware UTC datetimes (SQLite drops the zone)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; use timezone-aware UTC")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def init_engine(url: str | None = None) -> Engine:
    global _engine, _factory
    url = url or get_settings().database_url
    # hide_parameters: if a database error is logged, it must not contain customer messages or phone numbers
    kwargs: dict = {"pool_pre_ping": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    else:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    _engine = create_engine(url, **kwargs)
    _factory = sessionmaker(_engine, expire_on_commit=False, class_=Session)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def _factory_get() -> sessionmaker[Session]:
    if _factory is None:
        init_engine()
    assert _factory is not None
    return _factory


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction, connection) -> None:
    """At the start of every transaction tell Postgres which business this session may see."""
    if connection.dialect.name != "postgresql":
        return
    bid = session.info.get("business_id") or ""
    bypass = "on" if session.info.get("system") else "off"
    connection.exec_driver_sql(
        "SELECT set_config('app.business_id', %s, true), set_config('app.bypass_rls', %s, true)",
        (bid, bypass),
    )


@contextmanager
def _session(info: dict) -> Iterator[Session]:
    s = _factory_get()()
    s.info.update(info)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def tenant_session(business_id: str):
    if not business_id:
        raise ValueError("business_id is required for a tenant session")
    return _session({"business_id": business_id})


def system_session():
    return _session({"system": True})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
