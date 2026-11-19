"""Incoming text messages from Twilio."""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response

from .. import db as dbmod
from ..agent.runner import handle_customer_text
from ..config import get_settings
from ..models import Business, Conversation, Customer
from ..security import mask_phone, normalize_us_phone, rate_limited, verify_twilio_signature
from ..services import compliance, usage
from ..services.adapters import get_adapters
from ..services.audit import audit
from ..services.conversations import add_message, get_or_create_customer, get_or_create_sms_conversation
from ..services.notify import notify_owner
from .common import business_by_sms_number, claim_event

log = logging.getLogger("frontdesk.sms")
router = APIRouter()

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response/>'


def _xml() -> Response:
    return Response(content=EMPTY_TWIML, media_type="application/xml")


def process_inbound_sms(business_id: str, from_phone: str, body: str, message_sid: str, now=None) -> None:
    """Everything that happens for one incoming text. Runs after we have already answered Twilio."""
    now = now or dbmod.utcnow()
    adapters = get_adapters()
    # Step A: save the customer's message first, on its own, so it is never lost even if step B fails.
    with dbmod.tenant_session(business_id) as db:
        b = db.get(Business, business_id)
        cust = get_or_create_customer(db, b, from_phone, now)
        conv = get_or_create_sms_conversation(db, b, cust, now)
        add_message(db, conv, "customer", body, now, meta={"sid": message_sid, "channel": "sms"})
        usage.record_sms(db, b.id, conv.id, body, "in")
        cust_id, conv_id = cust.id, conv.id
    # Step B: decide and answer.
    try:
        with dbmod.tenant_session(business_id) as db:
            b = db.get(Business, business_id)
            cust = db.get(Customer, cust_id)
            conv = db.get(Conversation, conv_id)

            kw = compliance.classify_keyword(body, opted_out=cust.sms_opt_out)
            if kw == "stop":
                if not cust.sms_opt_out:
                    cust.sms_opt_out, cust.opt_out_at = True, now
                    audit(db, actor="webhook", action="sms_opt_out", business_id=b.id, detail={"phone": mask_phone(from_phone)})
                reply = compliance.send_sms(db, b, cust, compliance.stop_text(b), conversation=conv, now=now,
                                            kind="opt_out_confirmation", is_opt_confirmation=True, adapters=adapters)
                log.info("STOP handled: %s", reply)
                return
            if kw == "help":
                compliance.send_sms(db, b, cust, compliance.help_text(b), conversation=conv, now=now, kind="help",
                                    is_opt_confirmation=True, adapters=adapters)
                return
            if kw == "start":
                if cust.sms_opt_out:
                    cust.sms_opt_out, cust.opt_out_at = False, None
                    audit(db, actor="webhook", action="sms_opt_in", business_id=b.id, detail={"phone": mask_phone(from_phone)})
                compliance.send_sms(db, b, cust, compliance.start_text(b), conversation=conv, now=now, kind="opt_in_confirmation",
                                    is_opt_confirmation=True, adapters=adapters)
                return
            if cust.sms_opt_out:
                return  # after STOP we send nothing. We keep the message in the inbox for the owner.

            if rate_limited(f"sms:{b.id}:{from_phone}", limit=20, per_seconds=600):
                audit(db, actor="webhook", action="sms_rate_limited", business_id=b.id, detail={"phone": mask_phone(from_phone)})
                return

            result = handle_customer_text(db, b, cust, conv, body, channel="sms", now=now, adapters=adapters,
                                          record_customer_message=False)
            compliance.send_sms(db, b, cust, result.reply, conversation=conv, now=now, kind="agent_reply",
                                is_reply=True, record_message=False, adapters=adapters)
    except Exception:
        log.exception("failed to process an incoming text (sid %s)", message_sid)
        try:
            with dbmod.tenant_session(business_id) as db:
                b = db.get(Business, business_id)
                notify_owner(db, b, f"We could not answer a text from {from_phone}. Please check the inbox and reply by hand.",
                             kind="alert", urgent=True, now=now, adapters=adapters)
        except Exception:
            log.exception("could not even alert the owner")


@router.post("/webhooks/twilio/sms")
async def twilio_sms(request: Request, background: BackgroundTasks):
    s = get_settings()
    form = await request.form()
    params = {k: v for k, v in form.items() if isinstance(v, str)}
    url = s.public_base_url.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if not verify_twilio_signature(url, params, request.headers.get("X-Twilio-Signature"), s.twilio_auth_token):
        log.warning("rejected a text webhook with a bad signature")
        return Response(status_code=403, content="forbidden")

    to_number = normalize_us_phone(params.get("To"))
    from_phone = normalize_us_phone(params.get("From"))
    sid = params.get("MessageSid") or params.get("SmsMessageSid") or ""
    body = params.get("Body", "")
    if not (to_number and from_phone and sid):
        return _xml()
    business_id = business_by_sms_number(to_number)
    if not business_id:
        log.warning("text for an unknown number")
        return _xml()
    with dbmod.tenant_session(business_id) as db:
        if not claim_event(db, business_id, "twilio_sms", sid):
            return _xml()  # same notice twice: do the work once
    background.add_task(process_inbound_sms, business_id, from_phone, body, sid)
    return _xml()
