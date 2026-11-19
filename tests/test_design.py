"""The new design: chat widget, chat bubble for websites, demo website. Checks the parts that matter for safety."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

from .test_dashboard_and_isolation import OWNER_A, login, new_client, two  # noqa: F401  (fixtures)


def test_widget_shows_ai_disclosure_and_quick_buttons(biz):
    r = TestClient(app).get("/widget/acme")
    assert r.status_code == 200
    assert "You are chatting with an AI assistant" in r.text
    for label in ("Book a visit", "Prices", "Opening hours", "Urgent problem"):
        assert label in r.text
    assert "call 911" in r.text


def test_widget_may_be_framed_by_client_websites_but_dashboard_may_not(biz):
    c = TestClient(app)
    w = c.get("/widget/acme")
    assert "frame-ancestors *" in w.headers["content-security-policy"] and "x-frame-options" not in w.headers
    d = c.get("/login")
    assert "frame-ancestors 'none'" in d.headers["content-security-policy"] and d.headers["x-frame-options"] == "DENY"


def test_no_inline_styles_or_scripts_anywhere_in_the_new_pages(biz):
    """Our security rules (CSP) block inline styles and scripts. If one slips in, the page silently breaks."""
    c = TestClient(app)
    for path in ("/widget/acme", "/login", "/demo-site/acme"):
        html = c.get(path).text
        assert " style=" not in html and "<style" not in html, path
        assert "<script>" not in html and "onclick=" not in html, path


def test_chat_bubble_script_and_styles_are_served(biz):
    c = TestClient(app)
    js = c.get("/static/embed.js")
    assert js.status_code == 200 and "data-business" in js.text and "/widget/" in js.text
    # it only accepts the close message from our own server
    assert "e.origin === origin" in js.text
    assert c.get("/static/embed.css").status_code == 200


def test_demo_website_shows_the_business_and_loads_the_bubble(biz):
    r = TestClient(app).get("/demo-site/acme")
    assert r.status_code == 200
    assert 'src="/static/embed.js?v=' in r.text and 'data-business="acme"' in r.text
    assert "demo" in r.text.lower()
    assert TestClient(app).get("/demo-site/nobody-here").status_code == 404


def test_demo_website_does_not_exist_in_production(monkeypatch):
    from app.config import reset_settings_cache
    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://a@b/c")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://x.example")
    monkeypatch.setenv("ENCRYPTION_KEY", "k")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t")
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "v")
    reset_settings_cache()
    try:
        prod = create_app()
        assert not any(getattr(r, "path", "").startswith("/demo-site") for r in prod.routes)
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        reset_settings_cache()


def test_dashboard_menu_marks_the_current_page(two):
    c = login(new_client(), OWNER_A)
    html = c.get("/inbox").text
    assert 'href="/inbox" aria-current="page"' in html
    assert 'href="/" aria-current="page"' not in html
    assert 'href="/" aria-current="page"' in c.get("/").text


def test_pages_load_the_newest_style_file_never_an_old_saved_copy(biz):
    """After an update, a browser must not keep showing the old look. Files carry a version stamp and are re-checked."""
    import hashlib
    from pathlib import Path

    c = TestClient(app)
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.css").read_bytes()
    stamp = hashlib.sha256(css).hexdigest()[:10]
    for path in ("/login", "/widget/acme", "/demo-site/acme"):
        assert f"/static/app.css?v={stamp}" in c.get(path).text, path
    r = c.get(f"/static/app.css?v={stamp}")
    assert r.status_code == 200 and "no-cache" in r.headers.get("cache-control", "")
    assert "no-cache" in c.get("/static/embed.js").headers.get("cache-control", "")


def test_the_app_always_uses_the_light_design(biz):
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.css").read_text()
    assert "prefers-color-scheme:dark" not in css.replace(" ", "")


def test_tab_icon_exists_so_browsers_do_not_log_a_404(biz):
    """Chrome asks every site for /favicon.ico. A 404 there shows as an error in the browser's console."""
    c = TestClient(app)
    ico = c.get("/favicon.ico")
    assert ico.status_code == 200 and ico.headers["content-type"].startswith("image/svg+xml")
    assert "<script" not in ico.text
    for path in ("/login", "/widget/acme", "/demo-site/acme", "/product"):
        assert 'rel="icon" href="/static/favicon.svg?v=' in c.get(path).text, path
