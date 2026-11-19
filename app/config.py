"""All settings come from environment variables. Never put secrets in code."""
from __future__ import annotations

from functools import lru_cache
from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"  # "production" turns on strict checks
    public_base_url: str = "http://localhost:8000"  # used to check Twilio signatures

    # Secrets (must be set in production)
    secret_key: str = "dev-only-change-me"  # signs login cookies
    encryption_key: str = ""  # Fernet key; encrypts calendar tokens in the database

    # Database
    database_url: str = "sqlite:///./dev.db"  # the app connects with this (limited role)
    database_admin_url: str = ""  # only used to create tables (migrate command)
    app_db_role: str = "frontdesk_app"

    # AI. By default only FREE providers are used. A paid Anthropic key is blocked unless ALLOW_PAID_LLM=true.
    llm_provider: str = "groq"  # groq | gemini | openai_compat | anthropic
    llm_fallback_provider: str = ""  # used when the main one is busy or down, for example "gemini"
    allow_paid_llm: bool = False  # safety lock: the paid Anthropic provider refuses to start unless this is true
    groq_api_key: str = ""
    gemini_api_key: str = ""
    llm_base_url: str = ""  # only for openai_compat (any service with an OpenAI-style API)
    llm_api_key: str = ""  # only for openai_compat
    anthropic_api_key: str = ""
    # Leave MODEL_SIMPLE / MODEL_HARD empty to use the default model of the provider.
    model_simple: str = ""
    model_hard: str = ""
    gemini_model: str = ""  # model id for Gemini, copy it from Google AI Studio (for example when Gemini is the backup)
    llm_max_wait_seconds: float = 12.0  # how long a live chat may wait when a free provider says "slow down"
    # Price in US dollars per 1 million tokens, used ONLY for the paid anthropic provider. Free providers cost 0.
    price_in_simple: float = 0.10
    price_out_simple: float = 0.50
    price_in_hard: float = 2.00
    price_out_hard: float = 10.00
    max_steps_per_turn: int = 6
    max_steps_per_conversation: int = 40
    max_cost_per_conversation_usd: float = 0.50
    max_output_tokens: int = 600
    # May a business WITHOUT its own AI key use the server's key from .env? Not set = yes on a laptop, no in production.
    allow_shared_ai_key: bool | None = None
    max_daily_ai_cost_per_business_usd: float = 10.0  # stops a flood of chats from running up the AI bill

    # Phone / SMS (Twilio)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Voice (Vapi)
    vapi_webhook_secret: str = ""
    voice_provider_name: str = "vapi"
    voice_model_provider: str = ""  # empty = follow LLM_PROVIDER (groq -> "groq", gemini -> "google")
    voice_model: str = ""  # empty = the provider's default model
    voice_extra_assistant_json: str = ""  # extra Vapi assistant settings (voice, recording), as JSON

    # Google Calendar
    google_client_id: str = ""
    google_client_secret: str = ""

    # Email (SMTP) for owner reports and alerts
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # Cookies / safety
    cookie_secure: bool = False  # set true in production (https only)
    global_kill_switch: bool = False  # true = agent off for every business
    web_chat_allowed_origins: str = ""  # comma separated list

    require_admin_2fa: bool | None = None  # default: required in production, optional elsewhere

    # Public product page (/product). Empty = the page shows a placeholder like [YOUR EMAIL].
    product_contact_email: str = ""
    product_video_url: str = ""  # link to the demo video; must start with https://
    product_owner_name: str = ""
    product_setup_price: str = ""  # for example $300
    product_monthly_price: str = ""  # for example $99
    product_demo_business: str = ""  # short name of the demo business for the "Try the live demo" chat; empty = no demo chat

    # ---- which model, for which provider ----
    PROVIDER_DEFAULT_MODEL: ClassVar[dict[str, str]] = {"groq": "openai/gpt-oss-120b", "anthropic": "claude-haiku-5-5"}

    @property
    def main_model_simple(self) -> str:
        if self.model_simple:
            return self.model_simple
        if self.llm_provider == "gemini":
            return self.gemini_model
        return self.PROVIDER_DEFAULT_MODEL.get(self.llm_provider, "")

    @property
    def main_model_hard(self) -> str:
        if self.model_hard:
            return self.model_hard
        if self.llm_provider == "anthropic":
            return "claude-sonnet-5-5"
        return self.main_model_simple

    @property
    def is_free_provider(self) -> bool:
        return self.llm_provider != "anthropic"

    @property
    def vapi_model_provider(self) -> str:
        if self.voice_model_provider:
            return self.voice_model_provider
        return {"groq": "groq", "gemini": "google", "anthropic": "anthropic"}.get(self.llm_provider, self.llm_provider)

    @property
    def vapi_model(self) -> str:
        return self.voice_model or self.main_model_simple

    @property
    def admin_2fa_required(self) -> bool:
        return self.is_production if self.require_admin_2fa is None else self.require_admin_2fa

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def shared_ai_key_ok(self) -> bool:
        if self.allow_shared_ai_key is not None:
            return self.allow_shared_ai_key
        return not self.is_production

    def check_production_ready(self) -> list[str]:
        """Return a list of problems. Production start-up refuses if the list is not empty."""
        problems: list[str] = []
        if self.secret_key in ("", "dev-only-change-me") or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
        if not self.encryption_key:
            problems.append("ENCRYPTION_KEY must be set (a Fernet key)")
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL must point to Postgres in production")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if not self.twilio_auth_token:
            problems.append("TWILIO_AUTH_TOKEN is missing (needed to check webhook signatures)")
        if not self.vapi_webhook_secret:
            problems.append("VAPI_WEBHOOK_SECRET is missing (needed to check voice webhooks)")
        problems += self.check_llm_ready()
        if not self.public_base_url.startswith("https://"):
            problems.append("PUBLIC_BASE_URL must start with https:// in production")
        return problems


    def check_llm_ready(self) -> list[str]:
        """Problems with the AI settings (also used by the live test)."""
        problems: list[str] = []
        needs = {"groq": ("GROQ_API_KEY", self.groq_api_key), "gemini": ("GEMINI_API_KEY", self.gemini_api_key),
                 "openai_compat": ("LLM_API_KEY", self.llm_api_key), "anthropic": ("ANTHROPIC_API_KEY", self.anthropic_api_key)}
        for prov in (self.llm_provider, self.llm_fallback_provider):
            if not prov:
                continue
            if prov not in needs:
                problems.append(f"LLM provider '{prov}' is unknown (use groq, gemini, openai_compat or anthropic)")
                continue
            name, value = needs[prov]
            if not value or value.startswith("PASTE_"):
                problems.append(f"{name} is missing (needed for LLM_PROVIDER/LLM_FALLBACK_PROVIDER = {prov}). Put your free key in .env")
            if prov == "anthropic" and prov == self.llm_fallback_provider:
                problems.append("anthropic cannot be the backup provider (a paid provider is never used silently)")
            if prov == "anthropic" and not self.allow_paid_llm:
                problems.append("The paid Anthropic provider is locked. Set ALLOW_PAID_LLM=true only if you really want to pay.")
            if prov == "gemini" and not self.gemini_model:
                problems.append("GEMINI_MODEL is missing (copy the model id from Google AI Studio)")
            if prov == "openai_compat" and not (self.llm_base_url and self.main_model_simple):
                problems.append("LLM_BASE_URL and MODEL_SIMPLE are needed for openai_compat")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


# Re-export for convenience
__all__ = ["Settings", "get_settings", "reset_settings_cache", "Field"]
