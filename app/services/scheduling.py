"""Opening hours, free times, and booking / moving / cancelling appointments.

One appointment at a time per business in this version (one crew). The database enforces
this too (see migrate.py), so even two simultaneous requests cannot double book.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Appointment, Business, Conversation, Customer, Service
from .adapters import CalendarAdapter, CalendarUnavailable
from .notify import notify_owner

log = logging.getLogger("frontdesk.scheduling")

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class SlotUnavailable(Exception):
    pass


class BookingError(Exception):
    """A request we refuse (in the past, outside hours, not your appointment, ...)."""


# ---------- time helpers ----------
def tz_of(b: Business) -> ZoneInfo:
    return ZoneInfo(b.timezone)


def parse_local(b: Business, text: str) -> datetime:
    """'2026-10-13T15:00' (business local time) -> UTC datetime."""
    try:
        naive = datetime.fromisoformat(text.strip())
    except ValueError as exc:
        raise BookingError("Time must look like 2026-10-13T15:00") from exc
    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc)
    return naive.replace(tzinfo=tz_of(b)).astimezone(timezone.utc)


def fmt_local(b: Business, dt: datetime) -> str:
    local = dt.astimezone(tz_of(b))
    hour = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%A, %B')} {local.day} at {hour}"


def iso_local(b: Business, dt: datetime) -> str:
    return dt.astimezone(tz_of(b)).strftime("%Y-%m-%dT%H:%M")


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def open_windows(b: Business, day: date) -> list[tuple[datetime, datetime]]:
    tz = tz_of(b)
    out = []
    for start, end in (b.hours or {}).get(DAYS[day.weekday()], []):
        s = datetime.combine(day, _hhmm(start), tzinfo=tz).astimezone(timezone.utc)
        e = datetime.combine(day, _hhmm(end), tzinfo=tz).astimezone(timezone.utc)
        if e > s:
            out.append((s, e))
    return out


def hours_text(b: Business) -> str:
    names = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
             "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
    lines = []
    for d in DAYS:
        spans = (b.hours or {}).get(d, [])
        if spans:
            parts = ", ".join(f"{_nice(a)} to {_nice(z)}" for a, z in spans)
            lines.append(f"{names[d]}: {parts}")
        else:
            lines.append(f"{names[d]}: closed")
    return "\n".join(lines)


def _nice(hhmm: str) -> str:
    t = _hhmm(hhmm)
    h = t.hour % 12 or 12
    suffix = "AM" if t.hour < 12 else "PM"
    return f"{h}:{t.minute:02d} {suffix}" if t.minute else f"{h} {suffix}"


# ---------- free time ----------
def _busy(db: Session, b: Business, cal: CalendarAdapter, start: datetime, end: datetime,
          ignore_appt_id: str | None = None) -> list[tuple[datetime, datetime]]:
    rows = db.scalars(
        select(Appointment).where(
            Appointment.business_id == b.id,
            Appointment.status == "booked",
            Appointment.starts_at < end,
            Appointment.ends_at > start,
        )
    ).all()
    busy = [(a.starts_at, a.ends_at) for a in rows if a.id != ignore_appt_id]
    busy += cal.busy(start, end)  # may raise CalendarUnavailable: then we must not guess
    return busy


def _overlaps(s: datetime, e: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    return any(s < be and e > bs for bs, be in busy)


def free_slots(db: Session, b: Business, cal: CalendarAdapter, *, duration_min: int, from_utc: datetime,
               to_utc: datetime, now: datetime, ignore_appt_id: str | None = None) -> list[datetime]:
    earliest = max(from_utc, now + timedelta(minutes=b.min_notice_minutes))
    if earliest >= to_utc:
        return []
    busy = _busy(db, b, cal, earliest, to_utc, ignore_appt_id)
    step = timedelta(minutes=max(5, b.slot_minutes))
    dur = timedelta(minutes=duration_min)
    slots: list[datetime] = []
    day = (earliest.astimezone(tz_of(b)) - timedelta(days=1)).date()
    last_day = (to_utc.astimezone(tz_of(b)) + timedelta(days=1)).date()
    while day <= last_day:
        for w_start, w_end in open_windows(b, day):
            t = w_start
            while t + dur <= w_end:
                if t >= earliest and t + dur <= to_utc and not _overlaps(t, t + dur, busy):
                    slots.append(t)
                t += step
        day += timedelta(days=1)
    return sorted(set(slots))


def nearest_slots(db: Session, b: Business, cal: CalendarAdapter, *, duration_min: int, target: datetime,
                  now: datetime, n: int = 2) -> list[datetime]:
    pool = free_slots(db, b, cal, duration_min=duration_min, from_utc=target - timedelta(days=2),
                      to_utc=target + timedelta(days=10), now=now)
    pool.sort(key=lambda t: (abs((t - target).total_seconds()), t))
    return sorted(pool[:n])


def slots_on_day(db: Session, b: Business, cal: CalendarAdapter, *, duration_min: int, day: date,
                 now: datetime) -> list[datetime]:
    tz = tz_of(b)
    start = datetime.combine(day, time(0), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(timezone.utc)
    return free_slots(db, b, cal, duration_min=duration_min, from_utc=start, to_utc=end, now=now)


# ---------- services ----------
def find_service(db: Session, b: Business, name: str | None) -> Service | None:
    if not name:
        return None
    services = db.scalars(select(Service).where(Service.business_id == b.id, Service.active.is_(True))).all()
    wanted = name.strip().lower()
    for s in services:
        if s.name.lower() == wanted:
            return s
    for s in services:
        if wanted in s.name.lower() or s.name.lower() in wanted:
            return s
    return None


def default_duration(db: Session, b: Business, service: Service | None) -> int:
    return service.duration_min if service else max(b.slot_minutes, 60)


# ---------- changes ----------
def _lock_business(db: Session, b: Business) -> None:
    """Take a lock so only one booking change per business runs at a time (Postgres).
    'NO KEY UPDATE' is used on purpose: the stronger 'UPDATE' lock deadlocks with customer inserts."""
    db.execute(select(Business.id).where(Business.id == b.id).with_for_update(key_share=True))



def _is_inside_hours(b: Business, start: datetime, end: datetime) -> bool:
    day = start.astimezone(tz_of(b)).date()
    return any(ws <= start and end <= we for ws, we in open_windows(b, day))


def _check_bookable(db: Session, b: Business, cal: CalendarAdapter, start: datetime, end: datetime, now: datetime,
                    ignore_appt_id: str | None = None) -> None:
    if start < now + timedelta(minutes=b.min_notice_minutes):
        raise SlotUnavailable("too_soon_or_past")
    if not _is_inside_hours(b, start, end):
        raise SlotUnavailable("outside_hours")
    if _overlaps(start, end, _busy(db, b, cal, start, end, ignore_appt_id)):
        raise SlotUnavailable("taken")


def book(db: Session, b: Business, cal: CalendarAdapter, *, customer: Customer, conversation: Conversation | None,
         start: datetime, service: Service | None, service_name: str, now: datetime,
         notes: str | None = None) -> Appointment:
    _lock_business(db, b)
    dur = default_duration(db, b, service)
    end = start + timedelta(minutes=dur)
    _check_bookable(db, b, cal, start, end, now)  # CalendarUnavailable bubbles up on purpose
    title = f"{service_name}: {customer.name or 'New customer'}"
    desc = f"Phone: {customer.phone or 'unknown'}\nBooked by the AI front desk."
    event_id = cal.create_event(title=title, start=start, end=end, description=desc, tz=b.timezone)
    appt = Appointment(
        business_id=b.id, customer_id=customer.id, conversation_id=conversation.id if conversation else None,
        service_name=service_name, starts_at=start, ends_at=end, status="booked",
        external_event_id=event_id or None, notes=notes, created_at=now,
    )
    try:
        with db.begin_nested():
            db.add(appt)
            db.flush()
    except IntegrityError as exc:  # the database refused a double booking
        try:
            if event_id:
                cal.delete_event(event_id)
        except CalendarUnavailable:
            log.error("could not remove calendar event after a refused booking")
        raise SlotUnavailable("taken") from exc
    if conversation:
        conversation.booked = True
    notify_owner(db, b, f"New booking: {customer.name or customer.phone} - {service_name}, {fmt_local(b, start)}.",
                 kind="booking", conversation_id=conversation.id if conversation else None, now=now)
    return appt


def reschedule(db: Session, b: Business, cal: CalendarAdapter, *, appt: Appointment, new_start: datetime,
               now: datetime, conversation: Conversation | None = None) -> Appointment:
    if appt.business_id != b.id or appt.status != "booked":
        raise BookingError("not_found")
    _lock_business(db, b)
    new_end = new_start + (appt.ends_at - appt.starts_at)
    _check_bookable(db, b, cal, new_start, new_end, now, ignore_appt_id=appt.id)
    if appt.external_event_id:
        cal.update_event(appt.external_event_id, start=new_start, end=new_end, tz=b.timezone)
    old = appt.starts_at
    try:
        with db.begin_nested():
            appt.starts_at, appt.ends_at = new_start, new_end
            appt.reminder_sent_at = None
            db.flush()
    except IntegrityError as exc:
        if appt.external_event_id:
            try:
                cal.update_event(appt.external_event_id, start=old, end=old + (new_end - new_start), tz=b.timezone)
            except CalendarUnavailable:
                log.error("could not restore calendar event after a refused move")
        raise SlotUnavailable("taken") from exc
    notify_owner(db, b, f"Appointment moved: {fmt_local(b, old)} -> {fmt_local(b, new_start)} ({appt.service_name}).",
                 kind="booking", conversation_id=conversation.id if conversation else None, now=now)
    return appt


def cancel(db: Session, b: Business, cal: CalendarAdapter, *, appt: Appointment, now: datetime,
           conversation: Conversation | None = None) -> Appointment:
    if appt.business_id != b.id or appt.status != "booked":
        raise BookingError("not_found")
    appt.status = "cancelled"
    note = f"Appointment cancelled: {fmt_local(b, appt.starts_at)} ({appt.service_name})."
    if appt.external_event_id:
        try:
            cal.delete_event(appt.external_event_id)
        except CalendarUnavailable:
            appt.calendar_sync_pending = True
            note += " The calendar could not be updated; please remove the event by hand."
    notify_owner(db, b, note, kind="cancel", conversation_id=conversation.id if conversation else None, now=now)
    return appt


def upcoming_for_customer(db: Session, b: Business, customer: Customer, now: datetime) -> list[Appointment]:
    return list(
        db.scalars(
            select(Appointment)
            .where(Appointment.business_id == b.id, Appointment.customer_id == customer.id,
                   Appointment.status == "booked", Appointment.starts_at >= now)
            .order_by(Appointment.starts_at)
        )
    )
