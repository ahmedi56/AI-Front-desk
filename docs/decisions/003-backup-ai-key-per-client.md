# 003 - A backup AI key for each client (2026-10-10)

## What we built

Each client can now save two free AI keys of their own in Settings > AI account: a main key and a backup key.
If the main AI is busy or down, that client's assistant asks the backup AI. The customer gets a later answer, not
"technical problem".

## Why we chose this

- **Free AI services are often busy.** Our demo chat failed for this reason. On a real server every client uses
  their own key, so the backup in `.env` did not help them. Now each client has their own backup.
- **We reused code that already existed.** The "try the main one, then the next one" part was already built and
  tested for the shared key. We only connected it to the client's own keys.
- **The backup must be a different company than the main key** (for example Groq as main, Gemini as backup).
  Two keys from one company are down at the same time.

## What we rejected, and why

- **Two keys from the same company.** No real help, and a second account there would be a way around that
  company's free limits. The Settings page does not even offer the same company in the list.
- **Using your own key as a backup for clients.** Your rule: you never pay for, or share limits with, a client.
- **A paid provider as backup.** Never. Only Groq and Gemini (free) are accepted.
- **A backup key form on the admin page.** The page is already full. The admin page only shows a small tag
  "backup key ...1234". The client (or you, logged in as them during setup) saves it in Settings.

## What could go wrong

- **The backup is slow.** In our test, real Gemini took 5 to 13 seconds per call, and sometimes gave no answer.
  One chat turn needs 2 calls, so an answer from the backup can take 15 to 25 seconds.
- **The two AIs word answers differently.** The safety rules are in our code, so they hold with any AI. But the
  live test was run with Groq only, never with Gemini answering whole chats.
- **Both are down.** Then the old rule applies: the website chat says "send again" once, then hands over to you.

## What you should check

1. Run `app.migrate`, start the app, log in as the demo owner, open Settings > AI account. Save your free Groq
   key as the main key. A new block "Backup AI key" appears. Save your free Gemini key there, with the model name.
2. You should see two green "Connected" tags, and only the last 4 characters of each key.
3. Open Costs. The line "Answers from the backup AI" shows how often the main AI was busy (0 at the start).

## New words

- **Backup key**: a second AI key that is used only when the first one fails. Example: Groq says "slow down", so
  the assistant asks Gemini.
- **CSRF token**: a secret code in each form that proves the form came from our own page. Example: a form sent
  from another website has no code and is refused.
