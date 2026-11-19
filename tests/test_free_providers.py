"""The free AI providers (Groq, Gemini): message translation, 'slow down' handling, backup, and the paid-key lock.
No real service is called. A fake web server (httpx MockTransport) answers instead."""
from __future__ import annotations

import json

import httpx
import pytest

from app.agent import llm as llmmod
from app.agent.llm import FallbackLLM, LLMUnavailable, OpenAICompatLLM, build_backend, to_openai_messages, to_openai_tools
from app.agent.tools import TOOL_SCHEMAS
from app.config import Settings, reset_settings_cache
from app.services.usage import llm_cost


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(text="Hello", tool_calls=None, usage=(120, 30)):
    msg = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}})


def _backend(handler, **kw):
    return OpenAICompatLLM(name=kw.pop("name", "groq"), base_url="https://example.test/v1", api_key="k",
                           client=_client(handler), sleep=lambda s: None, **kw)


def test_history_in_claude_format_becomes_openai_messages():
    history = [
        {"role": "user", "content": "book me tomorrow"},
        {"role": "assistant", "content": [{"type": "text", "text": "Checking."},
                                          {"type": "tool_use", "id": "t1", "name": "check_free_times", "input": {"date": "2026-10-13"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": '{"ok": true}'}]},
    ]
    out = to_openai_messages("SYSTEM", history)
    assert out[0] == {"role": "system", "content": "SYSTEM"}
    assert out[1] == {"role": "user", "content": "book me tomorrow"}
    assert out[2]["tool_calls"][0]["function"]["name"] == "check_free_times"
    assert json.loads(out[2]["tool_calls"][0]["function"]["arguments"]) == {"date": "2026-10-13"}
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": '{"ok": true}'}


def test_every_tool_schema_translates():
    tools = to_openai_tools(TOOL_SCHEMAS)
    assert len(tools) == len(TOOL_SCHEMAS)
    assert all(t["type"] == "function" and t["function"]["parameters"]["type"] == "object" for t in tools)


def test_text_and_tool_call_answers_are_read():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return _ok("", tool_calls=[{"id": "", "type": "function", "function": {"name": "get_business_info", "arguments": "{}"}}])

    res = _backend(handler).complete(model="openai/gpt-oss-120b", system="S", messages=[{"role": "user", "content": "hi"}],
                                     tools=TOOL_SCHEMAS, max_tokens=600)
    assert res.tool_calls[0].name == "get_business_info" and res.tool_calls[0].id  # an empty id from the service gets a made-up one
    assert res.content[0]["type"] == "tool_use"
    assert res.input_tokens == 120 and res.output_tokens == 30
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["max_tokens"] >= 1500 and seen["body"]["reasoning_effort"] == "low"


def test_bad_tool_arguments_do_not_crash():
    def handler(request):
        return _ok("", tool_calls=[{"id": "a", "type": "function", "function": {"name": "save_lead", "arguments": "{not json"}}])

    res = _backend(handler).complete(model="m", system="S", messages=[{"role": "user", "content": "x"}], tools=TOOL_SCHEMAS, max_tokens=600)
    assert res.tool_calls[0].input == {}


def test_slow_down_then_success():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "1"}) if calls["n"] < 3 else _ok("ok now")

    res = _backend(handler, max_wait_seconds=10).complete(model="m", system="S", messages=[{"role": "user", "content": "x"}], tools=[], max_tokens=300)
    assert res.text == "ok now" and calls["n"] == 3


def test_wait_too_long_gives_up_quickly():
    def handler(request):
        return httpx.Response(429, headers={"retry-after": "25"})

    with pytest.raises(LLMUnavailable):
        _backend(handler, max_wait_seconds=12).complete(model="m", system="S", messages=[{"role": "user", "content": "x"}], tools=[], max_tokens=300)


def test_unknown_option_is_dropped_and_retried():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append("reasoning_effort" in body)
        return httpx.Response(400, json={"error": "unknown"}) if "reasoning_effort" in body else _ok("fine")

    res = _backend(handler).complete(model="openai/gpt-oss-20b", system="S", messages=[{"role": "user", "content": "x"}], tools=[], max_tokens=300)
    assert res.text == "fine" and seen == [True, False]


def test_empty_answer_and_network_errors_become_llm_unavailable():
    with pytest.raises(LLMUnavailable):
        _backend(lambda r: _ok("")).complete(model="m", system="S", messages=[{"role": "user", "content": "x"}], tools=[], max_tokens=300)

    def boom(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(LLMUnavailable):
        _backend(boom).complete(model="m", system="S", messages=[{"role": "user", "content": "x"}], tools=[], max_tokens=300)


def test_the_key_never_appears_in_error_text():
    def handler(request):
        return httpx.Response(401, text="bad key k")

    with pytest.raises(LLMUnavailable) as exc:
        _backend(handler).complete(model="m", system="S", messages=[{"role": "user", "content": "x"}], tools=[], max_tokens=300)
    assert "k" != str(exc.value) and "Bearer" not in str(exc.value)


def test_backup_provider_takes_over_and_uses_its_own_model():
    used = []

    def main_handler(request):
        used.append(("main", json.loads(request.content)["model"]))
        return httpx.Response(503)

    def backup_handler(request):
        used.append(("backup", json.loads(request.content)["model"]))
        return _ok("from backup")

    main = _backend(main_handler, name="groq")
    backup = _backend(backup_handler, name="gemini", model_override="gemini-flash-test")
    res = FallbackLLM([main, backup]).complete(model="openai/gpt-oss-120b", system="S", messages=[{"role": "user", "content": "x"}], tools=[], max_tokens=300)
    assert res.text == "from backup"
    assert used[-1] == ("backup", "gemini-flash-test")


def test_both_providers_down_means_llm_unavailable():
    a, b = _backend(lambda r: httpx.Response(500)), _backend(lambda r: httpx.Response(500))
    with pytest.raises(LLMUnavailable):
        FallbackLLM([a, b]).complete(model="m", system="S", messages=[{"role": "user", "content": "x"}], tools=[], max_tokens=300)


# ---------------- the money lock ----------------
def test_paid_anthropic_is_locked_by_default(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-used")
    monkeypatch.delenv("ALLOW_PAID_LLM", raising=False)
    reset_settings_cache()
    try:
        with pytest.raises(LLMUnavailable, match="paid_provider_locked"):
            build_backend("anthropic")
        problems = " ".join(Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="x").check_llm_ready())
        assert "locked" in problems
    finally:
        reset_settings_cache()


def test_anthropic_can_never_be_a_silent_backup():
    problems = " ".join(Settings(_env_file=None, llm_provider="groq", groq_api_key="g", llm_fallback_provider="anthropic",
                                 anthropic_api_key="x", allow_paid_llm=True).check_llm_ready())
    assert "backup" in problems
    with pytest.raises(LLMUnavailable):
        llmmod.build_backend("anthropic", is_fallback=True)


def test_default_settings_are_free_and_cost_nothing():
    s = Settings(_env_file=None)
    assert s.llm_provider == "groq" and s.allow_paid_llm is False and s.is_free_provider
    assert llm_cost("openai/gpt-oss-120b", 1_000_000, 1_000_000) == 0.0
    assert llm_cost("gemini-anything", 5_000, 500) == 0.0


def test_gemini_needs_a_model_id():
    problems = " ".join(Settings(_env_file=None, llm_provider="gemini", gemini_api_key="g").check_llm_ready())
    assert "GEMINI_MODEL" in problems


def test_voice_provider_follows_the_main_provider():
    assert Settings(_env_file=None, llm_provider="groq").vapi_model_provider == "groq"
    g = Settings(_env_file=None, llm_provider="gemini", gemini_model="m1")
    assert g.vapi_model_provider == "google" and g.vapi_model == "m1"


def test_whole_agent_works_through_a_free_provider(biz):
    """A full chat turn: the 'AI' asks for the business facts, then answers. Proves history translation works end to end."""
    from app import db as dbmod
    from app.agent.runner import handle_customer_text
    from app.models import Business
    from app.services.adapters import get_adapters
    from app.services.conversations import get_or_create_customer, get_or_create_sms_conversation

    from .conftest import NOW
    from .helpers import PHONE

    state = {"n": 0, "second_request": None}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return _ok("", tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "get_business_info", "arguments": "{}"}}])
        state["second_request"] = json.loads(request.content)
        return _ok("We open at 8 AM on weekdays.")

    with dbmod.tenant_session(biz) as db:
        b = db.get(Business, biz)
        cust = get_or_create_customer(db, b, PHONE, NOW)
        conv = get_or_create_sms_conversation(db, b, cust, NOW)
        res = handle_customer_text(db, b, cust, conv, "What time do you open?", channel="sms", now=NOW,
                                   adapters=get_adapters(), llm=_backend(handler))
    assert "8" in res.reply and res.steps == 2
    roles = [m["role"] for m in state["second_request"]["messages"]]
    assert roles[0] == "system" and "tool" in roles and "assistant" in roles  # the tool result was sent back in OpenAI form


def test_placeholder_key_is_treated_as_missing():
    problems = " ".join(Settings(_env_file=None, llm_provider="groq", groq_api_key="PASTE_YOUR_FREE_GROQ_KEY_HERE").check_llm_ready())
    assert "GROQ_API_KEY is missing" in problems


def test_smart_characters_in_replies_become_plain():
    from app.agent.runner import clean_reply

    raw = "Booked for Tuesday, October 13 at 10:00 AM. Call 512‑555‑0188 — it’s “ready”.​"
    out = clean_reply(raw)
    assert out == 'Booked for Tuesday, October 13 at 10:00 AM. Call 512-555-0188 - it\'s "ready".'
    assert all(ord(ch) < 128 for ch in out)  # plain characters only: one SMS segment holds 160 of them


def test_a_chat_reply_with_smart_characters_is_cleaned_end_to_end(biz):
    from app import db as dbmod
    from app.agent.runner import handle_customer_text
    from app.models import Business
    from app.services.adapters import get_adapters
    from app.services.conversations import get_or_create_customer, get_or_create_sms_conversation

    from .conftest import NOW
    from .helpers import PHONE

    backend = _backend(lambda r: _ok("We open at 8 AM on weekdays — see you then."))
    with dbmod.tenant_session(biz) as db:
        b = db.get(Business, biz)
        cust = get_or_create_customer(db, b, PHONE, NOW)
        conv = get_or_create_sms_conversation(db, b, cust, NOW)
        res = handle_customer_text(db, b, cust, conv, "When do you open?", channel="sms", now=NOW, adapters=get_adapters(), llm=backend)
    assert res.reply == "We open at 8 AM on weekdays - see you then."


def test_clean_reply_removes_stage_directions_the_model_wrote_in_brackets():
    from app.agent.runner import clean_reply

    assert clean_reply("I have 512-555-0188. Is that correct?(Waiting for confirmation...)") == "I have 512-555-0188. Is that correct?"
    assert clean_reply("Is that right? (waiting for your reply)") == "Is that right?"
    assert clean_reply("Booked. (Calling the booking tool now)") == "Booked."
    # normal brackets stay
    assert clean_reply("Drain cleaning is $149 (about one hour).") == "Drain cleaning is $149 (about one hour)."
    assert clean_reply("Call us (512) 555-0100 today.") == "Call us (512) 555-0100 today."
