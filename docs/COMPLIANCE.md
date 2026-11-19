# Rules the product follows (and the ones a lawyer must check)

**I am not a lawyer. Have a US lawyer read this before you take money.**

## Built into the code
- **Disclosure on every call**: "This is an AI assistant for {business}. This call may be recorded." Played first. (Some states need everyone's consent to record; the sentence gives notice. Ask a lawyer whether it is enough in each state you serve.)
- **Robot question**: if asked "are you a robot/real person", the agent says it is an AI. Always.
- **Texting**: only replies to people who texted or called first, plus appointment reminders, max 2 follow-ups, one review request. **No marketing.**
- **STOP / HELP / START** handled before the AI. After STOP nothing is sent (only the one confirmation). A bare "cancel" counts as STOP on purpose (safe side).
- **Quiet hours**: no texts 9 pm to 8 am in the business's time zone (follow-ups and reminders wait until morning).
- **Emergencies** (gas, fire, injury): the agent tells the caller to call 911 and alerts the owner at once. It does not try to diagnose.
- **Data deletion**: conversations are deleted after the retention days. We keep the STOP flag so we never text them again.
- **Kill switch**: the business can turn the agent off. Calls then forward to the handoff phone.
- **No payment details, no advice**: the agent is told never to ask for card or bank details and never to give medical, legal or financial advice. (Customers can still type anything; the prompt is a rule, not a filter. Do not sell to businesses that need to collect health or ID data.)

## Needs a lawyer or an account step
1. Contract with each client: you are a service provider; they are responsible for their customers' consent; limits on your liability; the agent can make mistakes.
2. Privacy policy and terms for the web chat widget.
3. A2P 10DLC registration per business (brand and campaign). Their website must show the privacy policy and how people opt in.
4. Recording-consent laws by state (e.g. California, Florida, Illinois need all-party consent).
5. State laws on AI disclosure (some states require it in more places, for example Utah and California for chatbots).
6. Business that give regulated advice (medical, legal, financial): **do not sell to them in the first version.**
7. Customer time zone: the code assumes the customer is in the business's time zone.
