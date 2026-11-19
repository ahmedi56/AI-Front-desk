"""The public product page (/product): the sales page, its "try it live" demo chat and its lead form.

A PROSPECT is a business owner who may become OUR client. Prospects belong to the platform, not to any business,
so they live in their own table and only the platform admin can read them.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db as dbmod
from ..channels.common import business_by_slug
from ..config import get_settings
from ..models import Prospect
from ..security import rate_limited
from ..services.adapters import get_adapters
from ..services.audit import audit
from .deps import templates

log = logging.getLogger("frontdesk.product")
router = APIRouter()

EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_URL_BAD = re.compile(r"[\s\"'<>]")
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")

# Fixed sentences. The page never shows text taken from the address bar.
ERRORS = {
    "check": "Please check your name, business name and email, then send again.",
    "wait": "Too many messages from this connection. Please try again in an hour.",
}


def demo_slug() -> str:
    """The demo business for the 'try it live' chat, or '' when none is set or it does not exist."""
    slug = get_settings().product_demo_business.strip().lower()
    return slug if _SLUG_OK.match(slug) and business_by_slug(slug) else ""


def _line(value: str, limit: int) -> str | None:
    """One clean line of untrusted text: no line breaks or control characters, cut to a maximum length."""
    text = " ".join(_CONTROL.sub(" ", value or "").split())[:limit].strip()
    return text or None


@router.get("/product", response_class=HTMLResponse)
def product_page(request: Request, sent: str = "", error: str = ""):
    """The owner's details come from .env; anything odd is left out, never trusted."""
    cfg = get_settings()
    email = cfg.product_contact_email.strip()
    video = cfg.product_video_url.strip()
    return templates.TemplateResponse(request, "product.html", {
        "email": email if EMAIL_OK.match(email) else "",
        "video_url": video if video.startswith("https://") and not _URL_BAD.search(video) else "",
        "owner_name": cfg.product_owner_name.strip()[:80],
        "setup_price": cfg.product_setup_price.strip()[:20],
        "monthly_price": cfg.product_monthly_price.strip()[:20],
        "demo_slug": demo_slug(),
        "sent": sent == "1",
        "error": ERRORS.get(error, ""),
    })


@router.post("/product/lead")
def product_lead(request: Request, name: str = Form(""), business: str = Form(""), email: str = Form(""),
                 phone: str = Form(""), website: str = Form(""), message: str = Form(""), company_url: str = Form("")):
    done = RedirectResponse("/product?sent=1#contact", status_code=303)
    if company_url.strip():
        return done  # a hidden field that people never see: only robots fill it in. Save nothing, tell them nothing.
    ip = request.client.host if request.client else "unknown"
    if rate_limited(f"prospect-ip:{ip}", limit=5, per_seconds=3600) or rate_limited("prospect-all", limit=300, per_seconds=86400):
        return RedirectResponse("/product?error=wait#contact", status_code=303)
    clean_name, clean_business, clean_email = _line(name, 120), _line(business, 160), email.strip()
    if not clean_name or not clean_business or len(clean_email) > 200 or not EMAIL_OK.match(clean_email):
        return RedirectResponse("/product?error=check#contact", status_code=303)
    text = " ".join(_CONTROL.sub(" ", message or "").split())[:1000] or None
    p = Prospect(name=clean_name, business_name=clean_business, email=clean_email.lower(), phone=_line(phone, 40),
                 website=_line(website, 200), message=text, created_at=dbmod.utcnow())
    with dbmod.system_session() as s:
        s.add(p)
        s.flush()
        audit(s, actor="public", action="prospect_created", business_id=None, detail={"prospect_id": p.id})
        body = (f"New prospect from the product page.\n\nName: {p.name}\nBusiness: {p.business_name}\nEmail: {p.email}\n"
                f"Phone: {p.phone or '-'}\nWebsite: {p.website or '-'}\nMessage: {p.message or '-'}\n\n"
                "You can also see it on your admin page, under Prospects.")
    _tell_owner(body)
    return done


def _tell_owner(body: str) -> None:
    """Email the platform owner, if an address is set. A broken email server never loses the prospect."""
    to = get_settings().product_contact_email.strip()
    if not EMAIL_OK.match(to):
        return
    try:
        get_adapters().email.send(to=to, subject="New prospect from your product page", body=body)
    except Exception as exc:  # the prospect is already saved and visible on the admin page
        log.info("prospect email not sent: %s", type(exc).__name__)
