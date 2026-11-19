"""Who is logged in, and the safety checks every dashboard page uses."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from .. import db as dbmod
from ..models import User
from ..config import get_settings
from ..security import csrf_ok, read_session_cookie

COOKIE = "fd_session"
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _localdt(value: datetime | None, tz: str = "UTC", fmt: str = "%b %d, %I:%M %p") -> str:
    if not value:
        return ""
    return value.astimezone(ZoneInfo(tz)).strftime(fmt).replace(" 0", " ")


templates.env.filters["localdt"] = _localdt

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_stamps: dict[str, tuple[float, str]] = {}


def static_url(name: str) -> str:
    """'/static/app.css?v=<fingerprint>'. The fingerprint changes when the file changes, so a browser never keeps
    showing an old saved copy after an update."""
    import hashlib

    f = STATIC_DIR / name
    try:
        mtime = f.stat().st_mtime
    except OSError:
        return f"/static/{name}"
    hit = _stamps.get(name)
    if not hit or hit[0] != mtime:
        hit = (mtime, hashlib.sha256(f.read_bytes()).hexdigest()[:10])
        _stamps[name] = hit
    return f"/static/{name}?v={hit[1]}"


templates.env.globals["static_url"] = static_url


@dataclass
class Principal:
    user_id: str
    email: str
    role: str  # admin | owner
    business_id: str | None
    csrf: str
    must_change_password: bool = False
    totp_enabled: bool = False


def get_principal(request: Request) -> Principal | None:
    data = read_session_cookie(request.cookies.get(COOKIE))
    if not data:
        return None
    with dbmod.system_session() as s:
        u = s.get(User, data["u"])
        if not u or u.session_version != data["v"]:
            return None
        return Principal(u.id, u.email, u.role, u.business_id, data["c"], u.must_change_password, u.totp_enabled)


class LoginRequired(Exception):
    pass


class AccountSetupRequired(Exception):
    """The person must change a temporary password or turn on 2-step login first."""


def require_login(request: Request) -> Principal:
    p = get_principal(request)
    if not p:
        raise LoginRequired()
    return p


def require_owner(request: Request) -> Principal:
    p = require_login(request)
    if p.role != "owner" or not p.business_id:
        raise HTTPException(status_code=403, detail="Business owners only")
    if p.must_change_password:
        raise AccountSetupRequired()
    return p


def require_admin(request: Request) -> Principal:
    p = require_login(request)
    if p.role != "admin":
        raise HTTPException(status_code=403, detail="Platform admins only")
    if p.must_change_password or (get_settings().admin_2fa_required and not p.totp_enabled):
        raise AccountSetupRequired()
    return p


def check_csrf(p: Principal, token: str | None) -> None:
    if not csrf_ok({"c": p.csrf}, token):
        raise HTTPException(status_code=403, detail="Form expired. Go back, reload the page and try again.")
