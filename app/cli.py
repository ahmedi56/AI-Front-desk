"""Small admin commands.

    python -m app.cli create-admin you@example.com        (asks for a password, 12+ characters)
    python -m app.cli demo-business                        (a sample business for trying things; development only)
    python -m app.cli demo-for "Joe's Plumbing"            (a ready sales demo with a prospect's name; development only)
"""
from __future__ import annotations

import getpass
import re
import sys

from sqlalchemy import select

from . import db as dbmod
from .config import get_settings
from .models import Business, Service, User
from .security import hash_password


def create_admin(email: str) -> None:
    password = getpass.getpass("Password (12+ characters, not shown): ")
    if password != getpass.getpass("Again: "):
        raise SystemExit("The two passwords are different. Nothing was saved.")
    try:
        hashed = hash_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc))
    with dbmod.system_session() as s:
        if s.scalar(select(User.id).where(User.email == email.lower())):
            raise SystemExit("That email already exists.")
        s.add(User(business_id=None, email=email.lower(), password_hash=hashed, role="admin"))
    print("Admin created. Log in at /login.")


def demo_business() -> None:
    if get_settings().is_production:
        raise SystemExit("Not allowed in production.")
    hours = {d: [["08:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}
    with dbmod.system_session() as s:
        if s.scalar(select(Business.id).where(Business.slug == "demo")):
            raise SystemExit("Demo business already exists.")
        b = Business(name="Demo Plumbing", slug="demo", timezone="America/Chicago", hours=hours, owner_email="owner@demo.test",
                     facts={"address": "1 Demo Street", "areas": "Anywhere"})
        s.add(b)
        s.flush()
        s.add(Service(business_id=b.id, name="Drain cleaning", price_text="$149", duration_min=60))
        s.add(User(business_id=b.id, email="owner@demo.test", password_hash=hash_password("demo-password-123"), role="owner"))
    print("Created. Log in as owner@demo.test / demo-password-123 (development only).")



DEMO_SERVICES = [
    ("Drain cleaning", "$149", 60, "Kitchen, bathroom and main drains cleared."),
    ("Leak repair", "from $95", 60, "Find and fix leaking pipes, taps and toilets."),
    ("Water heater repair", None, 90, "Repair or replace tank and tankless water heaters. Price after a look."),
]
DEMO_FAQS = [
    {"q": "Do you work on weekends?", "a": "Yes, Saturday mornings from 9 AM to 12 PM."},
    {"q": "Do you charge for a quote?", "a": "No. We give you the price before we start any work."},
    {"q": "Are you licensed and insured?", "a": "Yes, fully licensed and insured."},
]


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")[:40].strip("-")
    return base if len(base) >= 2 else "demo-business"


def demo_for(name: str) -> None:
    """A sales demo with the PROSPECT's business name, ready in seconds. Development only (fake data, known password)."""
    if get_settings().is_production:
        raise SystemExit("Not allowed in production. Sales demos run on your laptop only.")
    name = " ".join(name.split())[:200]
    if not name:
        raise SystemExit('Give a business name, for example: python -m app.cli demo-for "Joe\'s Plumbing"')
    hours = {d: [["08:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}
    hours["sat"] = [["09:00", "12:00"]]
    with dbmod.system_session() as s:
        base = _slugify(name)
        slug, n = base, 1
        while s.scalar(select(Business.id).where(Business.slug == slug)):
            n += 1
            slug = f"{base}-{n}"
        email = f"owner@{slug}.demo.test"
        b = Business(name=name, slug=slug, timezone="America/Chicago", hours=hours, owner_email=email,
                     facts={"address": "Demo address", "areas": "The city and nearby towns", "faqs": DEMO_FAQS,
                            "policies": "Free quotes. Payment when the job is done."})
        s.add(b)
        s.flush()
        for svc, price, mins, desc in DEMO_SERVICES:
            s.add(Service(business_id=b.id, name=svc, price_text=price, duration_min=mins, description=desc))
        s.add(User(business_id=b.id, email=email, password_hash=hash_password("demo-password-123"), role="owner"))
    url = get_settings().public_base_url.rstrip("/")
    print(f"Sales demo ready for {name}.")
    print(f"  Demo website: {url}/demo-site/{slug}")
    print(f"  Chat only:    {url}/widget/{slug}")
    print(f"  Dashboard:    {url}/login   ({email} / demo-password-123)")
    print("  Change the services and prices in Settings to match their real website.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "create-admin":
        create_admin(args[1])
    elif args == ["demo-business"]:
        demo_business()
    elif len(args) >= 2 and args[0] == "demo-for":
        demo_for(" ".join(args[1:]))
    else:
        print(__doc__)
