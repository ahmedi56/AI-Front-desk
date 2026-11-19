# AI Front Desk starter kit: start here

This kit gives your building agents their instructions. You do not need to know programming. You do need to check their work (see "What you must check").

## What is inside

- `PRODUCT.md`: the product brief. It says what we build and the rules it must follow.
- `CLAUDE.md`: the project rules the agent reads at the start of every session.
- `.claude/skills/`: four skills. A skill is a folder with a `SKILL.md` file that teaches the agent one repeatable task.
  - `acceptance-test`: turns plain-English scenarios into automatic tests.
  - `security-review`: checks the code against a security checklist.
  - `explain-decisions`: explains every step in simple English and builds your glossary.
  - `deploy-vps`: puts the product on a server safely.
- `.claude/agents/`: six subagents. A subagent is a specialist worker the main agent can hand a job to.

## How to start

1. Create an empty folder on your computer, for example `ai-front-desk`.
2. Unzip this kit into it, so that `PRODUCT.md`, `CLAUDE.md` and the `.claude` folder are inside.
3. Open the folder in the Claude desktop app, in the Claude Code section. You need a paid Claude plan or API credits for Claude Code. Check claude.com/pricing.
4. If the skills or subagents do not show up, check Claude Code's documentation for where it looks for them. File locations can change.
5. The full product is already built in the `front-desk` project (read `START-HERE.md` in it). This old kit is kept for the rules. If you want to continue building, send this first message:

> Read PRODUCT.md and CLAUDE.md. Use plan mode. Propose the architecture: for each technology say what it is in simple English, why you chose it, and what you rejected and why. Do not write any code yet.

6. Read the answer. If you cannot explain a choice in one sentence, reply: "Explain again with an example."
7. Then follow the runbook in the guide document, step by step.

## Accounts to create first (use a separate email address for the project)

- Free AI account (Groq, and Google AI Studio as backup). No card, no paid AI key.
- Voice platform account (Vapi or Retell). The architecture step will choose one.
- Twilio account, or another phone provider, for US numbers and texts. Check that you can open an account from Tunisia and buy US numbers before you spend money.
- Google account for Calendar.
- Private GitHub repository.
- VPS provider and a domain name, later, at the deploy step.
- Turn on 2FA (a code on your phone) everywhere.

## What you must check

- Test with a real phone after every step. Text it and call it.
- Read 10 real conversations every week.
- Log in as business A and try to see business B's data. You must not be able to.
- Approve each command that touches the server. If you do not understand a command, make the agent explain it first.

## Stop signs

Pause if an agent wants to turn off or skip the tests, asks for your passwords or personal keys, says "done" without showing test results, or makes a change you cannot explain in one sentence.

## Legal note

The rules in `PRODUCT.md` about AI disclosure, call recording and text messages come from vendor articles, not from a lawyer. Pay a US lawyer once to review your client agreement and call script before the first client.
