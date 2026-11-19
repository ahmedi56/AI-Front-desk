# Accounts you must create (in this order)

Write every password in a password manager (Bitwarden free is fine). Never put keys in chat messages, emails or Git.
For every service below, turn on two-factor login (2FA = a second code from your phone) before anything else.

**Day 1 check.** Before paying for anything, open the sign-up page of each service and check that Tunisia is accepted
(for your address, your phone number and your payment card / bank). I could not verify this for you.
If one refuses you, stop and tell Claude which one. Common fixes: a company registered in a supported country (for example Estonia e-Residency,
or a US LLC), or a partner who owns the account.

## 1. The AI: free accounts only (Groq first, Gemini as backup)
Rule from the owner: **no paid AI key, and no card on any AI account.**
1. Groq: go to console.groq.com, create an account, open **API Keys**, create a key. Put it in `.env` as `GROQ_API_KEY`.
   Do not add billing. The default model is `openai/gpt-oss-120b`. Look at your own limits page on Groq: the free limits change.
2. Gemini (backup): go to Google AI Studio, create an API key (do not link billing). Put it in `.env` as `GEMINI_API_KEY`.
   Copy the exact model id of a free "Flash" model from the AI Studio model list into `GEMINI_MODEL`. Then set `LLM_FALLBACK_PROVIDER=gemini`.
   Remember: Google may read free-tier prompts. Use fake data only while you test.
3. Free tiers are for testing. They have no uptime promise and small daily limits. See `docs/FREE-AI-MODELS.md`.
4. Later, when a client pays and wants it, the client opens their own AI account (see the offer guide). Only then does `ALLOW_PAID_LLM` matter.

## 2. A domain name and a server
1. Buy a domain (any registrar with 2FA). You need one name, for example `frontdesk-yourbrand.com`.
2. Rent a VPS (a small virtual server): Ubuntu 24.04, 2 CPU, 4 GB memory is enough to start. Prices change often, compare on the day.
3. Point the domain's **A record** to the server's IP address.
See `docs/DEPLOY.md`.

## 3. Twilio (US phone numbers and texts)
1. Create the account. Upgrade from trial when you are ready for real clients.
2. For each client you need: (a) a US phone number for texts, (b) the carrier registration called **A2P 10DLC**:
   a *Brand* (the client's real business name, tax number EIN, address, website) and a *Campaign* (what texts you send and sample messages).
   Approval takes days to weeks. **Until it is approved, the client uses voice only** (leave "carrier registration approved" unchecked in the admin page).
3. Messaging webhook for that number: `https://YOUR-DOMAIN/webhooks/twilio/sms` (method POST).
4. Copy the Account SID and Auth Token into `.env`. The Auth Token is also what proves a message really came from Twilio. Keep it secret.
5. Campaign sample messages must include the real opt-out text (Reply STOP to stop). Our code already answers STOP / HELP / START.
6. Your OWN number for owner alerts: `TWILIO_FROM_NUMBER`. Alerts to owners by text need your own brand registration. Email alerts always work without it.

## 4. Vapi (the voice platform)
1. Create the account and add a small amount of credit.
2. Create a **credential** of type Bearer Token: name `frontdesk`, header name `X-Vapi-Secret`, "Include Bearer prefix" OFF, token = a long random string.
   Put the same string in `.env` as `VAPI_WEBHOOK_SECRET`.
3. For each client, get a phone number in Vapi (or import the Twilio number). In that number's settings leave the assistant EMPTY and set the **Server URL**
   to `https://YOUR-DOMAIN/webhooks/vapi` with the credential above. (An empty assistant makes Vapi ask our server who should answer. That is how every client gets its own business facts.)
4. In the admin page, put this same number in "voice number" for that business.
5. Choose a voice in the Vapi dashboard and put it into `VOICE_EXTRA_ASSISTANT_JSON` (see `.env.example`). Turn on call recording only after your lawyer says the disclosure is right.

## 5. The client's phone: forward unanswered calls
The client keeps their normal number. Their phone company sets "forward when no answer / busy" to the Vapi number.
(On most US mobile carriers: dial `*71` + the forwarding number, or set it in the phone's call settings.) Test it with a real call.

## 6. Google Calendar (optional, per client)
1. console.cloud.google.com: create a project, enable the Google Calendar API, create an OAuth client (type: Web application).
2. Redirect address: `https://YOUR-DOMAIN/oauth/google/callback`.
3. Put the client id and secret in `.env`. Each client then clicks "Connect Google Calendar" in Settings.
4. Google may require an app review before many users can connect. Until then, only accounts you add as "test users" can connect.
   If a client does not use Google Calendar, bookings still work: they live in this system and show on the Appointments page.

## 7. Email (for daily reports and alerts)
Any SMTP service works (your domain's mail host, or a transactional email service). Fill the `SMTP_*` lines in `.env`.
Send yourself a test: the daily report goes out at 6 pm local time of the business.

## 8. Getting paid
Open and test your payout route (Stripe, Wise, Payoneer, or a partner) with a tiny real payment BEFORE the first client.
Start with invoices by bank transfer / PayPal for the first 1-2 clients if card billing is not available to you yet.

## 9. Create your admin login
On the server: `docker compose exec app python -m app.cli create-admin you@yourdomain.com` (it asks for a 12+ character password).
Then open `https://YOUR-DOMAIN/login`, go to Admin, and add the first business.
