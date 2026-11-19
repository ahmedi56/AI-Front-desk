---
name: front-desk-design
description: Keep the AI Front Desk's approved look exactly the same. Use before changing or adding ANY screen, template, CSS, the chat widget, the chat bubble, the demo website or the product page.
---

# Front Desk design (approved by the owner - do not restyle)

1. Read `DESIGN.md` (colors, type, layout, components, hard rules) and LOOK at the screenshots in
   `docs/design/screens/` before you change anything visible.
2. Build only with what exists: the tokens in `app/static/app.css` `:root`, its classes (`.status`, `.strip`, `.card`,
   `.tag`, `.checklist`, `.chat-*`, `.chips`...), icons from `app/templates/_icons.html`, and `base.html` (side menu).
   Do not add a new font, a new color, shadows, gradients, dark mode or a CSS framework.
3. No inline `style=""` or `<script>` in app pages (security rule, tested). Static files via `{{ static_url('file') }}`.
4. Write the test first (CLAUDE.md), then the change. Then:
   - `python -m pytest` (all pass),
   - the browser walkthrough in `evals/browser/` (all steps pass),
   - screenshots at 1280 px and 390 px wide, compared side by side with `docs/design/screens/`.
     Anything that does not match is a bug: fix it before saying "done".
5. If the owner asks for a different look, show it first (screenshot) and ask before changing the whole app.
