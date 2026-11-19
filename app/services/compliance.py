"""Texting rules. Every text we send goes through send_sms(), so these rules cannot be skipped.

These rules come from vendor articles. A US lawyer must review them before the first client.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..models import Business, Conversation, Customer, Message
from ..security import mask_phone
from . import usage
from .adapters import Adapters, SmsSendError, get_adapters

log = logging.getLogger("frontdesk.sms")

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
HELP_WORDS = {"help", "info"}
START_WORDS = {"start", "unstop"}


def classify_keyword(text: str, *, opted_out: bool = False) -> str | None:
    """Only a message that is exactly one of these words counts ('cancel my visit' does not)."""
    word = re.sub(r"[^a-z]", "", text.lower())
    if word in STOP_WORDS:
        return "stop"
    if word in HELP_WORDS:
        return "help"
    if word in START_WORDS or (opted_out and word == "yes"):
        return "start"
    return None


def help_text(b: Business) -> str:
    contact = f" Call {b.handoff_phone}." if b.handoff_phone else ""
    return f"{b.name}: we answer questions and book appointments by text.{contact} Reply STOP to stop messages. Msg&data rates may apply."


def stop_text(b: Business) -> str:
    contact = f" To reach us, call {b.handoff_phone}." if b.handoff_phone else ""
    return f"{b.name}: you are unsubscribed and will get no more texts. Reply START to turn texts back on.{contact}"


def start_text(b: Business) -> str:
    return f"{b.name}: texts are back on. Reply STOP any time to stop."


# ---------- quiet hours ----------
def in_quiet_hours(now: datetime, tz_name: str, start_hour: int, end_hour: int) -> bool:
    local = now.astimezone(ZoneInfo(tz_name))
    h = local.hour + local.minute / 60
    if start_hour > end_hour:  # e.g. 21 -> 8 crosses midnight
        return h >= start_hour or h < end_hour
    return start_hour <= h < end_hour


def next_allowed_time(now: datetime, tz_name: str, start_hour: int, end_hour: int) -> datetime:
    if not in_quiet_hours(now, tz_name, start_hour, end_hour):
        return now
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    target = datetime.combine(local.date(), time(end_hour), tzinfo=tz)
    if target <= local:
        target = datetime.combine(local.date() + timedelta(days=1), time(end_hour), tzinfo=tz)
    return target.astimezone(timezone.utc)


def customer_tz(b: Business, c: Customer) -> str:
    return c.timezone or b.timezone


# ---------- the one door for every outgoing text ----------
def send_sms(
    db: Session,
    business: Business,
    customer: Customer,
    body: str,
    *,
    conversation: Conversation | None,
    now: datetime,
    kind: str,
    is_reply: bool = False,
    is_opt_confirmation: bool = False,
    record_message: bool = True,
    adapters: Adapters | None = None,
) -> str:
    """Send one text if all rules allow it. Returns 'sent' or 'blocked:<reason>'.

    is_reply            : the customer just texted us (allowed at any hour)
    is_opt_confirmation : the single STOP/HELP/START answer (allowed even after STOP)
    """
    adapters = adapters or get_adapters()
    if not customer.phone:
        return "blocked:no_phone"
    if not business.sms_enabled:
        return "blocked:sms_not_enabled"
    if not business.sms_number:
        return "blocked:no_sms_number"
    if customer.sms_opt_out and not is_opt_confirmation:
        return "blocked:opted_out"
    if not (is_reply or is_opt_confirmation):
        if in_quiet_hours(now, customer_tz(business, customer), business.quiet_start, business.quiet_end):
            return "blocked:quiet_hours"
    try:
        adapters.sms.send(from_number=business.sms_number, to=customer.phone, body=body)
    except SmsSendError as exc:
        log.warning("sms failed for %s: %s", mask_phone(customer.phone), exc)
        return "blocked:send_failed"
    if conversation is not None and record_message:
        db.add(Message(business_id=business.id, conversation_id=conversation.id, role="agent", text=body,
                       created_at=now, meta={"kind": kind, "channel": "sms"}))
    if conversation is not None:
        conversation.last_activity_at = now
    usage.record_sms(db, business.id, conversation.id if conversation else None, body, "out")
    return "sent"
