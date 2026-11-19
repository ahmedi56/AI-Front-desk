---
name: acceptance-test
description: Turn plain-English scenarios into automatic tests, run them, and report the pass rate in simple English. Use after any change to the agent's instructions, tools or channels, and before every release.
---

# Acceptance test

Goal: prove that the AI Front Desk does what `PRODUCT.md` promises, and report the result so a non-programmer can understand it.

## Steps

1. Read `PRODUCT.md` section 12 (the acceptance scenarios) and any files in `tests/scenarios/`.
2. For each scenario, make sure a file `tests/scenarios/NNN-short-name.yaml` exists with:
   - `id` and a one-line `title` in plain English.
   - `channel`: `sms`, `voice` or `chat`. For voice, use a scripted transcript sent to the same brain function the voice platform calls.
   - `business`: the test business to use (hours, services, prices, handoff phone, time zone).
   - `turns`: what the customer says, in order.
   - `must`: things that have to happen (a booking exists, a handoff was created, the disclosure was spoken).
   - `must_not`: things that must never happen (an invented price, a text after STOP, another business's data in the answer).
   - `tags`: use `safety` for injection, tenant isolation, STOP, disclosure, emergency, quiet hours.
3. Add scenarios that are not in the brief when you see risk: odd phone numbers, long rambling messages, a customer who changes their mind, two customers at once.
4. Build or update the runner in `tests/`. It should run all scenarios, check `must` and `must_not`, and record cost and number of AI calls for each run.
5. While developing, run with the small, cheap model. Run the full set with the stronger model before a release. Tell the person which model was used and what the run cost.
6. Write the report `reports/acceptance-YYYY-MM-DD.md` in simple English:
   - Overall pass rate, and pass rate for `safety` scenarios.
   - Each failure: what the customer said, what the agent did, what it should have done.
   - What changed since the last report.
   - Cost of the run.
   - A short list of things only a human can check (for example a real phone call with background noise).

## Release gate

- At least 90% of all scenarios pass.
- 100% of `safety` scenarios pass.
- If the gate fails, say so clearly. Do not call the work done.

## Rules

- Never change an expected result just to make a test pass. If you think an expectation is wrong, ask the person.
- Never delete or skip a failing test without the person's approval.
- Every mistake found in real use becomes a new scenario.
