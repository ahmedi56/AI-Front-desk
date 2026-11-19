# HANDOFF - read this first in a new session

Last updated: 2026-10-10 (late night, session on the owner's laptop). Written for the next Claude session AND for the owner.

## 1. Who and what

- **Owner:** lives in Tunisia (time zone UTC+1), not a programmer, limited English. Wants SIMPLE English, short sentences, each new word explained in one line. No Arabizi.
- **Product: AI Front Desk.** An AI phone, text and website-chat assistant for small US service businesses (plumbers, HVAC...). It answers, books appointments, saves leads, hands urgent cases to a person, and alerts the owner.
- **Money model:** the owner has no money and no credit card. CLIENTS pay their own AI, phone and voice accounts. The owner is paid for the product only (setup + monthly support), by wire transfer.

## 2. HARD RULES from the owner (never break)

1. **NEVER use a paid AI key.** No Anthropic API key, even for 30 cents; nothing from the owner's Claude account inside the product. Only free tiers (Groq, Gemini free). The paid provider stays locked (`ALLOW_PAID_LLM=false`).
2. Never add a card or billing to any free account.
3. Never skip or weaken safety rules or tests.
4. Ask before deleting data, running commands on a server, or installing paid tools.
5. Be honest about what is NOT verified (section 7).
6. **Keep the approved design exactly** (section 5). Use the `front-desk-design` skill and `DESIGN.md` before any visual change.

## 3. Where everything is

- **Owner's laptop (the up-to-date copy):** `C:\Users\T560\Desktop\front-desk` (Windows, PowerShell, Python 3.13 in `.venv`). If the cloud copy is gone, read files from the laptop first.
- Cloud copy: `/home/claude/front-desk` (may be gone in a new session).
- Files are written to the laptop with the device bridge. Writing `.env` through the bridge is refused: give the owner text to paste.
- Client offer guide (Claude Docs): https://claude.ai/artifact/GR2ZhjSccwa7cNBqZnUSRL (placeholders: fees, contact, refund terms).
- **Sales kit** (Claude Docs): https://claude.ai/code/artifact/4ddbe63e-66b1-4cb1-ac4d-f31441dd9213 (who to sell to, pilot offer, demo video script, outreach messages + US law, call script, tracker, checklist before taking money).
- **Product page for clients** (Design canvas): https://claude.ai/artifact/4cGNimsp8jp9NYG6gtsEnS (placeholders [VIDEO LINK], [YOUR EMAIL], [Your name], prices; private until the owner shares it).

## 4. What is built and tested

- Stack: Python 3.13, FastAPI, SQLAlchemy, Postgres 16 (server) / SQLite (laptop), Jinja2 pages, httpx, Argon2, Fernet, Docker + Caddy.
- Safety: each business's data locked apart (Postgres row level security), audit log, no double booking, fixed rules before the AI (emergency, urgent, injection, "are you a robot", ask for a person, angry, Spanish, kill switch), price/confirmation/prompt-leak guards, step and cost caps, one SMS gate (STOP/HELP, quiet hours), 2-step login, CSRF, lockout, strict CSP (no inline styles/scripts).
- Channels: website chat widget (+ chat bubble for client sites), Twilio SMS, Vapi voice.
- Free AI: Groq `openai/gpt-oss-120b` main, Gemini backup; `clean_reply()` removes smart characters and "(waiting...)" notes.
- **Per-client AI keys:** each business stores its own free Groq/Gemini key, encrypted (`app/services/ai_keys.py`; Settings > AI account; admin page). Order: business key > shared `.env` key only if `ALLOW_SHARED_AI_KEY` (default yes on a laptop, no in production) > else hand the contact to the owner + one alert a day.
- Client workflow: Today "Finish setting up" checklist; Settings > Website chat (one-line embed code); admin preview links + AI key form; `python -m app.cli demo-for "Joe's Plumbing"` makes a sales demo in seconds (laptop only); `/demo-site/<slug>` = pretend client website (laptop only).
- `python -m app.migrate` also upgrades old databases (adds new fields, no data lost; checked on SQLite and Postgres).
- **Chat fixes (2026-10-10, after a real demo chat failed):** the AI failure reason is saved as a short code
  (`groq:429`) in the audit log and the owner alert; website chat asks "send again" on the FIRST AI failure and hands
  over on the second (SMS: still at once); an invalid phone number ends the turn with one fixed question (no loop);
  new guards: no phone number the customer never typed (`guard_phone`), glued answers are cut (`cut_runaway`).
  Note: `docs/decisions/001-chat-fixes-and-product-page.md`.
- **Product page:** `/product` (public), `app/templates/product.html` + `app/static/product.css` + Public Sans font
  in `app/static/fonts/`. Details come from `.env`: `PRODUCT_CONTACT_EMAIL`, `PRODUCT_VIDEO_URL` (https only),
  `PRODUCT_OWNER_NAME`, `PRODUCT_SETUP_PRICE`, `PRODUCT_MONTHLY_PRICE`. Empty = placeholder shown.
- **Product page, part 2 (2026-10-10):** (a) "Try the live demo": set `PRODUCT_DEMO_BUSINESS=<short name>` in `.env`
  and the page shows a green button + the chat bubble (`embed.js` with `data-demo`; any element with `data-fd-open`
  opens the chat). The demo chat shows "Demo only. This business is not real" (only for that business). Empty or
  unknown name = no demo chat. (b) Lead form `POST /product/lead` -> new table `prospects` (NO business_id; Postgres
  policy `platform_only`: only a system session can read it). Trap field `company_url`, 5 sends per hour per visitor,
  fixed error texts. Admin page has a "Prospects" list + "Mark contacted". An email goes to `PRODUCT_CONTACT_EMAIL`
  if SMTP works (never tested with a real mail server). Code: `app/routers/product.py`. Run `app.migrate` once
  (adds the table, loses no data). Note: `docs/decisions/002-live-demo-and-lead-form.md`.
- **Backup AI key per client (2026-10-10):** Settings > AI account has a second block "Backup AI key (optional)"
  (shown once a main key exists). Fields `businesses.ai_backup_provider / _key_enc / _model / _key_hint` (added by
  `app.migrate`, no data lost). `ai_keys.set_backup_key / clear_backup_key`; `llm_for_business` returns
  `FallbackLLM([main, backup])`. Rules: free providers only, encrypted, must be a DIFFERENT provider than the main key
  (owner's decision), removing the main key removes the backup too. Each backup answer adds a `llm_backup` usage
  event; Costs page shows "Answers from the backup AI"; admin page shows a tag only. Routes
  `POST /settings/ai/backup` and `/settings/ai/backup/remove`. Note: `docs/decisions/003-backup-ai-key-per-client.md`.
- **Weekly email report (2026-10-10):** `automations.send_weekly_report_if_due` (in `run_all`, so the worker sends it):
  from Monday 8 AM business time, about the week before (Mon-Sun), once per week (`businesses.last_weekly_report_date`
  = Monday of the week sent), later in the week if the worker was off. Numbers only, plain text, EMAIL only (no SMS),
  also saved as a `weekly_report` note on Today. A week with no customers sends nothing (owner's decision).
  `businesses.weekly_report_enabled` (empty = on): checkbox in Settings > Business. Button "Send me a report of the
  last 7 days now" -> `POST /settings/weekly-report/send` (3 per hour per business). `notify_owner` got an optional
  `subject`. Note: `docs/decisions/004-weekly-email-report.md`.
- **Tests on the laptop (2026-10-10 late):** `python -m pytest` -> 288 passed, 7 skipped (SQLite). Browser walkthrough
  with the fake AI: **59 of 59**, run with the laptop's own Chrome (the Chromium download from Playwright timed out):
  `$env:WALK_CHANNEL="chrome"; python evals/browser/walkthrough.py`. `evals/browser/serve_fake.py` makes NO admin
  user (the owner asked to remove that part), so for the full run Claude added the README's test admin to the
  throw-away database with a one-off helper outside the project. See `evals/browser/README.md` steps 1-3.
  NOT repeated on the laptop: the Postgres run. So the `prospects` database policy and its test (skipped on SQLite)
  have never run on a real Postgres. A tab icon (`static/favicon.svg`, `/favicon.ico`) was added because real Chrome
  logged a 404 for it. New reference screenshots: `docs/design/screens/product-*.png`.
- **Older tests (2026-10-10, cloud):** `python -m pytest` -> 200 passed, 6 skipped (SQLite); **206 passed on real Postgres**. Browser walkthrough of every feature with a fake AI: **50 of 50** (`evals/browser/README.md`). Live test on real Groq (`python -m evals.run_live_eval`): 17 scenarios, all 12 safety ones passed (one pass only).

## 5. The approved design (keep it exactly)

- Rules + tokens + components: **`DESIGN.md`**. Reference screenshots: **`docs/design/screens/`**. Skill: **`.claude/skills/front-desk-design/`**.
- Look: "clean and trusted". Navy #14233B side menu and status band, paper #F6F7F5, blue #1F5BD8 actions, green #1E7A55 on/booked, red #B42318 off/urgent, system font, light mode only.
- All CSS in `app/static/app.css`; icons in `app/templates/_icons.html`; static links via `{{ static_url('...') }}` (version stamp, so browsers never show an old look).

## 6. How the owner runs it on the laptop

`.env` (first line MUST be `APP_ENV=development`):

```
APP_ENV=development
LLM_PROVIDER=groq
GROQ_API_KEY=<owner's free Groq key>
ENCRYPTION_KEY=<made once with the command below; never change or lose it>
ALLOW_PAID_LLM=false
ANTHROPIC_API_KEY=
```

Make the encryption key once: `.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

```
.venv\Scripts\python -m app.migrate
.venv\Scripts\python -m app.cli demo-business          (first time only)
.venv\Scripts\python -m app.cli demo-for "Joe's Plumbing"
.venv\Scripts\python -m uvicorn app.main:app
```

- Dashboard: http://localhost:8000/login (owner@demo.test / demo-password-123, or the password the owner changed it to)
- Demo website: http://localhost:8000/demo-site/demo ; chat only: /widget/demo
- After an update: stop with Ctrl+C, run `app.migrate`, start again, press Ctrl+F5 once in the browser.
- Fake customer data only (free AI tiers may keep prompts).

## 7. NOT verified yet (say this honestly)

- Why the demo chat failed on 2026-10-10 is a guess (most likely Groq's free speed limit). The new reason code will
  show it next time. The chat fixes were tested with a fake AI only, not with real Groq.

- No real phone calls, no real texts, Vapi voice never tested, real Google Calendar never tested.
- No real server yet: Docker images never built, deploy files untested on a VPS. The app only runs on the laptop.
- Per-client keys tested with a fake AI; not yet with a real Groq key saved through Settings (owner to test).
- Gemini backup (2026-10-10): `.env` already has `GEMINI_API_KEY` and `GEMINI_MODEL=gemini-3.8-flash`; the owner must
  add the line `LLM_FALLBACK_PROVIDER=gemini` himself (Claude does not edit `.env`). Tried with the real Gemini: the
  chain works and tool calls are right, but Gemini was slow and uneven (5-13 s, some 30 s timeouts, one 503). See
  `docs/FREE-AI-MODELS.md`. That `.env` backup covers only the shared key.
- Weekly report: built and tested with fake email only. No real email was ever sent (no SMTP on the laptop), and
  the worker has never run for a whole real week.
- Backup key per client (built 2026-10-10): tested with a fake AI only. Never tried with a client's two real keys,
  and no live test has run with Gemini answering whole chats. New fields not tested on real Postgres.
- Real AI answers vary: run the live test 2-3 more times.
- The owner has not confirmed on the laptop: new design after Ctrl+F5, ENCRYPTION_KEY + migrate, the double-booking and "(waiting...)" fixes.
- Bank can receive US wire transfers? Twilio/Vapi/US bank accept a Tunisian owner? Free Groq/Gemini terms allow use for a paying business? All unverified.
- Legal review needed (AI/recording disclosure, client agreement, SMS carrier registration).
- `docs/FREE-AI-MODELS.md` says 2,000-4,000 tokens per call; real use is about 1,400.

## 8. Next steps, in order

0. Owner: run `app.migrate` once (new `prospects` table + backup key fields + weekly report fields), then in Settings > AI account save the
   free Groq key as main and the free Gemini key as backup, and add `PRODUCT_DEMO_BUSINESS=demo` to `.env`.
   Before the product page goes on a real server: run the tests on real Postgres (prospects policy).
   Also in `.env` set `COOKIE_SECURE=false` (it is `true` now; that is for a real https server only). Fill the
   five `PRODUCT_...` lines. Add the line `LLM_FALLBACK_PROVIDER=gemini` (turns on the Gemini backup; key and model are already there).

1. Owner: Ctrl+C, add ENCRYPTION_KEY, `app.migrate`, start, Ctrl+F5. Check the design matches `docs/design/screens/`. Send screenshots of anything different.
2. Owner: paste the real free Groq key in Settings > AI account, chat on the demo website, confirm answers come (proves per-client keys with the real service).
3. Owner: run the live test 2 more times; fix any scenario that fails sometimes (test first, then fix).
4. Record the 2-minute demo video (script in the sales kit), fill the product page placeholders, share it.
5. Start outreach: 20 personal messages a day (sales kit). Email/contact forms/social messages only; no cold texts or robot calls (TCPA).
6. Before the first client pays (sales kit checklist): bank check, a real server (find one payable without a card), pilot agreement, offer guide placeholders, AI terms check.
7. Later: deploy to a server (`deploy-vps` skill, ask before every server command), then test real calls and texts with the first client's own accounts.

## 9. Working style the owner likes

- One sentence on what you will do, then do it. End with: what came out, what to check, one next step.
- Simple English; explain each technical word once. Exact PowerShell commands to copy, and what the output should look like.
- Test first, then code; show the test result. Push changed files to the laptop folder.
- For a big task: show a short plan first (CLAUDE.md rule). For a visual change: follow `front-desk-design`.

## 10. Key files

- `CLAUDE.md` (project rules), `PRODUCT.md` (section 12 = acceptance scenarios), `DESIGN.md`, `START-HERE.md`, `DECISIONS.md`, `SECURITY.md`, `docs/GLOSSARY.md`, `docs/FREE-AI-MODELS.md`, `docs/SETUP-ACCOUNTS.md`, `docs/VERIFY-WITH-VENDORS.md`.
- `app/agent/` (llm, runner, tools, prompts, rules), `app/services/` (ai_keys, scheduling, notify, usage, automations), `app/routers/` (owner, admin, account, auth, deps), `app/channels/` (sms, voice, webchat), `app/templates/`, `app/static/` (app.css, chat.js, embed.js, embed.css), `app/cli.py`, `app/migrate.py`.
- Tests: `tests/`; live AI: `evals/run_live_eval.py`; browser walkthrough: `evals/browser/`.
- Skills: `.claude/skills/` - `fastapi` (official), `front-desk-design`, `acceptance-test`, `explain-decisions`, `deploy-vps`, `security-review`.
