"""The public product page (the sales page for new clients), built from the owner's approved design file."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import reset_settings_cache
from app.main import app


def page(monkeypatch=None, **env) -> str:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    r = TestClient(app).get("/product")
    assert r.status_code == 200
    return r.text


def test_product_page_is_public_and_has_every_section():
    html = page()
    for words in ("Your website answers customers while you're on a job.", "What it does for you",
                  "What it will never do", "The 30-day pilot", "Questions owners ask",
                  "Books real appointments", "Invent a price", "One line on your website"):
        assert words in html, words


def test_product_page_follows_the_security_rules():
    """No inline styles or scripts (the CSP blocks them), no outside font or script service, not frameable."""
    r = TestClient(app).get("/product")
    html = r.text
    assert " style=" not in html and "<style" not in html and "<script" not in html and "onclick=" not in html
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    assert 'href="/static/product.css?v=' in html
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_product_page_font_is_served_from_our_own_server():
    c = TestClient(app)
    font = c.get("/static/fonts/public-sans-latin.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"
    css = c.get("/static/product.css").text
    assert "fonts/public-sans-latin.woff2" in css and "http" not in css


def test_product_page_shows_placeholders_until_the_owner_fills_them(monkeypatch):
    html = page(monkeypatch, PRODUCT_CONTACT_EMAIL="", PRODUCT_VIDEO_URL="", PRODUCT_OWNER_NAME="",
                PRODUCT_SETUP_PRICE="", PRODUCT_MONTHLY_PRICE="")
    assert "[YOUR EMAIL]" in html and "[Your name]" in html and "[$300]" in html and "[$99]" in html
    assert "Watch the 2-minute demo" not in html  # no video link yet = no dead button
    assert "mailto:" not in html


def test_product_page_uses_the_owners_details(monkeypatch):
    html = page(monkeypatch, PRODUCT_CONTACT_EMAIL="hello@frontdesk.example", PRODUCT_VIDEO_URL="https://youtu.be/abc123",
                PRODUCT_OWNER_NAME="Sam Rivers", PRODUCT_SETUP_PRICE="$250", PRODUCT_MONTHLY_PRICE="$79")
    assert 'href="mailto:hello@frontdesk.example"' in html and 'href="https://youtu.be/abc123"' in html
    assert "by Sam Rivers" in html and "$250" in html and "$79" in html
    assert "[" not in html.split("<body", 1)[1].replace("[Your Plumbing Co.]", "")


def test_product_page_refuses_an_unsafe_video_link(monkeypatch):
    html = page(monkeypatch, PRODUCT_VIDEO_URL="javascript:alert(1)")
    assert "javascript:" not in html and "Watch the 2-minute demo" not in html


def test_product_page_escapes_the_owners_details(monkeypatch):
    html = page(monkeypatch, PRODUCT_OWNER_NAME="<b>Sam</b>", PRODUCT_CONTACT_EMAIL='a"@b.c')
    assert "<b>Sam</b>" not in html and "mailto:" not in html  # a strange email is not turned into a link


def test_dashboard_still_needs_a_login():
    r = TestClient(app, follow_redirects=False).get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
