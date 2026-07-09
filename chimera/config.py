"""Runtime configuration for Chimera.

Settings are read from environment variables and an optional ``.env`` file.
Nothing here requires a key at import time — the agent only needs credentials for
the providers it actually calls (see :mod:`chimera.providers.gateway`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

if TYPE_CHECKING:
    from chimera.providers.catalog import TierLadder

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

    # --- Model tiers (M16): weak -> mid -> top, vendor-agnostic. Any LiteLLM/OpenRouter
    # slug can occupy any role. Empty string = "let cost_mode decide" (see
    # chimera/providers/catalog.py); a non-empty value is an explicit user choice and
    # ALWAYS wins over the mode. ---
    weak_model: str = Field(default="", validation_alias="CHIMERA_WEAK_MODEL")
    mid_model: str = Field(default="", validation_alias="CHIMERA_MID_MODEL")
    orchestrator_model: str = Field(default="", validation_alias="CHIMERA_ORCHESTRATOR_MODEL")

    # --- Cost mode: how the tier ladder is filled when models aren't pinned.
    # "cheap" = weak-first aggressive; "balanced" = economic defaults; "premium" =
    # frontier everywhere; "auto" (default) = prioritizes the MID tier as the entry
    # point and lets the cascade climb/descend from there. ---
    cost_mode: str = Field(default="auto", validation_alias="CHIMERA_COST_MODE")

    # --- Cascade routing (M16-A6): weak -> gate -> mid -> gate -> fusion. Off by
    # default; `--cascade` on solve/chat or CHIMERA_CASCADE=1 enables. ---
    cascade: bool = Field(default=False, validation_alias="CHIMERA_CASCADE")

    # --- Per-delegation token budget for hierarchical orchestration (M16-A4),
    # enforced by the harness (BudgetedBackend), not by prompt instructions. ---
    delegation_budget: int = Field(default=8000, validation_alias="CHIMERA_DELEGATION_BUDGET")

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

    # --- Selective fusion: run a probe of the first `fusion_probe_k` panel models; if
    # they agree closely (a cheap local text-similarity check, no extra model call), skip
    # the rest of the panel AND the judge and synthesize from the agreeing answers;
    # otherwise escalate to the full panel -> judge -> synthesizer. Disagreement therefore
    # costs the same as full fusion; agreement is cheaper. ON by default: across 3 runs of
    # the `fusion-bench` hard suite it cut tokens ~20-28% and never lost accuracy on any
    # turn it actually short-circuited (16/16 correct). Set to "full" to disable. ---
    fusion_mode: str = Field(default="selective", validation_alias="CHIMERA_FUSION_MODE")
    fusion_probe_k: int = Field(default=2, validation_alias="CHIMERA_FUSION_PROBE_K")
    fusion_agreement_threshold: float = Field(
        default=0.8, validation_alias="CHIMERA_FUSION_AGREEMENT"
    )

    # --- Behaviour ---
    log_level: str = Field(default="INFO", validation_alias="CHIMERA_LOG_LEVEL")
    home: Path = Field(default=Path(".chimera"), validation_alias="CHIMERA_HOME")

    # --- Exact-match completion cache for tool-free turns (HORIZON prompt caching) ---
    cache: bool = Field(default=False, validation_alias="CHIMERA_CACHE")
    prompt_cache: bool = Field(default=False, validation_alias="CHIMERA_PROMPT_CACHE")
    """Opt-in: mark the stable system prefix with a provider cache breakpoint so the
    single agent / worker fleet reuse it at the cache read rate. Providers that cache
    automatically (OpenAI, DeepSeek) are left untouched; only breakpoint-requiring
    families (Anthropic/Claude) get an explicit cache_control marker."""

    # --- Browser tool: run Chromium headless (default) or headful for debugging. ---
    browser_headless: bool = Field(default=True, validation_alias="CHIMERA_BROWSER_HEADLESS")

    # --- Long-term memory backend: json (default, zero-dep) or sqlite (FTS5 full-text) ---
    memory_backend: str = Field(default="json", validation_alias="CHIMERA_MEMORY_BACKEND")

    # --- Opt-in semantic memory recall: embed facts + query and rank by cosine, so a
    # paraphrase with no shared token still retrieves the right fact (the gap memory-bench
    # exposes for pure keyword search). Off by default — needs an embeddings-capable key.
    # On any embedder error, search falls back to the keyword/FTS path (never a hard fail). ---
    semantic_memory: bool = Field(default=False, validation_alias="CHIMERA_SEMANTIC_MEMORY")
    embed_model: str = Field(
        default="openrouter/openai/text-embedding-3-small",
        validation_alias="CHIMERA_EMBED_MODEL",
    )

    # --- Opt-in: at the end of a chat session, if memory has grown past
    # `memory_budget`, consolidate near-duplicate facts with the model (bounded cost:
    # skipped entirely while memory is small). Off by default. ---
    auto_consolidate: bool = Field(default=False, validation_alias="CHIMERA_AUTO_CONSOLIDATE")
    memory_budget: int = Field(default=200, validation_alias="CHIMERA_MEMORY_BUDGET")

    # --- Auto-fuse error-sensitive turns in solve/crew without an explicit --fuse.
    # Off by default (fusion costs 2-3x); when on, the cost-aware router still keeps
    # cheap/tool turns single-model and only fuses deep or error-sensitive ones. ---
    auto_fuse: bool = Field(default=False, validation_alias="CHIMERA_AUTO_FUSE")

    # --- TRS skill cards (Improvement #1): retrieve learned reasoning cards (BM25 over
    # name+description+triggers) and inject the top-k into the worker's reasoning context.
    # Off by default (an experiment — injection can raise cost if retrieval misfires);
    # measure with `chimera skillcard-bench` before enabling. ---
    skill_cards: bool = Field(default=False, validation_alias="CHIMERA_SKILL_CARDS")
    skill_cards_k: int = Field(default=3, validation_alias="CHIMERA_SKILL_CARDS_K")

    # --- How the collective skill-accept gate scores cross-model transfer: "point" (the
    # raw pass fraction, default) or "wilson" (the lower Wilson confidence bound, so a
    # lucky small-sample pass no longer clears the threshold). "wilson" is strict on tiny
    # panels — use it with panels >= ~5, or lower CHIMERA_SKILL_MIN_TRANSFER. ---
    skill_accept_mode: str = Field(default="point", validation_alias="CHIMERA_SKILL_ACCEPT_MODE")

    # --- SkillCoach process filter for `chimera evolve export`: keep only trajectories
    # whose step-following score >= this (so a lucky success with failed tool steps is not
    # trained on). 0.0 = off (default). ---
    sft_min_process: float = Field(default=0.0, validation_alias="CHIMERA_SFT_MIN_PROCESS")

    # --- Compact tool schemas at advertise-time (Improvement #5a): strip annotation
    # noise and trim parameter prose from the `tools=` payload re-sent every ReAct step.
    # Semantics preserved (name/type/required/enum kept). Off by default; the win is
    # largest with verbose MCP/OpenAPI toolsets — measure with `chimera schema-bench`. ---
    compact_schemas: bool = Field(default=False, validation_alias="CHIMERA_COMPACT_SCHEMAS")

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
    # Optional OCI runtime for the docker sandbox (e.g. runsc = gVisor); empty = daemon default.
    sandbox_runtime: str = Field(default="", validation_alias="CHIMERA_SANDBOX_RUNTIME")

    # Per-session tool allowlist/denylist (names). Empty allowlist = no restriction (all
    # tools); a non-empty allowlist grants only those. Denylist removes even if allowed.
    tool_allowlist: list[str] = Field(default_factory=list, validation_alias="CHIMERA_TOOL_ALLOWLIST")
    tool_denylist: list[str] = Field(default_factory=list, validation_alias="CHIMERA_TOOL_DENYLIST")

    @field_validator(
        "fusion_panel",
        "fallback_models",
        "openrouter_keys",
        "openai_keys",
        "anthropic_keys",
        "gemini_keys",
        "deepseek_keys",
        "tool_allowlist",
        "tool_denylist",
        mode="before",
    )
    @classmethod
    def _split_panel(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    def tier_ladder(self) -> TierLadder:
        """The resolved weak/mid/top model ladder (explicit override > cost_mode)."""
        from chimera.providers.catalog import resolve_tiers

        return resolve_tiers(self)

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
