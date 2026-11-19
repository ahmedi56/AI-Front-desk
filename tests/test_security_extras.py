"""Extra protections found by attacking the first version of the code."""
from __future__ import annotations

import re
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.config import reset_settings_cache
from app.main import app
from app.models import Business, Customer, Notification, UsageEvent, User
from app.security import _totp_at, decrypt_text, encrypt_text, hash_password, totp_new_secret, totp_verify

from .conftest import NOW, make_business, make_owner, say, use_tool
from .helpers import PHONE, appointments, conversations, converse, notifications
from .test_dashboard_and_isolation import OWNER_A, csrf_of, login, new_client, two  # noqa: F401  (fixtures)
from .test_sms_channel import post_sms  # noqa: F401


# ---------- 2-step login ----------
def test_totp_code_math_matches_the_standard():
    # RFC 6238 test vector: secret "12345678901234567890", time 59 s -> 94287082 (8 digits); our 6-digit form is its last 6 digits
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert _totp_at(secret, 59 // 30, digits=8) == "94287082"
    assert _totp_at(secret, 59 // 30) == "287082"


def test_totp_rejects_reuse_and_wrong_codes():
    secret = totp_new_secret()
    now = 1_700_000_000
    code = _totp_at(secret, now // 30)
    step = totp_verify(secret, code, now=now)
    assert step == now // 30
    assert totp_verify(secret, code, now=now, last_step=step) is None, "the same code twice must fail"
    assert totp_verify(secret, "000000", now=now) is None or _totp_at(secret, now // 30) == "000000"
    assert totp_verify(secret, "abc", now=now) is None and totp_verify(secret, None, now=now) is None
    assert totp_verify(secret, _totp_at(secret, now // 30 + 5), now=now) is None, "codes from far in the future fail"


def test_owner_can_turn_on_2fa_and_login_then_needs_the_code(two):
    c = login(new_client(), OWNER_A)
    token = csrf_of(c, "/account")
    c.post("/account/2fa/start", data={"csrf_token": token})
    secret = re.search(r"<code>([A-Z2-7]{16,})</code>", c.get("/account").text).group(1)
    wrong = c.post("/account/2fa/confirm", data={"code": "123456", "csrf_token": token})
    assert wrong.status_code == 200 and "not right" in wrong.text
    code = _totp_at(secret, int(time.time() // 30))
    assert c.post("/account/2fa/confirm", data={"code": code, "csrf_token": token}).status_code == 303
    # fresh login: password alone is no longer enough
    c2 = new_client()
    assert c2.post("/login", data={"email": OWNER_A[0], "password": OWNER_A[1]}).status_code == 401
    assert c2.post("/login", data={"email": OWNER_A[0], "password": OWNER_A[1], "code": "000000"}).status_code == 401
    # (the code just used cannot be used again, so wait for the next one in a real life; here we use the next step)
    nxt = _totp_at(secret, int(time.time() // 30) + 1)
    assert c2.post("/login", data={"email": OWNER_A[0], "password": OWNER_A[1], "code": nxt}).status_code == 303


def test_admin_must_use_2fa_when_required(adapters, monkeypatch):
    monkeypatch.setenv("REQUIRE_ADMIN_2FA", "true")
    reset_settings_cache()
    with dbmod.system_session() as s:
        s.add(User(email="admin@platform.test", password_hash=hash_password("admin password long 99"), role="admin"))
    c = login(new_client(), ("admin@platform.test", "admin password long 99"))
    assert c.get("/admin").headers["location"] == "/account"
    token = csrf_of(c, "/account")
    c.post("/account/2fa/start", data={"csrf_token": token})
    secret = re.search(r"<code>([A-Z2-7]{16,})</code>", c.get("/account").text).group(1)
    c.post("/account/2fa/confirm", data={"code": _totp_at(secret, int(time.time() // 30)), "csrf_token": token})
    assert c.get("/admin").status_code == 200
    r = c.post("/account/2fa/disable", data={"password": "admin password long 99", "code": "123456", "csrf_token": token})
    assert "must keep 2-step login on" in r.text


# ---------- sessions ----------
def test_logging_out_kills_the_cookie_on_the_server(two):
    c = login(new_client(), OWNER_A)
    old = c.cookies.get("fd_session")
    token = csrf_of(c, "/account")
    c.post("/logout", data={"csrf_token": token})
    thief = new_client()
    thief.cookies.set("fd_session", old)
    assert thief.get("/").status_code == 303


def test_password_change_logs_out_other_devices(two):
    a = login(new_client(), OWNER_A)
    b = login(new_client(), OWNER_A)
    token = csrf_of(a, "/account")
    a.post("/account/password", data={"current_password": OWNER_A[1], "new_password": "brand new long password", "confirm_password": "brand new long password", "csrf_token": token})
    assert b.get("/").status_code == 303 and a.get("/").status_code == 200


# ---------- money and floods ----------
def test_daily_ai_budget_stops_the_agent_and_alerts_once(biz, llm, monkeypatch):
    monkeypatch.setenv("MAX_DAILY_AI_COST_PER_BUSINESS_USD", "0.01")
    reset_settings_cache()
    with dbmod.tenant_session(biz) as db:
        db.add(UsageEvent(business_id=biz, kind="llm", quantity=1, cost_usd=0.02, created_at=NOW - timedelta(hours=1)))
    r1 = converse(biz, "hello, I need a plumber", phone="+15125550101")
    r2 = converse(biz, "hello again", phone="+15125550102")
    assert r1.rule == r2.rule == "daily_limit" and not llm.calls
    assert len([n for n in notifications(biz) if "daily AI limit" in n.text]) == 1


def test_one_chat_cannot_flood_the_owner_with_urgent_alerts(biz, sms):
    for i in range(10):
        converse(biz, f"I smell gas {i}")
    urgent_sent = [n for n in notifications(biz) if n.urgent and n.delivered_via != "stored_only(limit)"]
    assert len(urgent_sent) == 3
    assert len(notifications(biz)) == 10, "all are still saved for the owner to see"


def test_web_visitor_phone_is_never_used_for_automatic_texts(biz, llm, sms, adapters):
    from app.services import automations
    from tests.test_misc import chat  # noqa: F401

    client = TestClient(app)
    llm.push(use_tool("book_appointment", start="2026-10-14T10:00", customer_name="Eve", service="Drain cleaning", contact_phone="512-555-0188", phone_confirmed=True),
             say("Booked Wednesday 10 AM."))
    client.post("/api/chat/acme/message", json={"text": "book Wednesday 10, my number is 512-555-0188"})
    appt = appointments(biz)[0]
    assert "512" in appt.notes or "+15125550188" in appt.notes, "the owner sees the number"
    with dbmod.tenant_session(biz) as db:
        assert db.query(Customer).filter(Customer.phone == "+15125550188").count() == 0, "but it is not stored as a verified customer phone"
        b = db.get(Business, biz)
        assert automations.send_reminders(db, b, appt.starts_at - timedelta(hours=1), adapters) == 0
    assert sms.sent == []


def test_textback_not_sent_to_someone_who_sent_stop(biz, sms, adapters):
    from app.services import automations
    from app.services.conversations import get_or_create_customer, new_conversation

    with dbmod.tenant_session(biz) as db:
        b = db.get(Business, biz)
        c = get_or_create_customer(db, b, PHONE, NOW)
        c.sms_opt_out = True
        conv = new_conversation(db, b, c, "voice", NOW - timedelta(minutes=5), external_id="callx")
        conv.ended_at = NOW - timedelta(minutes=4)
        assert automations.send_missed_call_textbacks(db, b, NOW, adapters) == 0
    assert sms.sent == []


def test_database_errors_do_not_print_customer_text(biz):
    from sqlalchemy.exc import IntegrityError

    secret_text = "my secret address is 5 Elm Street"
    with pytest.raises(IntegrityError) as exc:
        with dbmod.tenant_session(biz) as db:
            from app.models import Message

            db.add(Message(business_id=biz, conversation_id=None, role="customer", text=secret_text))
            db.flush()
    assert secret_text not in str(exc.value)


def test_encrypt_roundtrip_uses_fresh_randomness():
    a, b = encrypt_text("x"), encrypt_text("x")
    assert a != b and decrypt_text(a) == decrypt_text(b) == "x"
