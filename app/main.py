"""The web server.   Start with:  uvicorn app.main:app"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from . import db as dbmod
from .channels import sms as sms_channel
from .channels import voice as voice_channel
from .channels import webchat as web_channel
from .channels.common import business_by_slug
from .config import get_settings
from .models import Business
from .routers import account, admin, auth, owner, product
from .routers.deps import AccountSetupRequired, LoginRequired, get_principal, templates

log = logging.getLogger("frontdesk")
BASE = Path(__file__).resolve().parent

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; form-action 'self'; "
       "base-uri 'none'; frame-ancestors 'none'; object-src 'none'")
CSP_WIDGET = CSP.replace("frame-ancestors 'none'", "frame-ancestors *")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        widget = request.url.path.startswith("/widget/")
        resp.headers["Content-Security-Policy"] = CSP_WIDGET if widget else CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not widget:
            resp.headers["X-Frame-Options"] = "DENY"
        if get_settings().is_production:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith(("/webhooks", "/api")) or get_principal_cookie(request):
            resp.headers["Cache-Control"] = "no-store"
        return resp


class FreshStaticFiles(StaticFiles):
    """Our own style and script files: the browser must re-check them every time (a quick 'not changed' answer when
    nothing changed). Otherwise an update shows the new pages with the OLD look."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def get_principal_cookie(request: Request) -> bool:
    return "fd_session" in request.cookies


def create_app() -> FastAPI:
    s = get_settings()
    if s.is_production:
        problems = s.check_production_ready()
        if problems:
            raise RuntimeError("Refusing to start in production:\n- " + "\n- ".join(problems))
    app = FastAPI(title="AI Front Desk", docs_url=None if s.is_production else "/docs",
                  redoc_url=None, openapi_url=None if s.is_production else "/openapi.json")
    app.add_middleware(SecurityHeaders)
    app.mount("/static", FreshStaticFiles(directory=str(BASE / "static")), name="static")

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(AccountSetupRequired)
    async def _account_setup(request: Request, exc: AccountSetupRequired):
        return RedirectResponse("/account", status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith(("/webhooks", "/api")):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(request, "error.html", {"code": exc.status_code, "message": str(exc.detail)},
                                          status_code=exc.status_code)

    @app.get("/healthz")
    def healthz():
        try:
            with dbmod.system_session() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.get("/widget/{slug}", response_class=HTMLResponse)
    def widget(request: Request, slug: str, demo: str = ""):
        bid = business_by_slug(slug)
        if not bid:
            raise HTTPException(status_code=404, detail="Not found")
        with dbmod.tenant_session(bid) as db:
            b = db.get(Business, bid)
            # The "this is only a demo" label is for the product page's demo business, never for a real client's chat.
            is_demo = demo == "1" and slug == product.demo_slug()
            return templates.TemplateResponse(request, "widget.html", {"b": b, "slug": slug, "demo": is_demo})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        # Browsers ask for this address by themselves. Without it they log a "404 not found" error on every page.
        return FileResponse(BASE / "static" / "favicon.svg", media_type="image/svg+xml")

    if not s.is_production:
        # A pretend client website with our chat bubble on it. For demo videos and sales calls only; never on a real server.
        @app.get("/demo-site/{slug}", response_class=HTMLResponse)
        def demo_site(request: Request, slug: str):
            bid = business_by_slug(slug)
            if not bid:
                raise HTTPException(status_code=404, detail="Not found")
            with dbmod.tenant_session(bid) as db:
                b = db.get(Business, bid)
                return templates.TemplateResponse(request, "demo_site.html", {"b": b, "slug": slug})

    app.include_router(auth.router)
    app.include_router(product.router)
    app.include_router(sms_channel.router)
    app.include_router(voice_channel.router)
    app.include_router(web_channel.router)
    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(owner.router)
    return app


app = create_app()
