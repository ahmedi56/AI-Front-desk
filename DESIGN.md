# AI Front Desk design system (the ONE look - keep it exactly)

The owner approved this design on 2026-10-10. Every new screen must look like it belongs to it.
Reference screenshots: `docs/design/screens/` (look at them before changing any screen).

## The idea
"Clean and trusted" for a US plumber or HVAC owner who checks the dashboard on a phone between jobs.
Calm navy and white, one bold element per screen, numbers that line up, no decoration.

## Colors (all defined once in `app/static/app.css` `:root`)
| Token | Hex | Use |
| --- | --- | --- |
| `--navy` / `--ink` | #14233B | side menu, status band, chat header, main text |
| `--navy-2` | #1D3352 | menu hover/current item |
| `--paper` | #F6F7F5 | page background (cool off-white, never cream) |
| `--surface` | #FFFFFF | cards, tables, inputs |
| `--sunk` | #EEF1F0 | AI chat bubbles, code boxes, quiet fills |
| `--line` | #DCE1E6 | borders, dividers |
| `--muted` | #5B6878 | secondary text, hints |
| `--action` | #1F5BD8 | buttons, links, customer chat bubbles |
| `--ok` / `--ok-soft` | #1E7A55 / #E3F2EA | ON, booked, done, connected |
| `--urgent` / `--urgent-soft` | #B42318 / #FDECEA | OFF, kill switch, urgent, errors |
| `--warn` / `--warn-soft` | #9A5B00 / #FDF1DC | needs a person, no key yet |
| live dot / menu marker | #6EE7A8 | the pulsing "on" dot, current menu item marker |

Light mode ONLY (no dark mode): the owner wants it to look exactly like the screenshots.

## Type
- System font stack (`system-ui, -apple-system, "Segoe UI", Roboto, ...`): fast, no outside font service.
- h1 1.6rem/700, h2 1.1rem/650, body 16px/1.55. Numbers use `font-variant-numeric: tabular-nums` (`.num`, strip, tables).
- Sentence case everywhere. No ALL CAPS labels (only the URGENT tag).
- The product page (`/product`) alone uses Public Sans. The font file is in `app/static/fonts/` (never loaded from Google).

## Layout
- Desktop: navy side menu 240px (logo square + business name, icon + label links, Log out at the bottom) + content max 1060px.
- Phone (< 880px): the menu becomes a navy top bar; links scroll sideways; no sideways page scroll ever.
- Page head: h1 + one muted sentence; actions (Download CSV) on the right.
- Radii: 6 / 10 / 16 px (small, cards, big bands). Cards: white, 1px `--line` border, NO shadows (except chat bubble pop-up on client sites).

## Components (class names in app.css)
- `.status` - THE bold element of Today: navy band, pulsing green `.live` dot, "AI assistant: ON", kill switch button. Red band when OFF.
- `.strip` - today's numbers in ONE row with dividers (not separate cards). 6 cols -> 3 -> 2 on phones.
- `.setup` + `.checklist` + `.bar` - "Finish setting up" with progress bar; done items green dot + line-through.
- `.card`, `.card.table-wrap` + table - rows 11px/14px padding, `td.when` for dates, `td.empty` centered empty text.
- `.tag` (pill) + `.ok` `.warn` `.urgent/.off` variants.
- Buttons: `button` (blue), `.secondary` (white outline), `.danger` (red), `.small`. Icons: inline SVG from `templates/_icons.html` (1.8 stroke, 24 grid).
- Settings: left section menu `.toc` + cards with ids + sticky `.savebar`. A second part inside one card uses
  `.subsection` (thin top line), for example "Backup AI key" in the AI account card; its button is `.secondary`.
- Login: split screen `.auth` - navy left (promise + 3 green-dot points), form right.
- Chat widget: navy `.chat-head` (round initial avatar, name, "AI assistant, replies in seconds"), grey `.chat-note` disclosure,
  AI bubbles left grey, customer bubbles right blue, `.typing` dots, `.chips` quick buttons (urgent one red), round input + Send,
  "For a fire, gas smell or injury, call 911." footer.
- Product page: `templates/product.html` + its own `static/product.css` (classes start with `pp-`). Built from the
  owner's design file (2026-10-10). It is the only page with a shadow (sample chat card) and a mint #6EE7A8 button.
- Chat bubble on client sites: `static/embed.js` + `embed.css` - navy pill "Chat with us", 380x600 panel, full screen on phones.

## Hard rules (security + consistency)
1. NO inline `style=""` and NO inline `<script>` in app pages (the CSP blocks them; a test checks it). All CSS in `app/static/app.css` (the product page: `product.css`).
2. Link static files with `{{ static_url('app.css') }}` (version stamp), never a bare `/static/...` link.
3. Reuse tokens and existing classes. New color? Add a token to `:root` first, and only if no existing one fits.
4. Words: plain verbs, sentence case, say what happens ("Save key", "Turn OFF (kill switch)"); empty states tell the next step.
5. Every change: run `python -m pytest`, then the browser walkthrough (`evals/browser/README.md`), then take screenshots
   (desktop 1280 + phone 390) and compare with `docs/design/screens/`.
