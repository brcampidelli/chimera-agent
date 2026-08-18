"""The models this install can actually pick from, asked rather than remembered.

Every place in the app that names a model — the onboarding wizard, the Settings rows, and until now
the conversation itself, which had no way to name one at all — was a free-text box. The user typed a
slug from memory and found out whether it existed on the first call, as a provider error mid-turn.
:mod:`chimera.providers.ollama` already fixed that for local tags by asking the daemon; this module
does the same for the cloud, and for the same reason.

Three decisions, each about not claiming more than we know:

**The list is filtered by the keys the user has.** OpenRouter publishes four hundred models on an
endpoint that needs no credential, so listing them is easy and, for someone holding only an Anthropic
key, a trap: four hundred slugs that all answer 401. ``available_models`` therefore lists a remote
catalogue only when its provider is in ``configured_providers()`` — the same test
:func:`chimera.providers.catalog._reachable` applies to the tier ladder — with one deliberate
exception, ``provider=``, for the onboarding wizard: there the user is holding the key they are
about to paste, and refusing to show them what it buys is answering a question nobody asked.

**"Nothing answered" is not "there is nothing".** Same rule as the Ollama reader: the fetch failing
comes back as a ``reason`` token next to whatever we DID resolve (the curated catalogue, which needs
no network), so the picker can say *the full list is unavailable* rather than render an empty menu
that reads as *your key buys nothing*. The reason is a WORD, not a sentence — the app ships ten
languages and the server does not know which one is on screen.

**An unknown price is ``None``, never zero.** OpenRouter prices per token as a decimal string and
uses ``"-1"`` for models it cannot quote up front. Zero is a claim ("this is free") that a variable
price does not support, and it is the number a spend ceiling would divide by.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from chimera.telemetry import get_logger

_log = get_logger("providers.listing")

#: Where a model in the list came from. ``catalog`` is Chimera's own curated suggestions, which need
#: no network and are the fallback when a remote listing fails.
Source = Literal["catalog", "openrouter", "ollama"]

#: Why the remote list is missing or partial. ``""`` when nothing went wrong — including when the
#: user simply has no cloud key, which is ``no_provider`` rather than a failure.
Reason = Literal["", "no_provider", "unreachable", "http_error", "unreadable"]

#: OpenRouter's public model index. No credential: it is the same list the website renders, and
#: asking for it with a key attached would tie a plain catalogue read to the user's account.
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

#: Seconds to wait for the remote list. This is asked from a menu the user just clicked, so it has
#: to fail faster than they lose patience; the curated catalogue is rendered either way.
DEFAULT_TIMEOUT_S = 6.0

#: How long a fetched list is reused. Models arrive weekly, not by the minute, and the menu is
#: opened far more often than the catalogue changes.
CACHE_TTL_S = 3600.0

#: How long a FAILURE is remembered. Short, because the usual cause is a network that came back a
#: moment later — but not zero, or every reopened menu re-runs a six-second timeout.
CACHE_FAILURE_TTL_S = 60.0


@dataclass(frozen=True)
class ModelOption:
    """One model the user could pick, with the facts that decide whether they should.

    Every optional field is optional because it is genuinely unknown for some source, and a picker
    that renders ``None`` as ``0`` or as ``false`` states something we were never told.
    """

    #: What goes in the request — already prefixed, so the UI never has to assemble a slug.
    slug: str
    #: Human name as its own catalogue spells it ("DeepSeek: DeepSeek V3.1").
    label: str
    vendor: str
    source: Source
    #: Approximate context window in thousands of tokens; None when the source does not say.
    context_k: int | None = None
    #: USD per 1M tokens. None = unknown (never guessed); 0.0 = genuinely free.
    input_per_m: float | None = None
    output_per_m: float | None = None
    #: Whether the model can call tools. **None means unknown**, which is not the same as False: a
    #: coding turn without tools can only describe an edit, so the difference is worth a word in the
    #: UI rather than an assumption here.
    tools: bool | None = None
    #: Whether an image can be sent to it. Same rule for None.
    vision: bool | None = None
    #: Free tier — rate-limited, and worth marking, because "free" is why someone picks it.
    free: bool = False
    #: Present in Chimera's curated catalogue, i.e. a model this project has actually run.
    recommended: bool = False


@dataclass(frozen=True)
class ModelListing:
    """The pickable models, plus what we failed to reach while assembling them."""

    models: tuple[ModelOption, ...]
    #: Which catalogues actually contributed, so the client can say where the list came from.
    sources: tuple[Source, ...]
    reason: Reason = ""


# The remote list, kept between requests: (fetched_at, models, reason). A failure is cached too —
# see CACHE_FAILURE_TTL_S.
_cache: tuple[float, tuple[ModelOption, ...], Reason] | None = None


def _price_per_million(raw: Any) -> float | None:
    """OpenRouter's per-token decimal string as USD per 1M tokens, or None when it is not a price.

    ``"-1"`` is their marker for *quoted at request time*, and an empty or absent field means the
    same thing. Both become None: a spend ceiling that divides by a fabricated zero stops a turn for
    a reason nobody chose.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return round(value * 1_000_000, 6)


def _openrouter_option(entry: dict[str, Any]) -> ModelOption | None:
    """One entry of OpenRouter's index as a :class:`ModelOption`, or None if it is not usable."""
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    model_id = model_id.strip()

    name = entry.get("name")
    label = name.strip() if isinstance(name, str) and name.strip() else model_id
    # "DeepSeek: DeepSeek V3.1" — the vendor is the part before the colon, and when there is none
    # the slug's first segment is the same fact spelled less nicely.
    vendor = label.split(":", 1)[0].strip() if ":" in label else model_id.split("/", 1)[0]

    raw_pricing = entry.get("pricing")
    pricing: dict[str, Any] = raw_pricing if isinstance(raw_pricing, dict) else {}
    input_per_m = _price_per_million(pricing.get("prompt"))
    output_per_m = _price_per_million(pricing.get("completion"))

    context = entry.get("context_length")
    context_k = int(context) // 1000 if isinstance(context, int | float) and context > 0 else None

    params = entry.get("supported_parameters")
    tools = "tools" in params if isinstance(params, list) else None

    raw_architecture = entry.get("architecture")
    architecture: dict[str, Any] = raw_architecture if isinstance(raw_architecture, dict) else {}
    modalities = architecture.get("input_modalities")
    vision = "image" in modalities if isinstance(modalities, list) else None

    return ModelOption(
        # LiteLLM routes on the prefix, so the slug the request carries is the prefixed one. The
        # UI must never have to know that rule.
        slug=f"openrouter/{model_id}",
        label=label,
        vendor=vendor,
        source="openrouter",
        context_k=context_k,
        input_per_m=input_per_m,
        output_per_m=output_per_m,
        tools=tools,
        vision=vision,
        free=model_id.endswith(":free") or (input_per_m == 0.0 and output_per_m == 0.0),
    )


def openrouter_models(*, timeout_s: float = DEFAULT_TIMEOUT_S) -> tuple[tuple[ModelOption, ...], Reason]:
    """OpenRouter's public model index, cached. Never raises.

    Order is theirs — newest first — and it is kept rather than re-sorted here, because it is the
    only ranking the endpoint gives us and inventing another one (by price, by name) would present
    an opinion as data. The caller floats the curated models above it.
    """
    global _cache
    now = time.monotonic()
    if _cache is not None:
        fetched_at, models, reason = _cache
        ttl = CACHE_TTL_S if reason == "" else CACHE_FAILURE_TTL_S
        if now - fetched_at < ttl:
            return models, reason

    result = _fetch_openrouter(timeout_s)
    _cache = (now, *result)
    return result


def _fetch_openrouter(timeout_s: float) -> tuple[tuple[ModelOption, ...], Reason]:
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is a hard dependency
        return (), "unreachable"

    try:
        response = httpx.get(OPENROUTER_MODELS_URL, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 — no network is a normal state, not a 500
        _log.debug("openrouter model index unreachable: %s", exc)
        return (), "unreachable"
    if response.status_code >= 400:
        _log.debug("openrouter model index answered %s", response.status_code)
        return (), "http_error"

    try:
        payload = response.json()
        entries = payload["data"]
    except Exception:  # noqa: BLE001 — a 200 from a captive portal is not the index
        return (), "unreadable"
    if not isinstance(entries, list):
        return (), "unreadable"

    options = [
        option
        for entry in entries
        if isinstance(entry, dict) and (option := _openrouter_option(entry)) is not None
    ]
    return tuple(options), ""


def _catalog_options(providers: list[str]) -> list[ModelOption]:
    """Chimera's curated suggestions, filtered to the ones this user's keys can call.

    No network, so this is what the picker still has when the remote fetch fails — and what it has
    on a machine that is offline entirely.
    """
    from chimera.providers.catalog import CATALOG

    out: list[ModelOption] = []
    for entry in CATALOG:
        # Same test the tier ladder applies: the slug's first segment against the configured
        # providers. No keys at all means no filtering — nothing is reachable, so "unreachable"
        # carries no information and the suggestions remain the documented answer.
        if providers and entry.slug.split("/", 1)[0] not in providers:
            continue
        out.append(
            ModelOption(
                slug=entry.slug,
                label=entry.slug.split("/", 1)[-1],
                vendor=entry.vendor,
                source="catalog",
                context_k=entry.context_k,
                input_per_m=entry.input_per_m,
                output_per_m=entry.output_per_m,
                tools=entry.tools,
                # The catalogue does not record vision, and guessing from the vendor would be a
                # claim about a capability that decides whether an attached screenshot is seen.
                vision=None,
                free=entry.input_per_m == 0.0 and entry.output_per_m == 0.0,
                recommended=True,
            )
        )
    return out


def _ollama_options(base_url: str) -> list[ModelOption]:
    """The tags the local Ollama has pulled. Silent when it is not running — that is not an error
    here: this is one source among several, and a machine without Ollama is the normal case."""
    from chimera.providers.ollama import installed_models

    found = installed_models(base_url)
    if not found.reachable:
        return []
    return [
        ModelOption(
            slug=f"ollama/{tag}",
            label=tag,
            vendor="Ollama",
            source="ollama",
            # Ollama's tag list carries none of this. Local models are free to run, and that is the
            # one fact we can state without asking anything further.
            input_per_m=0.0,
            output_per_m=0.0,
            free=True,
        )
        for tag in found.models
    ]


def available_models(
    settings: Any,
    *,
    provider: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ModelListing:
    """Everything this install could put in a request's ``model`` field.

    ``provider`` forces one remote catalogue to be listed regardless of the keys present. It exists
    for the onboarding wizard, which asks *what does this key buy* while holding a key that has not
    been saved yet — the only moment where filtering by configured providers answers the wrong
    question.

    Curated models come first, deduplicated against the remote list so a model that is both keeps
    its recommendation. Everything else keeps the remote catalogue's own order.
    """
    providers = list(settings.configured_providers())
    wants_openrouter = (provider or "").strip().lower() == "openrouter" or "openrouter" in providers

    remote: tuple[ModelOption, ...] = ()
    reason: Reason = "" if wants_openrouter else "no_provider"
    if wants_openrouter:
        remote, reason = openrouter_models(timeout_s=timeout_s)

    curated = _catalog_options(providers)
    # A curated slug that the remote list also carries is ONE model: the remote entry has the live
    # price and context, the curated one has the recommendation. Merge rather than list twice.
    remote_by_slug = {option.slug: option for option in remote}
    merged: list[ModelOption] = []
    for option in curated:
        live = remote_by_slug.get(option.slug)
        # A curated OpenRouter slug the live index does not carry has been retired — the catalogue
        # says of itself that its slugs go stale, and this is what stale looks like. Dropped rather
        # than offered, because offering it is a 404 on the first call, after the user chose it.
        #
        # Only when the fetch SUCCEEDED: an empty `remote` is a network failure, and reading absence
        # from a list we never received would delete the whole recommendation set offline.
        if remote and option.slug.startswith("openrouter/") and live is None:
            _log.debug("catalog slug %s is not in OpenRouter's index — dropping it", option.slug)
            continue
        merged.append(
            ModelOption(
                slug=option.slug,
                label=live.label if live else option.label,
                vendor=live.vendor if live else option.vendor,
                source=option.source,
                context_k=live.context_k if live else option.context_k,
                # Prices come from the live entry when there is one: the catalogue says of itself
                # that its numbers are approximate and go stale.
                input_per_m=live.input_per_m if live else option.input_per_m,
                output_per_m=live.output_per_m if live else option.output_per_m,
                tools=live.tools if live and live.tools is not None else option.tools,
                vision=live.vision if live else option.vision,
                free=live.free if live else option.free,
                recommended=True,
            )
        )

    seen = {option.slug for option in merged}
    for option in remote:
        if option.slug not in seen:
            merged.append(option)
            seen.add(option.slug)

    local = _ollama_options(getattr(settings, "ollama_base_url", ""))
    for option in local:
        if option.slug not in seen:
            merged.append(option)
            seen.add(option.slug)

    sources: list[Source] = []
    if curated:
        sources.append("catalog")
    if remote:
        sources.append("openrouter")
    if local:
        sources.append("ollama")
    return ModelListing(models=tuple(merged), sources=tuple(sources), reason=reason)
