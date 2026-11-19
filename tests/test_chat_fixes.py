"""Fixes after a real demo chat failed (2026-10-10): the AI failed once and the chat was lost, the AI looped on an
invalid phone number, and one reply had many glued sentences and a phone number the customer never typed."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as dbmod
from app.agent.llm import LLMUnavailable
from app.agent.runner import cut_runaway, unknown_phone_in
from app.main import app
from app.models import AuditLog

from .conftest import say, use_tool
from .helpers import appointments, conversations, converse, notifications


@pytest.fixture
def client(adapters):
    return TestClient(app)


def chat(client, text, session_id=None):
    return client.post("/api/chat/acme/message", json={"text": text, "session_id": session_id}).json()


def fail(reason="groq:429"):
    def _raise(messages):
        raise LLMUnavailable(reason)
    return _raise


def audit_rows(business_id, action):
    with dbmod.tenant_session(business_id) as db:
        return list(db.scalars(select(AuditLog).where(AuditLog.business_id == business_id, AuditLog.action == action)))


# ---- 1. the reason of an AI failure is saved (a short code, never a secret) ----
def test_ai_failure_reason_is_saved_for_the_owner(biz, llm):
    llm.push(fail("groq:429"))
    r = converse(biz, "can you help me with my sink?")
    assert r.rule == "llm_down" and r.handed_off
    assert audit_rows(biz, "llm_unavailable")[0].detail["reason"] == "groq:429"
    assert any("groq:429" in n.text for n in notifications(biz))


def test_ai_failure_reason_is_cut_short_and_cleaned(biz, llm):
    llm.push(fail("groq:400 " + "x" * 300 + "\nBearer gsk_secret"))
    converse(biz, "hello, are you open on Friday?")
    reason = audit_rows(biz, "llm_unavailable")[0].detail["reason"]
    assert len(reason) <= 40 and "gsk_secret" not in reason and "\n" not in reason


# ---- 2. website chat: one failure asks the visitor to send again; a second one hands over ----
def test_web_chat_first_ai_failure_asks_to_send_again(client, biz, llm):
    llm.push(fail(), say("We open at 8 AM."))
    r1 = chat(client, "when do you open?")
    assert r1["handed_off"] is False and "again" in r1["reply"].lower()
    assert conversations(biz)[0].status == "open"
    r2 = chat(client, "when do you open?", r1["session_id"])
    assert r2["reply"] == "We open at 8 AM." and r2["handed_off"] is False


def test_web_chat_second_ai_failure_hands_over_to_a_person(client, biz, llm):
    llm.push(fail(), fail())
    r1 = chat(client, "when do you open?")
    r2 = chat(client, "when do you open?", r1["session_id"])
    assert r2["handed_off"] is True and "technical problem" in r2["reply"]
    assert conversations(biz)[0].handoff_reason == "ai_unavailable"
    assert len(audit_rows(biz, "llm_unavailable")) == 2


def test_text_messages_still_hand_over_on_the_first_ai_failure(biz, llm):
    llm.push(fail())
    r = converse(biz, "can you help me with my sink?", channel="sms")
    assert r.rule == "llm_down" and r.handed_off


# ---- 3. an invalid phone number: one clear question, no loop ----
def test_invalid_phone_ends_the_turn_with_one_clear_question(client, biz, llm):
    llm.push(use_tool("book_appointment", start="2026-10-13T09:00", service="Drain cleaning", customer_name="Ahmed",
                      contact_phone="555-123-4567", phone_confirmed=True))
    r = chat(client, "yes")
    assert len(llm.calls) == 1  # no second, third... try with the same bad number
    assert "valid US phone number" in r["reply"] and r["handed_off"] is False
    assert appointments(biz) == []


# ---- 4. guards on a messy reply ----
def test_cut_runaway_keeps_only_the_first_part_of_glued_sentences():
    messy = ("Thanks, Ahmed. Please provide a phone number for the appointment.Sure, please share your phone number "
             "(digits only).Got it. Just to confirm, is the number you provided 555-123-4567?")
    assert cut_runaway(messy) == "Thanks, Ahmed. Please provide a phone number for the appointment."


@pytest.mark.parametrize("fine", [
    "We open at 8 AM. Drain cleaning is $149.",
    "Our address is 12 Main St. See you soon!",
    "You are booked for Tuesday at 9:00 AM.",
    "Visit example.com for more. Thanks!",
])
def test_cut_runaway_leaves_normal_replies_alone(fine):
    assert cut_runaway(fine) == fine


def test_unknown_phone_in_finds_only_numbers_nobody_gave():
    allowed = "my number is (512) 555-0147\nPhone for people: +15125550111"
    assert unknown_phone_in("Is 512-555-0147 correct?", allowed) is False
    assert unknown_phone_in("Call us at 512-555-0111.", allowed) is False
    assert unknown_phone_in("Is 555-123-4567 correct?", allowed) is True
    assert unknown_phone_in("Tuesday at 9:00 AM for $149, or call 911.", allowed) is False


def test_reply_with_an_invented_phone_number_is_replaced(client, biz, llm):
    llm.push(say("Got it. Just to confirm, is the number you provided 555-123-4567? Please reply yes or no."))
    r = chat(client, "ahmed")
    assert "555-123-4567" not in r["reply"] and "phone number" in r["reply"].lower()


def test_reply_may_repeat_the_number_the_customer_typed(client, biz, llm):
    llm.push(say("Hi! How can I help?"), say("Is 512-555-0147 correct? Please reply yes or no."))
    r1 = chat(client, "hello")
    r2 = chat(client, "512 555 0147", r1["session_id"])
    assert r2["reply"] == "Is 512-555-0147 correct? Please reply yes or no."


def test_reply_may_give_the_business_phone_from_the_facts(biz, llm):
    llm.push(say("You can reach a person at 512-555-0111."))
    r = converse(biz, "what is your phone number?")
    assert "512-555-0111" in r.reply and r.guard is None
