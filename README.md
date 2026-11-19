# AI Front Desk

A platform that answers customers for many small US service businesses by **phone** and **text**, books appointments,
and follows up. The business owner only looks at a daily report and a simple admin screen.

This folder is the finished code, the automatic tests, the deploy files, and the guides.
**New here? Read `START-HERE.md` first.**

## What is inside

| Folder / file | What it is |
|---|---|
| `app/` | The program. Python + FastAPI. |
| `app/agent/` | The AI agent: fixed safety rules (`rules.py`), the tools (`tools.py`), the AI loop (free Groq/Gemini by default, paid Claude locked) and guards (`runner.py`) |
| `app/channels/` | Phone calls (Vapi), texts (Twilio), web chat |
| `app/services/` | Booking logic, texting rules (STOP, quiet hours), reminders and follow-ups, costs |
| `app/routers/` + `app/templates/` | Owner dashboard and platform admin pages |
| `app/mcp_server.py` | The agent's tools as an MCP server |
| `app/worker.py` | Background job: reminders, follow-ups, review requests, daily report, data cleanup |
| `tests/` | Automatic tests (150+) |
| `evals/` | The live test that uses the real AI (you run it with your key) |
| `deploy/` | Docker, Caddy, backup and server hardening scripts |
| `docs/` | Guides in simple English |
| `PRODUCT.md`, `CLAUDE.md`, `.claude/` | The product brief, the rules, the skills and agents for Claude Code |

## Run the tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest                      # fast, uses SQLite
```

To also check the database-level protections (business separation, double booking) you need a real Postgres:

```bash
TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/frontdesk_test python -m pytest
```

(See `docs/TEST-REPORT.md` for what passed when this was built, and what could NOT be tested.)

## Try it on your own computer

```bash
cp .env.example .env                  # then edit: set SECRET_KEY, ENCRYPTION_KEY (commands are inside the file)
export DATABASE_URL=sqlite:///./dev.db APP_ENV=development
python -m app.migrate
python -m app.cli demo-business       # sample business: owner@demo.test / demo-password-123
uvicorn app.main:app --reload         # open http://localhost:8000/login
```

## Go live

Follow `docs/SETUP-ACCOUNTS.md` (accounts), then `docs/DEPLOY.md` (server), then `docs/VERIFY-WITH-VENDORS.md`
(the real-phone checks that no test can replace). **Do not take a paying client before those are done.**
