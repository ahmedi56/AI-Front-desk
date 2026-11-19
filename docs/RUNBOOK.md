# Runbook: what to do when something happens

Commands run on the server in `/srv/frontdesk/deploy`. Read a command before you run it.

## The site is down
1. `docker compose ps` — is something "exited"?
2. `docker compose logs --tail=100 app` — the last error line says why.
3. Restart: `docker compose restart app worker`.
4. Still down: calls forward to the handoff phone only if the app answers. Tell clients by email; keep the message short and honest.

## A client says the agent said something wrong
1. Dashboard > Conversations: read the exact conversation.
2. Turn the agent OFF for that business (Admin > Agent button, or the owner's kill switch). Calls forward to a person.
3. Decide: was it a wrong fact (fix the business facts), or a wrong AI answer (write the case as a new test in `tests/test_agent_scenarios.py` with Claude Code, fix, run tests).
4. Tell the client what happened and what changed. Then turn the agent on.

## Emergency alert did not arrive
Check Dashboard > Notifications: the alert is always saved there. If "delivered_via" says `stored_only`, email/SMS sending failed: check the email settings and Twilio balance in `.env`.

## Daily AI cost limit reached
The owner gets one alert; the agent hands over to a person until the next day. Decide if it is real traffic (raise `MAX_DAILY_AI_COST_PER_BUSINESS_USD`) or an attack (leave it).

## Someone is spamming a business (web chat)
Per-address rate limits already apply. Turn the web chat off for that business, or block the address in Caddy.

## A key leaked (API key, Twilio token, SECRET_KEY)
1. Make a new key at the provider, put it in `.env`, `docker compose up -d`.
2. Delete the old key at the provider.
3. If SECRET_KEY changed: everyone must log in again (normal).
4. If ENCRYPTION_KEY leaked: reconnect each client's calendar after changing it (stored tokens can't be read with the new key).
5. Look in the audit log (Admin) for actions you do not recognize.

## An owner lost the phone with the authenticator app
Admin > that business > "Reset password". It also turns off their 2-step login; they set it up again after logging in.

## A client leaves
Ask in writing. Delete the business data (use Claude Code with the `security-review` skill so it is done once, correctly, and ask for a backup of the export first). Remember old backups also hold the data until they expire.

## A customer asks to delete their data
Delete their conversations (dashboard) — but keep the STOP flag if they had one.

## Updating the software
1. Run the tests (`pytest`), 2. back up (`deploy/scripts/backup.sh`), 3. `git pull`, 4. `docker compose up -d --build`, 5. open `/healthz`.

## Every month
- `pip-audit -r requirements.txt` and update pinned packages that have fixes.
- Restore test: `deploy/scripts/restore-test.sh`.
- Look at costs per business (Admin). Check each client still has margin.
