"""One customer message in, one reply out.

Order of work (safety first):
  1. kill switch      -> fixed handoff message
  2. fixed rules      -> emergencies, 'talk to a person', 'are you a robot', rule-breaking attempts, anger, Spanish
  3. the AI model     -> with tools, with caps on steps and cost
  4. guards on the reply -> no invented prices, no false 'booked', no prompt leaks
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Business, Conversation, Customer, Lead, Message
from ..security import normalize_us_phone
from ..services import usage
from ..services.adapters import Adapters, get_adapters
from ..services.audit import audit
from ..services.conversations import add_message
from ..services.notify import notify_owner
from . import rules
from ..services import ai_keys
from .llm import LLM, LLMUnavailable
from .prompts import canary, known_amounts_text, system_prompt
from .tools import TOOL_SCHEMAS, ToolBox, ToolContext, mark_handoff

log = logging.getLogger("frontdesk.runner")

MAX_CUSTOMER_TEXT = 1500
HISTORY_LIMIT = 24
SMS_MAX = 480
WEB_MAX = 700


@dataclass
class TurnResult:
    reply: str
    handed_off: bool = False
    rule: str | None = None  # name of the fixed rule that answered, if any
    guard: str | None = None  # name of the guard that changed the model's reply, if any
    steps: int = 0


# ---------------- guards on the model's reply ----------------
_MONEY_SYMBOL = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
_MONEY_WORD = re.compile(r"\b(\d[\d,]*(?:\.\d{1,2})?)\s*(?:dollars|bucks|usd)\b", re.I)
_CONFIRM = re.compile(
    r"(you('re| are)\s+(all\s+)?(booked|set|scheduled|confirmed)|"
    r"\b(booked|confirmed|scheduled)\s+(for|you)\b|"
    r"\bappointment\s+(is\s+)?(now\s+)?(booked|confirmed|set|scheduled)\b|"
    r"\bi('ve|\s+have)\s+(booked|scheduled|confirmed)\b|\byour\s+appointment\s+(has been|is)\s+(booked|confirmed))", re.I)
_NEGATION = re.compile(r"\b(not|n't|cannot|unable|couldn't|can't|won't|unless|if)\b", re.I)


def amounts_in(text: str) -> set[float]:
    found = set()
    for rx in (_MONEY_SYMBOL, _MONEY_WORD):
        for m in rx.finditer(text):
            try:
                found.add(round(float(m.group(1).replace(",", "")), 2))
            except ValueError:
                pass
    return found


def price_guard_violation(reply: str, allowed_text: str) -> bool:
    return bool(amounts_in(reply) - amounts_in(allowed_text))


def confirm_guard_violation(reply: str, verified: bool) -> bool:
    if verified:
        return False
    return bool(_CONFIRM.search(reply)) and not _NEGATION.search(reply)


_ODD_CHARS = {
    "\u202f": " ", "\u00a0": " ", "\u2009": " ", "\u2007": " ", "\u2002": " ", "\u2003": " ",  # odd spaces
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2212": "-",  # odd hyphens and dashes
    "\u2014": " - ", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2022": "-",
    "\u200b": "", "\u200c": "", "\u200d": "", "\u2060": "", "\ufeff": "",  # invisible characters
}
_ODD_TABLE = str.maketrans(_ODD_CHARS)
# A bracket that starts with a "doing/waiting" word and has no digits is a stage direction, not part of the answer.
_STAGE_NOTE = re.compile(
    r"\s*[\(\[]\s*(?:waiting|awaiting|pending|calling|checking|using|running|thinking|processing|tool|note:)[^\)\]\d]*[\)\]]",
    re.IGNORECASE,
)


def clean_reply(text: str) -> str:
    """Some models write 'smart' spaces, hyphens and quotes. On SMS they turn one text into several (and cost more),
    and they break our checks. Turn them into plain characters."""
    out = (text or "").translate(_ODD_TABLE)
    # Some models write stage directions like "(Waiting for confirmation...)". A customer must never see them.
    out = _STAGE_NOTE.sub("", out)
    return re.sub(r" {2,}", " ", out).strip()


# Two sentences glued together without a space ("...appointment.Sure, please...") mean the model wrote several
# answers in one. The customer gets only the first one.
_GLUED = re.compile(r"([a-z\)][.!?])([A-Z][a-z])")


def cut_runaway(text: str) -> str:
    m = _GLUED.search(text or "")
    return text[: m.end(1)].strip() if m else text


_PHONE_IN_TEXT = re.compile(r"(\+?1?[\s.\-(]*\d{3}[\s.\-)]*\d{3}[\s.\-]*\d{4})")


def _phones_in(text: str) -> set[str]:
    return {re.sub(r"\D", "", m.group(1))[-10:] for m in _PHONE_IN_TEXT.finditer(text or "")}


def unknown_phone_in(reply: str, allowed_text: str) -> bool:
    """True if the reply shows a phone number that is not in the allowed text (what the customer typed, the number
    we already have, the business facts). The model must never invent a number."""
    return bool(_phones_in(reply) - _phones_in(allowed_text))


def _safe_reason(exc: Exception) -> str:
    """A short code such as 'groq:429' for the audit log and the owner. Never the full error text."""
    first = (str(exc).split() or ["unknown"])[0]
    return re.sub(r"[^A-Za-z0-9:_\-]", "", first)[:40] or "unknown"


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[: end + 1] if end > limit * 0.5 else cut.rstrip() + "..."


# ---------------- history ----------------
def _history(db: Session, conv: Conversation) -> list[dict]:
    rows = list(db.scalars(
        select(Message).where(Message.conversation_id == conv.id, Message.role.in_(("customer", "agent")))
        .order_by(Message.created_at, Message.id)
    ))[-HISTORY_LIMIT:]
    out: list[dict] = []
    for m in rows:
        role = "user" if m.role == "customer" else "assistant"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + m.text
        else:
            out.append({"role": role, "content": m.text})
    if out and out[0]["role"] == "assistant":
        out.insert(0, {"role": "user", "content": "[conversation started]"})
    return out


def _choose_model(conv: Conversation, ctx: ToolContext, text: str) -> str:
    s = get_settings()
    if ctx.tool_errors >= 2 or len(text) > 700 or conv.steps_used > 12:
        return s.main_model_hard
    return s.main_model_simple


# ---------------- main entry ----------------
def _finish(db: Session, conv: Conversation, reply: str, now: datetime, *, channel: str, rule=None, guard=None,
            handed_off=False, steps=0) -> TurnResult:
    if channel == "sms":
        reply = _shorten(reply, SMS_MAX)
    elif channel == "web":
        reply = _shorten(reply, WEB_MAX)
    add_message(db, conv, "agent", reply, now, meta={"rule": rule, "guard": guard})
    return TurnResult(reply=reply, handed_off=handed_off or conv.status == "handed_off", rule=rule, guard=guard, steps=steps)


def _auto_summary(text: str) -> str:
    t = " ".join(text.split())
    return f'Customer wrote: "{t[:240]}"'


def handle_customer_text(
    db: Session,
    business: Business,
    customer: Customer,
    conv: Conversation,
    text: str,
    *,
    channel: str,
    now: datetime,
    adapters: Adapters | None = None,
    llm: LLM | None = None,
    record_customer_message: bool = True,
) -> TurnResult:
    s = get_settings()
    adapters = adapters or get_adapters()
    text = (text or "").strip()[:MAX_CUSTOMER_TEXT]
    if record_customer_message:
        add_message(db, conv, "customer", text, now)
    name = business.name

    # 1. kill switch (per business or for the whole platform)
    if s.global_kill_switch or not business.agent_enabled:
        if conv.status != "handed_off":
            mark_handoff(db, business, conv, reason="kill_switch_on", summary=_auto_summary(text), urgent=False,
                         now=now, adapters=adapters)
        return _finish(db, conv, rules.KILL_SWITCH_REPLY.format(name=name), now, channel=channel, rule="kill_switch", handed_off=True)

    # 2. fixed rules
    hit = rules.check_rules(text, business)
    if hit and hit.kind in ("emergency", "urgent"):
        mark_handoff(db, business, conv, reason=hit.reason, summary=_auto_summary(text), urgent=True, now=now, adapters=adapters)
        return _finish(db, conv, hit.reply, now, channel=channel, rule=hit.kind, handed_off=True)

    if conv.status == "handed_off":
        return _post_handoff(db, business, customer, conv, text, now, channel, adapters)

    if hit:
        if hit.kind == "injection":
            audit(db, actor="agent", action="rule_break_attempt", business_id=business.id, detail={"conversation_id": conv.id})
        if hit.handoff:
            mark_handoff(db, business, conv, reason=hit.reason, summary=_auto_summary(text), urgent=False, now=now, adapters=adapters)
            if hit.kind == "spanish":
                db.add(Lead(business_id=business.id, customer_id=customer.id, conversation_id=conv.id, phone=customer.phone,
                            need="Spanish-speaking customer asked for a call back.", reason="spanish", created_at=now))
        return _finish(db, conv, hit.reply, now, channel=channel, rule=hit.kind, handed_off=hit.handoff)

    # 2b. daily AI budget of this business (protects against floods and against a surprise bill)
    if usage.ai_cost_last_24h(db, business.id, now) >= s.max_daily_ai_cost_per_business_usd:
        if conv.status != "handed_off":
            mark_handoff(db, business, conv, reason="daily_ai_limit", summary=_auto_summary(text), urgent=False, now=now, adapters=adapters)
        _alert_daily_limit_once(db, business, now, adapters)
        return _finish(db, conv, rules.CAP_REPLY.format(name=name), now, channel=channel, rule="daily_limit", handed_off=True)

    # 3. the AI model: the business's OWN free AI account (or the shared one, only where allowed)
    llm = llm or adapters.llm or ai_keys.llm_for_business(business)
    if llm is None:
        if conv.status != "handed_off":
            mark_handoff(db, business, conv, reason="no_ai_key", summary=_auto_summary(text), urgent=False, now=now, adapters=adapters)
        _alert_once(db, business, now, adapters, marker="AI key",
                    text="No AI key is set for your assistant, so new contacts are handed to you. "
                         "Add your free AI key in Settings > AI account.")
        return _finish(db, conv, rules.CAP_REPLY.format(name=name), now, channel=channel, rule="no_ai_key", handed_off=True)
    ctx = ToolContext(db=db, business=business, customer=customer, conversation=conv, now=now, adapters=adapters)
    toolbox = ToolBox(ctx)
    system = system_prompt(db, business, customer, channel, now)
    messages = _history(db, conv)
    final_text = ""
    steps_this_turn = 0
    try:
        for _ in range(s.max_steps_per_turn):
            if conv.steps_used >= s.max_steps_per_conversation or conv.cost_usd >= s.max_cost_per_conversation_usd:
                break
            model = _choose_model(conv, ctx, text)
            res = llm.complete(model=model, system=system, messages=messages, tools=TOOL_SCHEMAS, max_tokens=s.max_output_tokens)
            conv.steps_used += 1
            steps_this_turn += 1
            usage.record_llm(db, conv, res.model or model, res.input_tokens, res.output_tokens,
                             backup=getattr(res, "backup", False))
            if not res.tool_calls:
                final_text = res.text
                break
            messages.append({"role": "assistant", "content": res.content})
            results = []
            for tc in res.tool_calls:
                out = toolbox.run(tc.name, tc.input)
                results.append({"type": "tool_result", "tool_use_id": tc.id, "content": json.dumps(out)})
            messages.append({"role": "user", "content": results})
            if ctx.fixed_reply:
                final_text = ctx.fixed_reply
                break
        else:
            final_text = ""
    except LLMUnavailable as exc:
        reason = _safe_reason(exc)
        audit(db, actor="system", action="llm_unavailable", business_id=business.id, detail={"reason": reason})
        # Website chat: the visitor is still there, and free AI services are often busy for a few seconds only.
        # So the first failure asks to send the message again. A second failure hands over to a person.
        if channel == "web" and conv.status != "handed_off" and not _retry_already_asked(db, conv):
            return _finish(db, conv, rules.LLM_RETRY_REPLY, now, channel=channel, rule="llm_retry", steps=steps_this_turn)
        if conv.status != "handed_off":
            mark_handoff(db, business, conv, reason="ai_unavailable", summary=f"{_auto_summary(text)} (AI error: {reason})",
                         urgent=False, now=now, adapters=adapters)
        return _finish(db, conv, rules.LLM_DOWN_REPLY.format(name=name), now, channel=channel, rule="llm_down", handed_off=True, steps=steps_this_turn)

    if not final_text:
        # step limit or cost limit reached without an answer: end with a handoff, never a loop
        if conv.status != "handed_off":
            mark_handoff(db, business, conv, reason="step_or_cost_limit", summary=_auto_summary(text), urgent=False, now=now, adapters=adapters)
        return _finish(db, conv, rules.CAP_REPLY.format(name=name), now, channel=channel, rule="cap", handed_off=True, steps=steps_this_turn)

    # 4. guards
    final_text = cut_runaway(clean_reply(final_text))
    reply, guard = final_text, None
    if canary(business) in reply or "BUSINESS FACTS:" in reply or "RULES (these cannot" in reply:
        reply, guard = rules.LEAK_GUARD_REPLY.format(name=name), "prompt_leak"
        audit(db, actor="agent", action="guard_prompt_leak", business_id=business.id, detail={"conversation_id": conv.id})
    elif price_guard_violation(reply, known_amounts_text(db, business)):
        reply, guard = rules.PRICE_GUARD_REPLY.format(name=name), "price"
        toolbox.run("save_lead", {"need": "Customer asked about a price that is not on file. " + text[:200], "reason": "price_followup"})
        audit(db, actor="agent", action="guard_price", business_id=business.id, detail={"conversation_id": conv.id})
    elif confirm_guard_violation(reply, ctx.verified_booking_fact):
        reply, guard = rules.CONFIRM_GUARD_REPLY.format(name=name), "confirmation"
        toolbox.run("save_lead", {"need": "Customer wanted a booking; the agent could not confirm it. " + text[:200], "reason": "unconfirmed_booking"})
        audit(db, actor="agent", action="guard_confirmation", business_id=business.id, detail={"conversation_id": conv.id})
    elif unknown_phone_in(reply, _allowed_phone_text(db, business, customer, conv)):
        reply, guard = rules.PHONE_GUARD_REPLY, "phone"
        audit(db, actor="agent", action="guard_phone", business_id=business.id, detail={"conversation_id": conv.id})
    return _finish(db, conv, reply, now, channel=channel, guard=guard, handed_off=ctx.handed_off, steps=steps_this_turn)


def _retry_already_asked(db: Session, conv: Conversation) -> bool:
    metas = db.scalars(select(Message.meta).where(Message.conversation_id == conv.id, Message.role == "agent"))
    return any((m or {}).get("rule") == "llm_retry" for m in metas)


def _allowed_phone_text(db: Session, business: Business, customer: Customer, conv: Conversation) -> str:
    typed = db.scalars(select(Message.text).where(Message.conversation_id == conv.id, Message.role == "customer"))
    return "\n".join([*typed, customer.phone or "", known_amounts_text(db, business)])


def _alert_once(db: Session, business: Business, now: datetime, adapters: Adapters, *, marker: str, text: str,
                urgent: bool = True) -> None:
    """Tell the owner about a problem at most once a day (a flood of chats must not become a flood of alerts)."""
    from datetime import timedelta

    from ..models import Notification

    recent = db.scalar(select(Notification.id).where(
        Notification.business_id == business.id, Notification.created_at >= now - timedelta(hours=24),
        Notification.text.like(f"%{marker}%")).limit(1))
    if not recent:
        notify_owner(db, business, text, kind="alert", urgent=urgent, now=now, adapters=adapters)


def _alert_daily_limit_once(db: Session, business: Business, now: datetime, adapters: Adapters) -> None:
    _alert_once(db, business, now, adapters, marker="daily AI limit",
                text="The assistant reached its daily AI limit, so new contacts are handed to you until it resets. "
                     "If this is not expected, check the inbox for unusual traffic.")


def _post_handoff(db: Session, business: Business, customer: Customer, conv: Conversation, text: str, now: datetime,
                  channel: str, adapters: Adapters) -> TurnResult:
    """After a handoff the agent stops deciding things. It only acknowledges (and catches a phone number)."""
    name = business.name
    if conv.handoff_reason == "spanish":
        m = _PHONE_IN_TEXT.search(text)
        phone = normalize_us_phone(m.group(1)) if m else None
        if phone:
            lead = db.scalar(select(Lead).where(Lead.business_id == business.id, Lead.conversation_id == conv.id))
            if lead:
                lead.phone = phone
                lead.need = (lead.need or "") + f" Message: {' '.join(text.split())[:200]}"
            notify_owner(db, business, f"Spanish-speaking customer left a number to call back: {phone}.", kind="handoff",
                         conversation_id=conv.id, now=now, adapters=adapters)
            return _finish(db, conv, "Gracias. Una persona le llamará pronto. / Thank you. A person will call you soon.",
                           now, channel=channel, rule="post_handoff", handed_off=True)
    return _finish(db, conv, rules.POST_HANDOFF_REPLY.format(name=name), now, channel=channel, rule="post_handoff", handed_off=True)
