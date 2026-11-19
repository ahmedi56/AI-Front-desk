"""A second, BACKUP AI key per business (for example Groq as main, Gemini as backup; both the client's own free
accounts). When the main AI is busy or down, the business's assistant asks its backup AI."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from app import db as dbmod
from app.agent.llm import LLMResult, LLMUnavailable
from app.main import app
from app.migrate import migrate
from app.models import AuditLog, Business, UsageEvent
from app.services import ai_keys

from .test_dashboard_and_isolation import OWNER_A, OWNER_B, csrf_of, login, make_admin, new_client, two  # noqa: F401
from .helpers import conversations, converse

MAIN_A = "gsk_" + "A" * 48
BACK_A = "AIza" + "a" * 35
MAIN_B = "gsk_" + "B" * 48
BACK_B = "AIza" + "b" * 35
GEMINI_MODEL = "gemini-flash-test"


def set_main(bid, provider="groq", key=MAIN_A, model=""):
    with dbmod.tenant_session(bid) as db:
        ai_keys.set_key(db.get(Business, bid), provider, key, model)


def set_backup(bid, provider="gemini", key=BACK_A, model=GEMINI_MODEL):
    with dbmod.tenant_session(bid) as db:
        ai_keys.set_backup_key(db.get(Business, bid), provider, key, model)


def status(bid) -> dict:
    with dbmod.tenant_session(bid) as db:
        return ai_keys.key_status(db.get(Business, bid))


@pytest.fixture
def fake_ai(monkeypatch, adapters):
    """No network. Records which key every AI call used. Providers listed in `down` fail like a busy service."""
    state = {"used": [], "down": set()}

    class Client:
        def __init__(self, provider, api_key, model):
            self.provider, self.api_key, self.name = provider, api_key, provider

        def complete(self, *, model, system, messages, tools, max_tokens):
            state["used"].append(self.api_key)
            if self.provider in state["down"]:
                raise LLMUnavailable(f"{self.provider}:429")
            text_ = f"answer from {self.provider}"
            return LLMResult(text=text_, model=self.provider + "-model", input_tokens=100, output_tokens=10,
                             content=[{"type": "text", "text": text_}])

    monkeypatch.setattr(ai_keys, "_build_client", lambda provider, api_key, model: Client(provider, api_key, model))
    ai_keys.clear_cache()
    adapters.llm = None  # use the real per-business choice
    yield state
    ai_keys.clear_cache()


# ---------------- storing the backup key ----------------
def test_backup_key_is_stored_encrypted_and_only_the_last_4_are_shown(biz):
    set_main(biz)
    set_backup(biz)
    with dbmod.tenant_session(biz) as db:
        b = db.get(Business, biz)
        assert b.ai_backup_key_enc and BACK_A not in b.ai_backup_key_enc
        assert (b.ai_backup_provider, b.ai_backup_model, b.ai_backup_key_hint) == ("gemini", GEMINI_MODEL, BACK_A[-4:])
    st = status(biz)
    assert st["backup_has_key"] and st["backup_hint"] == BACK_A[-4:] and BACK_A not in str(st) and MAIN_A not in str(st)


def test_backup_needs_a_main_key_first(biz):
    with pytest.raises(ai_keys.KeyError_, match="main"):
        set_backup(biz)


def test_backup_from_the_same_company_as_the_main_key_is_refused(biz):
    set_main(biz, "groq", MAIN_A)
    with pytest.raises(ai_keys.KeyError_, match="different"):
        set_backup(biz, "groq", "gsk_" + "Z" * 48, "")
    assert not status(biz)["backup_has_key"]


def test_paid_or_unknown_providers_and_bad_backup_keys_are_refused(biz):
    set_main(biz)
    for provider in ("anthropic", "openai", ""):
        with pytest.raises(ai_keys.KeyError_):
            set_backup(biz, provider, BACK_A, GEMINI_MODEL)
    for bad in ("short", "has spaces in it 1234567890123456789", "x" * 300):
        with pytest.raises(ai_keys.KeyError_):
            set_backup(biz, "gemini", bad, GEMINI_MODEL)
    with pytest.raises(ai_keys.KeyError_):
        set_backup(biz, "gemini", BACK_A, "")  # Gemini needs the exact model name
    assert not status(biz)["backup_has_key"]


def test_main_key_cannot_be_changed_to_the_backups_company(biz):
    set_main(biz, "groq", MAIN_A)
    set_backup(biz)
    with pytest.raises(ai_keys.KeyError_, match="backup"):
        set_main(biz, "gemini", "AIza" + "m" * 35, GEMINI_MODEL)
    assert status(biz)["provider_id"] == "groq"


def test_removing_the_main_key_also_removes_the_backup(biz):
    set_main(biz)
    set_backup(biz)
    with dbmod.tenant_session(biz) as db:
        ai_keys.clear_key(db.get(Business, biz))
    st = status(biz)
    assert not st["has_key"] and not st["backup_has_key"]


def test_removing_only_the_backup_keeps_the_main_key(biz):
    set_main(biz)
    set_backup(biz)
    with dbmod.tenant_session(biz) as db:
        ai_keys.clear_backup_key(db.get(Business, biz))
    st = status(biz)
    assert st["has_key"] and not st["backup_has_key"]


# ---------------- using it in a chat ----------------
def test_main_answers_when_it_works_and_the_backup_is_not_touched(fake_ai, biz):
    set_main(biz)
    set_backup(biz)
    r = converse(biz, "hello")
    assert r.reply == "answer from groq" and fake_ai["used"] == [MAIN_A]


def test_backup_answers_when_the_main_ai_is_down_and_the_chat_goes_on(fake_ai, biz):
    set_main(biz)
    set_backup(biz)
    fake_ai["down"] = {"groq"}
    r = converse(biz, "hello, what are your hours?")
    assert r.reply == "answer from gemini" and not r.handed_off and r.rule is None
    assert fake_ai["used"] == [MAIN_A, BACK_A]
    assert conversations(biz)[0].status == "open"


def test_answers_from_the_backup_are_counted(fake_ai, biz):
    set_main(biz)
    set_backup(biz)
    converse(biz, "hello")
    fake_ai["down"] = {"groq"}
    converse(biz, "hello again")
    with dbmod.tenant_session(biz) as db:
        kinds = [u.kind for u in db.scalars(select(UsageEvent).where(UsageEvent.business_id == biz))]
    assert kinds.count("llm") == 2 and kinds.count("llm_backup") == 1


def test_both_down_hands_over_and_saves_the_reason(fake_ai, biz):
    set_main(biz)
    set_backup(biz)
    fake_ai["down"] = {"groq", "gemini"}
    r = converse(biz, "can you help me with my sink?")
    assert r.rule == "llm_down" and r.handed_off and fake_ai["used"] == [MAIN_A, BACK_A]
    with dbmod.tenant_session(biz) as db:
        row = db.scalar(select(AuditLog).where(AuditLog.business_id == biz, AuditLog.action == "llm_unavailable"))
    assert row.detail["reason"] == "gemini:429"


def test_both_down_on_the_website_chat_asks_to_send_again_first(fake_ai, biz):
    set_main(biz)
    set_backup(biz)
    fake_ai["down"] = {"groq", "gemini"}
    c = TestClient(app)
    r1 = c.post("/api/chat/acme/message", json={"text": "hello"}).json()
    assert r1["handed_off"] is False and "again" in r1["reply"].lower()
    r2 = c.post("/api/chat/acme/message", json={"text": "hello", "session_id": r1["session_id"]}).json()
    assert r2["handed_off"] is True


def test_without_a_backup_key_nothing_changes(fake_ai, biz):
    set_main(biz)
    fake_ai["down"] = {"groq"}
    r = converse(biz, "can you help me with my sink?")
    assert r.rule == "llm_down" and fake_ai["used"] == [MAIN_A]


def test_a_business_backup_key_is_never_used_for_another_business(fake_ai, biz, biz2):
    set_main(biz, "groq", MAIN_A)
    set_backup(biz, "gemini", BACK_A)
    set_main(biz2, "groq", MAIN_B)
    set_backup(biz2, "gemini", BACK_B)
    fake_ai["down"] = {"groq"}
    converse(biz, "hello from a customer of A")
    converse(biz2, "hello from a customer of B", phone="+15125550177")
    assert fake_ai["used"] == [MAIN_A, BACK_A, MAIN_B, BACK_B]
    # B has no backup: A's backup must not help B
    with dbmod.tenant_session(biz2) as db:
        ai_keys.clear_backup_key(db.get(Business, biz2))
    fake_ai["used"].clear()
    r = converse(biz2, "hello again", phone="+15125550178")
    assert r.rule == "llm_down" and fake_ai["used"] == [MAIN_B]


def test_changing_or_removing_the_backup_takes_effect_at_once(fake_ai, biz):
    set_main(biz)
    set_backup(biz, "gemini", BACK_A)
    fake_ai["down"] = {"groq"}
    converse(biz, "hi")
    set_backup(biz, "gemini", BACK_B)
    converse(biz, "hi again", phone="+15125550155")
    assert fake_ai["used"] == [MAIN_A, BACK_A, MAIN_A, BACK_B]


def test_a_backup_key_that_cannot_be_unlocked_is_skipped_not_fatal(fake_ai, biz):
    set_main(biz)
    set_backup(biz)
    with dbmod.tenant_session(biz) as db:
        db.get(Business, biz).ai_backup_key_enc = "not-a-real-locked-value"
    ai_keys.clear_cache()
    assert converse(biz, "hello").reply == "answer from groq"


# ---------------- Settings page ----------------
def save_backup(c, token, **changes):
    data = {"provider": "gemini", "api_key": BACK_A, "model": GEMINI_MODEL, "csrf_token": token, **changes}
    return c.post("/settings/ai/backup", data=data, follow_redirects=True)


def test_owner_saves_and_removes_a_backup_key_in_settings(two, caplog):
    a, b = two
    caplog.set_level(logging.DEBUG)
    set_main(a)
    c = login(new_client(), OWNER_A)
    token = csrf_of(c, "/settings")
    assert "Backup AI key" in c.get("/settings").text
    r = save_backup(c, token)
    assert r.status_code == 200 and "Backup AI key saved" in r.text
    assert BACK_A not in r.text and BACK_A[-4:] in r.text and BACK_A not in caplog.text
    assert not status(b)["backup_has_key"]  # business B is untouched
    r = c.post("/settings/ai/backup/remove", data={"csrf_token": token}, follow_redirects=True)
    assert "Backup AI key removed" in r.text and not status(a)["backup_has_key"] and status(a)["has_key"]


def test_settings_refuses_a_bad_backup_with_a_clear_message(two):
    a, _ = two
    set_main(a)
    c = login(new_client(), OWNER_A)
    token = csrf_of(c, "/settings")
    assert "different company" in save_backup(c, token, provider="groq", api_key="gsk_" + "Z" * 48, model="").text
    assert "free" in save_backup(c, token, provider="anthropic").text.lower()
    assert not status(a)["backup_has_key"]


def test_backup_forms_need_the_csrf_token_and_a_login(two):
    a, _ = two
    set_main(a)
    c = login(new_client(), OWNER_A)
    assert c.post("/settings/ai/backup", data={"provider": "gemini", "api_key": BACK_A, "model": GEMINI_MODEL}).status_code == 403
    assert c.post("/settings/ai/backup/remove", data={}).status_code == 403
    r = new_client().post("/settings/ai/backup", data={"provider": "gemini", "api_key": BACK_A, "model": GEMINI_MODEL})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert not status(a)["backup_has_key"]


def test_one_owner_cannot_set_another_business_backup_key(two):
    a, b = two
    set_main(a)
    set_main(b, "groq", MAIN_B)
    c = login(new_client(), OWNER_B)
    save_backup(c, csrf_of(c, "/settings"), api_key=BACK_B)
    assert status(b)["backup_hint"] == BACK_B[-4:] and not status(a)["backup_has_key"]


def test_no_backup_form_before_a_main_key_exists(two):
    c = login(new_client(), OWNER_A)
    html = c.get("/settings").text
    assert 'action="/settings/ai/backup"' not in html


def test_costs_page_shows_how_often_the_backup_answered(two, fake_ai):
    a, _ = two
    set_main(a)
    set_backup(a)
    fake_ai["down"] = {"groq"}
    converse(a, "hello")
    c = login(new_client(), OWNER_A)
    assert "Answers from the backup AI" in c.get("/usage").text


def test_admin_sees_a_backup_tag_but_never_the_key(two):
    a, _ = two
    set_main(a)
    set_backup(a)
    make_admin()
    c = login(new_client(), ("admin@platform.test", "admin password long 99"))
    html = c.get("/admin").text
    assert "backup key ..." + BACK_A[-4:] in html and BACK_A not in html


# ---------------- old databases ----------------
def test_old_database_gets_the_backup_fields_without_losing_data(tmp_path):
    url = f"sqlite:///{tmp_path}/old.db"
    migrate(admin_url=url)
    eng = create_engine(url)
    with eng.begin() as c:  # today's tables minus the 4 new fields = a database from before this version
        for col in ("ai_backup_provider", "ai_backup_key_enc", "ai_backup_model", "ai_backup_key_hint"):
            c.execute(text(f"ALTER TABLE businesses DROP COLUMN {col}"))
        c.execute(text("INSERT INTO businesses (id, name, slug, timezone, sms_enabled, agent_enabled, quiet_start, quiet_end, "
                       "retention_days, hours, facts, slot_minutes, min_notice_minutes, reminder_hours, followup_delay_hours, "
                       "textback_enabled, textback_respects_quiet_hours, report_hour, calendar_kind, ai_provider, ai_key_hint, "
                       "created_at) VALUES ('b1', 'Old Co', 'old-co', 'America/Chicago', 0, 1, 21, 8, 90, '{}', '{}', 30, 60, "
                       "24, 4, 1, 1, 18, 'internal', 'groq', 'AAAA', '2026-10-01 00:00:00')"))
    eng.dispose()
    migrate(admin_url=url)
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("businesses")}
    assert {"ai_backup_provider", "ai_backup_key_enc", "ai_backup_model", "ai_backup_key_hint"} <= cols
    with create_engine(url).connect() as c:
        row = c.execute(text("SELECT name, ai_provider, ai_key_hint, ai_backup_provider FROM businesses WHERE id='b1'")).one()
        assert tuple(row) == ("Old Co", "groq", "AAAA", None), "no data lost, the main key is kept, no backup yet"
