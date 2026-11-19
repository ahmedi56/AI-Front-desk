"""The agent's tools.

SAFETY DESIGN: no tool takes a business id or a customer id. A ToolBox is created for ONE business and
ONE customer by our server, from the phone number / session, never from anything the model or customer says.
So even a model that has been tricked cannot read or change another customer's or business's data.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Appointment, Business, Conversation, Customer, Lead, Message, Notification
from ..security import normalize_us_phone, speak_phone
from ..services import scheduling as sch
from ..services.adapters import Adapters, CalendarUnavailable, get_adapters
from ..services.audit import audit
from ..services.notify import notify_owner
from .prompts import facts_block
from .rules import INVALID_PHONE_REPLY

log = logging.getLogger("frontdesk.tools")

MAX_UPCOMING_PER_CUSTOMER = 3
MAX_BOOKINGS_PER_CONVERSATION = 3
MAX_OWNER_NOTES_PER_CONVERSATION = 3



def _same_instant(a: datetime, b: datetime) -> bool:
    """Compare two times. SQLite gives back times without a zone; we treat those as UTC."""
    def fix(x: datetime) -> datetime:
        return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)
    return fix(a) == fix(b)

def _clip(value: Any, n: int) -> str:
    return str(value or "").strip()[:n]


_NAME_BAD = re.compile(r"[^\w .'\-]", re.UNICODE)


def clean_name(value: Any) -> str:
    """Names come from the customer, so they are untrusted. Keep letters, digits, spaces and . ' - only."""
    text = _NAME_BAD.sub("", " ".join(str(value or "").split()))
    return text.strip()[:60]


@dataclass
class ToolContext:
    db: Session
    business: Business
    customer: Customer
    conversation: Conversation
    now: datetime
    adapters: Adapters = field(default_factory=get_adapters)
    handed_off: bool = False
    urgent: bool = False
    verified_booking_fact: bool = False  # a booking tool succeeded in this turn
    tool_errors: int = 0
    bookings_this_conversation: int = 0
    owner_notes: int = 0
    calendar_down_reported: bool = False
    fixed_reply: str | None = None  # a tool may end the turn with a fixed sentence (no more AI steps)

    @property
    def calendar(self):
        return self.adapters.calendar_factory(self.business)


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_business_info",
        "description": "Get the business's opening hours, address, services with prices, and answers. Use it when the "
                       "customer asks about the business. These are the ONLY facts you may state.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_free_times",
        "description": "Find free appointment times. Give a local date (YYYY-MM-DD). If the customer asked for a "
                       "specific time, also give around_time (HH:MM, 24 hour). Returns the requested time if free, "
                       "otherwise the two nearest free times.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD in the business's local time"},
                "around_time": {"type": "string", "description": "HH:MM 24 hour, optional"},
                "service": {"type": "string", "description": "service name, optional"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Book an appointment for the current customer. Only call this after the customer agreed to a "
                       "specific time that check_free_times showed. Say it is booked only if this returns ok=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "local start time, YYYY-MM-DDTHH:MM"},
                "service": {"type": "string"},
                "customer_name": {"type": "string"},
                "contact_phone": {"type": "string", "description": "only if different from the number we already have"},
                "phone_confirmed": {"type": "boolean", "description": "true only after the customer confirmed the digits you read back"},
                "notes": {"type": "string", "description": "short note about the job, e.g. what is wrong"},
            },
            "required": ["start", "customer_name"],
        },
    },
    {
        "name": "list_my_appointments",
        "description": "List the current customer's own upcoming appointments (never anyone else's).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "move_appointment",
        "description": "Move one of the current customer's own appointments to a new time.",
        "input_schema": {
            "type": "object",
            "properties": {"appointment_id": {"type": "string"}, "new_start": {"type": "string", "description": "YYYY-MM-DDTHH:MM local"}},
            "required": ["appointment_id", "new_start"],
        },
    },
    {
        "name": "cancel_appointment",
        "description": "Cancel one of the current customer's own appointments. The owner is told.",
        "input_schema": {"type": "object", "properties": {"appointment_id": {"type": "string"}}, "required": ["appointment_id"]},
    },
    {
        "name": "save_lead",
        "description": "Save the customer's details for a person to follow up: name, what they need, preferred time. "
                       "Use it when you cannot answer from the facts (for example a price that is not listed).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "need": {"type": "string"},
                "preferred_time": {"type": "string"},
                "contact_phone": {"type": "string"},
                "phone_confirmed": {"type": "boolean"},
                "reason": {"type": "string", "description": "short code: price_followup | unknown_answer | other_language | other"},
            },
            "required": ["need"],
        },
    },
    {
        "name": "handoff_to_human",
        "description": "Hand the conversation to a person at the business. Always include a short summary. "
                       "Set urgent=true for safety emergencies or time-critical problems.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}, "summary": {"type": "string"}, "urgent": {"type": "boolean"}},
            "required": ["reason", "summary"],
        },
    },
    {
        "name": "notify_owner",
        "description": "Send a short note to the business owner (use sparingly; handoff_to_human already notifies).",
        "input_schema": {"type": "object", "properties": {"message": {"type": "string"}, "urgent": {"type": "boolean"}}, "required": ["message"]},
    },
]

TOOL_NAMES = {t["name"] for t in TOOL_SCHEMAS}


class ToolBox:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx

    # ---- dispatcher ----
    def run(self, name: str, args: dict | None) -> dict:
        args = args if isinstance(args, dict) else {}
        if name not in TOOL_NAMES:
            return {"ok": False, "error": "unknown_tool"}
        try:
            result = getattr(self, f"t_{name}")(**{k: v for k, v in args.items() if k in self._allowed(name)})
        except TypeError:
            result = {"ok": False, "error": "bad_arguments"}
        except sch.BookingError as exc:
            result = {"ok": False, "error": str(exc) or "refused"}
        except CalendarUnavailable:
            result = self._calendar_down()
        except Exception:  # a tool bug must never crash the conversation or leak details to the model
            log.exception("tool %s failed", name)
            result = {"ok": False, "error": "internal_error",
                      "instruction": "Something went wrong. Do not confirm anything. Hand over to a person."}
        if not result.get("ok", True):
            self.ctx.tool_errors += 1
        return result

    @staticmethod
    def _allowed(name: str) -> set[str]:
        for t in TOOL_SCHEMAS:
            if t["name"] == name:
                return set(t["input_schema"]["properties"])
        return set()

    # ---- helpers ----
    def _service(self, name: str | None):
        c = self.ctx
        return sch.find_service(c.db, c.business, name)

    def _calendar_down(self) -> dict:
        """The calendar failed. Never confirm. Keep the details and tell the owner (once)."""
        c = self.ctx
        if not c.calendar_down_reported:
            c.calendar_down_reported = True
            self._upsert_lead(need="Wants an appointment (calendar was unavailable)", reason="calendar_down")
            notify_owner(c.db, c.business,
                         f"The calendar was unavailable while {c.customer.phone or 'a customer'} tried to book. "
                         f"Their details are saved as a lead. Please confirm a time with them.",
                         kind="alert", urgent=False, conversation_id=c.conversation.id, now=c.now, adapters=c.adapters)
            audit(c.db, actor="agent", action="calendar_unavailable", business_id=c.business.id)
        return {
            "ok": False,
            "error": "calendar_unavailable",
            "instruction": "Do NOT confirm any time. Tell the customer a person will confirm shortly. Ask for their "
                           "name and best time if you do not have them, then use save_lead.",
        }

    def _upsert_lead(self, *, name=None, need=None, preferred_time=None, phone=None, reason=None) -> Lead:
        c = self.ctx
        lead = c.db.scalar(select(Lead).where(Lead.business_id == c.business.id, Lead.conversation_id == c.conversation.id))
        if lead is None:
            lead = Lead(business_id=c.business.id, customer_id=c.customer.id, conversation_id=c.conversation.id,
                        phone=c.customer.phone, created_at=c.now)
            c.db.add(lead)
        if name:
            lead.name = clean_name(name) or None
            if not c.customer.name:
                c.customer.name = lead.name
        if need:
            lead.need = _clip(need, 600)
        if preferred_time:
            lead.preferred_time = _clip(preferred_time, 200)
        if phone:
            lead.phone = phone
        if reason:
            lead.reason = _clip(reason, 100)
        return lead

    def _phone_check(self, contact_phone: str | None, confirmed: bool | None) -> tuple[str | None, dict | None]:
        """Returns (phone to use, error to give the model). A different number must be read back first."""
        if not contact_phone:
            return None, None
        p = normalize_us_phone(contact_phone)
        if not p:
            # The AI used to try the same bad number again and again. One clear question to the customer is better.
            self.ctx.fixed_reply = INVALID_PHONE_REPLY
            return None, {"ok": False, "error": "invalid_phone", "instruction": "That is not a valid US phone number. Ask again."}
        if p != self.ctx.customer.phone and confirmed is not True:
            return None, {"ok": False, "error": "confirm_phone",
                          "instruction": f"Read the number back digit by digit ({speak_phone(p)}) and ask the customer "
                                         "to confirm. Then call again with phone_confirmed=true."}
        return p, None

    # ---- tools ----
    def t_get_business_info(self) -> dict:
        c = self.ctx
        return {"ok": True, "facts": facts_block(c.db, c.business)}

    def t_check_free_times(self, date: str, around_time: str | None = None, service: str | None = None) -> dict:
        c = self.ctx
        b = c.business
        try:
            day = datetime.strptime(str(date)[:10], "%Y-%m-%d").date()
        except ValueError:
            return {"ok": False, "error": "bad_date", "instruction": "Use YYYY-MM-DD"}
        svc = self._service(service)
        if service and not svc and self._has_services():
            return {"ok": False, "error": "unknown_service", "services": self._service_names()}
        dur = sch.default_duration(c.db, b, svc)
        cal = c.calendar
        day_slots = sch.slots_on_day(c.db, b, cal, duration_min=dur, day=day, now=c.now)
        if around_time:
            try:
                h, m = around_time.split(":")
                target = sch.parse_local(b, f"{day.isoformat()}T{int(h):02d}:{int(m):02d}")
            except (ValueError, sch.BookingError):
                return {"ok": False, "error": "bad_time", "instruction": "Use HH:MM, 24 hour"}
            if target in day_slots or any(abs((s - target).total_seconds()) < 60 for s in day_slots):
                return {"ok": True, "requested_time_free": True, "start": sch.iso_local(b, target),
                        "label": sch.fmt_local(b, target)}
            near = sch.nearest_slots(c.db, b, cal, duration_min=dur, target=target, now=c.now, n=2)
            return {"ok": True, "requested_time_free": False,
                    "nearest": [{"start": sch.iso_local(b, s), "label": sch.fmt_local(b, s)} for s in near]}
        if day_slots:
            return {"ok": True, "slots": [{"start": sch.iso_local(b, s), "label": sch.fmt_local(b, s)} for s in day_slots[:6]]}
        noon = sch.parse_local(b, f"{day.isoformat()}T12:00")
        near = sch.nearest_slots(c.db, b, cal, duration_min=dur, target=noon, now=c.now, n=3)
        return {"ok": True, "slots": [], "note": "nothing free that day",
                "nearest": [{"start": sch.iso_local(b, s), "label": sch.fmt_local(b, s)} for s in near]}

    def _has_services(self) -> bool:
        return bool(self._service_names())

    def _service_names(self) -> list[str]:
        from ..models import Service

        c = self.ctx
        return [s.name for s in c.db.scalars(select(Service).where(Service.business_id == c.business.id, Service.active.is_(True)))]

    def t_book_appointment(self, start: str, customer_name: str, service: str | None = None,
                           contact_phone: str | None = None, phone_confirmed: bool | None = None,
                           notes: str | None = None) -> dict:
        c = self.ctx
        b = c.business
        name = clean_name(customer_name)
        if not name:
            return {"ok": False, "error": "need_name", "instruction": "Ask for the customer's name first."}
        phone, err = self._phone_check(contact_phone, phone_confirmed)
        if err:
            return err
        if not c.customer.phone and not phone:
            return {"ok": False, "error": "need_phone", "instruction": "Ask for a phone number and read it back to confirm."}
        if c.bookings_this_conversation >= MAX_BOOKINGS_PER_CONVERSATION:
            return {"ok": False, "error": "limit_reached", "instruction": "Too many bookings in this chat. Hand over to a person."}
        if len(sch.upcoming_for_customer(c.db, b, c.customer, c.now)) >= MAX_UPCOMING_PER_CUSTOMER:
            return {"ok": False, "error": "too_many_upcoming", "instruction": "This customer already has the maximum number of upcoming appointments. Hand over to a person."}
        svc = self._service(service)
        if self._has_services() and not svc:
            return {"ok": False, "error": "unknown_service", "services": self._service_names(),
                    "instruction": "Ask which of these services the customer wants."}
        service_name = svc.name if svc else (_clip(service, 100) or "Appointment")
        start_utc = sch.parse_local(b, start)
        # The model sometimes asks to book the same time again (for example after the customer says "thanks").
        # The customer's OWN booking would make that slot look "taken", so we answer "already booked" and change nothing.
        for mine in sch.upcoming_for_customer(c.db, b, c.customer, c.now):
            if _same_instant(mine.starts_at, start_utc):
                c.verified_booking_fact = True
                return {"ok": True, "already_booked": True, "appointment_id": mine.id, "service": mine.service_name,
                        "confirmation": f"Already booked: {mine.service_name} on {sch.fmt_local(b, mine.starts_at)}.",
                        "instruction": "This customer already has this exact appointment. Do not book again. Just confirm it once."}
        c.customer.name = c.customer.name or name
        note_text = _clip(notes, 500)
        if phone and phone != c.customer.phone:
            # A number typed in a chat or said on a call is NOT proven to belong to that person, so it is only written
            # in the note for the owner to call. We never send automatic texts to it.
            note_text = (note_text + f"\nContact phone (given by customer): {phone}").strip()
        try:
            appt = sch.book(c.db, b, c.calendar, customer=c.customer, conversation=c.conversation, start=start_utc,
                            service=svc, service_name=service_name, now=c.now, notes=note_text or None)
        except sch.SlotUnavailable as exc:
            reason = str(exc)
            dur = sch.default_duration(c.db, b, svc)
            near = sch.nearest_slots(c.db, b, c.calendar, duration_min=dur, target=start_utc, now=c.now, n=2)
            return {"ok": False, "error": reason,
                    "nearest": [{"start": sch.iso_local(b, s), "label": sch.fmt_local(b, s)} for s in near],
                    "instruction": "That time cannot be booked. Offer the nearest times."}
        c.bookings_this_conversation += 1
        c.verified_booking_fact = True
        audit(c.db, actor="agent", action="booked", business_id=b.id, detail={"appointment_id": appt.id})
        return {"ok": True, "appointment_id": appt.id, "service": service_name,
                "confirmation": f"Booked: {service_name} on {sch.fmt_local(b, appt.starts_at)}."}

    def t_list_my_appointments(self) -> dict:
        c = self.ctx
        items = sch.upcoming_for_customer(c.db, c.business, c.customer, c.now)
        c.verified_booking_fact = True
        return {"ok": True, "appointments": [
            {"appointment_id": a.id, "service": a.service_name, "start": sch.iso_local(c.business, a.starts_at),
             "label": sch.fmt_local(c.business, a.starts_at)} for a in items]}

    def _own_appointment(self, appointment_id: str) -> Appointment | None:
        c = self.ctx
        a = c.db.get(Appointment, str(appointment_id))
        # Same answer for "does not exist" and "belongs to someone else": we never reveal which.
        if a is None or a.business_id != c.business.id or a.customer_id != c.customer.id or a.status != "booked":
            audit(c.db, actor="agent", action="appointment_access_denied", business_id=c.business.id,
                  detail={"conversation_id": c.conversation.id})
            return None
        return a

    def t_move_appointment(self, appointment_id: str, new_start: str) -> dict:
        c = self.ctx
        a = self._own_appointment(appointment_id)
        if not a:
            return {"ok": False, "error": "not_found"}
        start_utc = sch.parse_local(c.business, new_start)
        try:
            sch.reschedule(c.db, c.business, c.calendar, appt=a, new_start=start_utc, now=c.now, conversation=c.conversation)
        except sch.SlotUnavailable as exc:
            dur = int((a.ends_at - a.starts_at).total_seconds() // 60)
            near = sch.nearest_slots(c.db, c.business, c.calendar, duration_min=dur, target=start_utc, now=c.now, n=2)
            return {"ok": False, "error": str(exc),
                    "nearest": [{"start": sch.iso_local(c.business, s), "label": sch.fmt_local(c.business, s)} for s in near]}
        c.verified_booking_fact = True
        return {"ok": True, "confirmation": f"Moved to {sch.fmt_local(c.business, a.starts_at)}."}

    def t_cancel_appointment(self, appointment_id: str) -> dict:
        c = self.ctx
        a = self._own_appointment(appointment_id)
        if not a:
            return {"ok": False, "error": "not_found"}
        sch.cancel(c.db, c.business, c.calendar, appt=a, now=c.now, conversation=c.conversation)
        c.verified_booking_fact = True
        return {"ok": True, "confirmation": "That appointment is cancelled."}

    def t_save_lead(self, need: str, name: str | None = None, preferred_time: str | None = None,
                    contact_phone: str | None = None, phone_confirmed: bool | None = None,
                    reason: str | None = None) -> dict:
        c = self.ctx
        phone, err = self._phone_check(contact_phone, phone_confirmed)
        if err:
            return err
        first = c.db.scalar(select(func.count()).select_from(Lead).where(Lead.business_id == c.business.id, Lead.conversation_id == c.conversation.id)) == 0
        lead = self._upsert_lead(name=name, need=need, preferred_time=preferred_time, phone=phone, reason=reason)
        c.db.flush()
        if first:
            notify_owner(c.db, c.business, f"New lead: {lead.name or lead.phone} - {lead.need}", kind="lead",
                         conversation_id=c.conversation.id, now=c.now, adapters=c.adapters)
        return {"ok": True, "lead_id": lead.id}

    def t_handoff_to_human(self, reason: str, summary: str, urgent: bool | None = None) -> dict:
        c = self.ctx
        mark_handoff(c.db, c.business, c.conversation, reason=_clip(reason, 190), summary=_clip(summary, 800),
                     urgent=bool(urgent), now=c.now, adapters=c.adapters)
        c.handed_off = True
        c.urgent = bool(urgent)
        return {"ok": True, "instruction": "Tell the customer politely that a person will follow up soon."}

    def t_notify_owner(self, message: str, urgent: bool | None = None) -> dict:
        c = self.ctx
        if c.owner_notes >= MAX_OWNER_NOTES_PER_CONVERSATION:
            return {"ok": False, "error": "limit_reached"}
        c.owner_notes += 1
        notify_owner(c.db, c.business, _clip(message, 500), kind="alert", urgent=bool(urgent),
                     conversation_id=c.conversation.id, now=c.now, adapters=c.adapters)
        return {"ok": True}


def mark_handoff(db: Session, business: Business, conv: Conversation, *, reason: str, summary: str, urgent: bool,
                 now: datetime, adapters: Adapters | None = None) -> None:
    """Mark a conversation as needing a person and tell the owner. Used by tools AND by the rules in code."""
    first_time = conv.status != "handed_off"
    conv.status = "handed_off"
    conv.handoff_reason = reason
    if summary:
        conv.summary = summary
    if first_time or urgent:
        who = conv.customer_id
        cust = db.get(Customer, who)
        phone = cust.phone if cust else None
        label = "EMERGENCY" if reason.startswith("emergency") else ("URGENT" if urgent else "Handoff")
        text = f"{label}: {reason}. Customer: {cust.name if cust and cust.name else phone or 'unknown'}"
        if phone:
            text += f" ({phone})"
        if summary:
            text += f". Summary: {summary}"
        kind = "emergency" if reason.startswith("emergency") else "handoff"
        notify_owner(db, business, text, kind=kind, urgent=urgent, conversation_id=conv.id, now=now, adapters=adapters)
    audit(db, actor="agent", action="handoff", business_id=business.id,
          detail={"conversation_id": conv.id, "reason": reason, "urgent": urgent})
