"""The weekly email report to the client: Monday 8 AM (business time), about the week before. Numbers only."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, inspect, text

from app import db as dbmod
from app.models import Appointment, Business, Lead, Notification
from app.services import automations
from app.services.conversations import get_or_create_customer, new_conversation

from .conftest import NOW  # Monday 2026-10-12, 10:00 in Chicago
from .helpers import notifications
from .test_dashboard_and_isolation import OWNER_A, csrf_of, login, new_client, two  # noqa: F401  (fixtures)

CHI = ZoneInfo("America/Chicago")
_phone = iter(range(1000, 9999))


def at(day: int, hour: int, minute: int = 0, tz=CHI) -> datetime:
    """A moment in October 2026, written in the business's local time."""
    return datetime(2026, 10, day, hour, minute, tzinfo=tz).astimezone(timezone.utc)


def chat(bid: str, when: datetime, channel="web", status="open", reason=None):
    with dbmod.tenant_session(bid) as db:
        b = db.get(Business, bid)
        cust = get_or_create_customer(db, b, f"+1512555{next(_phone)}", when)
        conv = new_conversation(db, b, cust, channel, when, external_id=f"x{next(_phone)}")
        conv.status, conv.handoff_reason = status, reason
        return conv.id, cust.id


def week_of_work(bid: str) -> None:
    """Last week (Mon Oct 5 - Sun Oct 11) for a business open Mon-Fri 8-17 and Sat 9-12."""
    chat(bid, at(6, 10))                                   # Tuesday, open
    chat(bid, at(6, 20))                                   # Tuesday evening: outside opening hours
    chat(bid, at(6, 23, 30), channel="sms")                # Tuesday night: outside
    chat(bid, at(7, 9), channel="voice", status="handed_off", reason="customer asked for a person")
    chat(bid, at(10, 14), status="handed_off", reason="emergency: gas smell")  # Saturday afternoon: outside
    _, cust = chat(bid, at(11, 23, 59))                    # Sunday, last minute of the week: outside
    chat(bid, at(12, 0, 30))                               # this Monday: NOT last week
    chat(bid, at(4, 22))                                   # Sunday before: the week before
    with dbmod.tenant_session(bid) as db:
        db.add(Appointment(business_id=bid, customer_id=cust, service_name="Drain cleaning", starts_at=at(13, 9),
                           ends_at=at(13, 10), created_at=at(8, 11)))
        db.add(Appointment(business_id=bid, customer_id=cust, service_name="Leak repair", starts_at=at(14, 9),
                           ends_at=at(14, 10), created_at=at(12, 9)))      # booked this week: not counted
        for when in (at(9, 12), at(9, 13), at(12, 9)):                     # 2 leads last week, 1 this week
            db.add(Lead(business_id=bid, customer_id=cust, name="Maria Lopez", need="my basement is flooded", created_at=when))


def report(bid: str, week_start_day: int = 5) -> dict:
    with dbmod.tenant_session(bid) as db:
        return automations.build_weekly_report(db, db.get(Business, bid), datetime(2026, 10, week_start_day).date())


def tick(bid: str, when: datetime, adapters) -> bool:
    with dbmod.tenant_session(bid) as db:
        return automations.send_weekly_report_if_due(db, db.get(Business, bid), when, adapters)


# ---------------- the numbers ----------------
def test_weekly_numbers_are_right(biz):
    week_of_work(biz)
    r = report(biz)
    assert (r["customers"], r["chats"], r["calls"]) == (6, 5, 1)
    assert (r["bookings"], r["leads"], r["handoffs"], r["emergencies"]) == (1, 2, 2, 1)
    assert r["outside_hours"] == 4 and r["previous_customers"] == 1
    assert r["busiest_day"] == ("Tuesday", 3)


def test_weekly_text_says_it_in_plain_words_with_numbers_only(biz):
    week_of_work(biz)
    t = report(biz)["text"]
    for part in ("Acme Plumbing", "Oct 5 to Oct 11", "answered 6 customers (the week before: 1)", "Visits booked: 1",
                 "Leads saved for you to call back: 2", "Handed to you (a person was needed): 2",
                 "Emergencies sent to you at once: 1", "Answered outside your opening hours: 4",
                 "Busiest day: Tuesday (3 customers)", "https://frontdesk.example.com/inbox"):
        assert part in t, part
    assert not re.search(r"\+?1?512\d{7}|555[- ]?\d{4}", t), "no customer phone numbers in the email"
    assert "gas smell" not in t and "basement" not in t and "Maria" not in t, "no customer names or words in the email"


def test_report_uses_the_business_own_time_zone(biz):
    """Monday 1 AM in Chicago is still Sunday 11 PM in Los Angeles, so it belongs to LAST week there."""
    chat(biz, at(12, 1))
    assert report(biz)["customers"] == 0
    with dbmod.tenant_session(biz) as db:
        db.get(Business, biz).timezone = "America/Los_Angeles"
    assert report(biz)["customers"] == 1


def test_business_a_report_never_counts_business_b(biz, biz2):
    week_of_work(biz2)
    chat(biz, at(6, 10))
    assert report(biz)["customers"] == 1 and report(biz)["bookings"] == 0 and report(biz)["leads"] == 0
    assert report(biz2)["customers"] == 6


# ---------------- when it is sent ----------------
def test_sent_once_on_monday_after_8_and_not_again_that_week(biz, adapters, email, sms):
    week_of_work(biz)
    assert tick(biz, at(12, 7, 59), adapters) is False          # Monday, one minute too early
    assert tick(biz, at(12, 8, 0), adapters) is True
    assert tick(biz, at(12, 8, 1), adapters) is False and tick(biz, at(16, 9), adapters) is False
    [mail] = email.sent
    assert mail["to"] == "owner@acme.test" and "your week" in mail["subject"] and "answered 6 customers" in mail["body"]
    assert sms.sent == [], "the weekly report is email only, never a text"
    notes = [n for n in notifications(biz) if n.kind == "weekly_report"]
    assert len(notes) == 1 and "answered 6 customers" in notes[0].text   # also on the Today page


def test_next_monday_sends_the_next_week(biz, adapters, email):
    week_of_work(biz)
    assert tick(biz, at(12, 8), adapters) is True
    chat(biz, at(14, 10))
    assert tick(biz, at(19, 8), adapters) is True
    assert "Oct 12 to Oct 18" in email.sent[-1]["body"] and len(email.sent) == 2


def test_a_worker_that_was_off_on_monday_sends_it_later_in_the_week(biz, adapters, email):
    week_of_work(biz)
    assert tick(biz, at(14, 15), adapters) is True              # Wednesday afternoon
    assert "Oct 5 to Oct 11" in email.sent[0]["body"]


def test_a_week_with_no_customers_sends_nothing(biz, adapters, email):
    chat(biz, at(12, 9))                                         # only this week
    assert tick(biz, at(12, 8, 30), adapters) is False
    assert email.sent == [] and [n for n in notifications(biz) if n.kind == "weekly_report"] == []


def test_switched_off_means_no_report(biz, adapters, email):
    week_of_work(biz)
    with dbmod.tenant_session(biz) as db:
        db.get(Business, biz).weekly_report_enabled = False
    assert tick(biz, at(12, 9), adapters) is False and email.sent == []


def test_a_broken_email_server_does_not_stop_anything(biz, adapters):
    class Broken:
        def send(self, **kw):
            raise RuntimeError("smtp down")

    adapters.email = Broken()
    week_of_work(biz)
    assert tick(biz, at(12, 9), adapters) is True                # saved on the Today page; not sent twice
    assert tick(biz, at(12, 9, 5), adapters) is False
    assert len([n for n in notifications(biz) if n.kind == "weekly_report"]) == 1


def test_the_worker_runs_the_weekly_report(biz, adapters, email):
    week_of_work(biz)
    with dbmod.tenant_session(biz) as db:
        out = automations.run_all(db, db.get(Business, biz), NOW, adapters)
    assert out["weekly_report"] is True


# ---------------- Settings ----------------
def test_owner_can_switch_the_weekly_report_off_and_on(two):
    a, _ = two
    c = login(new_client(), OWNER_A)
    html = c.get("/settings").text
    assert 'name="weekly_report"' in html and "weekly report" in html.lower()
    base = {"csrf_token": csrf_of(c), "timezone": "America/Chicago", "weekly_report_shown": "1"}
    c.post("/settings", data=base)                               # box not ticked
    with dbmod.tenant_session(a) as db:
        assert db.get(Business, a).weekly_report_enabled is False
    c.post("/settings", data={**base, "weekly_report": "1"})
    with dbmod.tenant_session(a) as db:
        assert db.get(Business, a).weekly_report_enabled is True


def test_send_now_button_sends_the_last_7_days(two, email):
    a, b = two
    chat(a, NOW - timedelta(hours=2))
    chat(b, NOW - timedelta(hours=2))
    chat(b, NOW - timedelta(hours=3))
    c = login(new_client(), OWNER_A)
    r = c.post("/settings/weekly-report/send", data={"csrf_token": csrf_of(c)}, follow_redirects=True)
    assert r.status_code == 200 and "Report sent" in r.text
    [mail] = email.sent
    assert mail["to"] == "owner@acme.test" and "answered 1 customer " in mail["body"] and "last 7 days" in mail["body"]


def test_send_now_needs_login_and_csrf_and_has_a_limit(two, email):
    a, _ = two
    chat(a, NOW - timedelta(hours=2))
    r = new_client().post("/settings/weekly-report/send", data={})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    c = login(new_client(), OWNER_A)
    assert c.post("/settings/weekly-report/send", data={}).status_code == 403
    token = csrf_of(c)
    for _ in range(3):
        assert "Report sent" in c.post("/settings/weekly-report/send", data={"csrf_token": token}, follow_redirects=True).text
    assert "Too many" in c.post("/settings/weekly-report/send", data={"csrf_token": token}, follow_redirects=True).text
    assert len(email.sent) == 3


def test_send_now_says_so_when_there_is_nothing_or_no_email(two, email):
    a, _ = two
    c = login(new_client(), OWNER_A)
    token = csrf_of(c)
    r = c.post("/settings/weekly-report/send", data={"csrf_token": token}, follow_redirects=True)
    assert "No customers in the last 7 days" in r.text and email.sent == []
    chat(a, NOW - timedelta(hours=2))
    with dbmod.tenant_session(a) as db:
        db.get(Business, a).owner_email = None
    r = c.post("/settings/weekly-report/send", data={"csrf_token": token}, follow_redirects=True)
    assert "Add an email for reports" in r.text and email.sent == []


def test_send_now_is_honest_when_email_is_not_set_up(two, adapters):
    from app.services.adapters import NullEmail

    a, _ = two
    adapters.email = NullEmail()
    chat(a, NOW - timedelta(hours=2))
    c = login(new_client(), OWNER_A)
    r = c.post("/settings/weekly-report/send", data={"csrf_token": csrf_of(c)}, follow_redirects=True)
    assert "could not be sent" in r.text and "Today page" in r.text


# ---------------- old databases ----------------
def test_old_database_gets_the_weekly_report_fields(tmp_path):
    from app.migrate import migrate

    url = f"sqlite:///{tmp_path}/old.db"
    migrate(admin_url=url)
    eng = create_engine(url)
    with eng.begin() as c:
        for col in ("weekly_report_enabled", "last_weekly_report_date"):
            c.execute(text(f"ALTER TABLE businesses DROP COLUMN {col}"))
    eng.dispose()
    migrate(admin_url=url)
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("businesses")}
    assert {"weekly_report_enabled", "last_weekly_report_date"} <= cols
