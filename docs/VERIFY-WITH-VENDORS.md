# Things that only a real test can confirm

I built and tested everything I could without real accounts. This list is what I **could not** confirm.
Do each check once, before the first paying client. Write the result next to each line.

## A. Voice platform (Vapi): one real test call

Why: I wrote the code from the public description of the messages. I could not make a real call.
The names of some fields may differ. If one differs, the call still gets answered by the safe fallback, but a feature may not work.

Do this: call your test number, book an appointment, say "gas leak", hang up. Then check:

| # | Check | How you see it | Result |
|---|-------|----------------|--------|
| 1 | The call is answered and the first sentence says "This is an AI assistant for ... This call may be recorded." | Listen | |
| 2 | The agent can book (the appointment appears in the dashboard) | Dashboard > Appointments | |
| 3 | After you hang up, the call appears with the written transcript | Dashboard > Conversations | |
| 4 | "Gas leak" makes an URGENT alert on the owner's phone/email | Phone | |
| 5 | A wrong secret is rejected: in Vapi's server settings change the secret on purpose, call again. The call must NOT reach the agent (it forwards or fails). Put the right secret back. | Listen | |
| 6 | The missed-call text arrives after a call you hang up in the first seconds | Phone | |

If 2 or 3 fail: look at `docker compose logs app`. The log shows the message type that was received. Send me (or Claude Code) that line and the field names get fixed in `app/channels/voice.py` (functions `_extract_tool_calls` and the end-of-call part).
The setting `VOICE_EXTRA_ASSISTANT_JSON` lets you add voice, language and recording options without changing code.

## B. Texting (Twilio)

1. Carrier registration (A2P 10DLC) must be **approved** for each business before `sms_enabled` is switched on. It takes days or weeks. Until then, only the voice agent and the web chat work.
2. Send a real text to the number. The reply must arrive. Then text STOP. You must get the stop confirmation and then nothing more, even if you text again.
3. Check the webhook URL in Twilio is exactly `https://YOUR-DOMAIN/webhooks/twilio/sms`. If the address differs by one letter, the signature check rejects every text (this is on purpose).

## C. Real free-model answers (Groq / Gemini)

Make a free Groq key (no card) and run `python -m evals.run_live_eval` (see `docs/FREE-AI-MODELS.md`). It costs $0, waits between calls, and prints the tokens used.
The 12 safety scenarios pass with any model because fixed rules in code handle them. The other 5 (price questions, phone read-back, booking wording) depend on the model. **Read the whole output yourself.**
Also check once that the code really talks to Groq: I tested it against a fake imitation of the service, not the real one. If the first real run fails with "AI unavailable", look at `docker compose logs app` or the printed error and send it to Claude Code.
For voice, Vapi calls the model itself. Vapi needs your Groq key added in its dashboard (provider keys). Check that in Vapi's settings.

## D. Calendar (Google)

Connecting a real Google Calendar was not tested. The booking works without it (our own database is the truth). If the calendar is down, the booking still succeeds and the owner is told. Test with one real calendar before you promise "syncs with Google Calendar".

## E. Server

The Docker images were never built in my workspace (no Docker engine). The first `docker compose up --build` may show a small error (a missing package, a wrong path). If it does, copy the error to Claude Code. Then run `deploy/scripts/restore-test.sh` once to prove your backups can be restored. **A backup you never restored is not a backup.**

## F. Money and accounts from Tunisia

I could not verify that Stripe, Twilio, Vapi, or a US bank account will accept an account opened from Tunisia. Check this BEFORE you spend time on clients. See `docs/SETUP-ACCOUNTS.md`. A common route is a US company registered through a service (for example Stripe Atlas) with a US business bank account. Ask a US accountant.

## G. Law

Calling and texting rules (TCPA, recording-consent laws that differ by state, A2P rules) need a US lawyer to read `docs/COMPLIANCE.md` and the client contract. This is not optional for a paid product.
