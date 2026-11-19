"""Text messages: signatures, STOP/HELP, duplicates, quiet hours, follow-ups, reminders, reviews.
Scenarios 16, 17, 18, 19, 26."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.main import app
from app.models import Appointment, Customer
from app.security import twilio_signature
from app.services import automations

from .conftest import NOW, say
from .helpers import PHONE, appointments, conversations, converse, messages, notifications

URL = "https://frontdesk.example.com/webhooks/twilio/sms"
TOKEN = "twilio-test-token"
BIZ_SMS = "+15125550199"


@pytest.fixture
def client(adapters):
    return TestClient(app)


def post_sms(client, body, *, sid="SM1", frm=PHONE, to=BIZ_SMS, sign=True, token=TOKEN):
    params = {"MessageSid": sid, "From": frm, "To": to, "Body": body}
    headers = {"X-Twilio-Signature": twilio_signature(URL, params, token)} if sign else {}
    return client.post("/webhooks/twilio/sms", data=params, headers=headers)


def test_forged_webhook_is_rejected_and_stores_nothing(client, biz, sms):
    r = post_sms(client, "hi", sign=False)
    assert r.status_code == 403
    r = post_sms(client, "hi", token="wrong-token")
    assert r.status_code == 403
    assert messages(biz) == [] and sms.sent == []


def test_signature_must_cover_the_message_body(client, biz, sms):
    params = {"MessageSid": "SM9", "From": PHONE, "To": BIZ_SMS, "Body": "hello"}
    sig = twilio_signature(URL, params, TOKEN)
    params["Body"] = "tampered"
    assert client.post("/webhooks/twilio/sms", data=params, headers={"X-Twilio-Signature": sig}).status_code == 403


def test_normal_text_gets_a_reply_from_the_business_number(client, biz, sms, llm):
    llm.push(say("We open at 8 AM."))
    assert post_sms(client, "when do you open?").status_code == 200
    assert len(sms.sent) == 1
    assert sms.sent[0]["to"] == PHONE and sms.sent[0]["from"] == BIZ_SMS and sms.sent[0]["body"] == "We open at 8 AM."
    roles = [m.role for m in sorted(messages(biz), key=lambda m: m.created_at)]
    assert roles.count("customer") == 1 and roles.count("agent") == 1


# ---- 19: same notice twice ----
def test_19_duplicate_webhook_does_the_work_once(client, biz, sms, llm):
    llm.push(use := say("You're booked."))
    for _ in range(3):
        post_sms(client, "hello there", sid="SMDUP")
    assert len(sms.sent) == 1
    assert len([m for m in messages(biz) if m.role == "customer"]) == 1


def test_19b_duplicate_booking_request_makes_one_booking(client, biz, sms, llm):
    from .conftest import use_tool

    script = [use_tool("book_appointment", start="2026-10-13T15:00", customer_name="Sam Lee", service="Drain cleaning"),
              say("Booked for Tuesday 3 PM.")]
    llm.push(*script)
    post_sms(client, "book me Tuesday 3pm, Sam Lee", sid="SMA")
    llm.push(*script)
    post_sms(client, "book me Tuesday 3pm, Sam Lee", sid="SMA")  # same sid: provider retry
    assert len(appointments(biz)) == 1


# ---- 16: STOP ----
def test_16_stop_sends_one_confirmation_then_silence(client, biz, sms, llm):
    post_sms(client, "STOP", sid="S1")
    assert len(sms.sent) == 1 and "unsubscribed" in sms.sent[0]["body"]
    with dbmod.tenant_session(biz) as db:
        c = db.query(Customer).filter_by(business_id=biz, phone=PHONE).one()
        assert c.sms_opt_out and c.opt_out_at
    post_sms(client, "hello?", sid="S2")
    post_sms(client, "book me a time", sid="S3")
    assert len(sms.sent) == 1, "after STOP nothing more may be sent"
    assert not llm.calls


@pytest.mark.parametrize("word", ["stop", "Stop.", "UNSUBSCRIBE", "end", "quit", "cancel", "STOPALL"])
def test_16b_all_standard_stop_words_work(client, biz, sms, word):
    post_sms(client, word, sid="S1")
    assert "unsubscribed" in sms.sent[0]["body"]


def test_16c_cancel_my_appointment_is_not_a_stop(client, biz, sms, llm):
    llm.push(say("Sure, which appointment?"))
    post_sms(client, "cancel my appointment please", sid="S1")
    assert "unsubscribed" not in sms.sent[0]["body"]


def test_16d_start_turns_texts_back_on(client, biz, sms, llm):
    post_sms(client, "STOP", sid="S1")
    post_sms(client, "START", sid="S2")
    assert "back on" in sms.sent[-1]["body"]
    llm.push(say("Hi! How can I help?"))
    post_sms(client, "hi", sid="S3")
    assert sms.sent[-1]["body"] == "Hi! How can I help?"


# ---- 17: HELP ----
def test_17_help_sends_help_message(client, biz, sms, llm):
    post_sms(client, "HELP", sid="H1")
    body = sms.sent[0]["body"]
    assert "Acme Plumbing" in body and "STOP" in body and "+15125550111" in body
    assert not llm.calls


def test_sms_not_enabled_business_never_sends(client, adapters, sms):
    from .conftest import make_business

    bid = make_business(name="Gamma", slug="gamma", number="+15125550300", sms_number="+15125550399", sms_enabled=False)
    post_sms(client, "hello", to="+15125550399", sid="G1")
    assert sms.sent == []
    assert messages(bid), "the message is still saved for the owner"


def test_unknown_number_is_ignored(client, biz, sms):
    assert post_sms(client, "hi", to="+15125559999").status_code == 200
    assert sms.sent == []


def test_failed_processing_alerts_the_owner(client, biz, sms, adapters):
    class Boom:
        def complete(self, **kw):
            raise RuntimeError("bug")

    adapters.llm = Boom()
    r = post_sms(client, "hello", sid="B1")
    assert r.status_code == 200
    assert any("could not answer a text" in n.text for n in notifications(biz))
    assert any(m.role == "customer" for m in messages(biz)), "the customer's text must not be lost"


def test_flood_is_limited(client, biz, sms, llm):
    for i in range(30):
        post_sms(client, f"hello {i}", sid=f"F{i}")
    assert len(sms.sent) <= 20


# ---- 18: quiet hours for follow-ups ----
def test_18_followup_waits_until_8am_local(biz, sms, llm, adapters):
    quiet_time = datetime(2026, 10, 13, 2, 30, tzinfo=timezone.utc)  # 9:30 pm in Chicago (CDT)
    llm.push(say("Sure, what day works for you?"))
    converse(biz, "I need a drain cleaned", now=quiet_time)
    sms.sent.clear()

    def tick(at):
        with dbmod.tenant_session(biz) as db:
            from app.models import Business

            return automations.run_followups(db, db.get(Business, biz), at, adapters)

    assert tick(quiet_time + timedelta(hours=4)) == 0, "01:30 local is quiet time"
    assert tick(datetime(2026, 10, 13, 12, 59, tzinfo=timezone.utc)) == 0, "7:59 local: still quiet"
    assert tick(datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)) == 1, "8:00 local: send"
    assert len(sms.sent) == 1 and "Would you still like help" in sms.sent[0]["body"]


def test_followups_stop_after_two(biz, sms, llm, adapters):
    llm.push(say("What day works?"))
    converse(biz, "need help with a leak")
    sms.sent.clear()
    from app.models import Business

    sent = 0
    t = NOW + timedelta(hours=5)
    for _ in range(6):
        with dbmod.tenant_session(biz) as db:
            sent += automations.run_followups(db, db.get(Business, biz), t, adapters)
        t += timedelta(hours=25)
    assert sent == 2 and len(sms.sent) == 2
    assert "STOP" in sms.sent[1]["body"]


def test_no_followup_to_someone_who_sent_stop(biz, sms, llm, adapters):
    llm.push(say("What day works?"))
    converse(biz, "need help with a leak")
    with dbmod.tenant_session(biz) as db:
        db.query(Customer).filter_by(business_id=biz).one().sms_opt_out = True
    sms.sent.clear()
    from app.models import Business

    with dbmod.tenant_session(biz) as db:
        assert automations.run_followups(db, db.get(Business, biz), NOW + timedelta(hours=6), adapters) == 0
    assert sms.sent == []


# ---- reminders ----
def _add_appt(biz, start, *, created_at=None, status="booked", phone=PHONE):
    from app.services.conversations import get_or_create_customer
    from app.models import Business

    with dbmod.tenant_session(biz) as db:
        b = db.get(Business, biz)
        c = get_or_create_customer(db, b, phone, NOW)
        a = Appointment(business_id=biz, customer_id=c.id, service_name="Drain cleaning", starts_at=start,
                        ends_at=start + timedelta(hours=1), status=status, created_at=created_at or NOW - timedelta(days=3))
        db.add(a)
        db.flush()
        return a.id


def test_reminder_sent_once_inside_the_window(biz, sms, adapters):
    from app.models import Business

    start = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)  # Tue 3 pm local
    _add_appt(biz, start)

    def tick(at):
        with dbmod.tenant_session(biz) as db:
            return automations.send_reminders(db, db.get(Business, biz), at, adapters)

    assert tick(start - timedelta(hours=30)) == 0
    assert tick(start - timedelta(hours=23)) == 1
    assert tick(start - timedelta(hours=22)) == 0
    assert "Reminder from Acme Plumbing" in sms.sent[0]["body"] and "Tuesday, October 13 at 3:00 PM" in sms.sent[0]["body"]


def test_reminder_not_sent_in_quiet_hours(biz, sms, adapters):
    from app.models import Business

    start = datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc)  # Tue 8:30 am local; 24h before = Mon 8:30 am: fine
    start2 = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)  # Tue 2 am local (odd but possible)
    _add_appt(biz, start2)
    with dbmod.tenant_session(biz) as db:
        # 24h before is Mon 2 am local = quiet. Check at that moment: nothing is sent
        assert automations.send_reminders(db, db.get(Business, biz), start2 - timedelta(hours=24), adapters) == 0
    assert sms.sent == []


# ---- 26: review request ----
def test_26_review_request_once_after_24h_never_after_stop(biz, sms, adapters):
    from app.models import Business

    with dbmod.tenant_session(biz) as db:
        db.get(Business, biz).review_link = "https://g.page/r/acme"
    start = NOW - timedelta(days=2)
    a1 = _add_appt(biz, start, status="completed", phone="+15125550147")
    a2 = _add_appt(biz, start, status="completed", phone="+15125550188")
    with dbmod.tenant_session(biz) as db:
        db.query(Customer).filter_by(business_id=biz, phone="+15125550188").one().sms_opt_out = True
    # too early: only 10 hours after the end of the visit
    with dbmod.tenant_session(biz) as db:
        assert automations.send_review_requests(db, db.get(Business, biz), start + timedelta(hours=11), adapters) == 0
    # more than 24 h after
    with dbmod.tenant_session(biz) as db:
        assert automations.send_review_requests(db, db.get(Business, biz), NOW, adapters) == 1
    with dbmod.tenant_session(biz) as db:
        assert automations.send_review_requests(db, db.get(Business, biz), NOW + timedelta(hours=2), adapters) == 0
    assert [s["to"] for s in sms.sent] == ["+15125550147"]
    assert "https://g.page/r/acme" in sms.sent[0]["body"]


def test_completed_visits_are_marked_automatically(biz, adapters):
    from app.models import Business

    a = _add_appt(biz, NOW - timedelta(hours=5))
    with dbmod.tenant_session(biz) as db:
        assert automations.auto_complete(db, db.get(Business, biz), NOW) == 1
        assert db.get(Appointment, a).status == "completed"


# ---- missed-call text-back ----
def test_missed_call_textback_respects_rules(biz, sms, adapters):
    from app.models import Business, Conversation
    from app.services.conversations import get_or_create_customer, new_conversation

    with dbmod.tenant_session(biz) as db:
        b = db.get(Business, biz)
        c = get_or_create_customer(db, b, PHONE, NOW)
        conv = new_conversation(db, b, c, "voice", NOW - timedelta(minutes=5), external_id="call_1")
        conv.ended_at = NOW - timedelta(minutes=4)
        assert automations.send_missed_call_textbacks(db, b, NOW, adapters) == 1
        assert automations.send_missed_call_textbacks(db, b, NOW, adapters) == 0, "only once"
    assert "couldn't finish helping you" in sms.sent[0]["body"]


def test_textback_waits_for_morning(biz, sms, adapters):
    from app.models import Business
    from app.services.conversations import get_or_create_customer, new_conversation

    night = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)  # midnight local
    with dbmod.tenant_session(biz) as db:
        b = db.get(Business, biz)
        c = get_or_create_customer(db, b, PHONE, night)
        conv = new_conversation(db, b, c, "voice", night, external_id="call_2")
        conv.ended_at = night
        assert automations.send_missed_call_textbacks(db, b, night, adapters) == 0
        assert automations.send_missed_call_textbacks(db, b, night + timedelta(hours=8), adapters) == 1
