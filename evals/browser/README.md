# Browser walkthrough (every feature, like a real client)

Runs 59 steps in a real browser (58 when no demo business is set for the product page): chat widget, booking,
emergency, kill switch, dashboard pages, settings, weekly report, AI key and backup AI key, password change, product page (lead form, live demo chat),
admin (prospects, new client), new client first login, demo website, phone size.
Uses a FAKE AI server (no internet, $0).

Quick start on Windows (screenshots and hand checks): `python evals/browser/serve_fake.py` does steps 1-3 in one
command (throw-away database, demo data, fake AI, app on port 8811). It does NOT make the admin user, so the
admin steps of the walkthrough fail with it; for all steps, do steps 1-3 below (with the admin).
To use the Chrome already on the laptop (no Chromium download): `$env:WALK_CHANNEL="chrome"` before step 4.

1. Fresh database + demo data + an admin (`admin@platform.test` / `admin password long 99`).
2. `python evals/browser/fake_ai.py` (listens on 127.0.0.1:8765).
3. Start the app on port 8811 with: `APP_ENV=development LLM_PROVIDER=openai_compat LLM_BASE_URL=http://127.0.0.1:8765/v1
   LLM_API_KEY=shared-test-key-0000 MODEL_SIMPLE=fake-model MODEL_HARD=fake-model ENCRYPTION_KEY=<a Fernet key>
   PRODUCT_DEMO_BUSINESS=demo` (the last one turns on the live demo chat step).
4. `python evals/browser/walkthrough.py` -> "59 of 59 steps passed".

Needs Playwright + Chromium (or Chrome, see above). The fake AI only proves OUR code works end to end; real answers
are checked by `evals/run_live_eval.py` with a free Groq key.
