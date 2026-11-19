"""Background worker: runs the automations for every business, once a minute.

Run one copy only. On Postgres it takes a lock, so a second copy waits instead of sending texts twice.
    python -m app.worker
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import select, text

from . import db as dbmod
from .config import get_settings
from .models import Business
from .services.automations import run_all

log = logging.getLogger("frontdesk.worker")
LOCK_ID = 7_431_001


def tick(now=None) -> dict[str, dict]:
    now = now or dbmod.utcnow()
    with dbmod.system_session() as s:
        ids = list(s.scalars(select(Business.id)))
    out = {}
    for bid in ids:
        try:
            with dbmod.tenant_session(bid) as db:
                b = db.get(Business, bid)
                out[bid] = run_all(db, b, now)
        except Exception:  # one business failing must not stop the others
            log.exception("worker failed for business %s", bid)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    problems = get_settings().check_production_ready() if get_settings().is_production else []
    if problems:
        raise SystemExit("Refusing to start:\n- " + "\n- ".join(problems))
    engine = dbmod.get_engine()
    conn = None
    if engine.dialect.name == "postgresql":
        conn = engine.connect()
        while not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_ID}).scalar():
            log.info("another worker is running; waiting")
            time.sleep(15)
    log.info("worker started")
    while True:
        started = time.monotonic()
        try:
            tick()
        except Exception:
            log.exception("tick failed")
        time.sleep(max(1.0, 60 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
