# 004 - Weekly email report to the client (2026-10-10)

## What we built

Every Monday at 8 AM (the business's own time), the client gets one short email about the week before:
customers answered, visits booked, leads saved, emergencies, and how many customers came outside opening hours.
The same text is saved as a note on the Today page. A button in Settings sends a report of the last 7 days at once.

## Why we chose this

- **It shows the client what they pay for.** The line "Answered outside your opening hours" counts customers who
  would have reached nobody.
- **Plain text, numbers only.** No customer names, phone numbers or messages are in the email. Email is not a safe
  place for private data, and plain text arrives in every mail program.
- **We reused the daily report code** and the background worker (the program that does timed jobs).

## What we rejected, and why

- **A weekly text message.** Texts cost the client money, and your rules keep texts for urgent things.
- **A pretty email with pictures and colors (HTML).** More work, more ways to break, and often blocked or cut by
  mail programs. It can come later.
- **Sending a report with zero customers.** You decided to skip it: it only reminds the client they paid for nothing.
- **Sending only on Monday at exactly 8 AM.** If the worker is off at that minute, the report would be lost. It is
  sent at the first chance after Monday 8 AM, once per week.

## What could go wrong

- **No email arrives.** Email (SMTP) is not set up on the laptop and was never tested with a real mail server.
  Until then the report is only on the Today page.
- **The worker is not running.** Then no report is made at all. On a server the worker must always run.
- **Wrong "outside opening hours" number** if the client's opening hours in Settings are wrong. It uses those hours.

## What you should check

1. Run `app.migrate`, start the app, chat once on the demo website.
2. Open Settings. Under "Email for reports" there is a checkbox "Send me a weekly report by email" and a button
   "Send me a report of the last 7 days now". Click the button.
3. You should see "The email could not be sent (email is not set up on this server yet). The report is on your
   Today page." Open Today: the report is in the list of notes.

## New words

- **Worker**: a background program that does timed jobs, such as reminders and reports. Example: every minute it
  checks if a report is due.
- **SMTP**: the standard way a program sends email through a mail company. Example: the app gives the weekly
  report to the SMTP server, and the server delivers it.
