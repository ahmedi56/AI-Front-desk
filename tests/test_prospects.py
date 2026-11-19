"""Product page: the lead form (prospects = the platform owner's own sales leads) and the "try it live" demo chat."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import db as dbmod
from app.config import reset_settings_cache
from app.main import app
from app.models import Prospect

from .conftest import TEST_PG
from .test_dashboard_and_isolation import OWNER_A, csrf_of, login, make_admin, new_client, two  # noqa: F401  (fixtures)

ADMIN = ("admin@platform.test", "admin password long 99")
GOOD = {"name": "Joe Miller", "business": "Joe's Plumbing", "email": "joe@joesplumbing.example", "phone": "512-555-0147",
        "website": "joesplumbing.example", "message": "Please show me the demo."}


def send(c: TestClient, **changes):
    return c.post("/product/lead", data={**GOOD, **changes})


def prospects():
    with dbmod.system_session() as s:
        return list(s.scalars(select(Prospect).order_by(Prospect.created_at)))


# ---------------- the form ----------------
def test_product_page_has_the_form_and_still_no_inline_code():
    html = new_client().get("/product").text
    assert 'action="/product/lead"' in html and 'id="contact"' in html
    for field in ("name", "business", "email", "phone", "website", "message"):
        assert f'name="{field}"' in html, field
    assert "I will only use this to reply to you" in html
    assert " style=" not in html and "<style" not in html and "<script>" not in html and "onclick=" not in html


def test_a_good_form_is_saved_and_the_visitor_is_thanked(adapters):
    c = new_client()
    r = send(c)
    assert r.status_code == 303 and r.headers["location"] == "/product?sent=1#contact"
    assert "Thank you" in c.get("/product?sent=1").text
    [p] = prospects()
    assert (p.name, p.business_name, p.email, p.status) == ("Joe Miller", "Joe's Plumbing", "joe@joesplumbing.example", "new")
    assert p.phone == "512-555-0147" and p.website == "joesplumbing.example" and p.message == "Please show me the demo."


def test_only_name_business_and_email_are_needed(adapters):
    assert send(new_client(), phone="", website="", message="").headers["location"] == "/product?sent=1#contact"
    assert prospects()[0].phone is None and prospects()[0].message is None


@pytest.mark.parametrize("changes", [{"email": "not-an-email"}, {"email": ""}, {"name": "  "}, {"business": ""},
                                     {"email": 'a"b@c.de'}, {"email": "a@b.c\nBcc: x@y.z"}])
def test_a_bad_form_saves_nothing_and_says_what_to_fix(adapters, changes):
    c = new_client()
    r = send(c, **changes)
    assert r.status_code == 303 and r.headers["location"] == "/product?error=check#contact"
    assert "Please check" in c.get("/product?error=check").text
    assert prospects() == []


def test_the_error_message_is_fixed_text_never_text_from_the_address(adapters):
    html = new_client().get("/product?error=<b>hacked</b>&sent=<i>x</i>").text
    assert "hacked" not in html and "<i>x</i>" not in html


def test_a_robot_that_fills_the_hidden_field_is_ignored_quietly(adapters):
    r = send(new_client(), company_url="http://spam.example")
    assert r.headers["location"] == "/product?sent=1#contact"  # the robot learns nothing
    assert prospects() == []


def test_too_many_forms_from_one_visitor_are_refused(adapters):
    c = new_client()
    for _ in range(5):
        assert send(c).headers["location"] == "/product?sent=1#contact"
    r = send(c)
    assert r.headers["location"] == "/product?error=wait#contact"
    assert "Too many" in c.get("/product?error=wait").text
    assert len(prospects()) == 5


def test_long_text_is_cut_and_odd_characters_are_cleaned(adapters):
    send(new_client(), name="J" * 500, message="m" * 5000, website="x" * 900, business="Acme\r\nPlumbing\x00")
    [p] = prospects()
    assert len(p.name) <= 120 and len(p.message) <= 1000 and len(p.website) <= 200
    assert p.business_name == "Acme Plumbing"


def test_the_owner_gets_an_email_only_when_an_address_is_set(adapters, email, monkeypatch):
    send(new_client())
    assert email.sent == []
    monkeypatch.setenv("PRODUCT_CONTACT_EMAIL", "me@frontdesk.example")
    reset_settings_cache()
    send(new_client(), name="Second Person")
    [mail] = email.sent
    assert mail["to"] == "me@frontdesk.example" and "Second Person" in mail["body"] and "\n" not in mail["subject"]


def test_a_failing_email_server_does_not_lose_the_prospect(adapters, monkeypatch):
    class Broken:
        def send(self, **kw):
            raise RuntimeError("smtp down")

    adapters.email = Broken()
    monkeypatch.setenv("PRODUCT_CONTACT_EMAIL", "me@frontdesk.example")
    reset_settings_cache()
    assert send(new_client()).headers["location"] == "/product?sent=1#contact"
    assert len(prospects()) == 1


def test_contact_details_are_not_written_to_the_logs(adapters, caplog):
    caplog.set_level(logging.DEBUG)
    send(new_client())
    assert "joe@joesplumbing.example" not in caplog.text and "512-555-0147" not in caplog.text


# ---------------- who may see prospects ----------------
def test_admin_sees_prospects_escaped_and_can_mark_them_contacted(adapters):
    send(new_client(), message="<script>alert(1)</script>", website="javascript:alert(1)")
    make_admin()
    c = login(new_client(), ADMIN)
    html = c.get("/admin").text
    assert "Prospects" in html and "Joe Miller" in html and "joe@joesplumbing.example" in html
    assert "<script>alert(1)</script>" not in html and 'href="javascript:' not in html
    pid = prospects()[0].id
    assert c.post(f"/admin/prospects/{pid}/contacted", data={}).status_code == 403  # no CSRF token
    r = c.post(f"/admin/prospects/{pid}/contacted", data={"csrf_token": csrf_of(c, "/admin")})
    assert r.status_code == 303 and prospects()[0].status == "contacted"


def test_a_business_owner_can_never_see_or_change_prospects(two):
    send(new_client())
    c = login(new_client(), OWNER_A)
    assert c.get("/admin").status_code == 403
    pid = prospects()[0].id
    assert c.post(f"/admin/prospects/{pid}/contacted", data={"csrf_token": csrf_of(c)}).status_code == 403
    assert prospects()[0].status == "new"
    for path in ("/", "/leads", "/inbox", "/export/leads.csv"):
        assert "Joe Miller" not in c.get(path).text, path


def test_nobody_sees_prospects_without_a_login(adapters):
    send(new_client())
    r = new_client().post(f"/admin/prospects/{prospects()[0].id}/contacted", data={})
    assert r.status_code == 303 and r.headers["location"] == "/login"


@pytest.mark.skipif(not TEST_PG, reason="needs the real Postgres database (set TEST_DATABASE_URL)")
def test_database_hides_prospects_from_every_business(two):
    a, _ = two
    send(new_client())
    with dbmod.tenant_session(a) as db:
        assert db.execute(text("SELECT count(*) FROM prospects")).scalar() == 0


# ---------------- "try it live" demo chat ----------------
def demo_page(monkeypatch, slug):
    monkeypatch.setenv("PRODUCT_DEMO_BUSINESS", slug)
    reset_settings_cache()
    return new_client().get("/product").text


def test_no_demo_chat_unless_a_demo_business_is_chosen(biz, monkeypatch):
    for slug in ("", "nobody-here", "bad slug!"):
        html = demo_page(monkeypatch, slug)
        assert "embed.js" not in html and "Try the live demo" not in html, slug


def test_demo_chat_bubble_and_button_appear_for_the_chosen_business(biz, monkeypatch):
    html = demo_page(monkeypatch, "acme")
    assert 'src="/static/embed.js?v=' in html and 'data-business="acme"' in html and "data-demo" in html
    assert "Try the live demo" in html and "data-fd-open" in html
    assert "<script>" not in html and " style=" not in html


def test_demo_label_shows_only_in_the_demo_chat_of_the_demo_business(biz, biz2, monkeypatch):
    monkeypatch.setenv("PRODUCT_DEMO_BUSINESS", "acme")
    reset_settings_cache()
    c = TestClient(app)
    label = "This business is not real"
    assert label in c.get("/widget/acme?demo=1").text
    assert label not in c.get("/widget/acme").text
    assert label not in c.get("/widget/beta?demo=1").text  # nobody can put the label on a real client's chat


def test_chat_bubble_script_can_open_from_a_page_button_and_pass_the_demo_mark():
    js = TestClient(app).get("/static/embed.js").text
    assert "data-fd-open" in js and "data-demo" in js and "e.origin === origin" in js
