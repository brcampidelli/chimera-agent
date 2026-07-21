"""Read/write the settings surface for the desktop app's Settings screen.

Security is the whole point of this module:

- **Secrets are never returned in cleartext.** ``read_config`` reports each credential as ``{set,
  hint}`` where the hint is at most the last 4 characters — enough to recognize which key is present,
  never the key itself. The server token reports only ``set`` (no hint at all).
- **Writes go to ``.env`` only, through an allowlist.** ``patch_config`` refuses any key that isn't a
  known setting or credential slot, so a request can't inject arbitrary lines into ``.env``. The value
  is written atomically and never logged.

This maps directly to the competitor's Model / API-Keys / Gateway settings panes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from chimera.config import Settings, get_settings

# Credential env-vars (secret) and the non-secret settings the UI may edit. Anything outside this set
# is rejected by patch_config, so the endpoint can't be used to write arbitrary .env lines.
_PROVIDER_LABELS = {
    "OPENROUTER_API_KEY": "OpenRouter",
    "OPENAI_API_KEY": "OpenAI",
    "ANTHROPIC_API_KEY": "Anthropic",
    "GEMINI_API_KEY": "Gemini",
    "DEEPSEEK_API_KEY": "DeepSeek",
    "TAVILY_API_KEY": "Tavily (web search)",
    "BRAVE_API_KEY": "Brave (web search)",
    "SERPAPI_API_KEY": "SerpAPI",
    "ELEVENLABS_API_KEY": "ElevenLabs (TTS)",
    "STABILITY_API_KEY": "Stability (images)",
}
# Messaging bot tokens — secret (masked on read), settable so the UI can configure a channel the
# agent reaches you on without editing .env by hand.
_MESSAGING_SECRETS = {"CHIMERA_DISCORD_BOT_TOKEN", "CHIMERA_TELEGRAM_BOT_TOKEN"}
_SECRET_KEYS = set(_PROVIDER_LABELS) | {"CHIMERA_SERVER_TOKEN"} | _MESSAGING_SECRETS
_EDITABLE_SETTINGS = {
    "CHIMERA_DEFAULT_MODEL",
    "CHIMERA_WEAK_MODEL",
    "CHIMERA_MID_MODEL",
    "CHIMERA_ORCHESTRATOR_MODEL",
    "CHIMERA_COST_MODE",
    "CHIMERA_CASCADE",
    "CHIMERA_API_BASE",
    "CHIMERA_FALLBACK_MODELS",
    "CHIMERA_CACHE",
    "CHIMERA_PROMPT_CACHE",
    "CHIMERA_MEMORY_BACKEND",
    "CHIMERA_SEMANTIC_MEMORY",
    "CHIMERA_AUTO_CONSOLIDATE",
    "CHIMERA_CHAT_MEMORY",  # the "Remember from chat" toggle (opt-in durable memory from chat)
    "CHIMERA_APP_CRON",  # run the cron daemon inside the desktop app (proactivity)
    "CHIMERA_APP_MESSAGING",  # auto-start messaging adapters in the desktop app at boot
    "CHIMERA_SANDBOX",
    "CHIMERA_SANDBOX_IMAGE",
    "CHIMERA_MCP_AUTOLOAD",
}
ALLOWED_KEYS = _SECRET_KEYS | _EDITABLE_SETTINGS


def _hint(value: str | None) -> str:
    """A safe recognition hint: the last 4 chars of a long secret, else empty. Never the whole value."""
    if not value or len(value) < 8:
        return ""
    return f"…{value[-4:]}"


def read_config(settings: Settings) -> dict[str, Any]:
    """The settings snapshot for the UI. Secrets are masked to ``{set, hint}`` — never cleartext."""
    creds = settings.credentials()
    providers = [
        {
            "env": env,
            "label": _PROVIDER_LABELS[env],
            "set": bool(creds.get(env)),
            "hint": _hint(creds.get(env)),
        }
        for env in _PROVIDER_LABELS
    ]
    ladder = settings.tier_ladder()
    return {
        "models": {
            "default": settings.default_model,
            "weak": settings.weak_model,
            "mid": settings.mid_model,
            "orchestrator": settings.orchestrator_model,
            "cost_mode": settings.cost_mode,
            "cascade": settings.cascade,
            "api_base": settings.api_base,
            "fallback_models": list(settings.fallback_models),
            "tiers": {"weak": ladder.weak, "mid": ladder.mid, "top": ladder.top},
        },
        "memory": {
            "backend": settings.memory_backend,
            "semantic": settings.semantic_memory,
            "auto_consolidate": settings.auto_consolidate,
            "remember_from_chat": settings.remember_from_chat,
        },
        "cache": {"completion": settings.cache, "prompt": settings.prompt_cache},
        "sandbox": {"mode": settings.sandbox, "image": settings.sandbox_image},
        "server": {"token_set": bool(settings.server_token)},
        "mcp": {"autoload": settings.mcp_autoload},
        "providers": providers,
    }


def doctor(settings: Settings) -> dict[str, Any]:
    """A config-health snapshot (no live provider pings): which providers have keys, the model ladder."""
    ladder = settings.tier_ladder()
    return {
        "has_any_key": settings.has_any_key(),
        "configured_providers": settings.configured_providers(),
        "default_model": settings.default_model,
        "tiers": {"weak": ladder.weak, "mid": ladder.mid, "top": ladder.top},
        "memory_backend": settings.memory_backend,
        "cache": settings.cache,
        "sandbox": settings.sandbox,
    }


def _write_env_var(path: Path, key: str, value: str) -> None:
    """Set ``KEY=value`` in ``.env`` atomically (mirrors the CLI's ``_set_env_var``)."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def patch_config(updates: dict[str, str], *, env_path: Path | None = None) -> dict[str, Any]:
    """Persist ``updates`` (env-var -> value) to ``.env`` after allowlisting the keys.

    Returns ``{"updated": [keys]}``. Raises ``ValueError`` naming any rejected key (so the endpoint
    can 400 it). Clears the ``get_settings`` cache so the next read sees the new values. Values are
    written verbatim and never logged.
    """
    rejected = [k for k in updates if k not in ALLOWED_KEYS]
    if rejected:
        raise ValueError(f"not editable: {', '.join(sorted(rejected))}")
    # Allowlisting the KEY isn't enough: a newline in the VALUE would split into extra .env lines and
    # inject arbitrary env vars (e.g. a provider key, or disabling the sandbox). Reject control chars.
    for key, value in updates.items():
        if any(c in str(value) for c in "\r\n"):
            raise ValueError(f"value for {key} may not contain a newline")
    path = env_path or Path(".env")
    for key, value in updates.items():
        _write_env_var(path, key, str(value))
        # Also update the live process env, so the running gateway / get_settings() sees the new value
        # THIS session without a restart — a key added in the onboarding wizard is usable immediately
        # (Settings reads from os.environ; .env is only re-read on a fresh process).
        os.environ[key] = str(value)
    get_settings.cache_clear()  # the lru_cache must not serve stale settings after a write
    return {"updated": sorted(updates)}
