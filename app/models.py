"""Database tables. Every table that holds business data has a business_id."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import db as _db
from .db import Base, UTCDateTime


def utcnow() -> datetime:
    return _db.utcnow()  # looked up at call time, so tests can freeze the clock


def new_id() -> str:
    return str(uuid.uuid4())


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    handoff_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    owner_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    inbound_number: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)  # voice
    sms_number: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    sms_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # true after A2P 10DLC approval
    agent_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # the kill switch
    quiet_start: Mapped[int] = mapped_column(Integer, default=21)  # hour, 0-23
    quiet_end: Mapped[int] = mapped_column(Integer, default=8)
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    hours: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"mon": [["08:00","17:00"]], ...}
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # address, areas, faqs, rules
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30)
    min_notice_minutes: Mapped[int] = mapped_column(Integer, default=60)
    reminder_hours: Mapped[int] = mapped_column(Integer, default=24)
    followup_delay_hours: Mapped[int] = mapped_column(Integer, default=4)
    review_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    textback_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    textback_respects_quiet_hours: Mapped[bool] = mapped_column(Boolean, default=True)
    report_hour: Mapped[int] = mapped_column(Integer, default=18)
    last_report_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Weekly email report (Monday 8 AM, about the week before). Empty = on. The date is the Monday of the last week sent.
    weekly_report_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_weekly_report_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    calendar_kind: Mapped[str] = mapped_column(String(20), default="internal")  # internal | google
    google_calendar_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    google_refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The client's OWN free AI account (Groq or Gemini). The key is encrypted; only its last 4 characters are kept readable.
    ai_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ai_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_key_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Optional BACKUP AI account of the same client, from a different free provider. Used when the main one is busy or down.
    ai_backup_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ai_backup_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_backup_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_backup_key_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Service(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    price_text: Mapped[str | None] = mapped_column(String(100), nullable=True)  # exactly as the owner typed
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str | None] = mapped_column(ForeignKey("businesses.id"), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20))  # "admin" or "owner"
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # 2-step login secret (encrypted)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)  # stops re-use of the same code
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("business_id", "phone", name="uq_customer_phone"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_contact_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)  # compliance record
    sms_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_out_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conv_business_activity", "business_id", "last_activity_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(10))  # voice | sms | web
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # call id
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | handed_off | closed
    handoff_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_customer_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    steps_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    followups_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_followup_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    textback_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    call_seconds: Mapped[int] = mapped_column(Integer, default=0)
    booked: Mapped[bool] = mapped_column(Boolean, default=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    purged: Mapped[bool] = mapped_column(Boolean, default=False)
    messages_started: Mapped[bool] = mapped_column(Boolean, default=False)  # false until the first message is saved

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # customer | agent | system
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appt_business_start", "business_id", "starts_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    service_name: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(20), default="booked")  # booked|cancelled|completed|no_show
    external_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    calendar_sync_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    review_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Lead(Base):
    """Details collected when the agent could not (or should not) finish the job itself."""

    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    need: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_time: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)  # price_followup, spanish, calendar_down...
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Notification(Base):
    """Every message we send to the business owner is recorded here."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # handoff | emergency | booking | cancel | report | alert
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    text: Mapped[str] = mapped_column(Text)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    delivered_via: Mapped[str | None] = mapped_column(String(50), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str | None] = mapped_column(ForeignKey("businesses.id"), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(100))  # user:<id> | agent | system | webhook
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))  # llm | call_seconds | sms_out | sms_in
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProcessedEvent(Base):
    """Remembers webhook event ids so the same notice twice does the work once."""

    __tablename__ = "processed_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_processed_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str | None] = mapped_column(ForeignKey("businesses.id"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[str] = mapped_column(String(200))
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # saved answer, so a repeat gets the same answer
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Prospect(Base):
    """A business owner who filled in the form on the product page. Belongs to the PLATFORM, not to any business:
    there is no business_id on purpose, and only the platform admin (a system session) may read this table."""

    __tablename__ = "prospects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    business_name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    website: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | contacted
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


# Tables that carry business data (used for row level security and the isolation tests)
TENANT_TABLES = [
    "services",
    "users",
    "customers",
    "conversations",
    "messages",
    "appointments",
    "leads",
    "notifications",
    "audit_log",
    "usage_events",
    "processed_events",
]
