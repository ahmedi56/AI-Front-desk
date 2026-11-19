"""Test setup. Tests run on a real Postgres when TEST_DATABASE_URL is set (this checks the row level
security and the double-booking rule), otherwise on SQLite (faster, but those two database rules are not checked).

No test ever sends a real text, makes a call, or calls the AI: all outside services are replaced by fakes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

os.environ.setdefault("APP_ENV", "test")
os.environ["SECRET_KEY"] = "test-secret-key-test-secret-key-0123456789"
os.environ["ENCRYPTION_KEY"] = "ZmFrZS1mZXJuZXQta2V5LWZvci10ZXN0cy0wMTIzNDU2Nzg="  # replaced below with a valid key
os.environ["TWILIO_AUTH_TOKEN"] = "twilio-test-token"
os.environ["VAPI_WEBHOOK_SECRET"] = "vapi-test-secret"
os.environ["PUBLIC_BASE_URL"] = "https://frontdesk.example.com"
os.environ["GROQ_API_KEY"] = "test"  # never a real key: tests use a scripted fake AI
# The laptop's own .env must not change the tests (a "secure" cookie is never sent back by the plain-http test client).
os.environ["COOKIE_SECURE"] = "false"
os.environ["LLM_PROVIDER"] = "groq"
os.environ["LLM_FALLBACK_PROVIDER"] = ""

from cryptography.fernet import Fernet  # noqa: E402

os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from app import config, db as dbmod, models  # noqa: E402,F401
from app.agent.llm import LLMResult, ToolCall, set_llm  # noqa: E402
from app.migrate import migrate  # noqa: E402
from app.models import Business, Customer, Service, User  # noqa: E402
from app.security import hash_password, reset_rate_limits  # noqa: E402
from app.services.adapters import Adapters, CalendarUnavailable, InternalCalendar, set_adapters  # noqa: E402

TEST_PG = os.environ.get("TEST_DATABASE_URL", "")  # admin address, e.g. postgresql://postgres@127.0.0.1:54329/frontdesk_test
APP_ROLE = "frontdesk_app"


def _reset_postgres(admin_url: str) -> None:
    eng = create_engine(admin_url.replace("postgresql://", "postgresql+psycopg://", 1), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
        exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": APP_ROLE}).first()
        if not exists:
            c.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD 'apppass' NOSUPERUSER NOCREATEDB NOCREATEROLE"))
        c.execute(text(f"GRANT ALL ON SCHEMA public TO {APP_ROLE}"))
    eng.dispose()


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Every test runs at the same moment (Monday 10:00 in Chicago) unless it says otherwise."""
    monkeypatch.setattr(dbmod, "utcnow", lambda: NOW)
    yield


@pytest.fixture(autouse=True)
def database(tmp_path):
    reset_rate_limits()
    config.reset_settings_cache()
    if TEST_PG:
        _reset_postgres(TEST_PG)
        migrate(admin_url=TEST_PG, app_role=APP_ROLE)
        app_url = TEST_PG.replace("//postgres@", f"//{APP_ROLE}:apppass@")
        dbmod.init_engine(app_url)
    else:
        url = f"sqlite:///{tmp_path}/test.db"
        migrate(admin_url=url)
        dbmod.init_engine(url)
    yield
    dbmod.get_engine().dispose()


# ---------- fakes ----------
@dataclass
class FakeSms:
    sent: list[dict] = field(default_factory=list)
    fail: bool = False

    def send(self, *, from_number, to, body):
        if self.fail:
            from app.services.adapters import SmsSendError

            raise SmsSendError("fake failure")
        self.sent.append({"from": from_number, "to": to, "body": body})
        return f"SM{len(self.sent)}"


@dataclass
class FakeEmail:
    sent: list[dict] = field(default_factory=list)

    def send(self, *, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})


class DownCalendar(InternalCalendar):
    def busy(self, start, end):
        raise CalendarUnavailable("down")

    def create_event(self, **kw):
        raise CalendarUnavailable("down")

    def update_event(self, *a, **kw):
        raise CalendarUnavailable("down")

    def delete_event(self, *a, **kw):
        raise CalendarUnavailable("down")


class ScriptedLLM:
    """Plays back prepared answers. Lets tests drive the tools without calling the real AI."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[dict] = []

    def complete(self, *, model, system, messages, tools, max_tokens):
        self.calls.append({"model": model, "system": system, "messages": [dict(m) for m in messages]})
        if not self.script:
            return LLMResult(text="OK", model=model, input_tokens=100, output_tokens=10, content=[{"type": "text", "text": "OK"}])
        item = self.script.pop(0)
        if callable(item):
            item = item(messages)
        return item

    def push(self, *items):
        self.script.extend(items)


def say(text: str, model: str = "claude-haiku-5-5") -> LLMResult:
    return LLMResult(text=text, model=model, input_tokens=1000, output_tokens=50, content=[{"type": "text", "text": text}])


def use_tool(tool_name: str, /, **args) -> LLMResult:
    model = "claude-haiku-5-5"
    name = tool_name
    tid = f"tu_{name}_{abs(hash(str(args))) % 10**6}"
    return LLMResult(text="", model=model, input_tokens=1000, output_tokens=50,
                     tool_calls=[ToolCall(id=tid, name=name, input=args)],
                     content=[{"type": "tool_use", "id": tid, "name": name, "input": args}])


@pytest.fixture
def sms():
    return FakeSms()


@pytest.fixture
def email():
    return FakeEmail()


@pytest.fixture
def llm():
    m = ScriptedLLM()
    set_llm(m)
    yield m
    set_llm(None)


@pytest.fixture
def adapters(sms, email, llm):
    a = Adapters(sms=sms, email=email, llm=llm)
    set_adapters(a)
    yield a
    set_adapters(None)


# A fixed "now": Monday 2026-10-12, 10:00 in Chicago (15:00 UTC)
NOW = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)

HOURS = {d: [["08:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri")} | {"sat": [["09:00", "12:00"]]}


def make_business(name="Acme Plumbing", slug="acme", *, number="+15125550100", sms_number="+15125550199",
                  sms_enabled=True, handoff="+15125550111", services=True, **kw) -> str:
    with dbmod.system_session() as s:
        b = Business(name=name, slug=slug, timezone="America/Chicago", handoff_phone=handoff, owner_email=f"owner@{slug}.test",
                     inbound_number=number, sms_number=sms_number, sms_enabled=sms_enabled, hours=HOURS,
                     facts={"address": "12 Main St, Austin TX", "areas": "Austin and nearby", "faqs": [
                         {"q": "Do you offer free estimates?", "a": "Estimates are free for jobs inside Austin."}]},
                     created_at=NOW - timedelta(days=30), **kw)
        s.add(b)
        s.flush()
        if services:
            s.add(Service(business_id=b.id, name="Drain cleaning", price_text="$149", duration_min=60,
                          description="Clear a blocked drain"))
            s.add(Service(business_id=b.id, name="Water heater install", price_text=None, duration_min=120))
            s.add(Service(business_id=b.id, name="Leak repair", price_text="$95 service call", duration_min=60))
        return b.id


def make_owner(business_id: str, email="owner@acme.test", password="correct horse battery") -> str:
    with dbmod.system_session() as s:
        u = User(business_id=business_id, email=email, password_hash=hash_password(password), role="owner")
        s.add(u)
        s.flush()
        return u.id


@pytest.fixture
def biz(adapters):
    return make_business()


@pytest.fixture
def biz2(adapters):
    return make_business(name="Beta HVAC", slug="beta", number="+15125550200", sms_number="+15125550299", handoff="+15125550222")
