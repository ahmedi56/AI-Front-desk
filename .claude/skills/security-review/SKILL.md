---
name: security-review
description: Review the code and setup against the project's security checklist and report findings in simple English. Use before every release, after big changes, and monthly.
---

# Security review

Goal: find security and privacy problems before a customer does. Be strict. Explain every finding in simple English, with what could go wrong in real life.

## How to work

1. Read `PRODUCT.md` and `CLAUDE.md`.
2. Go through each area below. Look at the real code and settings, not only the documents. Search the repository for secrets and risky patterns.
3. Write the report `reports/security-YYYY-MM-DD.md`. Do not fix anything silently. List first, then fix what the person approves.

## Checklist

**Secrets**
- No API keys, passwords or tokens in code, Git history, logs or test files.
- `.env` is ignored by Git. Keys are separate for testing and production.
- Spending limits exist on the AI, phone and voice accounts.

**Incoming webhooks**
- Every webhook (phone provider, voice platform, WhatsApp) verifies its signature before doing anything.
- Repeated webhooks cannot cause double bookings or double texts (message IDs are stored).

**Many businesses in one system**
- Every query that reads or writes business data is limited by `business_id`.
- Tests prove business A cannot read or change business B's data, including through the admin screen and the agent's tools.

**Login and admin screen**
- Passwords are hashed. Sessions expire. There is a limit on login attempts.
- Roles are enforced on the server, not only in the screen.
- Protection against common web attacks: injected database commands, cross-site scripting, cross-site request forgery.

**The AI agent**
- Tools have the smallest power possible and check the business and customer on every call.
- Customer text can never change instructions, reveal them, or reach other customers' data. There are tests for this.
- Step and cost limits exist. A loop ends with a handoff.
- Sensitive topics and emergencies go to a person.

**Compliance rules from `PRODUCT.md`**
- AI and recording disclosure starts every call.
- STOP and HELP work. Quiet hours are respected. No outbound marketing.
- A record exists of when each customer first contacted the business.

**Data and privacy**
- Only needed personal data is stored. Old chat and call text is deleted on schedule.
- Backups are encrypted and stored off the server. A restore has been tested.
- Logs do not contain secrets or full message text beyond what is needed.

**Server and deployment** (read with the `deploy-vps` skill)
- Login only with SSH keys. No direct root login. Firewall on. Automatic security updates.
- Postgres is not open to the internet. Containers do not run as root.
- Uptime alerts exist, and a kill switch works.

**Dependencies**
- Run a dependency check (for example `pip-audit`) and a secret scan. List known problems.

## Report format

For each finding:
- **Severity:** High (fix before any client), Medium (fix soon), Low (note).
- **What is wrong:** one or two simple sentences.
- **What could happen:** a real-life example.
- **How to fix:** simple steps.
- **Status:** open, fixed, or accepted by the person (with reason).

End the report with: the top three risks, and what the person should check themselves.
