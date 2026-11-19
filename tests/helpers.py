from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app import db as dbmod
from app.agent.runner import TurnResult, handle_customer_text
from app.models import Appointment, Business, Conversation, Customer, Lead, Message, Notification
from app.services.adapters import get_adapters
from app.services.conversations import get_or_create_customer, get_or_create_sms_conversation

from .conftest import NOW

PHONE = "+15125550147"


def converse(business_id: str, text: str, *, phone: str | None = PHONE, now: datetime = NOW, channel: str = "sms") -> TurnResult:
    with dbmod.tenant_session(business_id) as db:
        b = db.get(Business, business_id)
        cust = get_or_create_customer(db, b, phone, now)
        conv = get_or_create_sms_conversation(db, b, cust, now)
        return handle_customer_text(db, b, cust, conv, text, channel=channel, now=now, adapters=get_adapters())


def rows(business_id: str, model):
    with dbmod.tenant_session(business_id) as db:
        return list(db.scalars(select(model).where(model.business_id == business_id)))


def appointments(business_id: str, status: str | None = "booked"):
    return [a for a in rows(business_id, Appointment) if status is None or a.status == status]


def notifications(business_id: str):
    return rows(business_id, Notification)


def leads(business_id: str):
    return rows(business_id, Lead)


def conversations(business_id: str):
    return rows(business_id, Conversation)


def messages(business_id: str):
    return rows(business_id, Message)
