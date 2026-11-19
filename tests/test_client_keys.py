"""Per-client AI keys: each business uses ITS OWN free AI account. Keys are stored encrypted, never shown, never mixed."""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine, inspect, text

from app import db as dbmod
from app.config import reset_settings_cache
from app.models import Business
from app.services import ai_keys

from .conftest import ScriptedLLM, say
from .helpers import converse, notifications
from .test_dashboard_and_isolation import OWNER_A, OWNER_B, csrf_of, login, new_client, two  # noqa: F401  (fixtures)

KEY_A = "gsk_" + "A" * 48
KEY_B = "gsk_" + "B" * 48


def _set(bid: str, provider: str, key: str, model: str = "") -> None:
    with dbmod.tenant_session(bid) as db:
        ai_keys.set_key(db.get(Business, bid), provider, key, model)


@pytest.fixture
def fake_clients(monkeypatch, adapters):
    """Record which key each AI call used, without any network."""
    used: list[str] = []

    def build(provider, api_key, model):
        used.append(api_key)
        m = ScriptedLLM()
        m.push(say(f"answer with {provider}"))
        return m

    monkeypatch.setattr(ai_keys, "_build_client", build)
    ai_keys.clear_cache()
    adapters.llm = None  # use the real per-business choice, not the test's shared fake
    yield used
    ai_keys.clear_cache()


def test_key_is_stored_encrypted_and_only_the_last_4_are_shown(biz):
    _set(biz, "groq", KEY_A)
    with dbmod.tenant_session(biz) as db:
        b = db.get(Business, biz)
        assert b.ai_key_enc and KEY_A not in b.ai_key_enc
        assert b.ai_provider == "groq" and b.ai_key_hint == KEY_A[-4:]
        st = ai_keys.key_status(b)
        assert st["has_key"] and KEY_A not in str(st)


def test_paid_or_unknown_providers_and_bad_keys_are_refused(biz):
    for provider in ("anthropic", "openai", ""):
        with pytest.raises(ai_keys.KeyError_):
            _set(biz, provider, KEY_A)
    for bad in ("short", "has spaces in it 1234567890123456789", "x" * 300):
        with pytest.raises(ai_keys.KeyError_):
            _set(biz, "groq", bad)
    with pytest.raises(ai_keys.KeyError_):
        _set(biz, "gemini", "AIza" + "x" * 35, model="")  # Gemini needs the exact model name


def test_each_business_uses_its_own_key(fake_clients, biz, biz2):
    _set(biz, "groq", KEY_A)
    _set(biz2, "groq", KEY_B)
    converse(biz, "hello from a customer of A")
    converse(biz2, "hello from a customer of B", phone="+15125550177")
    assert fake_clients == [KEY_A, KEY_B]


def test_changing_the_key_takes_effect_at_once(fake_clients, biz):
    _set(biz, "groq", KEY_A)
    converse(biz, "hi")
    _set(biz, "groq", KEY_B)
    converse(biz, "hi again")
    assert fake_clients == [KEY_A, KEY_B]


def test_without_a_key_and_shared_key_off_the_owner_gets_the_contact_and_one_alert(fake_clients, biz, monkeypatch):
    monkeypatch.setenv("ALLOW_SHARED_AI_KEY", "false")
    reset_settings_cache()
    r1 = converse(biz, "I need a plumber")
    r2 = converse(biz, "hello?", phone="+15125550199")
    assert r1.rule == r2.rule == "no_ai_key" and fake_clients == []
    assert "person" in r1.reply.lower()
    assert len([n for n in notifications(biz) if "AI key" in n.text]) == 1


def test_shared_key_is_allowed_on_a_laptop_by_default_but_not_in_production(monkeypatch):
    monkeypatch.delenv("ALLOW_SHARED_AI_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    reset_settings_cache()
    from app.config import get_settings

    assert get_settings().shared_ai_key_ok is True
    monkeypatch.setenv("APP_ENV", "production")
    reset_settings_cache()
    assert get_settings().shared_ai_key_ok is False
    monkeypatch.setenv("APP_ENV", "test")
    reset_settings_cache()


def test_key_never_appears_in_pages_or_logs(two, caplog):
    caplog.set_level(logging.DEBUG)
    c = login(new_client(), OWNER_A)
    token = csrf_of(c, "/settings")
    r = c.post("/settings/ai", data={"provider": "groq", "api_key": KEY_A, "model": "", "csrf_token": token}, follow_redirects=True)
    assert r.status_code == 200 and "AI key saved" in r.text
    assert KEY_A not in r.text and KEY_A[-4:] in r.text
    assert KEY_A not in caplog.text
    # business B is untouched
    b2 = login(new_client(), OWNER_B)
    assert "No AI key yet" in b2.get("/settings").text


def test_owner_can_remove_the_key(two):
    c = login(new_client(), OWNER_A)
    token = csrf_of(c, "/settings")
    c.post("/settings/ai", data={"provider": "groq", "api_key": KEY_A, "model": "", "csrf_token": token})
    r = c.post("/settings/ai/remove", data={"csrf_token": token}, follow_redirects=True)
    assert "No AI key yet" in r.text


def test_bad_key_shows_a_clear_message_and_saves_nothing(two):
    c = login(new_client(), OWNER_A)
    token = csrf_of(c, "/settings")
    r = c.post("/settings/ai", data={"provider": "anthropic", "api_key": KEY_A, "model": "", "csrf_token": token}, follow_redirects=True)
    assert "No AI key yet" in r.text and "free" in r.text.lower()


def test_ai_key_form_needs_the_csrf_token(two):
    c = login(new_client(), OWNER_A)
    r = c.post("/settings/ai", data={"provider": "groq", "api_key": KEY_A, "model": ""})
    assert r.status_code == 403


def test_migrate_adds_new_columns_to_an_old_database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/old.db"
    eng = create_engine(url)
    from app.db import Base

    Base.metadata.create_all(eng)  # today's tables...
    with eng.begin() as c:  # ...minus the 4 new fields = the database on the owner's laptop before this version
        for col in ("ai_provider", "ai_key_enc", "ai_model", "ai_key_hint"):
            c.execute(text(f"ALTER TABLE businesses DROP COLUMN {col}"))
        c.execute(text("INSERT INTO businesses (id, name, slug, timezone, sms_enabled, agent_enabled, quiet_start, quiet_end, "
                       "retention_days, hours, facts, slot_minutes, min_notice_minutes, reminder_hours, followup_delay_hours, "
                       "textback_enabled, textback_respects_quiet_hours, report_hour, calendar_kind, created_at) VALUES "
                       "('x', 'Old Co', 'old', 'America/Chicago', 0, 1, 21, 8, 90, '{}', '{}', 30, 60, 24, 4, 1, 1, 18, "
                       "'internal', '2026-10-01 00:00:00')"))
    from app.migrate import migrate

    migrate(admin_url=url)
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("businesses")}
    assert {"ai_provider", "ai_key_enc", "ai_model", "ai_key_hint"} <= cols
    with create_engine(url).begin() as c:
        assert c.execute(text("SELECT name FROM businesses WHERE id='x'")).scalar() == "Old Co", "no data lost"
