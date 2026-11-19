# Why I chose each thing (and what I rejected)

| Choice | Why | Rejected |
|---|---|---|
| **Python + FastAPI** | Small, fast, clear errors, great for webhooks. Claude Code writes it well. | Node (fine, but no gain), Django (heavier than needed) |
| **Postgres** | Strong rules: row isolation (RLS), "no overlapping bookings" constraint, safe locks | SQLite in production (no RLS, weak under many writers). SQLite is used only for quick tests. |
| **RLS in the database** | If my code has a bug, the database still blocks cross-business reading | "WHERE business_id" in code only (one forgotten line = leak) |
| **Fixed safety rules before the AI** | Emergencies and "talk to a person" must work even if the AI is wrong or down | Only prompt instructions |
| **Small tools with no ids** | The AI cannot ask for another customer's data because the tool has no place to put it | General database access / MCP with free queries |
| **One send_sms() gate** | STOP, quiet hours and limits are enforced in one place, so nobody can forget them | Checks scattered in each feature |
| **Server-rendered pages (Jinja2)** | No big JavaScript app to maintain or attack; a strict content policy works | React single-page app |
| **Voice platform (Vapi) + our own brain** | Voice is hard (phone audio, interruptions). The logic and data stay with us so the platform can be replaced | All logic inside the voice platform |
| **Free AI first: Groq (gpt-oss-120b), Gemini as backup; paid Claude locked** | The owner has no money. Free tiers cost $0 for testing. A switch (`ALLOW_PAID_LLM`) keeps a paid key from being used by accident. The code keeps an optional Claude provider for later, when a client pays. | Paying for the AI from day one |
| **Backup AI provider** | Free tiers say "slow down" or go down. The second provider answers instead. If both fail, the customer is handed to a person. | A single provider |
| **Own database is the booking truth; calendar is a copy** | A calendar outage must not stop bookings | Calendar as the only source |
| **One appointment at a time per business** | Simple and correct for a one-crew shop; the lock makes double-booking impossible | Multi-technician scheduling (version 2) |
| **Docker + Caddy on one VPS** | Cheap, one box you can understand, automatic https | Kubernetes (far too much), serverless (hard with RLS and workers) |
| **Argon2 + signed cookies + CSRF + TOTP** | Standard, boring, proven | Building my own login scheme; SMS codes (can be intercepted) |
| **Cost caps everywhere** | A prank or loop must not make a $500 bill | Trusting good behavior |
| **Worker with a database lock** | Only one worker runs the reminders even if started twice | Separate scheduler service |

## What I did NOT build on purpose

- No WhatsApp (Meta approval takes time; add after the first clients).
- No billing module: use Stripe's hosted payment links by hand for the first clients.
- No live call transfer: the agent takes a message and alerts a person at once. Live transfer is a version-2 item.
- No marketing texts or calls: forbidden by design (legal risk).
