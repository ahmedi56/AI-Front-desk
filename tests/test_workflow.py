"""Client setup workflow: checklist on Today, website code in Settings, admin AI key, one-command sales demos."""
from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app import db as dbmod
from app.config import reset_settings_cache
from app.models import Business, Service, User

from .test_dashboard_and_isolation import OWNER_A, csrf_of, login, new_client, two  # noqa: F401  (fixtures)


def test_today_shows_what_is_left_to_set_up(two):
    html = login(new_client(), OWNER_A).get("/").text
    assert "Finish setting up" in html
    assert "Connect your free AI account" in html and "Put the chat on your website" in html
    assert re.search(r"\d of 6 done", html)


def test_checklist_disappears_when_everything_is_done(two):
    from app.services import ai_keys

    with dbmod.system_session() as s:
        b = s.scalar(select(Business).where(Business.slug == "acme"))
        b.facts = {**(b.facts or {}), "faqs": [{"q": "Do you work weekends?", "a": "Saturdays only."}]}
        b.handoff_phone = b.handoff_phone or "+15125550111"
        ai_keys.set_key(b, "groq", "gsk_" + "Z" * 40)
        bid = b.id
        if not s.scalar(select(Service.id).where(Service.business_id == bid)):
            s.add(Service(business_id=bid, name="Drain cleaning", price_text="$149"))
    from app.models import Conversation, Customer

    with dbmod.tenant_session(bid) as db:
        cu = Customer(business_id=bid)
        db.add(cu)
        db.flush()
        db.add(Conversation(business_id=bid, customer_id=cu.id, channel="web"))
    assert "Finish setting up" not in login(new_client(), OWNER_A).get("/").text


def test_settings_gives_the_one_line_website_code_for_this_business_only(two):
    html = login(new_client(), OWNER_A).get("/settings").text
    assert 'data-business="acme"' in html and "/static/embed.js" in html
    assert 'data-business="beta"' not in html


def test_admin_can_save_a_clients_ai_key_and_it_is_never_shown(adapters):
    from app.security import hash_password
    from app.services import ai_keys

    from .conftest import make_business

    bid = make_business()
    with dbmod.system_session() as s:
        s.add(User(email="admin@platform.test", password_hash=hash_password("admin password long 99"), role="admin"))
    c = login(new_client(), ("admin@platform.test", "admin password long 99"))
    token = csrf_of(c, "/admin")
    key = "gsk_" + "Q" * 44
    r = c.post(f"/admin/businesses/{bid}/ai", data={"provider": "groq", "api_key": key, "model": "", "csrf_token": token},
               follow_redirects=True)
    assert key not in r.text and "AI key ..." + key[-4:] in r.text
    with dbmod.system_session() as s:
        assert ai_keys.key_status(s.get(Business, bid))["has_key"]
    # owners cannot use the admin route
    assert c.post(f"/admin/businesses/{bid}/ai", data={"provider": "anthropic", "api_key": key, "csrf_token": token},
                  follow_redirects=True).status_code == 200  # refused with a message, nothing saved as anthropic
    with dbmod.system_session() as s:
        assert s.get(Business, bid).ai_provider == "groq"


def test_owner_cannot_reach_the_admin_ai_route(two):
    c = login(new_client(), OWNER_A)
    token = csrf_of(c, "/settings")
    with dbmod.system_session() as s:
        other = s.scalar(select(Business.id).where(Business.slug == "beta"))
    r = c.post(f"/admin/businesses/{other}/ai", data={"provider": "groq", "api_key": "gsk_" + "X" * 40, "csrf_token": token})
    assert r.status_code == 403


def test_demo_for_makes_a_ready_sales_demo(adapters, capsys):
    from app.cli import demo_for

    demo_for("Joe's Plumbing & Drain")
    out = capsys.readouterr().out
    assert "/demo-site/joes-plumbing-drain" in out
    with dbmod.system_session() as s:
        b = s.scalar(select(Business).where(Business.slug == "joes-plumbing-drain"))
        assert b and b.name == "Joe's Plumbing & Drain"
        assert len(s.scalars(select(Service).where(Service.business_id == b.id)).all()) >= 3
        assert (b.facts or {}).get("faqs") and b.hours.get("sat")
    demo_for("Joe's Plumbing & Drain")  # a second time gets its own short name, never touches the first
    assert "joes-plumbing-drain-2" in capsys.readouterr().out


def test_demo_for_refuses_in_production(monkeypatch):
    from app.cli import demo_for

    monkeypatch.setenv("APP_ENV", "production")
    reset_settings_cache()
    try:
        with pytest.raises(SystemExit):
            demo_for("Real Client Co")
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        reset_settings_cache()


def test_password_change_says_it_worked(two):
    c = login(new_client(), OWNER_A)
    token = csrf_of(c, "/account")
    r = c.post("/account/password", data={"current_password": OWNER_A[1], "new_password": "a brand new long password",
                                          "confirm_password": "a brand new long password", "csrf_token": token},
               follow_redirects=True)
    assert "Password changed" in r.text


def test_hidden_things_really_hide():
    """The chat's quick buttons are hidden with the 'hidden' attribute; a display rule must never override it."""
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.css").read_text()
    assert "[hidden]{display:none!important}" in css.replace(" ", "")
