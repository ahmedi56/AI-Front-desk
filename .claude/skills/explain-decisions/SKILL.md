---
name: explain-decisions
description: After each finished task, write a short plain-English note explaining what was built, why this technology was chosen, what was rejected, and add new words to the glossary. Use at the end of every task.
---

# Explain decisions

The person you work for is not a programmer and has limited English vocabulary. They need to understand what you build and why, so they can check it and explain it to clients.

## Steps

1. Create `docs/decisions/NNN-short-title.md`. Use the next number in order.
2. Write these parts, in simple English with short sentences:
   - **What we built:** two or three sentences.
   - **Why we chose this technology:** the main reason, in everyday words.
   - **What we rejected, and why:** at least one alternative.
   - **What could go wrong:** the main risk, with a real-life example.
   - **What you should check:** three plain steps the person can do, such as "text the test number and see a reply".
   - **New words:** each new technical word with a one-sentence meaning and a short example.
3. Add each new word to `docs/GLOSSARY.md`, in alphabetical order, with the same meaning and example. Do not repeat words that are already there.
4. In your chat answer, give a three-line summary and the path of the note.

## Writing rules

- Use words a 12-year-old can follow. Define any word that is not common.
- Compare with real-life things when it helps, for example "a webhook is like a doorbell: WhatsApp rings it when a message arrives".
- Be honest. If something is a guess or not tested, say so.
- If the person says they do not understand, explain again with a new example. Do not repeat the same words.
