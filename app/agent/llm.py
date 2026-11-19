"""The AI model behind a tiny interface. Tests use a fake one; production uses Claude."""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from ..config import get_settings

log = logging.getLogger("frontdesk.llm")


class LLMUnavailable(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    content: list[dict] = field(default_factory=list)  # assistant blocks, put back into the history
    backup: bool = False  # true when the backup provider answered because the main one failed


class LLM(Protocol):
    def complete(self, *, model: str, system: str, messages: list[dict], tools: list[dict], max_tokens: int) -> LLMResult: ...


class AnthropicLLM:
    def __init__(self, api_key: str | None = None):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key or get_settings().anthropic_api_key, timeout=25.0, max_retries=2)

    def complete(self, *, model, system, messages, tools, max_tokens) -> LLMResult:
        try:
            resp = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                tools=tools,
            )
        except self._anthropic.APIError as exc:
            log.warning("anthropic error: %s", type(exc).__name__)
            raise LLMUnavailable(type(exc).__name__) from exc
        text_parts, calls, content = [], [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input or {})))
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input or {})})
        u = resp.usage
        in_tokens = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) + (
            getattr(u, "cache_creation_input_tokens", 0) or 0)
        return LLMResult(text="".join(text_parts).strip(), tool_calls=calls, input_tokens=in_tokens,
                         output_tokens=u.output_tokens or 0, model=model, content=content)



# ---------------------------------------------------------------------------------------------
# Free providers: Groq, Google Gemini and any service with an "OpenAI-style" API.
# We keep the history in the Claude block format inside the app, and translate it here.
# ---------------------------------------------------------------------------------------------
GROQ_URL = "https://api.groq.com/openai/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """Translate our history (Claude block format) into OpenAI chat messages."""
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in content if b.get("type") == "tool_use"]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user blocks: tool results (and possibly text)
            for b in content:
                if b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b.get("content", ""))})
                elif b.get("type") == "text":
                    out.append({"role": "user", "content": b.get("text", "")})
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                               "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
            for t in tools]


class OpenAICompatLLM:
    """Talks to Groq, Gemini (its OpenAI-style door) or any similar service. Free tiers say 'slow down' (HTTP 429)
    often, so we wait a little and try again. In a live chat we never wait longer than max_wait_seconds."""

    def __init__(self, *, name: str, base_url: str, api_key: str, model_override: str | None = None,
                 max_wait_seconds: float = 12.0, client: httpx.Client | None = None, sleep=time.sleep):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_override = model_override
        self.max_wait = max_wait_seconds
        self.client = client or httpx.Client(timeout=30.0)
        self._sleep = sleep

    def _post(self, body: dict) -> dict:
        waited, attempt = 0.0, 0
        sent_without_effort = False
        while True:
            attempt += 1
            try:
                r = self.client.post(f"{self.base_url}/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.HTTPError as exc:
                log.warning("%s network error: %s", self.name, type(exc).__name__)
                raise LLMUnavailable(f"{self.name}:network") from exc
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError as exc:
                    raise LLMUnavailable(f"{self.name}:bad_json") from exc
            if r.status_code == 400 and "reasoning_effort" in body and not sent_without_effort:
                body = {k: v for k, v in body.items() if k != "reasoning_effort"}  # this model does not know the option
                sent_without_effort = True
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                try:
                    wait = float(r.headers.get("retry-after", "")) if r.headers.get("retry-after") else 2.0 * attempt
                except ValueError:
                    wait = 2.0 * attempt
                wait = min(max(wait, 0.5), 30.0)
                if waited + wait > self.max_wait:
                    log.warning("%s says slow down (%s) and the wait would be too long", self.name, r.status_code)
                    raise LLMUnavailable(f"{self.name}:{r.status_code}")
                self._sleep(wait)
                waited += wait
                continue
            log.warning("%s error: HTTP %s", self.name, r.status_code)
            raise LLMUnavailable(f"{self.name}:{r.status_code}")

    def complete(self, *, model, system, messages, tools, max_tokens) -> LLMResult:
        model = self.model_override or model
        body: dict = {"model": model, "messages": to_openai_messages(system, messages), "temperature": 0.2,
                      # reasoning models spend part of this on thinking, so the limit is higher than for Claude
                      "max_tokens": max(max_tokens, 1500)}
        if tools:
            body["tools"] = to_openai_tools(tools)
        if self.name == "groq" and "gpt-oss" in model:
            body["reasoning_effort"] = "low"
        data = self._post(body)
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable(f"{self.name}:bad_shape") from exc
        text = (msg.get("content") or "").strip()
        calls, content = [], []
        if text:
            content.append({"type": "text", "text": text})
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            cid = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"  # some services leave the id empty
            calls.append(ToolCall(id=cid, name=fn.get("name", ""), input=args))
            content.append({"type": "tool_use", "id": cid, "name": fn.get("name", ""), "input": args})
        if not text and not calls:
            raise LLMUnavailable(f"{self.name}:empty_answer")  # for example the answer was cut off while "thinking"
        u = data.get("usage") or {}
        return LLMResult(text=text, tool_calls=calls, input_tokens=int(u.get("prompt_tokens") or 0),
                         output_tokens=int(u.get("completion_tokens") or 0), model=model, content=content)


class FallbackLLM:
    """Try the main provider; if it is busy or down, try the next one."""

    def __init__(self, backends: list[LLM]):
        self.backends = backends

    def complete(self, **kw) -> LLMResult:
        last: Exception | None = None
        for i, b in enumerate(self.backends):
            try:
                res = b.complete(**kw)
                res.backup = i > 0
                return res
            except LLMUnavailable as exc:
                last = exc
                log.warning("AI provider failed, trying the next one: %s", exc)
        raise LLMUnavailable(str(last) if last else "no_provider")


def build_backend(provider: str, *, is_fallback: bool = False) -> LLM:
    s = get_settings()
    wait = s.llm_max_wait_seconds
    if provider == "groq":
        return OpenAICompatLLM(name="groq", base_url=GROQ_URL, api_key=s.groq_api_key,
                               model_override=None if not is_fallback else s.PROVIDER_DEFAULT_MODEL["groq"],
                               max_wait_seconds=wait)
    if provider == "gemini":
        return OpenAICompatLLM(name="gemini", base_url=GEMINI_URL, api_key=s.gemini_api_key, model_override=s.gemini_model or None,
                               max_wait_seconds=wait)
    if provider == "openai_compat":
        return OpenAICompatLLM(name="openai_compat", base_url=s.llm_base_url, api_key=s.llm_api_key, max_wait_seconds=wait)
    if provider == "anthropic":
        if is_fallback:
            raise LLMUnavailable("anthropic_cannot_be_backup")  # a paid provider is never a silent backup
        if not s.allow_paid_llm:
            raise LLMUnavailable("paid_provider_locked")  # on purpose: a paid key can never be used by accident
        return AnthropicLLM()
    raise LLMUnavailable(f"unknown_provider:{provider}")


_default: LLM | None = None


def get_llm() -> LLM:
    global _default
    if _default is None:
        s = get_settings()
        backends = [build_backend(s.llm_provider)]
        if s.llm_fallback_provider and s.llm_fallback_provider != s.llm_provider:
            backends.append(build_backend(s.llm_fallback_provider, is_fallback=True))
        _default = backends[0] if len(backends) == 1 else FallbackLLM(backends)
    return _default


def set_llm(llm: LLM | None) -> None:
    global _default
    _default = llm
