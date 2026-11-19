"""The business owner's screens. Every query is limited to the logged-in owner's own business."""
from __future__ import annotations

import csv
import io
import re
from datetime import timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select

from .. import db as dbmod
from ..config import get_settings
from ..models import Appointment, Business, Conversation, Customer, Lead, Message, Notification, Service, UsageEvent
from ..security import encrypt_text, normalize_us_phone, rate_limited
from ..services import ai_keys, automations
from ..services.audit import audit
from ..services.scheduling import DAYS
from .deps import Principal, check_csrf, require_login, require_owner, templates

router = APIRouter()

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DAY_NAMES = dict(zip(DAYS, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]))


def _page(request: Request, p: Principal, db, name: str, **ctx):
    b = db.get(Business, p.business_id)
    base = {"p": p, "b": b, "csrf": p.csrf, "day_names": DAY_NAMES}
    return templates.TemplateResponse(request, name, {**base, **ctx})


def _csv_cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # stops spreadsheet formula injection


# ---------------- overview ----------------
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    p = require_login(request)
    if p.role == "admin":
        return RedirectResponse("/admin", status_code=303)
    p = require_owner(request)
    now = dbmod.utcnow()
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        today = now.astimezone(ZoneInfo(b.timezone)).date()
        report = automations.build_daily_report(db, b, today)
        week = automations.build_daily_report(db, b, today - timedelta(days=1))
        notes = db.scalars(select(Notification).where(Notification.business_id == b.id).order_by(Notification.created_at.desc()).limit(10)).all()
        upcoming = db.scalars(select(Appointment).where(Appointment.business_id == b.id, Appointment.status == "booked",
                                                        Appointment.starts_at >= now).order_by(Appointment.starts_at).limit(5)).all()
        new_leads = db.scalar(select(func.count()).select_from(Lead).where(Lead.business_id == b.id, Lead.status == "new")) or 0
        return _page(request, p, db, "home.html", today=report, yesterday=week, notes=notes, upcoming=upcoming, new_leads=new_leads,
                     setup=_setup_steps(db, b))


def _setup_steps(db, b: Business) -> list[dict]:
    """What a new client still has to do before the assistant is really ready. Shown on Today until all are done."""
    f = b.facts or {}
    has_services = bool(db.scalar(select(func.count()).select_from(Service).where(Service.business_id == b.id, Service.active.is_(True))))
    has_web_chat = bool(db.scalar(select(func.count()).select_from(Conversation).where(Conversation.business_id == b.id,
                                                                                         Conversation.channel == "web")))
    st = ai_keys.key_status(b)
    return [
        {"done": has_services, "text": "Add your services and prices", "href": "/settings#services"},
        {"done": any(b.hours.get(d) for d in DAYS) if b.hours else False, "text": "Set your opening hours", "href": "/settings#hours"},
        {"done": bool(f.get("faqs")), "text": "Answer the questions customers ask most", "href": "/settings#faq"},
        {"done": bool(b.handoff_phone), "text": "Add the phone number that gets urgent alerts", "href": "/settings#business"},
        {"done": st["has_key"], "text": "Connect your free AI account", "href": "/settings#ai",
         "note": "" if st["has_key"] else ("Using the shared test key for now." if st["shared_ok"] else "Until then, every contact is handed to you.")},
        {"done": has_web_chat, "text": "Put the chat on your website", "href": "/settings#chat",
         "note": "" if has_web_chat else "Done when the first website chat arrives."},
    ]


@router.post("/agent/toggle")
def toggle_agent(request: Request, csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        b.agent_enabled = not b.agent_enabled
        audit(db, actor=f"user:{p.user_id}", action="agent_on" if b.agent_enabled else "agent_off", business_id=b.id)
    return RedirectResponse("/", status_code=303)


# ---------------- inbox ----------------
@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request):
    p = require_owner(request)
    with dbmod.tenant_session(p.business_id) as db:
        convs = db.scalars(select(Conversation).where(Conversation.business_id == p.business_id)
                           .order_by(Conversation.last_activity_at.desc()).limit(60)).all()
        custs = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_([c.customer_id for c in convs] or [""])))}
        return _page(request, p, db, "inbox.html", convs=convs, custs=custs)


@router.get("/inbox/{conv_id}", response_class=HTMLResponse)
def conversation_page(request: Request, conv_id: str):
    p = require_owner(request)
    with dbmod.tenant_session(p.business_id) as db:
        conv = db.get(Conversation, conv_id)
        if conv is None or conv.business_id != p.business_id:  # same answer whether it exists or not
            raise HTTPException(status_code=404, detail="Not found")
        cust = db.get(Customer, conv.customer_id)
        msgs = db.scalars(select(Message).where(Message.conversation_id == conv.id, Message.business_id == p.business_id)
                          .order_by(Message.created_at, Message.id)).all()
        return _page(request, p, db, "conversation.html", conv=conv, cust=cust, msgs=msgs)


# ---------------- appointments and leads ----------------
@router.get("/appointments", response_class=HTMLResponse)
def appointments(request: Request):
    p = require_owner(request)
    now = dbmod.utcnow()
    with dbmod.tenant_session(p.business_id) as db:
        rows = db.scalars(select(Appointment).where(Appointment.business_id == p.business_id, Appointment.starts_at >= now - timedelta(days=7))
                          .order_by(Appointment.starts_at).limit(100)).all()
        custs = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_([a.customer_id for a in rows] or [""])))}
        return _page(request, p, db, "appointments.html", rows=rows, custs=custs)


@router.post("/appointments/{appt_id}/status")
def appointment_status(request: Request, appt_id: str, status: str = Form(...), csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    if status not in ("completed", "no_show", "cancelled"):
        raise HTTPException(status_code=400, detail="Bad status")
    with dbmod.tenant_session(p.business_id) as db:
        a = db.get(Appointment, appt_id)
        if a is None or a.business_id != p.business_id:
            raise HTTPException(status_code=404, detail="Not found")
        a.status = status
        if status == "no_show":
            a.review_requested_at = dbmod.utcnow()  # never ask a no-show for a review
        audit(db, actor=f"user:{p.user_id}", action=f"appointment_{status}", business_id=p.business_id, detail={"appointment_id": a.id})
    return RedirectResponse("/appointments", status_code=303)


@router.get("/leads", response_class=HTMLResponse)
def leads(request: Request):
    p = require_owner(request)
    with dbmod.tenant_session(p.business_id) as db:
        rows = db.scalars(select(Lead).where(Lead.business_id == p.business_id).order_by(Lead.created_at.desc()).limit(100)).all()
        return _page(request, p, db, "leads.html", rows=rows)


@router.post("/leads/{lead_id}/done")
def lead_done(request: Request, lead_id: str, csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    with dbmod.tenant_session(p.business_id) as db:
        lead = db.get(Lead, lead_id)
        if lead is None or lead.business_id != p.business_id:
            raise HTTPException(status_code=404, detail="Not found")
        lead.status = "done"
    return RedirectResponse("/leads", status_code=303)


# ---------------- usage ----------------
@router.get("/usage", response_class=HTMLResponse)
def usage_page(request: Request):
    p = require_owner(request)
    now = dbmod.utcnow()
    since = now - timedelta(days=30)
    with dbmod.tenant_session(p.business_id) as db:
        rows = db.execute(select(UsageEvent.kind, func.sum(UsageEvent.quantity), func.sum(UsageEvent.cost_usd))
                          .where(UsageEvent.business_id == p.business_id, UsageEvent.created_at >= since).group_by(UsageEvent.kind)).all()
        by_kind = {k: (float(q or 0), float(c or 0)) for k, q, c in rows}
        n_calls = db.scalar(select(func.count()).select_from(Conversation).where(Conversation.business_id == p.business_id, Conversation.channel == "voice", Conversation.started_at >= since)) or 0
        n_chats = db.scalar(select(func.count()).select_from(Conversation).where(Conversation.business_id == p.business_id, Conversation.channel != "voice", Conversation.started_at >= since)) or 0
        total = sum(c for _, c in by_kind.values())
        per_call = (by_kind.get("call_seconds", (0, 0))[1] + by_kind.get("llm", (0, 0))[1]) / n_calls if n_calls else 0
        per_chat = (by_kind.get("sms_out", (0, 0))[1] + by_kind.get("sms_in", (0, 0))[1] + by_kind.get("llm", (0, 0))[1]) / n_chats if n_chats else 0
        return _page(request, p, db, "usage.html", by_kind=by_kind, total=total, n_calls=n_calls, n_chats=n_chats, per_call=per_call, per_chat=per_chat)


# ---------------- settings ----------------
def _parse_faqs(text: str) -> list[dict]:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        q = re.search(r"^Q:\s*(.+)$", block, re.M)
        a = re.search(r"^A:\s*(.+)$", block, re.M | re.S)
        if q and a:
            out.append({"q": q.group(1).strip()[:300], "a": a.group(1).strip()[:800]})
    return out[:40]


def _faq_text(faqs: list[dict]) -> str:
    return "\n\n".join(f"Q: {f.get('q','')}\nA: {f.get('a','')}" for f in faqs or [])


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", error: str = "", ai_error: str = ""):
    p = require_owner(request)
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        services = db.scalars(select(Service).where(Service.business_id == b.id, Service.active.is_(True)).order_by(Service.name)).all()
        f = b.facts or {}
        s = get_settings()
        return _page(request, p, db, "settings.html", services=services, faq_text=_faq_text(f.get("faqs", [])),
                     urgent_keywords=", ".join(f.get("urgent_keywords", [])), saved=saved, error=error[:200],
                     google_ready=bool(s.google_client_id), ai=ai_keys.key_status(b), ai_error=ai_error[:200],
                     ai_providers=ai_keys.FREE_PROVIDERS, base_url=s.public_base_url.rstrip("/"),
                     demo_site=not s.is_production)


@router.post("/settings")
async def settings_save(request: Request):
    p = require_owner(request)
    form = await request.form()
    check_csrf(p, form.get("csrf_token"))  # type: ignore[arg-type]
    err = None
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        try:
            tz = str(form.get("timezone", b.timezone)).strip()
            ZoneInfo(tz)
            b.timezone = tz
        except (ZoneInfoNotFoundError, ValueError):
            err = "Unknown time zone (example: America/Chicago)."
        phone = str(form.get("handoff_phone", "")).strip()
        if phone:
            n = normalize_us_phone(phone)
            if not n:
                err = err or "The handoff phone number is not a valid US number."
            else:
                b.handoff_phone = n
        email = str(form.get("owner_email", "")).strip()[:254]
        if email and "@" in email:
            b.owner_email = email
        if form.get("weekly_report_shown"):  # only a form that shows the checkbox may change it
            b.weekly_report_enabled = bool(form.get("weekly_report"))
        hours = {}
        for d in DAYS:
            o, c = str(form.get(f"{d}_open", "")).strip(), str(form.get(f"{d}_close", "")).strip()
            if o and c:
                if not (TIME_RE.match(o) and TIME_RE.match(c) and o < c):
                    err = err or f"Check the opening hours for {DAY_NAMES[d]} (use 08:00 and 17:00 style, opening before closing)."
                else:
                    hours[d] = [[o, c]]
        if not err:
            b.hours = hours
        try:
            qs, qe = int(form.get("quiet_start", b.quiet_start)), int(form.get("quiet_end", b.quiet_end))
            rd = int(form.get("retention_days", b.retention_days))
            if not (0 <= qs <= 23 and 0 <= qe <= 23 and 7 <= rd <= 730):
                raise ValueError
            b.quiet_start, b.quiet_end, b.retention_days = qs, qe, rd
        except ValueError:
            err = err or "Quiet hours must be 0-23 and data retention 7-730 days."
        link = str(form.get("review_link", "")).strip()[:500]
        if link and not link.startswith("https://"):
            err = err or "The review link must start with https://"
        else:
            b.review_link = link or None
        facts = dict(b.facts or {})
        facts["address"] = str(form.get("address", ""))[:300]
        facts["areas"] = str(form.get("areas", ""))[:500]
        facts["policies"] = str(form.get("policies", ""))[:1500]
        facts["emergency_note"] = str(form.get("emergency_note", ""))[:500]
        facts["faqs"] = _parse_faqs(str(form.get("faqs", "")))
        facts["urgent_keywords"] = [k.strip().lower()[:40] for k in str(form.get("urgent_keywords", "")).split(",") if k.strip()][:20]
        b.facts = facts
        audit(db, actor=f"user:{p.user_id}", action="settings_changed", business_id=b.id)
    return RedirectResponse("/settings?saved=1" if not err else f"/settings?error={urlencode({'e': err})[2:]}", status_code=303)


@router.post("/settings/weekly-report/send")
def weekly_report_send_now(request: Request, csrf_token: str = Form("")):
    """'Send me a report of the last 7 days now': lets the owner (and a sales demo) see the report at once."""
    p = require_owner(request)
    check_csrf(p, csrf_token)

    def back(**kw):
        return RedirectResponse("/settings?" + urlencode(kw) + "#business", status_code=303)

    if rate_limited(f"weekly-now:{p.business_id}", limit=3, per_seconds=3600):
        return back(error="Too many reports in one hour. Please try again later.")
    now = dbmod.utcnow()
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        if not b.owner_email:
            return back(error="Add an email for reports first, then save.")
        first_day = now.astimezone(ZoneInfo(b.timezone)).date() - timedelta(days=6)
        report = automations.build_period_report(db, b, first_day, title="the last 7 days", before_label="the 7 days before")
        if not report["customers"]:
            return back(error="No customers in the last 7 days, so there is nothing to report yet.")
        note = automations.notify_owner(db, b, report["text"], kind="weekly_report", now=now,
                                        subject=f"{b.name}: the last 7 days with the AI front desk")
        audit(db, actor=f"user:{p.user_id}", action="weekly_report_sent_now", business_id=b.id)
        emailed = "email" in (note.delivered_via or "")
    return back(saved="report" if emailed else "report-stored")


@router.post("/settings/services")
def service_add(request: Request, name: str = Form(...), price_text: str = Form(""), duration_min: int = Form(60),
                description: str = Form(""), csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    name = name.strip()[:200]
    if not name or not (10 <= duration_min <= 600):
        return RedirectResponse("/settings?error=Service+needs+a+name+and+a+duration+of+10-600+minutes", status_code=303)
    with dbmod.tenant_session(p.business_id) as db:
        db.add(Service(business_id=p.business_id, name=name, price_text=price_text.strip()[:100] or None,
                       duration_min=duration_min, description=description.strip()[:500] or None))
        audit(db, actor=f"user:{p.user_id}", action="service_added", business_id=p.business_id)
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/services/{service_id}/delete")
def service_delete(request: Request, service_id: str, csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    with dbmod.tenant_session(p.business_id) as db:
        s = db.get(Service, service_id)
        if s is None or s.business_id != p.business_id:
            raise HTTPException(status_code=404, detail="Not found")
        s.active = False
        audit(db, actor=f"user:{p.user_id}", action="service_removed", business_id=p.business_id)
    return RedirectResponse("/settings?saved=1", status_code=303)


# ---------------- exports ----------------
@router.get("/export/appointments.csv")
def export_appointments(request: Request):
    p = require_owner(request)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["starts_at_utc", "ends_at_utc", "service", "status", "customer_name", "customer_phone"])
    with dbmod.tenant_session(p.business_id) as db:
        rows = db.execute(select(Appointment, Customer).join(Customer, Customer.id == Appointment.customer_id)
                          .where(Appointment.business_id == p.business_id).order_by(Appointment.starts_at)).all()
        for a, c in rows:
            w.writerow([_csv_cell(x) for x in (a.starts_at.isoformat(), a.ends_at.isoformat(), a.service_name, a.status, c.name, c.phone)])
        audit(db, actor=f"user:{p.user_id}", action="export_appointments", business_id=p.business_id)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=appointments.csv"})


@router.get("/export/leads.csv")
def export_leads(request: Request):
    p = require_owner(request)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["created_at_utc", "name", "phone", "need", "preferred_time", "reason", "status"])
    with dbmod.tenant_session(p.business_id) as db:
        for l in db.scalars(select(Lead).where(Lead.business_id == p.business_id).order_by(Lead.created_at)):
            w.writerow([_csv_cell(x) for x in (l.created_at.isoformat(), l.name, l.phone, l.need, l.preferred_time, l.reason, l.status)])
        audit(db, actor=f"user:{p.user_id}", action="export_leads", business_id=p.business_id)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=leads.csv"})


# ---------------- Google Calendar connection ----------------
def _state() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="google-oauth-state")


def google_exchange_code(code: str, redirect_uri: str) -> dict:
    s = get_settings()
    r = httpx.post("https://oauth2.googleapis.com/token", timeout=10, data={
        "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    r.raise_for_status()
    return r.json()


@router.get("/settings/calendar/connect")
def calendar_connect(request: Request):
    p = require_owner(request)
    s = get_settings()
    if not s.google_client_id:
        raise HTTPException(status_code=503, detail="Google Calendar is not set up on this server yet.")
    state = _state().dumps({"b": p.business_id, "u": p.user_id})
    q = urlencode({
        "client_id": s.google_client_id, "redirect_uri": s.public_base_url.rstrip("/") + "/oauth/google/callback",
        "response_type": "code", "access_type": "offline", "prompt": "consent", "state": state,
        "scope": "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.freebusy"})
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + q, status_code=303)


@router.get("/oauth/google/callback")
def calendar_callback(request: Request, code: str = "", state: str = ""):
    p = require_owner(request)
    try:
        data = _state().loads(state, max_age=600)
    except BadSignature:
        raise HTTPException(status_code=400, detail="This connection link expired. Start again from Settings.")
    if data.get("b") != p.business_id or data.get("u") != p.user_id:
        raise HTTPException(status_code=403, detail="Wrong account")
    s = get_settings()
    try:
        tok = google_exchange_code(code, s.public_base_url.rstrip("/") + "/oauth/google/callback")
    except Exception:
        return RedirectResponse("/settings?error=Google+did+not+accept+the+connection", status_code=303)
    refresh = tok.get("refresh_token")
    if not refresh:
        return RedirectResponse("/settings?error=Google+did+not+give+long-term+access.+Try+again", status_code=303)
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        b.google_refresh_token_enc = encrypt_text(refresh)
        b.google_calendar_id = "primary"
        b.calendar_kind = "google"
        audit(db, actor=f"user:{p.user_id}", action="calendar_connected", business_id=b.id)
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/calendar/disconnect")
def calendar_disconnect(request: Request, csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        b.google_refresh_token_enc, b.google_calendar_id, b.calendar_kind = None, None, "internal"
        audit(db, actor=f"user:{p.user_id}", action="calendar_disconnected", business_id=b.id)
    return RedirectResponse("/settings?saved=1", status_code=303)


# ---------------- the client's own AI account ----------------
@router.post("/settings/ai")
def ai_key_save(request: Request, provider: str = Form(""), api_key: str = Form(""), model: str = Form(""),
                csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        try:
            ai_keys.set_key(b, provider, api_key, model)
        except ai_keys.KeyError_ as exc:
            return RedirectResponse("/settings?" + urlencode({"ai_error": str(exc)}) + "#ai", status_code=303)
        audit(db, actor=f"user:{p.user_id}", action="ai_key_set", business_id=b.id, detail={"provider": b.ai_provider})
    return RedirectResponse("/settings?saved=ai#ai", status_code=303)


@router.post("/settings/ai/backup")
def ai_backup_key_save(request: Request, provider: str = Form(""), api_key: str = Form(""), model: str = Form(""),
                       csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        try:
            ai_keys.set_backup_key(b, provider, api_key, model)
        except ai_keys.KeyError_ as exc:
            return RedirectResponse("/settings?" + urlencode({"ai_error": str(exc)}) + "#ai", status_code=303)
        audit(db, actor=f"user:{p.user_id}", action="ai_backup_key_set", business_id=b.id, detail={"provider": b.ai_backup_provider})
    return RedirectResponse("/settings?saved=ai-backup#ai", status_code=303)


@router.post("/settings/ai/backup/remove")
def ai_backup_key_remove(request: Request, csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        ai_keys.clear_backup_key(b)
        audit(db, actor=f"user:{p.user_id}", action="ai_backup_key_removed", business_id=b.id)
    return RedirectResponse("/settings?saved=ai-backup-removed#ai", status_code=303)


@router.post("/settings/ai/remove")
def ai_key_remove(request: Request, csrf_token: str = Form("")):
    p = require_owner(request)
    check_csrf(p, csrf_token)
    with dbmod.tenant_session(p.business_id) as db:
        b = db.get(Business, p.business_id)
        ai_keys.clear_key(b)
        audit(db, actor=f"user:{p.user_id}", action="ai_key_removed", business_id=b.id)
    return RedirectResponse("/settings?saved=ai-removed#ai", status_code=303)
