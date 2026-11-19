"""Finding or creating customers and conversations, and saving messages."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Business, Conversation, Customer, Message

OPEN_REUSE = timedelta(hours=24)
HANDOFF_REUSE = timedelta(hours=12)


def get_or_create_customer(db: Session, b: Business, phone: str | None, now: datetime) -> Customer:
    if phone:
        existing = db.scalar(select(Customer).where(Customer.business_id == b.id, Customer.phone == phone))
        if existing:
            return existing
    cust = Customer(business_id=b.id, phone=phone, first_contact_at=now)
    try:
        with db.begin_nested():
            db.add(cust)
            db.flush()
    except IntegrityError:  # two requests created the same customer at the same moment
        existing = db.scalar(select(Customer).where(Customer.business_id == b.id, Customer.phone == phone))
        if existing:
            return existing
        raise
    return cust


def new_conversation(db: Session, b: Business, customer: Customer, channel: str, now: datetime,
                     external_id: str | None = None) -> Conversation:
    conv = Conversation(business_id=b.id, customer_id=customer.id, channel=channel, external_id=external_id,
                        started_at=now, last_activity_at=now)
    db.add(conv)
    db.flush()
    return conv


def get_or_create_sms_conversation(db: Session, b: Business, customer: Customer, now: datetime) -> Conversation:
    latest = db.scalar(
        select(Conversation)
        .where(Conversation.business_id == b.id, Conversation.customer_id == customer.id, Conversation.channel == "sms")
        .order_by(Conversation.last_activity_at.desc())
        .limit(1)
    )
    if latest and latest.status != "closed":
        window = HANDOFF_REUSE if latest.status == "handed_off" else OPEN_REUSE
        if now - latest.last_activity_at <= window:
            return latest
    return new_conversation(db, b, customer, "sms", now)


def conversation_by_external(db: Session, b: Business, external_id: str) -> Conversation | None:
    return db.scalar(select(Conversation).where(Conversation.business_id == b.id, Conversation.external_id == external_id))


def add_message(db: Session, conv: Conversation, role: str, text: str, now: datetime, meta: dict | None = None) -> Message:
    # Every message in a conversation gets a strictly later time than the one before, so the order is never a tie
    # (a customer message and our reply are handled in the same instant).
    stamp = now
    if conv.last_activity_at and stamp <= conv.last_activity_at and conv.messages_started:
        stamp = conv.last_activity_at + timedelta(microseconds=1)
    msg = Message(business_id=conv.business_id, conversation_id=conv.id, role=role, text=text, created_at=stamp, meta=meta or {})
    db.add(msg)
    conv.last_activity_at = stamp
    conv.messages_started = True
    if role == "customer":
        conv.last_customer_message_at = stamp
    return msg
