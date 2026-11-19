# Prompt for the next session (copy everything below the line)

---

Read HANDOFF.md, CLAUDE.md and DESIGN.md in my front-desk folder
(C:\Users\T560\Desktop\front-desk) before doing anything.

STEP 1 - FIND SKILLS FIRST
Use my find-skills skill to search for skills that can make this product better:
- design: UI/UX polish, dashboards, chat widgets, landing pages, accessibility, mobile
- features: SaaS onboarding, booking/scheduling, CRM and leads, analytics, notifications
- quality: testing, security review, Python/FastAPI, deployment to a VPS
Show me a short list (skill name, what it does, why it helps this project) and
install or load the ones that fit. Then use them for the work below.

STEP 2 - IMPROVE FEATURES AND DESIGN
- Start with step 1 of "Next steps" in HANDOFF.md, then suggest the next most
  useful features for getting my first US clients.
- Design: keep the approved look (front-desk-design skill, DESIGN.md, screenshots
  in docs/design/screens/): navy side menu, status band with kill switch, numbers
  strip, same colors, light mode only. You may IMPROVE details (spacing, empty
  states, small interactions, mobile), but before changing the overall look,
  show me screenshots and ask me first.
- Big task: show me a short plan first, then wait for my OK.

STEP 3 - CHECK BEFORE "DONE"
- Tests first, then code. Run python -m pytest (all pass).
- Run the browser walkthrough (evals/browser/README.md) - all steps pass.
- Take screenshots at 1280px and 390px wide and compare with docs/design/screens/.
- Copy changed files to my laptop folder and update HANDOFF.md.

MY RULES
- Never use a paid AI key (free Groq/Gemini only). Never add a card anywhere.
- Simple English, short sentences, explain each new technical word once.
- Give me exact PowerShell commands to copy, and say what I should see.
