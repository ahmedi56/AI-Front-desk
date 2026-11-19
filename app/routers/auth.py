from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db as dbmod
from ..config import get_settings
from ..models import User
from ..security import SESSION_MAX_AGE, decrypt_text, make_session_cookie, rate_limited, totp_verify, verify_password
from ..services.audit import audit
from sqlalchemy import select
from .deps import COOKIE, check_csrf, get_principal, require_login, templates

router = APIRouter()

MAX_FAILED = 5
LOCK_FOR = timedelta(minutes=15)
GENERIC_ERROR = "Wrong email or password, or the account is temporarily locked."


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_principal(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), code: str = Form("")):
    ip = request.client.host if request.client else "unknown"
    email = email.strip().lower()[:254]
    if rate_limited(f"login-ip:{ip}", limit=20, per_seconds=600) or rate_limited(f"login-email:{email}", limit=8, per_seconds=600):
        return templates.TemplateResponse(request, "login.html", {"error": "Too many tries. Wait 10 minutes."}, status_code=429)
    now = dbmod.utcnow()
    with dbmod.system_session() as s:
        u = s.scalar(select(User).where(User.email == email))
        locked = bool(u and u.locked_until and u.locked_until > now)
        ok = verify_password(password, u.password_hash if u else None)  # always runs, same time if the email is unknown
        step = None
        if ok and u and u.totp_enabled and u.totp_secret_enc:
            secret = decrypt_text(u.totp_secret_enc)
            step = totp_verify(secret, code, last_step=u.totp_last_step) if secret else None
            ok = step is not None
        if not u or locked or not ok:
            if u and not locked:
                u.failed_logins += 1
                if u.failed_logins >= MAX_FAILED:
                    u.locked_until, u.failed_logins = now + LOCK_FOR, 0
                audit(s, actor="webhook", action="login_failed", business_id=u.business_id, detail={"user": u.id})
            return templates.TemplateResponse(request, "login.html", {"error": GENERIC_ERROR}, status_code=401)
        u.failed_logins, u.locked_until = 0, None
        if step:
            u.totp_last_step = step
        cookie, _ = make_session_cookie(u.id, u.session_version)
        audit(s, actor=f"user:{u.id}", action="login", business_id=u.business_id)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, cookie, max_age=SESSION_MAX_AGE, httponly=True, secure=get_settings().cookie_secure, samesite="lax", path="/")
    return resp


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    p = require_login(request)
    check_csrf(p, csrf_token)
    with dbmod.system_session() as s:
        u = s.get(User, p.user_id)
        u.session_version += 1  # the old cookie stops working on the server, not only in the browser
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.post("/logout-everywhere")
def logout_everywhere(request: Request, csrf_token: str = Form("")):
    p = require_login(request)
    check_csrf(p, csrf_token)
    with dbmod.system_session() as s:
        u = s.get(User, p.user_id)
        u.session_version += 1  # every old cookie stops working
        audit(s, actor=f"user:{u.id}", action="logout_everywhere", business_id=u.business_id)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp
