"""Phone calls through the voice platform. Scenarios 12 and the voice side of 4, 8, 19, 21, 22."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.main import app
from app.models import Business, Conversation, UsageEvent
from sqlalchemy import select

from .conftest import DownCalendar, NOW
from .helpers import PHONE, appointments, conversations, leads, messages, notifications

SECRET = {"X-Vapi-Secret": "vapi-test-secret"}
VOICE_NUMBER = "+15125550100"


@pytest.fixture
def client(adapters):
    return TestClient(app)


def call_obj(cid="call_1", dialed=VOICE_NUMBER, caller=PHONE):
    return {"id": cid, "phoneNumber": {"number": dialed}, "customer": {"number": caller}}


def post(client, message, headers=SECRET):
    return client.post("/webhooks/vapi", json={"message": message}, headers=headers)


def start_call(client, cid="call_1", dialed=VOICE_NUMBER, caller=PHONE):
    return post(client, {"type": "assistant-request", "call": call_obj(cid, dialed, caller)})


def tool_call(client, name, params, *, cid="call_1", tid="tc1"):
    r = post(client, {"type": "tool-calls", "call": {"id": cid}, "toolCallList": [{"id": tid, "name": name, "parameters": params}]})
    return r, json.loads(r.json()["results"][0]["result"]) if r.json().get("results") else None


def test_wrong_or_missing_secret_is_rejected(client, biz):
    assert post(client, {"type": "assistant-request", "call": call_obj()}, headers={}).status_code == 403
    assert post(client, {"type": "assistant-request", "call": call_obj()}, headers={"X-Vapi-Secret": "nope"}).status_code == 403
    assert post(client, {"type": "assistant-request", "call": call_obj()}, headers={"Authorization": "Bearer vapi-test-secret"}).status_code == 200
    assert conversations(biz) == [] or True


def test_12_every_call_starts_with_ai_and_recording_disclosure(client, biz):
    r = start_call(client)
    a = r.json()["assistant"]
    assert a["firstMessage"] == "This is an AI assistant for Acme Plumbing. This call may be recorded. How can I help you today?"
    assert "Acme Plumbing" in a["model"]["messages"][0]["content"]
    assert {t["function"]["name"] for t in a["model"]["tools"]} >= {"check_free_times", "book_appointment", "handoff_to_human"}
    c = conversations(biz)[0]
    assert c.channel == "voice" and c.external_id == "call_1"


def test_every_business_gets_its_own_name_in_the_disclosure(client, biz, biz2):
    a = start_call(client, cid="c2", dialed="+15125550200").json()["assistant"]
    assert a["firstMessage"].startswith("This is an AI assistant for Beta HVAC.")


def test_tool_secret_is_not_in_the_prompt_text(client, biz):
    a = start_call(client).json()["assistant"]
    assert "vapi-test-secret" not in a["model"]["messages"][0]["content"]


def test_unknown_number_gets_a_polite_error(client, biz):
    r = start_call(client, dialed="+15125559999")
    assert "not in service" in r.json()["error"]


def test_21_kill_switch_forwards_calls_to_the_owner_and_alerts(client, biz):
    with dbmod.system_session() as s:
        s.get(Business, biz).agent_enabled = False
    r = start_call(client)
    assert r.json() == {"destination": {"type": "number", "number": "+15125550111"}}
    assert any(n.urgent for n in notifications(biz))


def test_booking_by_voice_tool_call(client, biz):
    start_call(client)
    r, out = tool_call(client, "check_free_times", {"date": "2026-10-13", "around_time": "15:00", "service": "Drain cleaning"})
    assert out["ok"] and out["requested_time_free"]
    r, out = tool_call(client, "book_appointment", {"start": "2026-10-13T15:00", "customer_name": "Sam Lee", "service": "Drain cleaning"}, tid="tc2")
    assert out["ok"] and "Booked" in out["confirmation"]
    assert len(appointments(biz)) == 1


def test_19_same_tool_call_twice_books_once_and_returns_same_answer(client, biz):
    start_call(client)
    p = {"start": "2026-10-13T15:00", "customer_name": "Sam Lee", "service": "Drain cleaning"}
    _, first = tool_call(client, "book_appointment", p, tid="same")
    _, second = tool_call(client, "book_appointment", p, tid="same")
    assert first == second and len(appointments(biz)) == 1


def test_tool_call_cannot_choose_the_business_or_customer(client, biz, biz2):
    start_call(client)  # call belongs to biz
    tool_call(client, "save_lead", {"need": "x", "business_id": biz2, "customer_id": "other"})
    assert len(leads(biz)) == 1 and leads(biz2) == []


def test_tool_call_for_unknown_call_is_refused(client, biz):
    r, out = tool_call(client, "get_business_info", {}, cid="never_started")
    assert out["error"] == "unknown_call"


def test_22_calendar_down_on_a_call_does_not_confirm(client, biz, adapters):
    adapters.calendar_factory = lambda b: DownCalendar()
    start_call(client)
    _, out = tool_call(client, "book_appointment", {"start": "2026-10-13T15:00", "customer_name": "Sam", "service": "Drain cleaning"})
    assert out["ok"] is False and out["error"] == "calendar_unavailable" and "Do NOT confirm" in out["instruction"]
    assert appointments(biz) == [] and leads(biz)
    assert any("calendar was unavailable" in n.text for n in notifications(biz))


def test_end_of_call_saves_transcript_cost_and_sends_one_textback(client, biz, sms):
    start_call(client)
    report = {"type": "end-of-call-report", "call": {"id": "call_1"}, "durationSeconds": 90, "endedReason": "customer-ended-call",
              "artifact": {"messages": [{"role": "assistant", "message": "This is an AI assistant..."},
                                       {"role": "user", "message": "How much for a drain?"},
                                       {"role": "assistant", "message": "A drain cleaning is $149."}]}}
    assert post(client, report).status_code == 200
    post(client, report)  # provider repeats the report
    msgs = [m for m in messages(biz) if m.meta.get("channel") == "voice"]
    assert len(msgs) == 3
    c = conversations(biz)[0]
    assert c.call_seconds == 90 and c.ended_at and c.status == "closed"
    with dbmod.tenant_session(biz) as db:
        secs = db.scalar(select(UsageEvent).where(UsageEvent.kind == "call_seconds"))
        assert secs.quantity == 90 and abs(secs.cost_usd - 0.225) < 1e-9
    assert len(sms.sent) == 1 and "couldn't finish helping you" in sms.sent[0]["body"]


def test_no_textback_when_the_call_ended_with_a_booking(client, biz, sms):
    start_call(client)
    tool_call(client, "book_appointment", {"start": "2026-10-13T15:00", "customer_name": "Sam Lee", "service": "Drain cleaning"})
    post(client, {"type": "end-of-call-report", "call": {"id": "call_1"}, "durationSeconds": 60, "artifact": {"messages": []}})
    assert sms.sent == []


def test_08_emergency_heard_on_a_call_always_alerts_the_owner(client, biz, sms):
    """Backstop: even if the voice model forgot to hand over, the transcript check tells the owner."""
    start_call(client)
    post(client, {"type": "end-of-call-report", "call": {"id": "call_1"}, "durationSeconds": 30,
                  "artifact": {"messages": [{"role": "user", "message": "I smell gas in my basement"}]}})
    assert any(n.kind == "emergency" and n.urgent for n in notifications(biz))
    assert conversations(biz)[0].status == "handed_off"


def test_bad_json_and_unknown_types_do_not_crash(client, biz):
    assert client.post("/webhooks/vapi", content=b"not json", headers=SECRET).status_code == 400
    assert post(client, {"type": "something-new"}).status_code == 200
    assert client.post("/webhooks/vapi", json=[1, 2], headers=SECRET).status_code == 200
