"""My account: change password, turn on 2-step login (authenticator app)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db as dbmod
from ..config import get_settings
from ..models import Business, User
from ..security import (SESSION_MAX_AGE, decrypt_text, encrypt_text, hash_password, make_session_cookie, rate_limited,
                        totp_new_secret, totp_uri, totp_verify, verify_password)
from ..services.audit import audit
from .deps import COOKIE, check_csrf, require_login, templates

router = APIRouter()


def _render(request: Request, p, *, message: str = "", error: str = "", pending_secret: str | None = None, uri: str | None = None):
    with dbmod.system_session() as s:
        u = s.get(User, p.user_id)
        b = s.get(Business, p.business_id) if p.business_id else None
        return templates.TemplateResponse(request, "account.html", {
            "p": p, "b": b, "csrf": p.csrf, "message": message, "error": error, "u": u,
            "pending_secret": pending_secret, "uri": uri,
            "must_2fa": p.role == "admin" and get_settings().admin_2fa_required and not u.totp_enabled,
        })


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, done: str = ""):
    p = require_login(request)
    pending, uri = None, None
    with dbmod.system_session() as s:
        u = s.get(User, p.user_id)
        if u.totp_secret_enc and not u.totp_enabled:
            pending = decrypt_text(u.totp_secret_enc)
            uri = totp_uri(pending, u.email) if pending else None
    message = "Password changed. Any other device you were logged in on has been logged out." if done == "1" else ""
    return _render(request, p, pending_secret=pending, uri=uri, message=message)


def _reissue(resp, user_id: str, version: int):
    cookie, _ = make_session_cookie(user_id, version)
    resp.set_cookie(COOKIE, cookie, max_age=SESSION_MAX_AGE, httponly=True, secure=get_settings().cookie_secure, samesite="lax", path="/")


@router.post("/account/password")
def change_password(request: Request, current_password: str = Form(...), new_password: str = Form(...),
                    confirm_password: str = Form(...), csrf_token: str = Form("")):
    p = require_login(request)
    check_csrf(p, csrf_token)
    if rate_limited(f"pw-change:{p.user_id}", limit=5, per_seconds=600):
        return _render(request, p, error="Too many tries. Wait 10 minutes.")
    if new_password != confirm_password:
        return _render(request, p, error="The two new passwords are different.")
    if new_password == current_password:
        return _render(request, p, error="Choose a password you have not used here before.")
    with dbmod.system_session() as s:
        u = s.get(User, p.user_id)
        if not verify_password(current_password, u.password_hash):
            return _render(request, p, error="The current password is wrong.")
        try:
            u.password_hash = hash_password(new_password)
        except ValueError:
            return _render(request, p, error="The new password must have at least 12 characters.")
        u.must_change_password = False
        u.session_version += 1  # every other device is logged out
        version = u.session_version
        audit(s, actor=f"user:{u.id}", action="password_changed", business_id=u.business_id)
    resp = RedirectResponse("/account?done=1", status_code=303)
    _reissue(resp, p.user_id, version)
    return resp


@router.post("/account/2fa/start")
def start_2fa(request: Request, csrf_token: str = Form("")):
    p = require_login(request)
    check_csrf(p, csrf_token)
    with dbmod.system_session() as s:
        u = s.get(User, p.user_id)
        if not u.totp_enabled:
            u.totp_secret_enc = encrypt_text(totp_new_secret())
    return RedirectResponse("/account", status_code=303)


@router.post("/account/2fa/confirm")
def confirm_2fa(request: Request, code: str = Form(...), csrf_token: str = Form("")):
    p = require_login(request)
    check_csrf(p, csrf_token)
    if rate_limited(f"2fa-confirm:{p.user_id}", limit=8, per_seconds=600):
        return _render(request, p, error="Too many tries. Wait 10 minutes.")
    with dbmod.system_session() as s:
        u = s.get(User, p.user_id)
        secret = decrypt_text(u.totp_secret_enc) if u.totp_secret_enc else None
        step = totp_verify(secret, code, last_step=u.totp_last_step) if secret else None
        if step is None:
            return _render(request, p, error="That code is not right. Check the time on your phone and try again.",
                           pending_secret=secret, uri=totp_uri(secret, u.email) if secret else None)
        u.totp_enabled, u.totp_last_step = True, step
        audit(s, actor=f"user:{u.id}", action="2fa_enabled", business_id=u.business_id)
    return RedirectResponse("/account?done=1", status_code=303)


@router.post("/account/2fa/disable")
def disable_2fa(request: Request, password: str = Form(...), code: str = Form(...), csrf_token: str = Form("")):
    p = require_login(request)
    check_csrf(p, csrf_token)
    if p.role == "admin" and get_settings().admin_2fa_required:
        return _render(request, p, error="Platform admins must keep 2-step login on.")
    with dbmod.system_session() as s:
        u = s.get(User, p.user_id)
        secret = decrypt_text(u.totp_secret_enc) if u.totp_secret_enc else None
        if not verify_password(password, u.password_hash) or not secret or totp_verify(secret, code, last_step=u.totp_last_step) is None:
            return _render(request, p, error="Password or code is wrong.")
        u.totp_enabled, u.totp_secret_enc = False, None
        audit(s, actor=f"user:{u.id}", action="2fa_disabled", business_id=u.business_id)
    return RedirectResponse("/account?done=1", status_code=303)
