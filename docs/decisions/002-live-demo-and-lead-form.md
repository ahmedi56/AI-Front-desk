# 002 - Live demo chat and lead form on the product page (2026-10-10)

## What we built

1. **"Try the live demo".** A visitor on `/product` can chat with a demo plumber on the page itself.
2. **A lead form.** A visitor leaves a name, business name and email. You see it on your admin page under
   "Prospects". A prospect is a business owner who may become your client.

## Why we chose this

- **The demo reuses the chat bubble we already have.** It is the same one a client puts on a website. So the
  visitor sees the real product, and we wrote almost no new code.
- **Prospects have their own table.** They are YOUR sales leads, not a client's customers. No business can read
  them. On the real database (Postgres) a rule in the database itself blocks it, not only our code.
- **A hidden trap field against robots.** People never see it. Robots fill in every field, so we know them.

## What we rejected, and why

- **A CAPTCHA puzzle.** It annoys real plumbers on a phone, and it needs an outside company.
- **Saving prospects in the "leads" table.** That table belongs to businesses. Mixing them would be a privacy risk.
- **Showing the error text from the address bar.** Someone could put their own words on your page. The page only
  shows two fixed sentences.
- **A demo label on every chat.** A real client's chat must never say "not real". The label shows only for the one
  demo business you choose.

## What could go wrong

- **Many visitors use up the free AI limit of the demo business.** Then the demo chat says "send again" or hands
  over. Each visitor is limited, but a busy day could still reach Groq's free limit.
- **You miss a prospect.** The email alert only works when email (SMTP) is set up, and that was never tested with a
  real mail server. Until then, look at the admin page every day.
- **The database rule for prospects was tested only in code review, not on a real Postgres**, because the laptop
  has no Postgres. Run the full tests on Postgres before the page goes on a server.

## What you should check

1. Add `PRODUCT_DEMO_BUSINESS=demo` to `.env`, run `app.migrate`, start the app, open `/product`.
   Click the green "Try the live demo" button and ask a question. The chat must say "Demo only".
2. Fill in the form at the bottom and send it. You should see "Thank you. I will contact you within one business day."
3. Log in as the platform admin and open `/admin`. The new prospect is in the "Prospects" list.
   Click "Mark contacted".

## New words

- **Prospect**: a business owner who may become your client. Example: Joe fills in the form on your product page.
- **Trap field (honeypot)**: a hidden form field that only robots fill in. Example: if it has text, we save nothing.
- **Row level security policy**: a rule inside the database that decides who may read which rows. Example: a
  business can never read the prospects table.
