# Free AI models: which one, why, and the limits

Numbers below come from the providers' own pages and from articles dated 2026. **Free limits change often. Look at your own limits page.**

## Recommended setup for testing ($0)

| Role | Choice | Why |
|---|---|---|
| Main | **Groq, model `openai/gpt-oss-120b`** | Open model, supports tools, clear published limits, free without a card, the article says free-tier data is not used for training |
| Backup | **Google Gemini, a free Flash model** (copy its id into `GEMINI_MODEL`) | A different company, so it is rarely down at the same time. Likely strong quality |
| Paid Claude | **Locked** (`ALLOW_PAID_LLM=false`) | Owner rule: nothing that costs money |

## Limits (Groq, base limits from its docs page)
- `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`: 30 requests a minute, 1,000 a day, **8,000 tokens a minute**, 200,000 tokens a day. Limits are per model.
- Our agent sends the instructions, the tool list and the history on every step, so each AI step is roughly 2,000 to 4,000 tokens. That means only a few steps per minute and roughly 50 to 100 steps a day on one model. Fine for testing, not enough for a real client. The live test script waits between calls and counts tokens.

## Gemini free tier
- Google no longer publishes the free limits. Open Google AI Studio to see yours. In December 2025 the free limits were cut sharply.
- **Free-tier prompts may be used to improve Google's products and read by people.** Fake data only.

## Not available
- xAI Grok: no ongoing free API tier was found. Check your own console.

## How good is it, really?
Nobody can promise it matches Claude. What is certain:
- The 12 safety scenarios (emergency, human request, robot question, tricks) are handled by fixed rules in code, so they pass with any model.
- Price honesty, booking wording and phone read-back depend on the model. Run `python -m evals.run_live_eval` and read every answer.
- Free models may reply more slowly, and "thinking" models may use part of the token limit before answering. The code gives them more room and retries once without the special option if the model refuses it.

## What happens when a provider is busy
1. The code waits a few seconds and tries again (up to the `LLM_MAX_WAIT_SECONDS` limit, 12 seconds by default).
2. If that fails, it tries the backup provider.
3. If both fail, the customer gets a polite message and the owner is alerted. The customer is handed to a person.

## Settings (in `.env`)
```
LLM_PROVIDER=groq
GROQ_API_KEY=...
LLM_FALLBACK_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=<exact id from AI Studio>
ALLOW_PAID_LLM=false
```

## Honest status
**Real Gemini as backup, tried on 2026-10-10 from the owner's laptop (model `gemini-3.8-flash`, fake data, about 10
small requests):** the chain Groq -> Gemini works, and Gemini understands our tools (it called `check_free_times`
and `get_business_info` correctly). But it was slow and uneven: 6 answers took 5 to 13 seconds, 3 gave no answer in
30 seconds, and 1 said "high demand" (error 503). One chat turn needs 2 AI calls, so a backup answer can take 15 to
25 seconds. The backup only covers the shared key in `.env`; a business with its OWN key has no backup yet.

Before that day, all of this was tested with a fake web server that imitates the providers. **Groq through this
code path was checked only by the live test.** Small differences (for example how a service words an error) may need a fix after your first real run.
