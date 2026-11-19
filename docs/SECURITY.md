# Security: what is protected, how, and what is not

Simple words: "tenant" = one business client. "Row Level Security (RLS)" = the database itself refuses to show a row to the wrong business, even if our code has a bug.

## 1. One business can never see another business

- Every table has a `business_id`.
- The database has RLS switched on and FORCED. Each request opens a session that tells the database "I am business X". An unscoped session sees nothing.
- The app connects as a limited database role. It cannot switch RLS off, change the structure, or edit the audit log.
- Tests: `tests/test_dashboard_and_isolation.py` tries to read, change and delete business B's data as business A (dashboard, web chat, voice, MCP). All fail. Run on real Postgres.

## 2. The AI agent is boxed in

- Fixed rules in code run BEFORE the AI: emergencies, "talk to a person", "are you a robot", angry customers, Spanish, and attempts to break the rules or reveal instructions. These do not depend on the AI behaving.
- The AI's tools do not accept a business or customer id. They always act on the current customer of the current business. Unknown arguments are thrown away.
- Checks on what the AI says: a price must come from the business facts; "booked" can only be said if the booking tool succeeded; a hidden canary word stops prompt leaks.
- **Money lock:** the paid Anthropic provider refuses to start unless `ALLOW_PAID_LLM=true`, and can never be a silent backup. Free models are priced at 0, so the step limits and the free providers' own rate limits are what protect you there.
- Limits: 6 steps per message, 40 messages per conversation, $0.50 per conversation, $10 per day per business. A loop or a prank cannot create a big bill.
- After a handoff to a person, the AI only says that someone will contact them.
- Customer names are cleaned before they enter any prompt (stops "my name is ignore your rules").

## 3. Logins

- Passwords: Argon2 (slow on purpose), minimum 12 characters.
- Cookie: HttpOnly, SameSite=Lax, Secure in production. Logout and password change cancel old cookies on the server.
- A form token (CSRF) on every change.
- 5 wrong passwords lock the account for 15 minutes; the login page also limits tries per address.
- Two-step login (authenticator app): required for platform admins in production, optional for owners. A code can be used only once.
- Temporary passwords are shown once on screen (never in the address bar) and must be changed at first login.

## 4. Webhooks (calls and texts coming in)

- Twilio: the signature is checked. Vapi: a secret header is checked (constant-time compare). Bad ones are rejected.
- Each message id is processed once (idempotent), so a retry cannot double-book or double-send.
- Texts are saved before processing, so a crash does not lose a customer message; if processing fails the owner is alerted.

## 5. Secrets and data

- Secrets only in `.env` (mode 600), never in code or logs. Calendar tokens are encrypted (Fernet).
- Phone numbers are masked in logs. Database error text is hidden so customer text does not leak into logs.
- The app refuses to start in production with weak or missing keys.
- Old conversations are deleted after N days (default in settings); the "do not text me" flag is kept so STOP is remembered.
- The audit log is append-only for the app role.

## 6. Server

- Only Caddy is open to the internet (ports 80/443). The database is on an internal network with no route out or in.
- The app runs read-only, as a normal user, without extra Linux powers.
- Security headers and a strict content policy (no inline scripts or styles). API docs are off in production.
- `/admin` can be limited to your IP (`ADMIN_ALLOWED_IPS`).
- `pip-audit` found no known vulnerabilities in the pinned packages on the day of the build. Re-run it monthly.

## 7. Known weak spots (honest list)

1. Rate limits are kept in memory. They work for one server process; with many servers they must move to the database or Redis.
2. The voice channel cannot apply the price and "booked" checks to the spoken words in real time (the voice platform speaks directly). It relies on the prompt, the tools, and an after-call check. This is weaker than text.
3. A determined attacker who controls the owner's phone AND password can act as the owner. Turn on 2-step login for owners.
4. The ENCRYPTION_KEY and `.env` on the server are the crown jewels. Anyone who gets the server's root gets them. Keep the server updated, use SSH keys only.
5. No outside penetration test was done. Before many clients, pay a security person for one.
6. Prompt-injection can never be proven fully impossible. We limit the damage (tools are small), we do not claim it is zero.

## 8. If something goes wrong

See `docs/RUNBOOK.md` ("A client says the agent said something wrong", "Key leaked").
