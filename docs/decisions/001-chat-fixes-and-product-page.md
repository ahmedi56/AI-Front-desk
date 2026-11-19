# 001 - Chat fixes and the product page (2026-10-10)

## What we built

1. Four fixes for the website chat, after a real demo chat ended with "technical problem".
2. A public product page at `/product`, built from the owner's design file.

## The chat fixes

- **The reason is saved.** When the AI service fails, the app now saves a short code such as `groq:429`
  (429 means "slow down, too many requests"). The owner sees it in the alert. Before, nothing was saved.
- **One more chance on the website chat.** The first failure says "Please send your message again in a moment".
  Only a second failure hands the chat to a person. Texts (SMS) still hand over at the first failure.
- **No loop on a wrong phone number.** If the number is not a valid US number, the chat asks the customer one clear
  question. Before, the AI tried the same wrong number again and again, and used up the free limit.
- **Two new guards.** A guard is a check on the AI's answer before the customer sees it.
  - The answer may not show a phone number that the customer never typed.
  - If the AI glues many answers together, the customer gets only the first one.

## Why we chose this

- Free AI services are often busy for a few seconds. Losing a customer for that is too strict.
- Fixed rules in our own code are safer than hoping the AI behaves.

## What we rejected, and why

- **Making the AI instructions shorter to save tokens.** The instructions passed the live safety test. Changing
  them needs the live test again, so this waits.
- **Waiting 30 seconds inside one answer.** A visitor would see nothing for too long.
- **The product page at `/`.** That address is the dashboard and must ask for a login. The page is at `/product`.
- **Loading the font from Google.** Our security rule blocks outside files. The font file is in the project.

## What could go wrong

- The phone guard could block a real number if the AI writes it in a strange way. Then the chat asks the customer
  for the number again. That is annoying, but safe.
- We still do not know for sure why the demo chat failed. The new reason code will tell us next time.

## What you should check

1. Open `http://localhost:8000/product`. It should look like your design file.
2. In the demo chat, book a visit and give the number `555-123-4567`. The chat should ask for a valid number once.
3. If "technical problem" comes again, open the Inbox. The alert shows a code like `groq:429`. Send it to Claude.

## New words

- **Guard**: a check on the AI's answer before the customer sees it. Example: the price guard removes a price the
  owner never wrote.
- **Rate limit**: the most requests a service accepts in one minute. Example: the free Groq account says "slow
  down" when a chat uses too many tokens too fast.
- **Reason code**: a short label for an error, such as `groq:429`. Example: it tells us the service was busy.
- **Self-hosted font**: a font file kept on our own server, not loaded from another company.
