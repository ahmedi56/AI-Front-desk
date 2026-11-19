# START HERE

The product is **built**. This file tells you, in order, what you must do yourself. I wrote the code and the tests.
I could not do the steps that need your real accounts, real phones, and a real server.

## Step 0. What "built" means (the honest version)

Done and tested (170+ automatic tests, run on a real Postgres database):
- The agent: answers from the business's facts, books / moves / cancels, hands over to a person, never invents a price
- Safety rules in plain code: gas / fire / injury, "talk to a person", "are you a robot", angry customers, Spanish, rule-breaking attempts
- Texting rules: STOP / HELP / START, quiet hours (9 pm - 8 am), at most 2 follow-ups, one review request, nothing after STOP
- Business separation: business A can never see business B (the database itself enforces it)
- Owner dashboard, platform admin page, kill switch, cost numbers, daily report, data deletion after N days
- Deploy files: Docker, Caddy (automatic https), encrypted backups, server hardening script

**Not done / not possible for me to test** (details in `docs/TEST-REPORT.md` and `docs/VERIFY-WITH-VENDORS.md`):
- No real phone call, no real text, no real free-model (Groq, Gemini) answers, no real Google Calendar were used. All were replaced by fakes in tests.
- The Docker images were not built (no Docker engine in my workspace). The compose file is checked for syntax only.
- Some field names in the voice platform's messages could not be confirmed from their docs. One real test call will confirm them.
- The legal rules need a US lawyer.

## Step 1. Run the tests yourself (10 minutes)

Open this folder in the Claude desktop app (Claude Code tab) and say:
*"Run the tests and tell me the result in simple English."*
Or in a terminal: see `README.md`.

## Step 2. Run the live test with a FREE AI model (costs $0)

1. Make a free Groq key (see `docs/FREE-AI-MODELS.md`). No card. Put it in `.env` as `GROQ_API_KEY` (copy `.env.example` to `.env` first).
2. Run: `python -m evals.run_live_eval`
   It waits between calls to respect the free limits, so it takes several minutes. It prints the tokens used and "Money spent: $0".
   To save your free daily allowance, run a few scenarios first: `python -m evals.run_live_eval --only 1,2,8,11`
3. All safety scenarios must pass. The others show how the free model words things. Send the printed result to Claude Code to improve the instructions.
The paid Anthropic provider is locked (`ALLOW_PAID_LLM=false`). Leave it locked.

## Step 3. Create the accounts (`docs/SETUP-ACCOUNTS.md`)

Before you spend money, check on day 1 that you can open: Twilio, Vapi, Stripe/Wise/Payoneer (to get paid) from Tunisia.
If one of them refuses, tell me which one and we change the plan.

## Step 4. Get a server and deploy (`docs/DEPLOY.md`)

Use the `deploy-vps` skill: ask Claude Code *"Use the deploy-vps skill. Ask me before every command."*

## Step 5. Test with a real phone (`docs/VERIFY-WITH-VENDORS.md`)

Call your own number, text it, say STOP, try to break it. Write down what happens.

## Step 6. First client

Free pilot for 2 weeks with ONE business you know. Read every conversation in the inbox every day.
Only then charge money. Before the first paying client: a US lawyer reviews `docs/COMPLIANCE.md`.

## Stop signs (do not continue if one is true)

- A test fails and nobody can explain why
- The agent said a price that is not in the business's facts
- A text was sent after someone wrote STOP
- You can see one business's data from another business's login
- The server asks you to open the database port to the internet
