"""Tell the business owner about things that need a person."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Business, Notification
from ..security import mask_phone
from .adapters import Adapters, get_adapters

log = logging.getLogger("frontdesk.notify")

MAX_URGENT_PER_HOUR = 3
MAX_NOTIFICATIONS_PER_CONVERSATION = 6  # stops a looping or abusive chat from flooding the owner


def notify_owner(
    db: Session,
    business: Business,
    text: str,
    *,
    kind: str,
    urgent: bool = False,
    conversation_id: str | None = None,
    now: datetime | None = None,
    adapters: Adapters | None = None,
    subject: str | None = None,
) -> Notification:
    """Always saved in the database (the owner sees it in the dashboard). Then we try email and SMS."""
    adapters = adapters or get_adapters()
    note = Notification(business_id=business.id, kind=kind, urgent=urgent, text=text, conversation_id=conversation_id)
    if now:
        note.created_at = now

    limited = False
    if conversation_id:
        # Count BEFORE adding this note, so the new note is not counted against itself.
        base = (Notification.business_id == business.id, Notification.conversation_id == conversation_id)
        total = db.scalar(select(func.count()).select_from(Notification).where(*base)) or 0
        urgent_recent = db.scalar(select(func.count()).select_from(Notification).where(
            *base, Notification.urgent.is_(True), Notification.created_at >= (now or datetime.now(timezone.utc)) - timedelta(hours=1))) or 0
        # one chat can not flood the owner: at most 6 notes, and at most 3 URGENT alerts per hour
        limited = (total >= MAX_NOTIFICATIONS_PER_CONVERSATION and not urgent) or (urgent and urgent_recent >= MAX_URGENT_PER_HOUR)
    db.add(note)
    if limited:
        note.delivered_via = "stored_only(limit)"
        return note

    delivered: list[str] = []
    subject = " ".join((subject or ("URGENT: " if urgent else "") + f"{business.name}: {kind}").split())[:200]
    if business.owner_email:
        try:
            adapters.email.send(to=business.owner_email, subject=subject, body=text)
            delivered.append("email")
        except Exception as exc:  # one failing channel must not stop the others
            from .adapters import NullEmail

            if isinstance(adapters.email, NullEmail):
                log.info("owner email skipped: no email server set up (SMTP_HOST); the alert is on the dashboard")
            else:
                log.warning("owner email failed: %s", type(exc).__name__)
    if business.handoff_phone and (urgent or kind in ("handoff", "emergency", "report")):
        from_number = business.sms_number if business.sms_enabled and business.sms_number else get_settings().twilio_from_number
        if from_number:
            try:
                adapters.sms.send(from_number=from_number, to=business.handoff_phone, body=text[:600])
                delivered.append("sms")
            except Exception as exc:
                log.warning("owner sms failed for %s: %s", mask_phone(business.handoff_phone), type(exc).__name__)
    note.delivered_via = ",".join(delivered) or "stored_only"
    return note
