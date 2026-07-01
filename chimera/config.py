"""Runtime configuration for Chimera.

Settings are read from environment variables and an optional ``.env`` file.
Nothing here requires a key at import time — the agent only needs credentials for
the providers it actually calls (see :mod:`chimera.providers.gateway`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DEFAULT_PANEL = [
    "openrouter/anthropic/claude-opus-4-8",
    "openrouter/openai/gpt-5.5",
    "openrouter/google/gemini-3.1-pro",
]
_DEFAULT_JUDGE = "openrouter/anthropic/claude-opus-4-8"


class Settings(BaseSettings):
    """Process-wide configuration, populated from env / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Provider keys (each optional; LiteLLM also reads these directly) ---
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    deepseek_api_key: str | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")

    # --- Credential pools: comma-separated keys per provider, rotated round-robin ---
    openrouter_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_OPENROUTER_KEYS"
    )
    openai_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_OPENAI_KEYS"
    )
    anthropic_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_ANTHROPIC_KEYS"
    )
    gemini_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_GEMINI_KEYS"
    )
    deepseek_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_DEEPSEEK_KEYS"
    )

    # --- Optional feature credentials (pre-set slots; set only what you use) ---
    tavily_api_key: str | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    brave_api_key: str | None = Field(default=None, validation_alias="BRAVE_API_KEY")
    serpapi_key: str | None = Field(default=None, validation_alias="SERPAPI_API_KEY")
    x_bearer_token: str | None = Field(default=None, validation_alias="X_BEARER_TOKEN")
    stability_api_key: str | None = Field(default=None, validation_alias="STABILITY_API_KEY")
    elevenlabs_api_key: str | None = Field(default=None, validation_alias="ELEVENLABS_API_KEY")
    spotify_client_id: str | None = Field(default=None, validation_alias="SPOTIFY_CLIENT_ID")
    spotify_client_secret: str | None = Field(default=None, validation_alias="SPOTIFY_CLIENT_SECRET")

    # --- Default single model (Tier 1 / cheap tasks) ---
    default_model: str = Field(
        default="openrouter/openai/gpt-5.5", validation_alias="CHIMERA_DEFAULT_MODEL"
    )

    # --- Custom endpoint for self-hosted/OpenAI-compatible servers (Ollama, vLLM) ---
    api_base: str | None = Field(default=None, validation_alias="CHIMERA_API_BASE")

    # --- Fallback chain: tried in order if the primary model errors ---
    fallback_models: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CHIMERA_FALLBACK_MODELS"
    )

    # --- Fusion engine (panel -> judge -> synthesizer) ---
    fusion_panel: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(_DEFAULT_PANEL), validation_alias="CHIMERA_FUSION_PANEL"
    )
    fusion_judge: str = Field(default=_DEFAULT_JUDGE, validation_alias="CHIMERA_FUSION_JUDGE")
    fusion_synthesizer: str = Field(
        default=_DEFAULT_JUDGE, validation_alias="CHIMERA_FUSION_SYNTHESIZER"
    )

    # --- Behaviour ---
    log_level: str = Field(default="INFO", validation_alias="CHIMERA_LOG_LEVEL")
    home: Path = Field(default=Path(".chimera"), validation_alias="CHIMERA_HOME")

    # --- Exact-match completion cache for tool-free turns (HORIZON prompt caching) ---
    cache: bool = Field(default=False, validation_alias="CHIMERA_CACHE")

    # --- Long-term memory backend: json (default, zero-dep) or sqlite (FTS5 full-text) ---
    memory_backend: str = Field(default="json", validation_alias="CHIMERA_MEMORY_BACKEND")

    # --- Opt-in: at the end of a chat session, if memory has grown past
    # `memory_budget`, consolidate near-duplicate facts with the model (bounded cost:
    # skipped entirely while memory is small). Off by default. ---
    auto_consolidate: bool = Field(default=False, validation_alias="CHIMERA_AUTO_CONSOLIDATE")
    memory_budget: int = Field(default=200, validation_alias="CHIMERA_MEMORY_BUDGET")

    # --- Auto-fuse error-sensitive turns in solve/crew without an explicit --fuse.
    # Off by default (fusion costs 2-3x); when on, the cost-aware router still keeps
    # cheap/tool turns single-model and only fuses deep or error-sensitive ones. ---
    auto_fuse: bool = Field(default=False, validation_alias="CHIMERA_AUTO_FUSE")

    # --- Messaging bot tokens (only needed for the matching `chimera serve --<platform>`) ---
    discord_bot_token: str | None = Field(default=None, validation_alias="CHIMERA_DISCORD_BOT_TOKEN")
    telegram_bot_token: str | None = Field(default=None, validation_alias="CHIMERA_TELEGRAM_BOT_TOKEN")
    slack_bot_token: str | None = Field(default=None, validation_alias="CHIMERA_SLACK_BOT_TOKEN")
    slack_app_token: str | None = Field(default=None, validation_alias="CHIMERA_SLACK_APP_TOKEN")
    whatsapp_access_token: str | None = Field(default=None, validation_alias="CHIMERA_WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_number_id: str | None = Field(
        default=None, validation_alias="CHIMERA_WHATSAPP_PHONE_NUMBER_ID"
    )
    whatsapp_verify_token: str | None = Field(
        default=None, validation_alias="CHIMERA_WHATSAPP_VERIFY_TOKEN"
    )
    signal_api_url: str | None = Field(default=None, validation_alias="CHIMERA_SIGNAL_API_URL")
    signal_number: str | None = Field(default=None, validation_alias="CHIMERA_SIGNAL_NUMBER")

    # --- Email (SMTP) for the send_email reference tool ---
    smtp_host: str | None = Field(default=None, validation_alias="CHIMERA_SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="CHIMERA_SMTP_PORT")
    smtp_user: str | None = Field(default=None, validation_alias="CHIMERA_SMTP_USER")
    smtp_password: str | None = Field(default=None, validation_alias="CHIMERA_SMTP_PASSWORD")
    smtp_from: str | None = Field(default=None, validation_alias="CHIMERA_SMTP_FROM")

    # --- IMAP for the read_email reference tool ---
    imap_host: str | None = Field(default=None, validation_alias="CHIMERA_IMAP_HOST")
    imap_port: int = Field(default=993, validation_alias="CHIMERA_IMAP_PORT")
    imap_user: str | None = Field(default=None, validation_alias="CHIMERA_IMAP_USER")
    imap_password: str | None = Field(default=None, validation_alias="CHIMERA_IMAP_PASSWORD")

    # --- Default iCalendar feed for the calendar_events reference tool ---
    calendar_ics_url: str | None = Field(default=None, validation_alias="CHIMERA_CALENDAR_ICS_URL")

    # --- Execution sandbox for the shell tool (local = host, docker = isolated) ---
    sandbox: str = Field(default="local", validation_alias="CHIMERA_SANDBOX")
    sandbox_image: str = Field(
        default="python:3.12-slim", validation_alias="CHIMERA_SANDBOX_IMAGE"
    )

    @field_validator(
        "fusion_panel",
        "fallback_models",
        "openrouter_keys",
        "openai_keys",
        "anthropic_keys",
        "gemini_keys",
        "deepseek_keys",
        mode="before",
    )
    @classmethod
    def _split_panel(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def credential_pool(self, provider: str) -> list[str]:
        """Only the explicit multi-key pool (``CHIMERA_<PROVIDER>_KEYS``), [] if unset.

        This is what the gateway rotates round-robin. A provider with just a single
        ``*_API_KEY`` returns [] here — its key is read from the environment as before.
        """
        pools = {
            "openrouter": self.openrouter_keys,
            "openai": self.openai_keys,
            "anthropic": self.anthropic_keys,
            "gemini": self.gemini_keys,
            "deepseek": self.deepseek_keys,
        }
        return list(pools.get(provider, []))

    def key_pool(self, provider: str) -> list[str]:
        """Usable keys for a provider: the pool if set, else the single key."""
        pool = self.credential_pool(provider)
        if pool:
            return pool
        single = {
            "openrouter": self.openrouter_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
        }.get(provider)
        return [single] if single else []

    def configured_providers(self) -> list[str]:
        """Names of providers that currently have a key (single or pool)."""
        names = ("openrouter", "openai", "anthropic", "gemini", "deepseek")
        return [name for name in names if self.key_pool(name)]

    def has_any_key(self) -> bool:
        return bool(self.configured_providers())

    def credentials(self) -> dict[str, str | None]:
        """All known credential slots keyed by env-var name (value or None)."""
        return {
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "OPENAI_API_KEY": self.openai_api_key,
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "GEMINI_API_KEY": self.gemini_api_key,
            "DEEPSEEK_API_KEY": self.deepseek_api_key,
            "TAVILY_API_KEY": self.tavily_api_key,
            "BRAVE_API_KEY": self.brave_api_key,
            "SERPAPI_API_KEY": self.serpapi_key,
            "X_BEARER_TOKEN": self.x_bearer_token,
            "STABILITY_API_KEY": self.stability_api_key,
            "ELEVENLABS_API_KEY": self.elevenlabs_api_key,
            "SPOTIFY_CLIENT_ID": self.spotify_client_id,
            "SPOTIFY_CLIENT_SECRET": self.spotify_client_secret,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide settings instance."""
    return Settings()
