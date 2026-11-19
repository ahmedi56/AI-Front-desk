"""Web chat box that a business can put on its website."""
from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import db as dbmod
from ..agent.runner import handle_customer_text
from ..models import Business, Customer
from ..security import rate_limited
from ..services.adapters import get_adapters
from ..services.conversations import conversation_by_external, get_or_create_customer, new_conversation
from .common import business_by_slug

router = APIRouter()


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    session_id: str | None = Field(default=None, max_length=80)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/api/chat/{slug}/message")
def chat_message(slug: str, body: ChatIn, request: Request):
    ip = request.client.host if request.client else "unknown"
    if rate_limited(f"web-ip:{ip}", limit=30, per_seconds=300):
        return JSONResponse({"error": "Too many messages. Please wait a few minutes."}, status_code=429)
    business_id = business_by_slug(slug)
    if not business_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    token = body.session_id if body.session_id and len(body.session_id) >= 22 else secrets.token_urlsafe(24)
    if rate_limited(f"web-sess:{_hash(token)}", limit=25, per_seconds=600):
        return JSONResponse({"error": "Too many messages. Please wait a few minutes."}, status_code=429)
    now = dbmod.utcnow()
    with dbmod.tenant_session(business_id) as db:
        b = db.get(Business, business_id)
        ext = "web:" + _hash(token)
        conv = conversation_by_external(db, b, ext)
        if conv is None:
            cust = get_or_create_customer(db, b, None, now)
            conv = new_conversation(db, b, cust, "web", now, external_id=ext)
        else:
            cust = db.get(Customer, conv.customer_id)
        result = handle_customer_text(db, b, cust, conv, body.text, channel="web", now=now, adapters=get_adapters())
    # The session id is a secret that proves "this is the same visitor". It is only ever returned to that visitor.
    return {"session_id": token, "reply": result.reply, "handed_off": result.handed_off}
