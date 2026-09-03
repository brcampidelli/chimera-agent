"""Multi-vendor model catalog + tier resolution (M16-A1).

Chimera is vendor-agnostic by design: ANY model (via LiteLLM/OpenRouter slugs) can
occupy ANY role — orchestrator, worker, or weak probe. This module is the curated
*suggestion list* behind ``chimera models`` and ``chimera init``, plus the resolver
that turns a cost mode (``cheap | balanced | premium | auto``) into a concrete
:class:`TierLadder` when the user has not pinned models explicitly.

Honesty rules:
- The catalog is DATA, not logic — slugs and prices go stale; update them here (or
  override at runtime) without touching orchestration code.
- Prices are approximate public list rates for *estimation*; ``None`` means unknown
  (never guessed), ``0.0`` means a genuinely free tier.
- An explicit user override (env/config) ALWAYS beats the cost mode; the cost mode
  beats the built-in default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Tier = Literal["weak", "mid", "top"]
CostMode = Literal["cheap", "balanced", "premium", "auto"]

COST_MODES: tuple[CostMode, ...] = ("cheap", "balanced", "premium", "auto")


@dataclass(frozen=True)
class CatalogEntry:
    """One suggested model for a tier. Slugs/prices are data — verify, then trust."""

    slug: str
    tier: Tier
    vendor: str
    input_per_m: float | None
    """USD per 1M input tokens; None = unknown (never guessed), 0.0 = free tier."""
    output_per_m: float | None
    tools: bool
    """Whether tool-calling is reliable enough to route tool turns here."""
    context_k: int
    """Approximate context window, thousands of tokens."""
    notes: str = ""


# Curated multi-vendor suggestions per tier. DATA ONLY — extend/correct freely; `chimera models`
# renders this and `resolve_tiers` picks defaults from it.
#
# **Every slug and price below was checked against OpenRouter's live index on 2026-08-18**, and that
# pass is the reason this comment is longer than the last one. Six of the fourteen entries had been
# WITHDRAWN by the provider — including the `weak` rung of every cost preset, so a run that routed a
# role to the weak tier was calling a model that does not exist. Nothing detected it: the catalogue
# is data, data has no tests, and the failure only appears as a provider error inside somebody's run.
# Every surviving entry also had the wrong price, the wrong context window, or both.
#
# The lesson is in `tests/test_catalog_is_live.py`: a slug is a claim about somebody else's product,
# and it decays whether or not anyone looks. That test looks — `-m integration`, so it never reds a
# working build over a network hiccup.
#
# Two deliberate consequences of the withdrawals:
#
# - **The free tiers are gone from the presets.** Both `:free` slugs were retired (the paid variants
#   of the same models survive), and a preset that depends on a free tier is a preset that breaks
#   when the vendor stops donating. `weak` is now the cheapest PAID model here — cents, and it works.
# - **Prices come from the live index now, not from this table.** The picker in the app reads them at
#   runtime; these are for `chimera models` and for the estimate when nothing else knows. They are
#   still approximate by nature, so the same warning applies: verify, then trust.
CATALOG: tuple[CatalogEntry, ...] = (
    # --- weak: near-free probes. Cheap first drafts, k-sample agreement. ---
    CatalogEntry(
        "openrouter/deepseek/deepseek-v4-flash", "weak", "DeepSeek",
        0.0886, 0.1772, tools=True, context_k=1048,
        notes="cheapest capable probe here, with a frontier-sized window; unmeasured in this repo",
    ),
    CatalogEntry(
        "openrouter/mistralai/mistral-small-3.2-24b-instruct", "weak", "Mistral",
        0.075, 0.20, tools=True, context_k=131,
        notes="the local-lift goldilocks model; cheap paid weak with usable tools. The window read\n        256k here until 2026-09-03; the provider serves 131k",
    ),
    CatalogEntry(
        "openrouter/meta-llama/llama-3.3-70b-instruct", "weak", "Meta",
        0.10, 0.32, tools=True, context_k=131,
        notes="the paid variant; the :free one was withdrawn on 2026-08-18. Priced 0.71/0.71 here\n        until a live check on 2026-09-03 measured 0.10/0.32 — and the note that input and\n        output cost the same stopped being true with it",
    ),
    CatalogEntry(
        "openrouter/openai/gpt-oss-20b", "weak", "OpenAI",
        0.03, 0.13, tools=True, context_k=131,
        notes="the paid variant; the :free one was withdrawn on 2026-08-21",
    ),
    # --- mid: the daily workhorses. Reliable tools, cents per task. ---
    CatalogEntry(
        "openrouter/deepseek/deepseek-v4-flash-0731", "mid", "DeepSeek",
        0.065, 0.18, tools=True, context_k=1310,
        notes="the product default and the fusion judge since 2026-09-03. Same vendor as the chat-v3.1 it replaced, at 0.065/0.18 against 0.25/0.95 (3.8x cheaper in, 5.3x out) with eight times the window. Wrote a file on the first ask in a live probe, in 72s",
    ),
    CatalogEntry(
        "openrouter/z-ai/glm-5.3-flash", "mid", "Zhipu (GLM)",
        0.075, 0.25, tools=True, context_k=1310,
        notes="the best price-to-index here by a distance: 0.075/0.25 with a third-party agentic index of 58.2, within a point of claude-opus-5 at 66x the input price. NOT the default, and the reason is measured: on the same one-file probe it took 257s against 72s for the slug above. Reach for it when the window or the index matters more than latency",
    ),
    CatalogEntry(
        "openrouter/deepseek/deepseek-chat-v3.1", "mid", "DeepSeek",
        0.25, 0.95, tools=True, context_k=163,
        notes="proven in this repo's benches; the product default. Priced 0.55/1.65 here until a\n        live check on 2026-09-03 measured 0.25/0.95",
    ),
    CatalogEntry(
        "openrouter/z-ai/glm-4.6", "mid", "Zhipu (GLM)",
        0.55, 2.20, tools=True, context_k=204,
        notes="strong agentic mid. Priced 0.50/2.00 here until a live check on 2026-09-03 measured\n        0.55/2.20 — this one went UP, which is the case a drift check has to be able to see:\n        anything written assuming prices only fall would have read this as still correct",
    ),
    CatalogEntry(
        "openrouter/google/gemini-2.5-flash", "mid", "Google",
        0.30, 2.50, tools=True, context_k=1048,
        notes="huge context",
    ),
    CatalogEntry(
        "openrouter/openai/gpt-5.6-luna", "mid", "OpenAI",
        0.20, 1.20, tools=True, context_k=1050,
        notes="replaces gpt-5.5-mini, withdrawn on 2026-08-18",
    ),
    CatalogEntry(
        "openrouter/qwen/qwen3-coder", "mid", "Qwen (Alibaba)",
        0.30, 1.00, tools=True, context_k=262,
        notes="code-leaning mid",
    ),
    # --- top: orchestrator/judge class. Decompose, adjudicate, synthesize. ---
    CatalogEntry(
        "openrouter/z-ai/glm-5.3", "top", "Zhipu (GLM)",
        1.40, 4.40, tools=True, context_k=1310,
        notes="the top rung of `balanced` and `auto` since 2026-09-03, and the reason is the slug below rather than this one: R1 carried a 64k window into a tier that asks for 100k. This has 1310k, a third-party agentic index of 59.1 against R1's 3.1, and wrote a file in 51s against R1's 209s. It costs twice as much per token and buys a working top tier",
    ),
    CatalogEntry(
        "openrouter/deepseek/deepseek-r1", "top", "DeepSeek",
        0.70, 2.50, tools=True, context_k=64,
        notes="economic reasoner; the default economic orchestrator. Note the SMALL window",
    ),
    CatalogEntry(
        "openrouter/moonshotai/kimi-k2", "top", "Moonshot (Kimi)",
        0.57, 2.30, tools=True, context_k=131,
        notes="strong agentic frontier-class",
    ),
    CatalogEntry(
        "openrouter/openai/gpt-5.5", "top", "OpenAI",
        5.00, 30.00, tools=True, context_k=1050,
        notes="frontier; this repo's default_model until 2026-08-18, and the reason it changed",
    ),
    CatalogEntry(
        "openrouter/google/gemini-3.8-flash", "top", "Google",
        0.75, 3.75, tools=True, context_k=1048,
        notes="the Google seat on the default fusion panel since 2026-09-03, replacing the -preview slug below there. A -preview in a DEFAULT is a default that can be withdrawn without notice, and there is no stable Gemini 3.x pro to move to: only the flash line ships non-preview. Cheaper (0.75/3.75 against 2.00/12.00) AND the better third-party agentic index (50 against 23)",
    ),
    CatalogEntry(
        "openrouter/google/gemini-3.1-pro-preview", "top", "Google",
        2.00, 12.00, tools=True, context_k=1048,
        notes="replaces gemini-3.1-pro, withdrawn on 2026-08-18",
    ),
    CatalogEntry(
        "openrouter/anthropic/claude-opus-5", "top", "Anthropic",
        5.00, 25.00, tools=True, context_k=1000,
        notes="frontier; replaces claude-opus-4-8, withdrawn on 2026-08-18",
    ),
    CatalogEntry(
        "openrouter/qwen/qwen3-max", "top", "Qwen (Alibaba)",
        0.78, 3.90, tools=True, context_k=262,
        notes="replaces qwen-max, withdrawn on 2026-08-18",
    ),
)


@dataclass(frozen=True)
class ProviderInfo:
    """A credential slot that serves models: what to call it, and what to do once it has a key."""

    env: str
    label: str
    default_model: str
    """A slug that works the moment this key is saved — the answer to "and now which model?".

    It exists because a key alone is not a working setup. Every preset in ``_PRESETS`` is an
    OpenRouter slug, so someone who saves an Anthropic key and changes nothing gets a weak/mid/top
    ladder pointing at a vendor they have no key for, and the first call fails with a 401 that names
    the wrong provider. Pinning one concrete model at setup time is what makes the ladder collapse
    onto something reachable (``fallback_single_model``) instead of onto nothing.
    """

    keys_url: str
    """Where a human goes to get one. Kept here rather than in a screen so the CLI and the desktop
    wizard cannot drift into pointing at different pages for the same provider."""


#: The providers with a settings field, a rotating key pool and a labelled slot in the UI.
#:
#: DATA, with the same warning the catalog carries about itself: slugs go stale. Anything here is a
#: starting point the user can overwrite, never a constraint — a key for any of LiteLLM's other
#: hundred-odd vendors works too (see :mod:`chimera.providers.discovery`), it simply arrives without
#: a suggestion, which is honest rather than unsupported.
#:
#: **Checked against each vendor's own model list on 2026-08-09.** Worth repeating whenever these are
#: touched, because the failure is silent in a specific way: a superseded model still answers, still
#: bills, and nothing in a passing test suite can tell you that a new install is being pointed at
#: last year's generation. Every entry below is the vendor's own "start here if you are unsure",
#: which is exactly the question a first run is asking.
PROVIDERS: tuple[ProviderInfo, ...] = (
    # This must MATCH ``Settings.default_model``, because for OpenRouter the wizard shows the
    # suggestion WITHOUT writing it — a different slug here would put a number on screen that is not
    # the one in use. Both moved to DeepSeek V3.1 together: see the note on `default_model` for why a
    # first install should not start on the most expensive model in the catalogue.
    ProviderInfo(
        "OPENROUTER_API_KEY",
        "OpenRouter",
        "openrouter/deepseek/deepseek-v4-flash-0731",
        "https://openrouter.ai/keys",
    ),
    # gpt-5.6-sol, via its documented alias; same $5/$30 as the 5.5 it replaces.
    ProviderInfo(
        "OPENAI_API_KEY", "OpenAI", "openai/gpt-5.6", "https://platform.openai.com/api-keys"
    ),
    # A canonical dateless ID, not a convenience alias — Anthropic pins these to one snapshot.
    ProviderInfo(
        "ANTHROPIC_API_KEY",
        "Anthropic",
        "anthropic/claude-opus-5",
        "https://console.anthropic.com/settings/keys",
    ),
    # The current stable Flash; `gemini-flash-latest` is the hot-swapping alias, so not that one.
    ProviderInfo(
        "GEMINI_API_KEY", "Gemini", "gemini/gemini-3.6-flash", "https://aistudio.google.com/apikey"
    ),
    # DeepSeek's long-lived alias for its chat model; unchanged.
    ProviderInfo(
        "DEEPSEEK_API_KEY",
        "DeepSeek",
        "deepseek/deepseek-chat",
        "https://platform.deepseek.com/api_keys",
    ),
)

#: ``"anthropic"`` -> its slot. The name is the env var without the suffix, lowercased — the same
#: shape :func:`chimera.providers.discovery.provider_from_env_var` produces, so a first-class name
#: and a discovered one are spelled identically everywhere they meet.
PROVIDERS_BY_NAME: dict[str, ProviderInfo] = {
    p.env.removesuffix("_API_KEY").lower(): p for p in PROVIDERS
}


def provider_names() -> list[str]:
    """The accepted ``--provider`` values, in the order they are offered."""
    return list(PROVIDERS_BY_NAME)


def entries(tier: Tier | None = None, vendor: str | None = None) -> list[CatalogEntry]:
    """Catalog entries, optionally filtered by tier and/or vendor substring."""
    found = list(CATALOG)
    if tier is not None:
        found = [e for e in found if e.tier == tier]
    if vendor is not None:
        needle = vendor.lower()
        found = [e for e in found if needle in e.vendor.lower()]
    return found


@dataclass(frozen=True)
class TierLadder:
    """Concrete weak -> mid -> top model assignment, plus where the cascade enters."""

    weak: str
    mid: str
    top: str
    entry: Tier = "weak"
    """Which tier handles a request first (the cascade escalates from here)."""

    source: str = "preset"
    """Where these slugs came from: ``override`` | ``preset`` | ``fallback_single_model``.

    Carried rather than recomputed because two screens and the CLI need to explain the ladder, and a
    second copy of the reasoning is a second thing to keep in sync. ``fallback_single_model`` is the
    one that has to be SAID: it means the presets were unreachable and every tier collapsed onto the
    user's own model, so role routing has nothing left to route.
    """

    def ladder(self) -> list[str]:
        return [self.weak, self.mid, self.top]

    def model_for(self, tier: Tier) -> str:
        return {"weak": self.weak, "mid": self.mid, "top": self.top}[tier]


# Preset ladders per cost mode. `auto` deliberately ENTERS AT MID (the user's
# "automático prioriza o médio"): the weak tier is skipped as an entry point but
# stays available for k-sample probes; escalation still climbs to top/fusion.
# The weak rung was a `:free` slug in three of these four, and on 2026-08-18 that slug no longer
# existed — so every profile that routed a role to the weak tier (explore, k-sample probes) was
# calling a model OpenRouter had withdrawn. It is a paid model now, at roughly a tenth of a cent per
# thousand tokens: a preset whose cheapest rung depends on a vendor's donation is a preset that
# breaks when the donation ends, and it ended.
_PRESETS: dict[CostMode, TierLadder] = {
    "cheap": TierLadder(
        weak="openrouter/mistralai/mistral-small-3.2-24b-instruct",
        mid="openrouter/deepseek/deepseek-v4-flash-0731",
        top="openrouter/deepseek/deepseek-v4-flash-0731",  # never pay reasoner rates
        entry="weak",
    ),
    "balanced": TierLadder(
        weak="openrouter/mistralai/mistral-small-3.2-24b-instruct",
        mid="openrouter/deepseek/deepseek-v4-flash-0731",
        top="openrouter/z-ai/glm-5.3",
        entry="weak",
    ),
    "auto": TierLadder(
        weak="openrouter/mistralai/mistral-small-3.2-24b-instruct",
        mid="openrouter/deepseek/deepseek-v4-flash-0731",
        top="openrouter/z-ai/glm-5.3",
        entry="mid",
    ),
    "premium": TierLadder(
        weak="openrouter/deepseek/deepseek-v4-flash-0731",
        mid="openrouter/openai/gpt-5.5",
        top="openrouter/anthropic/claude-opus-5",
        entry="mid",
    ),
}


class _TierSettings(Protocol):
    """The slice of Settings this resolver needs (duck-typed to avoid an import cycle)."""

    weak_model: str
    mid_model: str
    orchestrator_model: str
    cost_mode: str
    default_model: str

    def configured_providers(self) -> list[str]: ...


def _reachable(slug: str, providers: list[str]) -> bool:
    """Can this slug actually be called with the keys this user has?

    Matched on the slug's FIRST SEGMENT, because that is what the gateway itself derives the
    provider from. Matching on the catalogue's ``vendor`` field would produce the exact opposite
    answer for the most common case: ``openrouter/anthropic/claude-opus-4-8`` is vendored by
    Anthropic and reachable with an **OpenRouter** key.
    """
    return slug.split("/", 1)[0] in providers


def resolve_tiers(settings: _TierSettings) -> TierLadder:
    """Explicit override > cost mode preset > the one model this user can actually call.

    An empty string in a tier field means "let the cost mode decide"; any non-empty value is the
    user's explicit choice and always wins.

    **The presets are all OpenRouter slugs**, and until this checked the keys, a user with a single
    non-OpenRouter key got a ladder of three models they could not call — silently, and *instead of*
    the one model they had configured. Choosing any role profile then routed the run away from the
    only thing that worked. That is a bad failure when the user picks the profile; it is a worse one
    now that the system picks it for them, so the check has to exist before the picking does.

    **No keys at all means no filtering.** Nothing is reachable, so "unreachable" carries no
    information, and the presets remain the documented answer for a machine that is not configured
    yet. The filter only bites when the user has SOME keys and the ladder wants OTHERS.
    """
    mode = settings.cost_mode if settings.cost_mode in _PRESETS else "auto"
    preset = _PRESETS[mode]  # type: ignore[index]
    chosen = TierLadder(
        weak=settings.weak_model or preset.weak,
        mid=settings.mid_model or preset.mid,
        top=settings.orchestrator_model or preset.top,
        entry=preset.entry,
        source="override"
        if (settings.weak_model or settings.mid_model or settings.orchestrator_model)
        else "preset",
    )

    providers = settings.configured_providers()
    if not providers or chosen.source == "override":
        # Only slugs WE chose are second-guessed. A tier the user typed stands even when it looks
        # unreachable: they may be pointing at a proxy, a local gateway, or a provider this does not
        # enumerate, and overriding an explicit choice because of an inference about their keys is
        # the same silent rerouting this function exists to stop — just aimed at a different target.
        return chosen
    if any(_reachable(slug, providers) for slug in chosen.ladder()):
        # At least one rung is callable: leave the ladder alone. A partially reachable ladder still
        # escalates through something real, and silently rewriting the reachable rungs would hide a
        # misconfiguration the user is better off seeing in the receipt.
        return chosen

    fallback = settings.default_model
    if not fallback or not _reachable(fallback, providers):
        # Even the default is unreachable. Nothing here can improve that, and inventing a slug would
        # be a guess about which of their keys to spend.
        return chosen
    return TierLadder(
        weak=fallback, mid=fallback, top=fallback, entry=preset.entry,
        source="fallback_single_model",
    )


def register_catalog_prices() -> None:
    """Feed known catalog prices into the fusion receipts price table.

    Makes free tiers price as measured-zero (instead of unknown/None) and adds
    tier models the base table lacks. Idempotent enough: set_price prepends, and
    lookups take the first (most recent) match.
    """
    from chimera.fusion.receipts import ModelPrice, set_price

    for entry in CATALOG:
        if entry.input_per_m is not None and entry.output_per_m is not None:
            # Register the slug tail (after the provider prefix) so substring
            # matching hits regardless of the openrouter/ prefix.
            pattern = entry.slug.split("/", 1)[-1]
            set_price(pattern, ModelPrice(entry.input_per_m, entry.output_per_m))
