from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import db as dbmod
from ..models import Business, ProcessedEvent

log = logging.getLogger("frontdesk.channels")


def business_by_sms_number(number: str) -> str | None:
    with dbmod.system_session() as s:
        return s.scalar(select(Business.id).where(Business.sms_number == number))


def business_by_voice_number(number: str) -> str | None:
    with dbmod.system_session() as s:
        return s.scalar(select(Business.id).where(Business.inbound_number == number))


def business_by_slug(slug: str) -> str | None:
    with dbmod.system_session() as s:
        return s.scalar(select(Business.id).where(Business.slug == slug))


def claim_event(db: Session, business_id: str, provider: str, event_id: str, payload: str | None = None) -> bool:
    """Remember an event id. Returns False if we have seen it before (so the work is not done twice)."""
    try:
        with db.begin_nested():
            db.add(ProcessedEvent(business_id=business_id, provider=provider, event_id=event_id, payload=payload))
            db.flush()
        return True
    except IntegrityError:
        return False


def saved_event_payload(db: Session, provider: str, event_id: str) -> str | None:
    return db.scalar(select(ProcessedEvent.payload).where(ProcessedEvent.provider == provider, ProcessedEvent.event_id == event_id))
