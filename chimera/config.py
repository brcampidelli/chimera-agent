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

    # --- Default single model (Tier 1 / cheap tasks) ---
    default_model: str = Field(
        default="openrouter/openai/gpt-5.5", validation_alias="CHIMERA_DEFAULT_MODEL"
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

    @field_validator("fusion_panel", mode="before")
    @classmethod
    def _split_panel(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def configured_providers(self) -> list[str]:
        """Names of providers that currently have a key set."""
        mapping = {
            "openrouter": self.openrouter_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
        }
        return [name for name, key in mapping.items() if key]

    def has_any_key(self) -> bool:
        return bool(self.configured_providers())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide settings instance."""
    return Settings()
