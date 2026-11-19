"""Small plug-in pieces for outside services (SMS, email, calendar, AI).

The rest of the code only talks to these simple interfaces. To change a vendor
(for example Twilio -> Telnyx) you write one new class here. Nothing else changes.
Tests replace these with fakes so no real message, call or payment is ever made.
"""
from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Callable, Protocol

import httpx

from ..config import get_settings
from ..models import Business

log = logging.getLogger("frontdesk.adapters")


class CalendarUnavailable(Exception):
    """The calendar could not be reached or refused the request. Never confirm a booking after this."""


class SmsSendError(Exception):
    pass


# ---------------- SMS ----------------
class SmsProvider(Protocol):
    def send(self, *, from_number: str, to: str, body: str) -> str: ...


class TwilioSms:
    def __init__(self, account_sid: str, auth_token: str, client: httpx.Client | None = None):
        self.sid, self.token = account_sid, auth_token
        self.client = client or httpx.Client(timeout=10)

    def send(self, *, from_number: str, to: str, body: str) -> str:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        try:
            r = self.client.post(url, auth=(self.sid, self.token), data={"From": from_number, "To": to, "Body": body})
        except httpx.HTTPError as exc:
            raise SmsSendError(f"network error: {type(exc).__name__}") from exc
        if r.status_code >= 300:
            # Do not log the body of the text. The error code from Twilio is enough.
            raise SmsSendError(f"twilio status {r.status_code}")
        return r.json().get("sid", "")


class NullSms:
    def send(self, *, from_number: str, to: str, body: str) -> str:
        raise SmsSendError("no SMS provider configured")


# ---------------- Email ----------------
class EmailSender(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> None: ...


class SmtpEmail:
    def __init__(self, host: str, port: int, user: str, password: str, sender: str):
        self.host, self.port, self.user, self.password, self.sender = host, port, user, password, sender

    def send(self, *, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=10) as s:
            s.starttls()
            if self.user:
                s.login(self.user, self.password)
            s.send_message(msg)


class NullEmail:
    def send(self, *, to: str, subject: str, body: str) -> None:
        raise RuntimeError("no email configured")


# ---------------- Calendar ----------------
class CalendarAdapter(Protocol):
    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]: ...
    def create_event(self, *, title: str, start: datetime, end: datetime, description: str, tz: str) -> str: ...
    def update_event(self, event_id: str, *, start: datetime, end: datetime, tz: str) -> None: ...
    def delete_event(self, event_id: str) -> None: ...


class InternalCalendar:
    """The database is the calendar. Used when the business has not connected Google Calendar."""

    def busy(self, start, end):
        return []

    def create_event(self, *, title, start, end, description, tz):
        return ""

    def update_event(self, event_id, *, start, end, tz):
        return None

    def delete_event(self, event_id):
        return None


class GoogleCalendar:
    API = "https://www.googleapis.com/calendar/v3"
    TOKEN_URL = "https://oauth2.googleapis.com/token"

    def __init__(self, *, refresh_token: str, calendar_id: str, client_id: str, client_secret: str,
                 client: httpx.Client | None = None):
        self.refresh_token, self.calendar_id = refresh_token, calendar_id
        self.client_id, self.client_secret = client_id, client_secret
        self.client = client or httpx.Client(timeout=8)
        self._access: str | None = None

    def _token(self) -> str:
        if self._access:
            return self._access
        try:
            r = self.client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.HTTPError as exc:
            raise CalendarUnavailable("cannot reach Google") from exc
        if r.status_code != 200:
            raise CalendarUnavailable(f"Google refused the login ({r.status_code})")
        self._access = r.json()["access_token"]
        return self._access

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            r = self.client.request(method, f"{self.API}{path}", headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise CalendarUnavailable("cannot reach Google Calendar") from exc
        if r.status_code >= 400 and not (method == "DELETE" and r.status_code in (404, 410)):
            raise CalendarUnavailable(f"Google Calendar error {r.status_code}")
        return r

    def busy(self, start, end):
        r = self._call(
            "POST",
            "/freeBusy",
            json={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": self.calendar_id}]},
        )
        items = r.json().get("calendars", {}).get(self.calendar_id, {}).get("busy", [])
        return [(datetime.fromisoformat(i["start"].replace("Z", "+00:00")),
                 datetime.fromisoformat(i["end"].replace("Z", "+00:00"))) for i in items]

    def create_event(self, *, title, start, end, description, tz):
        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
        }
        return self._call("POST", f"/calendars/{self.calendar_id}/events", json=body).json()["id"]

    def update_event(self, event_id, *, start, end, tz):
        body = {"start": {"dateTime": start.isoformat(), "timeZone": tz},
                "end": {"dateTime": end.isoformat(), "timeZone": tz}}
        self._call("PATCH", f"/calendars/{self.calendar_id}/events/{event_id}", json=body)

    def delete_event(self, event_id):
        self._call("DELETE", f"/calendars/{self.calendar_id}/events/{event_id}")


def default_calendar_factory(business: Business) -> CalendarAdapter:
    if business.calendar_kind == "google" and business.google_refresh_token_enc and business.google_calendar_id:
        from ..security import decrypt_text

        token = decrypt_text(business.google_refresh_token_enc)
        s = get_settings()
        if token and s.google_client_id:
            return GoogleCalendar(
                refresh_token=token,
                calendar_id=business.google_calendar_id,
                client_id=s.google_client_id,
                client_secret=s.google_client_secret,
            )
        # Google is selected but broken: fail safe (never silently pretend the calendar is free)
        return _BrokenCalendar()
    return InternalCalendar()


class _BrokenCalendar:
    def busy(self, start, end):
        raise CalendarUnavailable("calendar is not connected correctly")

    def create_event(self, **kw):
        raise CalendarUnavailable("calendar is not connected correctly")

    def update_event(self, *a, **kw):
        raise CalendarUnavailable("calendar is not connected correctly")

    def delete_event(self, *a, **kw):
        raise CalendarUnavailable("calendar is not connected correctly")


# ---------------- The bundle ----------------
@dataclass
class Adapters:
    sms: SmsProvider = field(default_factory=NullSms)
    email: EmailSender = field(default_factory=NullEmail)
    calendar_factory: Callable[[Business], CalendarAdapter] = default_calendar_factory
    llm: object | None = None  # set lazily to the Claude client


_adapters: Adapters | None = None


def build_default_adapters() -> Adapters:
    s = get_settings()
    a = Adapters()
    if s.twilio_account_sid and s.twilio_auth_token:
        a.sms = TwilioSms(s.twilio_account_sid, s.twilio_auth_token)
    if s.smtp_host and s.smtp_from:
        a.email = SmtpEmail(s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password, s.smtp_from)
    return a


def get_adapters() -> Adapters:
    global _adapters
    if _adapters is None:
        _adapters = build_default_adapters()
    return _adapters


def set_adapters(a: Adapters | None) -> None:
    global _adapters
    _adapters = a
