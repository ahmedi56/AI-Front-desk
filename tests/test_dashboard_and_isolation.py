"""Owner dashboard, logins, and keeping businesses apart. Scenario 20."""
from __future__ import annotations

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import db as dbmod
from app.main import app
from app.models import Appointment, Business, Conversation, Customer, Lead, Message, Service, User
from app.security import hash_password

from .conftest import NOW, TEST_PG, make_business, make_owner, say
from .helpers import PHONE, appointments, conversations, converse, leads

OWNER_A = ("owner@acme.test", "correct horse battery")
OWNER_B = ("owner@beta.test", "another long password 42")


@pytest.fixture
def two(adapters):
    a = make_business()
    b = make_business(name="Beta HVAC", slug="beta", number="+15125550200", sms_number="+15125550299", handoff="+15125550222")
    make_owner(a, *OWNER_A)
    make_owner(b, *OWNER_B)
    return a, b


def new_client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def login(c: TestClient, creds):
    r = c.post("/login", data={"email": creds[0], "password": creds[1]})
    assert r.status_code == 303, r.text
    return c


def csrf_of(c: TestClient, path="/settings") -> str:
    html = c.get(path).text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


# ---- login ----
def test_pages_need_login(two):
    c = new_client()
    for path in ("/", "/inbox", "/settings", "/appointments", "/leads", "/usage", "/export/leads.csv", "/admin"):
        r = c.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/login", path


def test_wrong_password_and_lockout(two):
    c = new_client()
    for _ in range(5):
        r = c.post("/login", data={"email": OWNER_A[0], "password": "wrong password!!"})
        assert r.status_code == 401
    # now even the right password is refused for a while
    r = c.post("/login", data={"email": OWNER_A[0], "password": OWNER_A[1]})
    assert r.status_code == 401


def test_unknown_email_gets_the_same_answer(two):
    c = new_client()
    r1 = c.post("/login", data={"email": "nobody@x.test", "password": "whatever long enough"})
    r2 = c.post("/login", data={"email": OWNER_A[0], "password": "wrong password!!"})
    assert r1.status_code == r2.status_code == 401
    assert "Wrong email or password" in r1.text and "Wrong email or password" in r2.text


def test_login_is_rate_limited(two):
    c = new_client()
    codes = [c.post("/login", data={"email": f"x{i}@x.test", "password": "whatever long enough"}).status_code for i in range(25)]
    assert 429 in codes


def test_cookie_flags_and_security_headers(two):
    c = new_client()
    r = c.post("/login", data={"email": OWNER_A[0], "password": OWNER_A[1]})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    h = c.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"


def test_forged_cookie_is_useless(two):
    c = new_client()
    c.cookies.set("fd_session", "forged.value.here")
    assert c.get("/").status_code == 303


def test_logout_everywhere_kills_old_cookies(two):
    c = login(new_client(), OWNER_A)
    token = csrf_of(c)
    old_cookie = c.cookies.get("fd_session")
    assert c.post("/logout-everywhere", data={"csrf_token": token}).status_code == 303
    c2 = new_client()
    c2.cookies.set("fd_session", old_cookie)
    assert c2.get("/").status_code == 303


# ---- CSRF ----
def test_posts_without_csrf_token_are_refused(two):
    c = login(new_client(), OWNER_A)
    assert c.post("/agent/toggle", data={}).status_code == 403
    assert c.post("/agent/toggle", data={"csrf_token": "wrong"}).status_code == 403
    assert c.post("/settings/services", data={"name": "x"}).status_code == 403
    a, _ = two
    with dbmod.system_session() as s:
        assert s.get(Business, a).agent_enabled


# ---- 20: business A can never touch business B ----
def test_20_owner_cannot_open_other_businesses_data(two, llm):
    a, b = two
    llm.push(say("Hello B customer"))
    converse(b, "hello from B's customer", phone="+15125550188")
    llm.push(say("ok"))
    converse(a, "hello from A's customer")
    b_conv = conversations(b)[0]
    with dbmod.tenant_session(b) as db:
        db.add(Appointment(business_id=b, customer_id=b_conv.customer_id, service_name="Secret", starts_at=NOW + timedelta(days=2),
                           ends_at=NOW + timedelta(days=2, hours=1)))
        db.add(Lead(business_id=b, customer_id=b_conv.customer_id, need="B's private lead"))
        lead_b = db.query(Lead).filter_by(business_id=b).one().id
        appt_b = db.query(Appointment).filter_by(business_id=b).one().id
        svc_b = Service(business_id=b, name="B only service")
        db.add(svc_b)
        db.flush()
        svc_b_id = svc_b.id

    c = login(new_client(), OWNER_A)
    token = csrf_of(c)
    assert c.get(f"/inbox/{b_conv.id}").status_code == 404
    inbox = c.get("/inbox").text
    assert "+15125550188" not in inbox and "hello from B" not in inbox
    assert c.post(f"/appointments/{appt_b}/status", data={"status": "cancelled", "csrf_token": token}).status_code == 404
    assert c.post(f"/leads/{lead_b}/done", data={"csrf_token": token}).status_code == 404
    assert c.post(f"/settings/services/{svc_b_id}/delete", data={"csrf_token": token}).status_code == 404
    for path in ("/appointments", "/leads", "/export/appointments.csv", "/export/leads.csv", "/settings", "/usage", "/"):
        body = c.get(path).text
        assert "B's private lead" not in body and "Secret" not in body and "B only service" not in body, path
    with dbmod.system_session() as s:
        assert s.get(Appointment, appt_b).status == "booked"
        assert s.get(Lead, lead_b).status == "new"
        assert s.get(Service, svc_b_id).active


def test_owner_cannot_use_admin_pages(two):
    c = login(new_client(), OWNER_A)
    assert c.get("/admin").status_code == 403
    token = csrf_of(c)
    r = c.post("/admin/businesses", data={"name": "X", "slug": "xx", "owner_email": "x@x.test", "csrf_token": token})
    assert r.status_code == 403


def test_a_business_has_its_own_customers_and_opt_outs(two):
    a, b = two
    converse(a, "hi there", phone=PHONE)
    converse(b, "hi there", phone=PHONE)
    with dbmod.tenant_session(a) as db:
        db.query(Customer).filter_by(business_id=a).one().sms_opt_out = True
    with dbmod.tenant_session(b) as db:
        assert db.query(Customer).filter_by(business_id=b).one().sms_opt_out is False


# ---- owner actions ----
def test_owner_kill_switch_from_the_dashboard(two, llm):
    a, _ = two
    c = login(new_client(), OWNER_A)
    token = csrf_of(c)
    assert "ON" in c.get("/").text
    assert c.post("/agent/toggle", data={"csrf_token": token}).status_code == 303
    with dbmod.system_session() as s:
        assert s.get(Business, a).agent_enabled is False
    assert converse(a, "hello?").rule == "kill_switch"


def test_settings_save_and_validation(two):
    a, _ = two
    c = login(new_client(), OWNER_A)
    token = csrf_of(c)
    form = {"csrf_token": token, "timezone": "America/New_York", "handoff_phone": "(512) 555-0111", "owner_email": "o@acme.test",
            "address": "1 Main", "areas": "Austin", "policies": "Pay after the job.", "emergency_note": "", "urgent_keywords": "Burst Pipe, flooding",
            "faqs": "Q: Parking?\nA: Yes, free.\n\nQ: Cards?\nA: We take cards.", "quiet_start": "21", "quiet_end": "8",
            "retention_days": "60", "review_link": "https://g.page/r/acme", "mon_open": "09:00", "mon_close": "18:00"}
    r = c.post("/settings", data=form)
    assert r.headers["location"] == "/settings?saved=1"
    with dbmod.system_session() as s:
        b = s.get(Business, a)
        assert b.timezone == "America/New_York" and b.retention_days == 60 and b.hours == {"mon": [["09:00", "18:00"]]}
        assert b.facts["urgent_keywords"] == ["burst pipe", "flooding"] and len(b.facts["faqs"]) == 2

    bad = dict(form, timezone="Mars/Olympus", review_link="http://insecure", mon_close="08:00", retention_days="2")
    r = c.post("/settings", data=bad)
    assert "error" in r.headers["location"]


def test_urgent_keyword_from_settings_triggers_handoff(two):
    a, _ = two
    with dbmod.system_session() as s:
        b = s.get(Business, a)
        b.facts = {**b.facts, "urgent_keywords": ["burst pipe"]}
    r = converse(a, "Hi, we have a burst pipe in the basement")
    assert r.rule == "urgent" and r.handed_off


def test_add_service_and_price_appear_to_the_agent(two, llm):
    a, _ = two
    c = login(new_client(), OWNER_A)
    token = csrf_of(c)
    c.post("/settings/services", data={"name": "Toilet repair", "price_text": "from $120", "duration_min": "45", "csrf_token": token})
    llm.push(say("Toilet repair is from $120."))
    r = converse(a, "price for toilet repair?")
    assert r.guard is None, "a price the owner typed is allowed"
    assert "Toilet repair: price from $120" in llm.calls[0]["system"]


def test_csv_export_blocks_formula_injection(two):
    a, _ = two
    converse(a, "hello")
    with dbmod.tenant_session(a) as db:
        cust = db.query(Customer).filter_by(business_id=a).one()
        db.add(Lead(business_id=a, customer_id=cust.id, name="=HYPERLINK(\"http://evil\")", need="+cmd|' /C calc'!A0"))
    c = login(new_client(), OWNER_A)
    body = c.get("/export/leads.csv").text
    assert "'=HYPERLINK" in body and "'+cmd" in body
    assert c.get("/export/leads.csv").headers["content-type"].startswith("text/csv")


def test_customer_text_is_escaped_in_the_inbox(two, llm):
    a, _ = two
    llm.push(say("ok"))
    converse(a, "<script>alert(1)</script> hello there")
    c = login(new_client(), OWNER_A)
    conv_id = conversations(a)[0].id
    html = c.get(f"/inbox/{conv_id}").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_usage_page_shows_cost_numbers(two, llm):
    a, _ = two
    llm.push(say("hello!"))
    converse(a, "hi there")
    c = login(new_client(), OWNER_A)
    html = c.get("/usage").text
    assert "AI tokens" in html and "per chat" in html


# ---- platform admin ----
def make_admin():
    with dbmod.system_session() as s:
        s.add(User(business_id=None, email="admin@platform.test", password_hash=hash_password("admin password long 99"), role="admin"))


def test_admin_creates_business_and_owner_can_log_in(adapters):
    make_admin()
    c = login(new_client(), ("admin@platform.test", "admin password long 99"))
    assert c.get("/", follow_redirects=False).headers["location"] == "/admin"
    token = csrf_of(c, "/admin")
    r = c.post("/admin/businesses", data={"name": "Gamma Roofing", "slug": "gamma", "owner_email": "g@gamma.test", "timezone": "America/Chicago",
                                          "handoff_phone": "512-555-0133", "voice_number": "+15125550333", "csrf_token": token})
    assert r.status_code == 200 and "temp" not in str(r.url), "the temporary password must not be in the address"
    assert r.headers["cache-control"] == "no-store"
    m = re.search(r"<code>([\w-]{12,})</code>", r.text)
    assert m
    owner = login(new_client(), ("g@gamma.test", m.group(1)))
    # a temporary password must be replaced before anything else works
    assert owner.get("/").headers["location"] == "/account"
    assert owner.get("/inbox").headers["location"] == "/account"
    token_o = csrf_of(owner, "/account")
    bad = owner.post("/account/password", data={"current_password": m.group(1), "new_password": "short", "confirm_password": "short", "csrf_token": token_o})
    assert "at least 12" in bad.text
    ok = owner.post("/account/password", data={"current_password": m.group(1), "new_password": "my new long password", "confirm_password": "my new long password", "csrf_token": token_o})
    assert ok.status_code == 303
    assert "Gamma Roofing" in owner.get("/").text
    # the old temporary password no longer works
    assert new_client().post("/login", data={"email": "g@gamma.test", "password": m.group(1)}).status_code == 401
    # same slug again is refused
    r = c.post("/admin/businesses", data={"name": "Again", "slug": "gamma", "owner_email": "g2@gamma.test", "csrf_token": token})
    assert "error" in r.headers["location"]


def test_admin_enables_texting_only_with_a_number(adapters):
    make_admin()
    bid = make_business(sms_enabled=False, sms_number=None)
    c = login(new_client(), ("admin@platform.test", "admin password long 99"))
    token = csrf_of(c, "/admin")
    c.post(f"/admin/businesses/{bid}/numbers", data={"sms_enabled": "1", "sms_number": "", "voice_number": "+15125550100", "csrf_token": token})
    with dbmod.system_session() as s:
        assert s.get(Business, bid).sms_enabled is False
    c.post(f"/admin/businesses/{bid}/numbers", data={"sms_enabled": "1", "sms_number": "(512) 555-0199", "voice_number": "+15125550100", "csrf_token": token})
    with dbmod.system_session() as s:
        b = s.get(Business, bid)
        assert b.sms_enabled and b.sms_number == "+15125550199"


# ---- the database itself refuses cross-business access (Postgres only) ----
pg_only = pytest.mark.skipif(not TEST_PG, reason="needs the real Postgres database (set TEST_DATABASE_URL)")


@pg_only
def test_database_hides_other_businesses_even_with_a_buggy_query(two, llm):
    a, b = two
    converse(a, "A message one")
    converse(b, "B message one", phone="+15125550188")
    for table in ("messages", "conversations", "customers", "notifications", "usage_events", "processed_events", "audit_log"):
        with dbmod.tenant_session(a) as db:
            rows = db.execute(text(f"SELECT DISTINCT business_id FROM {table}")).scalars().all()  # no WHERE on purpose
            assert set(rows) <= {a}, f"{table} leaked another business"
    with dbmod.tenant_session(a) as db:
        assert db.execute(text("SELECT count(*) FROM businesses")).scalar() == 1


@pg_only
def test_database_refuses_writing_into_another_business(two):
    a, b = two
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        with dbmod.tenant_session(a) as db:
            db.execute(text("INSERT INTO services (id, business_id, name, duration_min, active) VALUES ('x1', :b, 'evil', 60, true)"), {"b": b})


@pg_only
def test_session_without_scope_sees_nothing(two):
    a, b = two
    converse(a, "hello")
    s = dbmod._factory_get()()  # no tenant, no system flag
    try:
        assert s.execute(text("SELECT count(*) FROM messages")).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM businesses")).scalar() == 0
    finally:
        s.close()


@pg_only
def test_database_refuses_double_booking(two):
    a, _ = two
    from sqlalchemy.exc import IntegrityError

    converse(a, "hello")
    cust = conversations(a)[0].customer_id
    start = NOW + timedelta(days=3)
    with dbmod.tenant_session(a) as db:
        db.add(Appointment(business_id=a, customer_id=cust, service_name="x", starts_at=start, ends_at=start + timedelta(hours=1)))
    with pytest.raises(IntegrityError):
        with dbmod.tenant_session(a) as db:
            db.add(Appointment(business_id=a, customer_id=cust, service_name="y", starts_at=start + timedelta(minutes=30), ends_at=start + timedelta(hours=2)))
    with dbmod.tenant_session(a) as db:  # a cancelled one does not block, and back-to-back is fine
        db.add(Appointment(business_id=a, customer_id=cust, service_name="z", starts_at=start + timedelta(hours=1), ends_at=start + timedelta(hours=2)))


@pg_only
def test_app_role_cannot_edit_or_erase_the_audit_log(two):
    from sqlalchemy.exc import DBAPIError

    a, _ = two
    converse(a, "hello")
    with dbmod.tenant_session(a) as db:
        from app.services.audit import audit

        audit(db, actor="test", action="x", business_id=a)
    with pytest.raises(DBAPIError):
        with dbmod.tenant_session(a) as db:
            db.execute(text("DELETE FROM audit_log"))
    with pytest.raises(DBAPIError):
        with dbmod.tenant_session(a) as db:
            db.execute(text("UPDATE audit_log SET action = 'tampered'"))
