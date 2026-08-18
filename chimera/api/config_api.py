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

from chimera.config import Settings, get_settings, pinned_by_environment
from chimera.providers.catalog import PROVIDERS

# Credential env-vars (secret) and the non-secret settings the UI may edit. Anything outside this set
# is rejected by patch_config, so the endpoint can't be used to write arbitrary .env lines.
#: Credentials that buy a capability rather than a model — search, speech, images. Listed on the
#: settings screen beside the providers, and deliberately NOT offered by the first-run wizard: none
#: of them makes ``has_any_key`` true, so choosing one there would be a dead end that confirms.
_TOOL_CREDENTIALS = {
    "TAVILY_API_KEY": "Tavily (web search)",
    "BRAVE_API_KEY": "Brave (web search)",
    "SERPAPI_API_KEY": "SerpAPI",
    "ELEVENLABS_API_KEY": "ElevenLabs (TTS)",
    "STABILITY_API_KEY": "Stability (images)",
}
# The model providers come from the catalog, which owns their slugs and their labels. Keeping a
# second list here is how the CLI and the app end up disagreeing about what a provider is called.
_PROVIDER_LABELS = {p.env: p.label for p in PROVIDERS} | _TOOL_CREDENTIALS
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
    # Only reachable by hand-editing .env until now. `api_base` is next to it on the screen and is
    # NOT a substitute: that one is sent on every call, this one only points the Ollama provider.
    "CHIMERA_OLLAMA_BASE_URL",
    # The model behind the semantic-memory toggle three rows below. Offering the switch and hiding
    # its dependency is how a control ends up confirming a change it did not make: recall degrades
    # to lexical on any embedder failure, without a word on this screen.
    "CHIMERA_EMBED_MODEL",
    # The model behind the editor's inline completion. It needs a BASE tag (an instruct model
    # ignores the suffix and answers in prose), which is a thing nobody guesses — so the field has
    # to be on the screen, not in a file the user has to be told about.
    "CHIMERA_COMPLETE_MODEL",
    "CHIMERA_CACHE",
    "CHIMERA_PROMPT_CACHE",
    "CHIMERA_MEMORY_BACKEND",
    "CHIMERA_SEMANTIC_MEMORY",
    "CHIMERA_AUTO_CONSOLIDATE",
    "CHIMERA_CHAT_MEMORY",  # the "Remember from chat" toggle (opt-in durable memory from chat)
    "CHIMERA_APP_CRON",  # run the cron daemon inside the desktop app (proactivity)
    "CHIMERA_APP_MESSAGING",  # auto-start messaging adapters in the desktop app at boot
    "CHIMERA_GUARD_CHAT",  # assemble the chat agent with the coding turn's denylist + taint ledger
    "CHIMERA_SANDBOX",
    "CHIMERA_SANDBOX_IMAGE",
    # Watch the page the agent is on. The setting was written, wired and reachable only by editing
    # `.env`: `default_registry` has always passed `settings.browser_headless` to the browser tool,
    # and `PATCH /api/config` has always refused the key. So the one way to see what the agent is
    # doing on a web page was a file the app never mentions.
    "CHIMERA_BROWSER_HEADLESS",
    "CHIMERA_MCP_AUTOLOAD",
    # The learn-to-use wire. Off by default, which means the agent writes skills and never reads one
    # back — the promise of the product with the switch missing from the product.
    "CHIMERA_SKILL_CARDS",
    # How much the agent may do. Editable because "configure my right hand" is unanswerable without
    # them, and because the alternative — hand-editing .env — is what people were already doing,
    # unaided, on the three settings with the largest blast radius here.
    "CHIMERA_REACH",
    "CHIMERA_APPROVAL",
    "CHIMERA_HOST_EXEC",
    "CHIMERA_TOOL_DENYLIST",
}
ALLOWED_KEYS = _SECRET_KEYS | _EDITABLE_SETTINGS


def is_editable(key: str) -> bool:
    """Whether ``patch_config`` will write this env var.

    The fixed allowlist above, plus any ``<PROVIDER>_API_KEY`` the credential gate now accepts. The
    two have to agree: once :mod:`chimera.providers.discovery` lets a Groq key start the agent, a
    screen that cannot save one is a screen that tells the user their key is unsupported. The
    discovery helper is also what keeps the search and speech credentials out — they match the same
    name pattern and are not providers of models.
    """
    from chimera.providers.discovery import provider_from_env_var

    return key in ALLOWED_KEYS or provider_from_env_var(key) is not None

#: When a saved setting actually starts applying, for the ones where the answer is not "now".
#:
#: Declared here, beside the allowlist, because the answer is a property of where the value is READ
#: — not of the screen that writes it. A list maintained in the frontend would go stale the first
#: time a read moves, and it would go stale silently, which is the failure this whole field exists
#: to stop: a control that confirms and does nothing spends the user's trust in every other control
#: on the screen.
#:
#: Anything absent from this map applies to the next call. That is the common case now that the
#: gateway and the request handlers read through instead of holding a boot-time snapshot.
NEXT_CONVERSATION = "next_conversation"
NEXT_LAUNCH = "next_launch"
APPLIES_WHEN: dict[str, str] = {
    # Decided when a conversation is built (`factory()` in `chimera app`), so an open conversation
    # keeps the behaviour it started with — deliberately: changing a running chat's guard or backend
    # underneath it would make its transcript describe two different agents.
    "CHIMERA_CASCADE": NEXT_CONVERSATION,
    "CHIMERA_GUARD_CHAT": NEXT_CONVERSATION,
    "CHIMERA_CHAT_MEMORY": NEXT_CONVERSATION,
    # Read once, when `default_registry` constructs the browser tool — and the tool then keeps the
    # Chromium it launched for as long as it lives. Re-reading the value could not pull a window
    # onto the screen of a browser that is already running headless, so the honest answer is the
    # next conversation, which is when a fresh registry (and a fresh browser) is built.
    "CHIMERA_BROWSER_HEADLESS": NEXT_CONVERSATION,
    # These start something at boot — a daemon thread and a set of MCP subprocesses. Re-reading the
    # value would not undo that, so the honest answer is the relaunch, not a re-read.
    "CHIMERA_APP_CRON": NEXT_LAUNCH,
    "CHIMERA_MCP_AUTOLOAD": NEXT_LAUNCH,
}


def _hint(value: str | None) -> str:
    """A safe recognition hint: the last 4 chars of a long secret, else empty. Never the whole value."""
    if not value or len(value) < 8:
        return ""
    return f"…{value[-4:]}"


def _pool_env(provider: str) -> str:
    """``"openrouter"`` -> ``CHIMERA_OPENROUTER_KEYS``, or ``ValueError`` for anything else.

    Only the five with a settings field have a pool: rotation and cooldown are per-provider state
    the gateway keeps, not something a name alone can conjure. A discovered provider gets its single
    key from the environment and is none the worse for it.
    """
    from chimera.providers.catalog import PROVIDERS_BY_NAME, provider_names

    if provider not in PROVIDERS_BY_NAME:
        raise ValueError(f"unknown provider: {provider} (known: {', '.join(provider_names())})")
    return f"CHIMERA_{provider.upper()}_KEYS"


def read_pools(settings: Settings) -> list[dict[str, Any]]:
    """Every provider's rotation pool, masked — position and last four characters, nothing else."""
    from chimera.providers.catalog import PROVIDERS_BY_NAME

    return [
        {
            "provider": name,
            "env": _pool_env(name),
            "keys": [
                {"index": i, "hint": _hint(key)}
                for i, key in enumerate(settings.credential_pool(name))
            ],
        }
        for name in PROVIDERS_BY_NAME
    ]


def _write_pool(provider: str, keys: list[str], env_path: Path | None) -> dict[str, Any]:
    env = _pool_env(provider)
    value = ",".join(keys)
    _write_env_var(env_path or Path(".env"), env, value)
    os.environ[env] = value
    get_settings.cache_clear()
    return {"provider": provider, "count": len(keys)}


def pool_add(provider: str, key: str, *, env_path: Path | None = None) -> dict[str, Any]:
    """Append one key to a provider's pool.

    The client sends a key and never a list, which is what makes the read-modify-write safe: the
    server holds the only copy of the other keys, so a stale or masked client view cannot destroy
    them. Rejects a comma (it is the pool separator, so one key would silently become two) and
    anything shaped like the mask this API hands out.
    """
    candidate = (key or "").strip()
    if not candidate:
        raise ValueError("key may not be empty")
    if "," in candidate:
        raise ValueError("key may not contain a comma — that is the separator between pool entries")
    if any(c in candidate for c in "\r\n"):
        raise ValueError("key may not contain a newline")
    if candidate.startswith("…") or set(candidate) <= {"*", "•", "·"}:
        # A client echoing back what it displayed. Cheap to check, and it fails loudly here instead
        # of quietly replacing a working pool with its own mask.
        raise ValueError("that looks like a masked hint, not a key")
    existing = list(get_settings().credential_pool(provider)) if provider else []
    if candidate in existing:
        raise ValueError("that key is already in the pool")
    return _write_pool(provider, [*existing, candidate], env_path)


def pool_remove(provider: str, index: int, *, env_path: Path | None = None) -> dict[str, Any]:
    """Drop the key at ``index``. An index, never a value — the client has never seen one."""
    existing = list(get_settings().credential_pool(provider)) if provider else []
    _pool_env(provider)  # validates the provider even when the pool is empty
    if not 0 <= index < len(existing):
        raise ValueError(f"no key at index {index} (pool has {len(existing)})")
    return _write_pool(provider, existing[:index] + existing[index + 1 :], env_path)


def read_config(settings: Settings) -> dict[str, Any]:
    """The settings snapshot for the UI. Secrets are masked to ``{set, hint}`` — never cleartext."""
    creds = settings.credentials()
    known = {p.env: p for p in PROVIDERS}
    providers = [
        {
            "env": env,
            "label": _PROVIDER_LABELS[env],
            "set": bool(creds.get(env)),
            "hint": _hint(creds.get(env)),
            "llm": env in known,
            "model": known[env].default_model if env in known else "",
            "keys_url": known[env].keys_url if env in known else "",
        }
        for env in _PROVIDER_LABELS
    ]
    # Providers Chimera was never told about. A key for any of LiteLLM's other ~100 vendors now opens
    # the gate, so the screen listing credentials has to be able to show one — otherwise the app
    # reports "no key" about a key it is currently using. They arrive without a suggested model or a
    # sign-up page, which is the honest answer: we discovered the credential, we did not ship support
    # for the vendor. The value is masked by the same `_hint`.
    from chimera.providers.discovery import generic_providers

    for name in generic_providers():
        env = f"{name.upper()}_API_KEY"
        value = os.environ.get(env)
        providers.append(
            {
                "env": env,
                "label": name.title(),
                "set": bool(value),
                "hint": _hint(value),
                "llm": True,
                "model": "",
                "keys_url": "",
            }
        )
    ladder = settings.tier_ladder()
    pools = read_pools(settings)
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
            "ollama_base_url": settings.ollama_base_url,
            "complete_model": settings.complete_model,
        },
        "memory": {
            "backend": settings.memory_backend,
            "semantic": settings.semantic_memory,
            "auto_consolidate": settings.auto_consolidate,
            "remember_from_chat": settings.remember_from_chat,
            "skill_cards": settings.skill_cards,
            "embed_model": settings.embed_model,
        },
        "cache": {"completion": settings.cache, "prompt": settings.prompt_cache},
        "sandbox": {"mode": settings.sandbox, "image": settings.sandbox_image},
        "browser": {"headless": settings.browser_headless},
        "autonomy": {
            "reach": settings.reach,
            "approval": settings.approval,
            "host_exec": settings.host_exec,
            "denied_tools": list(settings.tool_denylist),
        },
        # Off by default and that is a real exposure — see Settings.guard_chat. Exposed here because
        # the posture line points at this switch by name when it reports a conversation as unguarded.
        "guard": {"chat": settings.guard_chat},
        "server": {"token_set": bool(settings.server_token)},
        "mcp": {"autoload": settings.mcp_autoload},
        "automation": {"cron": settings.app_cron},
        "providers": providers,
        "pools": pools,
        # Keys absent here apply to the next call; see APPLIES_WHEN.
        "applies": dict(APPLIES_WHEN),
        # Which of those keys this deployment's environment pins — see `pinned_by_environment`.
        # Every writable key is offered, secrets included: an `OPENROUTER_API_KEY` exported by the
        # unit file reverts a key pasted here exactly the same way a `CHIMERA_REACH` does, and it is
        # the one this screen is least likely to be believed about. The pool variables are in the
        # set for the same reason and are NOT in `ALLOWED_KEYS`: they are written by the pool
        # endpoints rather than by `patch_config`, through the same `.env` and with the same fate.
        "pinned": pinned_by_environment(ALLOWED_KEYS | {str(p["env"]) for p in pools}),
    }


def doctor(settings: Settings) -> dict[str, Any]:
    """A config-health snapshot (no live provider pings): which providers have keys, the model ladder."""
    from chimera.acp.agents import available_agents

    ladder = settings.tier_ladder()
    return {
        "has_any_key": settings.has_any_key(),
        "configured_providers": settings.configured_providers(),
        "default_model": settings.default_model,
        "tiers": {"weak": ladder.weak, "mid": ladder.mid, "top": ladder.top},
        "memory_backend": settings.memory_backend,
        "cache": settings.cache,
        "sandbox": settings.sandbox,
        # Capability by capability, measured on THIS machine. A frozen sidecar is built by CI on a
        # machine nobody looked at, so "the adapter should be there" stops being evidence at exactly
        # the point a user needs the answer — and `npx` missing reads identically to a bug in us.
        "external_agents": available_agents(),
        # Whether a spend cap could even work here — see pricing_capability.
        "spend": pricing_capability(settings),
        # The editor's own capabilities, measured on THIS machine. Same reason as the agents above:
        # a downloaded app is the exact place where "it should be installed" stops being evidence,
        # and the answer a new user needs is "what do I install", not "something is unavailable".
        "editor": editor_capabilities(settings),
    }


def pricing_capability(settings: Settings) -> dict[str, object]:
    """Whether this machine's default model can be priced at all.

    A dollar cap stops the run when it meets a call it cannot price — the safe rule, and the one
    that turns an unpriced default model into a feature that refuses to work. The moment to learn
    that is while reading `doctor`, not when a 3 a.m. cron job halts. So the answer is reported
    before anyone sets a cap, with the model named.
    """
    from chimera.fusion.receipts import resolve_price

    model = settings.default_model
    priced = resolve_price(model) is not None
    return {
        "key": "spend_cap",
        "label": "Spend cap (dollar ceiling)",
        "available": priced,
        "probed": True,  # the price table either resolves this model or it does not
        "detail": model,
        "hint": (
            ""
            if priced
            else f"no list price known for {model}: a spend cap would stop on its first call. "
            "Register one with chimera.fusion.receipts.set_price, or cap a priced model."
        ),
    }


def editor_capabilities(settings: Settings) -> list[dict[str, object]]:
    """Diagnostics and inline completion: present or absent, with the command that fixes absent.

    The two are known to DIFFERENT degrees, and `probed` is what says so. `ruff` is a program, so
    resolving it is a real answer. The completion model lives behind a server that may be on another
    machine; pinging it would make `doctor` slow and occasionally wrong about a machine that is
    merely asleep, so all that is known there is that a model and a URL were configured.

    Collapsing the two into one word would be the lie this whole surface exists to avoid: "available"
    for a completion model nobody has reached is a promise the editor then quietly fails to keep.
    """
    from chimera.api.lsp_api import ruff_available

    return [
        {
            "key": "diagnostics",
            "label": "Editor diagnostics (ruff)",
            "available": ruff_available(),
            "probed": True,  # the program either resolves on this machine or it does not
            "detail": "ruff server",
            "hint": "pip install ruff (or install Chimera's 'dev' extra)",
        },
        {
            "key": "completion",
            "label": "Inline completion (local model)",
            # CONFIGURED, not reached. The editor reports the live answer, because it is the only
            # surface that has just asked one.
            "available": bool(settings.complete_model and settings.ollama_base_url),
            "probed": False,
            "detail": f"{settings.complete_model or '(unset)'} at {settings.ollama_base_url or '(unset)'}",
            "hint": f"ollama pull {settings.complete_model}" if settings.complete_model else "set CHIMERA_COMPLETE_MODEL",
        },
    ]


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
    rejected = [k for k in updates if not is_editable(k)]
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
