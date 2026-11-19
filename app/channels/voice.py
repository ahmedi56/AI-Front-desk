"""Phone calls through Vapi (the managed voice platform).

Vapi listens and speaks. All business logic stays here:
  * assistant-request  -> we say which business this is and give Vapi the instructions + the tools
  * tool-calls         -> we run the tool for THAT call's business and customer
  * end-of-call-report -> we save the transcript, the cost, and send the missed-call text

Field names below follow Vapi's public docs as of Oct 2026. Fields marked CHECK must be confirmed with one real
test call (see docs/VERIFY-WITH-VENDORS.md). Everything is read defensively so an unexpected shape never crashes.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import db as dbmod
from ..agent import rules
from ..agent.prompts import system_prompt
from ..agent.tools import TOOL_SCHEMAS, ToolBox, ToolContext, mark_handoff
from ..config import get_settings
from ..models import Business, Conversation, Customer
from ..security import mask_phone, normalize_us_phone, verify_shared_secret
from ..services import automations, usage
from ..services.adapters import get_adapters
from ..services.audit import audit
from ..services.conversations import add_message, conversation_by_external, get_or_create_customer, new_conversation
from .common import business_by_voice_number, claim_event, saved_event_payload
from sqlalchemy import select

log = logging.getLogger("frontdesk.voice")
router = APIRouter()

DISCLOSURE = "This is an AI assistant for {name}. This call may be recorded. How can I help you today?"


def disclosure(name: str) -> str:
    return DISCLOSURE.format(name=name)


def _dig(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def dialed_number(msg: dict) -> str | None:
    """CHECK with a real call: where Vapi puts the number that was called."""
    for path in (("call", "phoneNumber", "number"), ("phoneNumber", "number"), ("call", "phoneNumber", "e164")):
        v = _dig(msg, *path)
        if v:
            return normalize_us_phone(str(v))
    return None


def caller_number(msg: dict) -> str | None:
    """CHECK with a real call: where Vapi puts the caller's number."""
    for path in (("call", "customer", "number"), ("customer", "number")):
        v = _dig(msg, *path)
        if v:
            return normalize_us_phone(str(v))
    return None


def call_id(msg: dict) -> str | None:
    v = _dig(msg, "call", "id")
    return str(v) if v else None


def tool_definitions(server_url: str, secret_header: dict) -> list[dict]:
    out = []
    for t in TOOL_SCHEMAS:
        out.append({
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
            "server": {"url": server_url, "headers": secret_header},
        })
    return out


def build_assistant(db, b: Business, cust: Customer, now) -> dict:
    s = get_settings()
    url = s.public_base_url.rstrip("/") + "/webhooks/vapi"
    assistant = {
        "firstMessage": disclosure(b.name),
        "model": {
            "provider": s.vapi_model_provider,
            "model": s.vapi_model,
            "messages": [{"role": "system", "content": system_prompt(db, b, cust, "voice", now)}],
            "tools": tool_definitions(url, {"X-Vapi-Secret": s.vapi_webhook_secret}),
            "temperature": 0.2,
            "maxTokens": 250,
        },
        "server": {"url": url, "headers": {"X-Vapi-Secret": s.vapi_webhook_secret}},
        "serverMessages": ["tool-calls", "end-of-call-report", "status-update"],
        "maxDurationSeconds": 900,
        "silenceTimeoutSeconds": 30,
    }
    if s.voice_extra_assistant_json:
        try:
            assistant.update(json.loads(s.voice_extra_assistant_json))  # voice, recording settings, etc.
        except json.JSONDecodeError:
            log.error("VOICE_EXTRA_ASSISTANT_JSON is not valid JSON; ignored")
    return assistant


def _json(data: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


# ---------------- message handlers ----------------
def handle_assistant_request(msg: dict, now=None) -> dict:
    now = now or dbmod.utcnow()
    dialed, caller, cid = dialed_number(msg), caller_number(msg), call_id(msg)
    business_id = business_by_voice_number(dialed) if dialed else None
    if not business_id:
        log.warning("call to an unknown number")
        return {"error": "Sorry, this number is not in service. Goodbye."}
    with dbmod.tenant_session(business_id) as db:
        b = db.get(Business, business_id)
        cust = get_or_create_customer(db, b, caller, now)
        conv = conversation_by_external(db, b, cid) if cid else None
        if conv is None:
            conv = new_conversation(db, b, cust, "voice", now, external_id=cid)
        if get_settings().global_kill_switch or not b.agent_enabled:
            if conv.status != "handed_off":
                mark_handoff(db, b, conv, reason="kill_switch_on", summary=f"Call from {caller or 'unknown number'} while the agent was off.",
                             urgent=True, now=now, adapters=get_adapters())
            if b.handoff_phone:
                return {"destination": {"type": "number", "number": b.handoff_phone}}
            return {"error": f"Thanks for calling {b.name}. We cannot take your call right now. Please try again soon."}
        add_message(db, conv, "agent", disclosure(b.name), now, meta={"kind": "disclosure"})
        return {"assistant": build_assistant(db, b, cust, now)}


def _parse_params(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _tool_calls(msg: dict) -> list[dict]:
    """CHECK with a real call: Vapi sends toolCallList (and also toolWithToolCallList)."""
    items = msg.get("toolCallList")
    if isinstance(items, list) and items:
        return [{"id": i.get("id"), "name": i.get("name"), "params": _parse_params(i.get("parameters"))} for i in items if isinstance(i, dict)]
    out = []
    for w in msg.get("toolWithToolCallList", []) or []:
        tc = w.get("toolCall", {}) if isinstance(w, dict) else {}
        out.append({"id": tc.get("id"), "name": w.get("name"), "params": _parse_params(tc.get("parameters"))})
    return out


def handle_tool_calls(msg: dict, now=None) -> dict:
    now = now or dbmod.utcnow()
    cid = call_id(msg)
    results = []
    if not cid:
        return {"results": []}
    with dbmod.system_session() as s:  # only to find which business this call belongs to
        found = s.execute(select(Conversation.business_id, Conversation.id).where(Conversation.external_id == cid, Conversation.channel == "voice")).first()
    if not found:
        return {"results": [{"name": c["name"], "toolCallId": c["id"], "result": json.dumps({"ok": False, "error": "unknown_call"})} for c in _tool_calls(msg)]}
    business_id, conv_id = found
    with dbmod.tenant_session(business_id) as db:
        b = db.get(Business, business_id)
        conv = db.get(Conversation, conv_id)
        cust = db.get(Customer, conv.customer_id)
        ctx = ToolContext(db=db, business=b, customer=cust, conversation=conv, now=now, adapters=get_adapters())
        box = ToolBox(ctx)
        for c in _tool_calls(msg):
            tid = str(c["id"])
            event = f"tool:{cid}:{tid}"
            if not claim_event(db, business_id, "vapi_tool", event):
                saved = saved_event_payload(db, "vapi_tool", event) or json.dumps({"ok": True, "note": "already done"})
                results.append({"name": c["name"], "toolCallId": tid, "result": saved})
                continue
            out = box.run(str(c["name"]), c["params"])
            payload = json.dumps(out)
            from ..models import ProcessedEvent

            row = db.scalar(select(ProcessedEvent).where(ProcessedEvent.provider == "vapi_tool", ProcessedEvent.event_id == event))
            if row:
                row.payload = payload
            results.append({"name": c["name"], "toolCallId": tid, "result": payload})
    return {"results": results}


def handle_end_of_call(msg: dict, now=None) -> dict:
    now = now or dbmod.utcnow()
    cid = call_id(msg)
    if not cid:
        return {}
    with dbmod.system_session() as s:
        found = s.execute(select(Conversation.business_id, Conversation.id).where(Conversation.external_id == cid, Conversation.channel == "voice")).first()
    if not found:
        return {}
    business_id, conv_id = found
    adapters = get_adapters()
    with dbmod.tenant_session(business_id) as db:
        if not claim_event(db, business_id, "vapi_eoc", cid):
            return {}
        b = db.get(Business, business_id)
        conv = db.get(Conversation, conv_id)
        cust = db.get(Customer, conv.customer_id)
        lines = _dig(msg, "artifact", "messages") or msg.get("messages") or []
        emergency_text = None
        for line in lines:
            if not isinstance(line, dict):
                continue
            role = {"user": "customer", "assistant": "agent", "bot": "agent"}.get(str(line.get("role")))
            text = str(line.get("message") or line.get("content") or "").strip()
            if not role or not text:
                continue
            add_message(db, conv, role, text[:2000], now, meta={"channel": "voice"})
            if role == "customer" and emergency_text is None and rules.check_rules(text, b) and rules.check_rules(text, b).kind in ("emergency", "urgent"):
                emergency_text = text
        if not lines and (t := _dig(msg, "artifact", "transcript")):
            add_message(db, conv, "customer", str(t)[:4000], now, meta={"channel": "voice", "kind": "transcript"})
        seconds = int(msg.get("durationSeconds") or _dig(msg, "call", "duration") or 0)
        if seconds:
            usage.record_call_seconds(db, conv, seconds)
        conv.ended_at = now
        if emergency_text and conv.status != "handed_off":
            # backstop: the voice model should have called handoff_to_human; if it did not, a person is told now
            mark_handoff(db, b, conv, reason="emergency: detected in call transcript", summary=f'Caller said: "{emergency_text[:240]}"',
                         urgent=True, now=now, adapters=adapters)
        elif conv.status == "open":
            conv.status = "closed"
        audit(db, actor="webhook", action="call_ended", business_id=b.id,
              detail={"conversation_id": conv.id, "seconds": seconds, "phone": mask_phone(cust.phone), "ended_reason": msg.get("endedReason")})
        automations.send_missed_call_textbacks(db, b, now, adapters)
    return {}


@router.post("/webhooks/vapi")
async def vapi_webhook(request: Request):
    s = get_settings()
    header = request.headers.get("x-vapi-secret") or request.headers.get("authorization")
    if not verify_shared_secret(header, s.vapi_webhook_secret):
        log.warning("rejected a voice webhook with a bad secret")
        return Response(status_code=403, content="forbidden")
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="bad request")
    msg = body.get("message") if isinstance(body, dict) else None
    if not isinstance(msg, dict):
        return _json({})
    kind = msg.get("type")
    try:
        if kind == "assistant-request":
            return _json(handle_assistant_request(msg))
        if kind == "tool-calls":
            return _json(handle_tool_calls(msg))
        if kind == "end-of-call-report":
            return _json(handle_end_of_call(msg))
    except Exception:
        log.exception("voice webhook failed (%s)", kind)
        if kind == "tool-calls":
            return _json({"results": [{"name": c["name"], "toolCallId": c["id"],
                                       "result": json.dumps({"ok": False, "error": "internal_error", "instruction": "Do not confirm anything. A person will follow up."})}
                                      for c in _tool_calls(msg)]})
        return _json({})
    return _json({})
