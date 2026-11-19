"""Platform admin: you. Add clients, switch on texting after the carrier approves it, see costs."""
from __future__ import annotations

import re
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from .. import db as dbmod
from ..config import get_settings
from ..models import Business, Conversation, Prospect, UsageEvent, User
from ..security import hash_password, normalize_us_phone
from ..services import ai_keys
from ..services.audit import audit
from .deps import check_csrf, require_admin, templates

router = APIRouter(prefix="/admin")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def _render(request: Request, p, *, created: str = "", temp: str = "", error: str = ""):
    since = dbmod.utcnow() - timedelta(days=30)
    with dbmod.system_session() as s:
        businesses = s.scalars(select(Business).order_by(Business.name)).all()
        cost_rows = s.execute(select(UsageEvent.business_id, func.sum(UsageEvent.cost_usd)).where(UsageEvent.created_at >= since)
                              .group_by(UsageEvent.business_id)).all()
        cost = {bid: float(c or 0) for bid, c in cost_rows}
        convs = dict(s.execute(select(Conversation.business_id, func.count()).where(Conversation.started_at >= since)
                               .group_by(Conversation.business_id)).all())
        prospects = s.scalars(select(Prospect).order_by(Prospect.created_at.desc()).limit(50)).all()
        resp = templates.TemplateResponse(request, "admin.html", {
            "p": p, "csrf": p.csrf, "businesses": businesses, "cost": cost, "convs": convs,
            "created": created, "temp": temp, "prospects": prospects, "error": error[:200], "ai_providers": ai_keys.FREE_PROVIDERS,
            "ai": {b.id: ai_keys.key_status(b) for b in businesses}, "demo_site": not get_settings().is_production})
        if temp:
            resp.headers["Cache-Control"] = "no-store"
        return resp


@router.get("", response_class=HTMLResponse)
def admin_home(request: Request, error: str = ""):
    p = require_admin(request)
    return _render(request, p, error=error)


@router.post("/businesses")
def create_business(request: Request, name: str = Form(...), slug: str = Form(...), owner_email: str = Form(...),
                    timezone: str = Form("America/Chicago"), handoff_phone: str = Form(""), voice_number: str = Form(""),
                    csrf_token: str = Form("")):
    p = require_admin(request)
    check_csrf(p, csrf_token)
    slug = slug.strip().lower()
    owner_email = owner_email.strip().lower()
    if not SLUG_RE.match(slug) or "@" not in owner_email:
        return RedirectResponse("/admin?error=Check+the+short+name+and+the+email", status_code=303)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return RedirectResponse("/admin?error=Unknown+time+zone", status_code=303)
    handoff = normalize_us_phone(handoff_phone) if handoff_phone.strip() else None
    voice = normalize_us_phone(voice_number) if voice_number.strip() else None
    temp = secrets.token_urlsafe(14)  # shown once; the owner must change it (they can ask you for a new one)
    with dbmod.system_session() as s:
        if s.scalar(select(Business.id).where(Business.slug == slug)) or s.scalar(select(User.id).where(User.email == owner_email)):
            return RedirectResponse("/admin?error=That+short+name+or+email+already+exists", status_code=303)
        b = Business(name=name.strip()[:200], slug=slug, timezone=timezone, handoff_phone=handoff, owner_email=owner_email,
                     inbound_number=voice, hours={d: [["08:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}, facts={})
        s.add(b)
        s.flush()
        s.add(User(business_id=b.id, email=owner_email, password_hash=hash_password(temp), role="owner", must_change_password=True))
        audit(s, actor=f"user:{p.user_id}", action="business_created", business_id=b.id, detail={"slug": slug})
    return _render(request, p, created=slug, temp=temp)  # shown once, in the answer to this form, never in the address bar


@router.post("/prospects/{prospect_id}/contacted")
def prospect_contacted(request: Request, prospect_id: str, csrf_token: str = Form("")):
    p = require_admin(request)
    check_csrf(p, csrf_token)
    with dbmod.system_session() as s:
        row = s.get(Prospect, prospect_id)
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        row.status = "contacted"
        audit(s, actor=f"user:{p.user_id}", action="prospect_contacted", detail={"prospect_id": row.id})
    return RedirectResponse("/admin#prospects", status_code=303)


@router.post("/businesses/{business_id}/numbers")
def set_numbers(request: Request, business_id: str, voice_number: str = Form(""), sms_number: str = Form(""),
                sms_enabled: str = Form(""), csrf_token: str = Form("")):
    p = require_admin(request)
    check_csrf(p, csrf_token)
    with dbmod.system_session() as s:
        b = s.get(Business, business_id)
        if not b:
            raise HTTPException(status_code=404, detail="Not found")
        b.inbound_number = normalize_us_phone(voice_number) if voice_number.strip() else None
        b.sms_number = normalize_us_phone(sms_number) if sms_number.strip() else None
        # Texting may only be switched on once the carrier registration (A2P 10DLC) for THIS business is approved
        b.sms_enabled = bool(sms_enabled) and bool(b.sms_number)
        audit(s, actor=f"user:{p.user_id}", action="numbers_changed", business_id=b.id, detail={"sms_enabled": b.sms_enabled})
    return RedirectResponse("/admin", status_code=303)


@router.post("/businesses/{business_id}/reset-password")
def reset_password(request: Request, business_id: str, csrf_token: str = Form("")):
    p = require_admin(request)
    check_csrf(p, csrf_token)
    temp = secrets.token_urlsafe(14)
    with dbmod.system_session() as s:
        u = s.scalar(select(User).where(User.business_id == business_id, User.role == "owner"))
        if not u:
            raise HTTPException(status_code=404, detail="Not found")
        u.password_hash = hash_password(temp)
        u.must_change_password = True
        u.totp_enabled, u.totp_secret_enc = False, None  # lost phone: they set up 2-step login again
        u.session_version += 1
        audit(s, actor=f"user:{p.user_id}", action="owner_password_reset", business_id=business_id)
        slug = s.get(Business, business_id).slug
    return _render(request, p, created=slug, temp=temp)


@router.post("/businesses/{business_id}/agent")
def admin_toggle_agent(request: Request, business_id: str, csrf_token: str = Form("")):
    p = require_admin(request)
    check_csrf(p, csrf_token)
    with dbmod.system_session() as s:
        b = s.get(Business, business_id)
        if not b:
            raise HTTPException(status_code=404, detail="Not found")
        b.agent_enabled = not b.agent_enabled
        audit(s, actor=f"user:{p.user_id}", action="agent_on" if b.agent_enabled else "agent_off", business_id=b.id)
    return RedirectResponse("/admin", status_code=303)


@router.post("/businesses/{business_id}/ai")
def admin_set_ai_key(request: Request, business_id: str, provider: str = Form(""), api_key: str = Form(""),
                     model: str = Form(""), csrf_token: str = Form("")):
    """You enter the client's own free AI key during setup (they can also do it in their Settings)."""
    p = require_admin(request)
    check_csrf(p, csrf_token)
    problem = ""
    with dbmod.system_session() as s:
        b = s.get(Business, business_id)
        if not b:
            raise HTTPException(status_code=404, detail="Not found")
        try:
            ai_keys.set_key(b, provider, api_key, model)
            audit(s, actor=f"user:{p.user_id}", action="ai_key_set", business_id=b.id, detail={"provider": b.ai_provider})
        except ai_keys.KeyError_ as exc:
            problem = str(exc)
    if problem:
        return _render(request, p, error=problem)
    return RedirectResponse("/admin", status_code=303)
