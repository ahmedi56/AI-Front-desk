# Test report (built on 2026-10-10)

## Result
- **171 automatic tests pass on real Postgres 16** (`TEST_DATABASE_URL`). 0 failures.
- **165 pass, 6 skipped on the quick SQLite database.** The 6 skipped ones need Postgres features (row isolation, overlapping-booking constraint, concurrent booking, audit log protection) and run only on Postgres.
- `evals/run_live_eval.py --selftest` (no real AI): the **12 of 12 safety scenarios pass** because fixed rules handle them. 4 of the other 5 scenarios fail in selftest on purpose (no real AI to write the reply). You run the real one with a FREE Groq key (cost $0). No paid AI key was ever used.
- `pip-audit` on `requirements.txt`: no known vulnerabilities (build day).
- `docker-compose.yml` is valid YAML. Images were NOT built.

## What the tests cover
| Area | Examples |
|---|---|
| Agent behavior | books, moves, cancels; refuses unknown price; never says "booked" without the tool; handoff; angry customer; Spanish; robot question; prompt reveal; injection text; name tricks; cost/step caps |
| Safety rules | gas/fire/injury goes to a person at once; kill switch first; urgent alerts limited (3 per hour per chat) |
| Texting | signature check; same message twice = processed once; STOP/HELP/START; quiet hours; max follow-ups; nothing after STOP; saved even if processing fails |
| Voice | bad secret rejected; disclosure first; transient assistant; tool calls repeated safely; transcript saved; emergency backstop; textback; forwarding when agent off |
| Booking | no overlap; many people booking at once, only one gets the slot (Postgres) |
| Isolation | business A cannot read/write B through dashboard, web chat, voice, MCP; unscoped DB session sees nothing |
| Logins | wrong password, lockout, CSRF, logout kills old cookie, forced password change, 2-step login (code reuse blocked), admin 2-step required |
| Free AI providers | message translation to the OpenAI format, tool calls, "slow down" retry, wait limit, backup provider, a whole chat turn through a fake Groq, the paid-key lock, cost 0 for free models |
| Data | purge keeps STOP flag; audit log cannot be edited; errors do not print customer text |

## What was NOT tested (be honest with clients)
1. Real phone calls, real texts, real Groq or Gemini answers, real Google Calendar. (Fakes and a local fake AI server were used. The live test script was run only against that fake server.)
2. Docker image build and a real server deploy.
3. Voice platform field names (one test call needed — `docs/VERIFY-WITH-VENDORS.md`).
4. Many users at the same time (load). In-memory rate limits only suit one app process.
5. Outside penetration test.
6. Legal review.
7. Opening accounts and getting paid from Tunisia.

## Known limits of the product
One appointment at a time per business; customer assumed in the business time zone; no WhatsApp; no billing screen; no live call transfer; voice answers are checked after the call, not word-by-word.

## Bugs found by my own attack tests and fixed
Double booking under concurrency (lock added); owner could not update own business row under RLS (policy fixed); tied message timestamps scrambled order; urgent alerts counted themselves (cap hit one early); password shown in the address bar (moved to the page body); logout only cleared the browser cookie (now cancels on server); admin needed 2-step login (added).
