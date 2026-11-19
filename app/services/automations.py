"""Things the system does on its own: reminders, follow-ups, review requests, missed-call text-back,
daily report, cleaning up old data. Each function is safe to run again and again (it marks what it did).

Every text goes through compliance.send_sms(), so STOP and quiet hours are always obeyed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Appointment, Business, Conversation, Customer, Lead, Message, Notification, ProcessedEvent
from . import compliance
from .adapters import Adapters, get_adapters
from .notify import notify_owner
from .scheduling import fmt_local, open_windows

log = logging.getLogger("frontdesk.automations")

MAX_FOLLOWUPS = 2
FOLLOWUP_GAP = timedelta(hours=24)
FOLLOWUP_MAX_AGE = timedelta(days=3)
REVIEW_DELAY = timedelta(hours=24)
AUTO_COMPLETE_AFTER = timedelta(hours=2)


def auto_complete(db: Session, b: Business, now: datetime) -> int:
    rows = db.scalars(select(Appointment).where(Appointment.business_id == b.id, Appointment.status == "booked",
                                                Appointment.ends_at <= now - AUTO_COMPLETE_AFTER)).all()
    for a in rows:
        a.status = "completed"
    return len(rows)


def send_reminders(db: Session, b: Business, now: datetime, adapters: Adapters | None = None) -> int:
    sent = 0
    rows = db.scalars(select(Appointment).where(
        Appointment.business_id == b.id, Appointment.status == "booked", Appointment.reminder_sent_at.is_(None),
        Appointment.starts_at > now, Appointment.starts_at <= now + timedelta(hours=b.reminder_hours))).all()
    for a in rows:
        if a.created_at > a.starts_at - timedelta(hours=b.reminder_hours):
            continue  # booked inside the reminder window: the confirmation is the reminder
        cust = db.get(Customer, a.customer_id)
        body = f"Reminder from {b.name}: your {a.service_name} appointment is {fmt_local(b, a.starts_at)}. Reply here to move or cancel."
        result = compliance.send_sms(db, b, cust, body, conversation=_conv(db, a.conversation_id), now=now,
                                     kind="reminder", adapters=adapters)
        if result == "sent":
            a.reminder_sent_at = now
            sent += 1
    return sent


def _conv(db: Session, cid: str | None) -> Conversation | None:
    return db.get(Conversation, cid) if cid else None


def run_followups(db: Session, b: Business, now: datetime, adapters: Adapters | None = None) -> int:
    """At most 2 follow-ups to a lead who went quiet, then stop. Never during quiet hours."""
    sent = 0
    delay = timedelta(hours=b.followup_delay_hours)
    convs = db.scalars(select(Conversation).where(
        Conversation.business_id == b.id, Conversation.channel == "sms", Conversation.status == "open",
        Conversation.booked.is_(False), Conversation.followups_sent < MAX_FOLLOWUPS,
        Conversation.last_customer_message_at.is_not(None), Conversation.started_at >= now - FOLLOWUP_MAX_AGE)).all()
    for c in convs:
        last = db.scalar(select(Message).where(Message.conversation_id == c.id, Message.role.in_(("customer", "agent")))
                         .order_by(Message.created_at.desc(), Message.id.desc()).limit(1))
        if not last or last.role != "agent":
            continue  # the customer spoke last: the ball is with us / a person, not a quiet lead
        if c.followups_sent == 0:
            due = c.last_activity_at + delay
        else:
            due = (c.last_followup_at or c.last_activity_at) + FOLLOWUP_GAP
        if now < due:
            continue
        cust = db.get(Customer, c.customer_id)
        if c.followups_sent == 0:
            body = f"Hi, it's {b.name}. Would you still like help with this? Reply here and I'll get you a time."
        else:
            body = f"Last note from {b.name}: if you still need us, just reply here. Reply STOP to stop texts."
        if compliance.send_sms(db, b, cust, body, conversation=c, now=now, kind="followup", adapters=adapters) == "sent":
            c.followups_sent += 1
            c.last_followup_at = now
            sent += 1
    return sent


def send_review_requests(db: Session, b: Business, now: datetime, adapters: Adapters | None = None) -> int:
    if not b.review_link:
        return 0
    sent = 0
    rows = db.scalars(select(Appointment).where(
        Appointment.business_id == b.id, Appointment.status == "completed", Appointment.review_requested_at.is_(None),
        Appointment.ends_at <= now - REVIEW_DELAY)).all()
    for a in rows:
        cust = db.get(Customer, a.customer_id)
        if cust.sms_opt_out:
            a.review_requested_at = now  # never ask someone who sent STOP; mark so we do not look again
            continue
        body = f"Thanks for choosing {b.name}! If we did a good job, a quick review helps a lot: {b.review_link}"
        result = compliance.send_sms(db, b, cust, body, conversation=_conv(db, a.conversation_id), now=now,
                                     kind="review", adapters=adapters)
        if result == "sent":
            a.review_requested_at = now  # once only
            sent += 1
    return sent


def send_missed_call_textbacks(db: Session, b: Business, now: datetime, adapters: Adapters | None = None) -> int:
    """A call ended without a booking: send ONE text (if texts are approved for this business)."""
    if not b.textback_enabled:
        return 0
    sent = 0
    rows = db.scalars(select(Conversation).where(
        Conversation.business_id == b.id, Conversation.channel == "voice", Conversation.ended_at.is_not(None),
        Conversation.booked.is_(False), Conversation.textback_sent_at.is_(None),
        Conversation.status != "handed_off", Conversation.ended_at >= now - timedelta(hours=24))).all()
    for c in rows:
        cust = db.get(Customer, c.customer_id)
        body = (f"Hi, this is {b.name}. Sorry we couldn't finish helping you on the phone. "
                f"Reply here with what you need and I'll get you a time.")
        result = compliance.send_sms(db, b, cust, body, conversation=c, now=now, kind="textback",
                                     is_reply=not b.textback_respects_quiet_hours, adapters=adapters)
        if result == "sent":
            c.textback_sent_at = now
            sent += 1
        elif result in ("blocked:opted_out", "blocked:sms_not_enabled", "blocked:no_phone", "blocked:no_sms_number"):
            c.textback_sent_at = now  # nothing to retry: skip forever
    return sent


# ---------------- daily report ----------------
def _local_day_bounds(b: Business, day) -> tuple[datetime, datetime]:
    tz = ZoneInfo(b.timezone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def build_daily_report(db: Session, b: Business, day) -> dict:
    s, e = _local_day_bounds(b, day)
    convs = db.scalars(select(Conversation).where(Conversation.business_id == b.id, Conversation.started_at >= s,
                                                  Conversation.started_at < e)).all()
    calls = sum(1 for c in convs if c.channel == "voice")
    chats = sum(1 for c in convs if c.channel in ("sms", "web"))
    handoffs = sum(1 for c in convs if c.status == "handed_off")
    bookings = db.scalar(select(func.count()).select_from(Appointment).where(
        Appointment.business_id == b.id, Appointment.created_at >= s, Appointment.created_at < e)) or 0
    recovered = 0
    for c in db.scalars(select(Conversation).where(Conversation.business_id == b.id, Conversation.textback_sent_at >= s,
                                                   Conversation.textback_sent_at < e)):
        replied = db.scalar(select(func.count()).select_from(Message).where(
            Message.conversation_id == c.id, Message.role == "customer", Message.created_at > c.textback_sent_at)) or 0
        recovered += 1 if replied else 0
    cost = sum(c.cost_usd for c in convs)
    text = (f"{b.name} daily report for {day.strftime('%A, %B')} {day.day}:\n"
            f"- Calls answered by the assistant: {calls}\n- Text and web chats: {chats}\n"
            f"- Appointments booked: {bookings}\n- Handed to a person: {handoffs}\n"
            f"- Missed calls recovered by text: {recovered}")
    return {"calls": calls, "chats": chats, "bookings": bookings, "handoffs": handoffs, "recovered": recovered,
            "ai_cost_usd": round(cost, 4), "text": text}


def send_daily_report_if_due(db: Session, b: Business, now: datetime, adapters: Adapters | None = None) -> bool:
    local = now.astimezone(ZoneInfo(b.timezone))
    today = local.date().isoformat()
    if local.hour < b.report_hour or b.last_report_date == today:
        return False
    report = build_daily_report(db, b, local.date())
    notify_owner(db, b, report["text"], kind="report", now=now, adapters=adapters)
    b.last_report_date = today
    return True


# ---------------- weekly report (email to the client, Monday 8 AM, about the week before) ----------------
WEEKLY_REPORT_HOUR = 8
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _is_open_at(b: Business, moment: datetime) -> bool:
    return any(s <= moment < e for s, e in open_windows(b, moment.astimezone(ZoneInfo(b.timezone)).date()))


def _count_customers(db: Session, b: Business, s: datetime, e: datetime) -> int:
    return db.scalar(select(func.count()).select_from(Conversation).where(
        Conversation.business_id == b.id, Conversation.started_at >= s, Conversation.started_at < e)) or 0


def build_period_report(db: Session, b: Business, first_day, days: int = 7, *, title: str, before_label: str) -> dict:
    """Numbers only: no customer names, phone numbers or message text ever go into this report."""
    tz = ZoneInfo(b.timezone)
    s = _local_day_bounds(b, first_day)[0]
    e = _local_day_bounds(b, first_day + timedelta(days=days))[0]
    convs = db.scalars(select(Conversation).where(Conversation.business_id == b.id, Conversation.started_at >= s,
                                                  Conversation.started_at < e)).all()
    customers = len(convs)
    calls = sum(1 for c in convs if c.channel == "voice")
    handoffs = sum(1 for c in convs if c.status == "handed_off")
    emergencies = sum(1 for c in convs if (c.handoff_reason or "").startswith("emergency"))
    outside = sum(1 for c in convs if not _is_open_at(b, c.started_at))
    bookings = db.scalar(select(func.count()).select_from(Appointment).where(
        Appointment.business_id == b.id, Appointment.created_at >= s, Appointment.created_at < e)) or 0
    leads = db.scalar(select(func.count()).select_from(Lead).where(
        Lead.business_id == b.id, Lead.created_at >= s, Lead.created_at < e)) or 0
    previous = _count_customers(db, b, _local_day_bounds(b, first_day - timedelta(days=days))[0], s)
    per_day: dict[int, int] = {}
    for c in convs:
        d = c.started_at.astimezone(tz).weekday()
        per_day[d] = per_day.get(d, 0) + 1
    busiest = max(per_day.items(), key=lambda kv: (kv[1], -kv[0])) if per_day else None
    cost = sum(c.cost_usd or 0.0 for c in convs)
    last_day = first_day + timedelta(days=days - 1)
    lines = [
        f"{b.name} - {title}, {first_day.strftime('%b')} {first_day.day} to {last_day.strftime('%b')} {last_day.day}",
        "",
        f"Your assistant answered {customers} customer{'' if customers == 1 else 's'} ({before_label}: {previous})",
        f"- Website and text chats: {customers - calls}",
        f"- Phone calls: {calls}",
        f"- Visits booked: {bookings}",
        f"- Leads saved for you to call back: {leads}",
        f"- Handed to you (a person was needed): {handoffs}",
        f"- Emergencies sent to you at once: {emergencies}",
        f"- Answered outside your opening hours: {outside}",
        "",
    ]
    if busiest:
        lines.append(f"Busiest day: {_DAY_NAMES[busiest[0]]} ({busiest[1]} customer{'' if busiest[1] == 1 else 's'})")
    lines += [f"AI cost: ${cost:.2f}", "", f"See the details: {get_settings().public_base_url.rstrip('/')}/inbox"]
    return {"customers": customers, "chats": customers - calls, "calls": calls, "bookings": bookings, "leads": leads,
            "handoffs": handoffs, "emergencies": emergencies, "outside_hours": outside, "previous_customers": previous,
            "busiest_day": (_DAY_NAMES[busiest[0]], busiest[1]) if busiest else None, "ai_cost_usd": round(cost, 4),
            "text": "\n".join(lines)}


def build_weekly_report(db: Session, b: Business, week_start) -> dict:
    """week_start is a Monday (a date in the business's own time zone)."""
    return build_period_report(db, b, week_start, title="your week", before_label="the week before")


def send_weekly_report_if_due(db: Session, b: Business, now: datetime, adapters: Adapters | None = None) -> bool:
    """Once per week, from Monday 8 AM local time. If the worker was off on Monday, it is sent later that week.
    A week with no customers sends nothing (the owner asked for that)."""
    if b.weekly_report_enabled is False:
        return False
    local = now.astimezone(ZoneInfo(b.timezone))
    this_monday = local.date() - timedelta(days=local.weekday())
    if local.weekday() == 0 and local.hour < WEEKLY_REPORT_HOUR:
        return False
    week_start = this_monday - timedelta(days=7)
    if b.last_weekly_report_date == week_start.isoformat():
        return False
    b.last_weekly_report_date = week_start.isoformat()  # marked first: whatever happens, never twice for one week
    report = build_weekly_report(db, b, week_start)
    if not report["customers"]:
        return False
    notify_owner(db, b, report["text"], kind="weekly_report", now=now, adapters=adapters,
                 subject=f"{b.name}: your week with the AI front desk")
    return True


# ---------------- data retention ----------------
def purge_old_data(db: Session, b: Business, now: datetime) -> int:
    """Delete message text and lead details older than the business's retention period.
    We KEEP: customer phone + opt-out flag (we must still honour STOP) + first contact time (compliance record)."""
    db.execute(delete(ProcessedEvent).where(ProcessedEvent.business_id == b.id, ProcessedEvent.created_at < now - timedelta(days=30)))
    cutoff = now - timedelta(days=b.retention_days)
    old = db.scalars(select(Conversation).where(Conversation.business_id == b.id, Conversation.purged.is_(False),
                                                Conversation.last_activity_at < cutoff)).all()
    ids = [c.id for c in old]
    if not ids:
        return 0
    db.execute(delete(Message).where(Message.business_id == b.id, Message.conversation_id.in_(ids)))
    db.execute(delete(Lead).where(Lead.business_id == b.id, Lead.conversation_id.in_(ids)))
    for c in old:
        c.purged = True
        c.summary = None
    return len(ids)


def run_all(db: Session, b: Business, now: datetime, adapters: Adapters | None = None) -> dict:
    adapters = adapters or get_adapters()
    return {
        "completed": auto_complete(db, b, now),
        "reminders": send_reminders(db, b, now, adapters),
        "followups": run_followups(db, b, now, adapters),
        "reviews": send_review_requests(db, b, now, adapters),
        "textbacks": send_missed_call_textbacks(db, b, now, adapters),
        "report": send_daily_report_if_due(db, b, now, adapters),
        "weekly_report": send_weekly_report_if_due(db, b, now, adapters),
        "purged": purge_old_data(db, b, now),
    }
