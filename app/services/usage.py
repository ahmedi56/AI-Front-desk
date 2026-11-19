"""Track what each business costs us: AI tokens, call minutes, texts."""
from __future__ import annotations

import math

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Conversation, UsageEvent

SMS_COST_PER_SEGMENT_USD = 0.011  # carrier fee + provider fee, rough all-in; CHECK your own invoices
VOICE_COST_PER_MINUTE_USD = 0.15  # rough all-in (voice platform + AI + phone); CHECK your own invoices


def llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    s = get_settings()
    if not model.startswith("claude"):
        return 0.0  # free providers (Groq, Gemini free tier) cost nothing; only paid Claude models are priced
    if model == s.main_model_hard:
        pin, pout = s.price_in_hard, s.price_out_hard
    else:
        pin, pout = s.price_in_simple, s.price_out_simple
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


def record_llm(db: Session, conv: Conversation, model: str, input_tokens: int, output_tokens: int, *,
               backup: bool = False) -> float:
    cost = llm_cost(model, input_tokens, output_tokens)
    conv.cost_usd = (conv.cost_usd or 0.0) + cost
    if backup:  # counted apart, so the owner sees on the Costs page how often the main AI was busy
        db.add(UsageEvent(business_id=conv.business_id, conversation_id=conv.id, kind="llm_backup", quantity=1,
                          cost_usd=0.0, detail={"model": model}))
    db.add(
        UsageEvent(
            business_id=conv.business_id,
            conversation_id=conv.id,
            kind="llm",
            quantity=input_tokens + output_tokens,
            cost_usd=cost,
            detail={"model": model, "in": input_tokens, "out": output_tokens},
        )
    )
    return cost


def sms_segments(body: str) -> int:
    # Plain text: 160 chars per message, 153 when joined. (Emoji or accents make it shorter: this is an estimate.)
    n = len(body)
    return 1 if n <= 160 else math.ceil(n / 153)


def record_sms(db: Session, business_id: str, conversation_id: str | None, body: str, direction: str) -> None:
    seg = sms_segments(body)
    db.add(
        UsageEvent(
            business_id=business_id,
            conversation_id=conversation_id,
            kind=f"sms_{direction}",
            quantity=seg,
            cost_usd=seg * SMS_COST_PER_SEGMENT_USD,
        )
    )


def record_call_seconds(db: Session, conv: Conversation, seconds: int) -> None:
    conv.call_seconds = seconds
    db.add(
        UsageEvent(
            business_id=conv.business_id,
            conversation_id=conv.id,
            kind="call_seconds",
            quantity=seconds,
            cost_usd=seconds / 60 * VOICE_COST_PER_MINUTE_USD,
        )
    )


def ai_cost_last_24h(db: Session, business_id: str, now) -> float:
    from datetime import timedelta

    from sqlalchemy import func, select

    total = db.scalar(select(func.sum(UsageEvent.cost_usd)).where(
        UsageEvent.business_id == business_id, UsageEvent.kind == "llm", UsageEvent.created_at >= now - timedelta(hours=24)))
    return float(total or 0.0)
