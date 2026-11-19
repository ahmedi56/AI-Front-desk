"""Each client's OWN AI account.

Why: the owner of this platform must never pay for a client's AI use, and one client must never use up another
client's free limits. So every business can store its own free Groq or Gemini key.

Safety:
  * Only FREE providers are accepted here (Groq, Gemini). A paid provider can never be set per client.
  * The key is encrypted in the database (Fernet, with ENCRYPTION_KEY). Only the last 4 characters are kept readable,
    so the owner can see WHICH key is saved. The full key is never shown, logged or sent back to a browser.
  * A business's key is only ever used for that business's own conversations.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading

from ..agent.llm import GEMINI_URL, GROQ_URL, LLM, FallbackLLM, OpenAICompatLLM, get_llm
from ..config import get_settings
from ..models import Business
from ..security import decrypt_text, encrypt_text

log = logging.getLogger("frontdesk.ai_keys")

FREE_PROVIDERS = {"groq": "Groq (free)", "gemini": "Google Gemini (free)"}
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.]{20,200}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9_\-./:]{2,100}$")


class KeyError_(ValueError):
    """A key or provider we refuse. The message is safe to show to the owner (it never contains the key)."""


def _checked(provider: str, api_key: str, model: str) -> tuple[str, str, str]:
    """The same checks for the main key and the backup key. Returns the cleaned values or raises KeyError_."""
    provider = (provider or "").strip().lower()
    api_key = (api_key or "").strip()
    model = (model or "").strip()
    if provider not in FREE_PROVIDERS:
        raise KeyError_("Choose Groq or Gemini. Only free AI accounts can be used here.")
    if not _KEY_RE.match(api_key):
        raise KeyError_("That does not look like an API key. Copy the whole key, with no spaces.")
    if provider == "gemini" and not _MODEL_RE.match(model):
        raise KeyError_("For Gemini, also copy the exact model name from Google AI Studio.")
    if model and not _MODEL_RE.match(model):
        raise KeyError_("The model name has characters that are not allowed.")
    if not get_settings().encryption_key:
        raise KeyError_("The server has no ENCRYPTION_KEY yet, so keys cannot be stored safely. Add it to .env first.")
    return provider, api_key, model


def set_key(b: Business, provider: str, api_key: str, model: str = "") -> None:
    provider, api_key, model = _checked(provider, api_key, model)
    if b.ai_backup_key_enc and b.ai_backup_provider == provider:
        raise KeyError_(f"Your backup key is already from {FREE_PROVIDERS[provider]}. Remove the backup key first, "
                        "or choose the other company for the main key.")
    b.ai_provider = provider
    b.ai_key_enc = encrypt_text(api_key)
    b.ai_model = model or None
    b.ai_key_hint = api_key[-4:]
    clear_cache()


def set_backup_key(b: Business, provider: str, api_key: str, model: str = "") -> None:
    """A second key, used only when the main AI is busy or down. It must be from a DIFFERENT free provider: two keys
    from one company fail together, and a second account there would only be a way around that company's limits."""
    provider, api_key, model = _checked(provider, api_key, model)
    if not (b.ai_key_enc and b.ai_provider in FREE_PROVIDERS):
        raise KeyError_("Save your main AI key first. The backup is only used when the main one is busy.")
    if provider == b.ai_provider:
        raise KeyError_(f"Choose a different company than your main key ({FREE_PROVIDERS[provider]}). "
                        "A backup from the same company is down at the same time.")
    b.ai_backup_provider = provider
    b.ai_backup_key_enc = encrypt_text(api_key)
    b.ai_backup_model = model or None
    b.ai_backup_key_hint = api_key[-4:]
    clear_cache()


def clear_backup_key(b: Business) -> None:
    b.ai_backup_provider = b.ai_backup_key_enc = b.ai_backup_model = b.ai_backup_key_hint = None
    clear_cache()


def clear_key(b: Business) -> None:
    """Removes the main key. A backup without a main key makes no sense, so it goes too."""
    b.ai_provider = b.ai_key_enc = b.ai_model = b.ai_key_hint = None
    clear_backup_key(b)


def key_status(b: Business) -> dict:
    """What the dashboard may show: never the key itself."""
    return {
        "has_key": bool(b.ai_key_enc),
        "provider": FREE_PROVIDERS.get(b.ai_provider or "", ""),
        "provider_id": b.ai_provider or "",
        "hint": b.ai_key_hint or "",
        "model": b.ai_model or "",
        "shared_ok": get_settings().shared_ai_key_ok,
        "backup_has_key": bool(b.ai_backup_key_enc),
        "backup_provider": FREE_PROVIDERS.get(b.ai_backup_provider or "", ""),
        "backup_provider_id": b.ai_backup_provider or "",
        "backup_hint": b.ai_backup_key_hint or "",
        "backup_model": b.ai_backup_model or "",
    }


# ---- choosing the AI client for one business ----
_cache: dict[tuple[str, str], LLM] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _build_client(provider: str, api_key: str, model: str | None) -> LLM:
    s = get_settings()
    if provider == "groq":
        return OpenAICompatLLM(name="groq", base_url=GROQ_URL, api_key=api_key,
                               model_override=model or s.PROVIDER_DEFAULT_MODEL["groq"], max_wait_seconds=s.llm_max_wait_seconds)
    if provider == "gemini":
        return OpenAICompatLLM(name="gemini", base_url=GEMINI_URL, api_key=api_key, model_override=model,
                               max_wait_seconds=s.llm_max_wait_seconds)
    raise KeyError_("unknown provider")


def llm_for_business(b: Business) -> LLM | None:
    """The business's own AI client; else the server's shared one if allowed; else None (the owner is told)."""
    if b.ai_key_enc and b.ai_provider in FREE_PROVIDERS:
        # cache by business AND a fingerprint of the stored value, so a changed key takes effect at once
        fp = hashlib.sha256((b.ai_key_enc + "|" + (b.ai_backup_key_enc or "")).encode()).hexdigest()[:16]
        with _lock:
            hit = _cache.get((b.id, fp))
        if hit:
            return hit
        key = decrypt_text(b.ai_key_enc)
        if not key:
            log.warning("stored AI key could not be decrypted for a business (ENCRYPTION_KEY changed?)")
            return None
        client = _build_client(b.ai_provider, key, b.ai_model)
        # The business's OWN backup account (a different free provider), tried only when the main one fails.
        if b.ai_backup_key_enc and b.ai_backup_provider in FREE_PROVIDERS and b.ai_backup_provider != b.ai_provider:
            backup_key = decrypt_text(b.ai_backup_key_enc)
            if backup_key:
                client = FallbackLLM([client, _build_client(b.ai_backup_provider, backup_key, b.ai_backup_model)])
            else:
                log.warning("stored backup AI key could not be decrypted for a business; using the main key only")
        with _lock:
            _cache[(b.id, fp)] = client
        return client
    if get_settings().shared_ai_key_ok:
        return get_llm()
    return None
