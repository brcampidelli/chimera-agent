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


# Curated multi-vendor suggestions per tier (mid-2026). DATA ONLY — extend/correct
# freely; `chimera models` renders this and `resolve_tiers` picks defaults from it.
CATALOG: tuple[CatalogEntry, ...] = (
    # --- weak: free/near-free probes. Cheap first drafts, k-sample agreement. ---
    CatalogEntry(
        "openrouter/qwen/qwen3-next-80b-a3b-instruct:free", "weak", "Qwen (Alibaba)",
        0.0, 0.0, tools=False, context_k=256,
        notes="free tier; rate-limited; unreliable tool calling",
    ),
    CatalogEntry(
        "openrouter/meta-llama/llama-3.3-70b-instruct:free", "weak", "Meta",
        0.0, 0.0, tools=False, context_k=128,
        notes="free tier; rate-limited",
    ),
    CatalogEntry(
        "openrouter/mistralai/mistral-small-3.2-24b-instruct", "weak", "Mistral",
        0.10, 0.30, tools=True, context_k=128,
        notes="the local-lift goldilocks model; cheap paid weak with usable tools",
    ),
    # --- mid: the daily workhorses. Reliable tools, cents per task. ---
    CatalogEntry(
        "openrouter/deepseek/deepseek-chat-v3.1", "mid", "DeepSeek",
        0.14, 0.28, tools=True, context_k=128,
        notes="proven in this repo's benches; excellent cost/quality",
    ),
    CatalogEntry(
        "openrouter/z-ai/glm-4.6", "mid", "Zhipu (GLM)",
        None, None, tools=True, context_k=200,
        notes="strong agentic mid; check current OpenRouter price",
    ),
    CatalogEntry(
        "openrouter/google/gemini-2.5-flash", "mid", "Google",
        None, None, tools=True, context_k=1000,
        notes="huge context; check current price",
    ),
    CatalogEntry(
        "openrouter/openai/gpt-5.5-mini", "mid", "OpenAI",
        None, None, tools=True, context_k=400,
        notes="check current price",
    ),
    CatalogEntry(
        "openrouter/qwen/qwen3-coder", "mid", "Qwen (Alibaba)",
        0.20, 0.80, tools=True, context_k=256,
        notes="code-leaning mid",
    ),
    # --- top: orchestrator/judge class. Decompose, adjudicate, synthesize. ---
    CatalogEntry(
        "openrouter/deepseek/deepseek-r1", "top", "DeepSeek",
        0.55, 2.19, tools=False, context_k=128,
        notes="economic reasoner; the default economic orchestrator",
    ),
    CatalogEntry(
        "openrouter/moonshotai/kimi-k2", "top", "Moonshot (Kimi)",
        None, None, tools=True, context_k=256,
        notes="strong agentic frontier-class; check current price",
    ),
    CatalogEntry(
        "openrouter/openai/gpt-5.5", "top", "OpenAI",
        None, None, tools=True, context_k=400,
        notes="frontier; this repo's historical default_model",
    ),
    CatalogEntry(
        "openrouter/google/gemini-3.1-pro", "top", "Google",
        None, None, tools=True, context_k=1000,
        notes="frontier; huge context",
    ),
    CatalogEntry(
        "openrouter/anthropic/claude-opus-4-8", "top", "Anthropic",
        None, None, tools=True, context_k=200,
        notes="frontier; this repo's fusion-judge default",
    ),
    CatalogEntry(
        "openrouter/qwen/qwen-max", "top", "Qwen (Alibaba)",
        None, None, tools=True, context_k=256,
        notes="check current price",
    ),
)


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

    def ladder(self) -> list[str]:
        return [self.weak, self.mid, self.top]

    def model_for(self, tier: Tier) -> str:
        return {"weak": self.weak, "mid": self.mid, "top": self.top}[tier]


# Preset ladders per cost mode. `auto` deliberately ENTERS AT MID (the user's
# "automático prioriza o médio"): the weak tier is skipped as an entry point but
# stays available for k-sample probes; escalation still climbs to top/fusion.
_PRESETS: dict[CostMode, TierLadder] = {
    "cheap": TierLadder(
        weak="openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
        mid="openrouter/deepseek/deepseek-chat-v3.1",
        top="openrouter/deepseek/deepseek-chat-v3.1",  # never pay reasoner rates
        entry="weak",
    ),
    "balanced": TierLadder(
        weak="openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
        mid="openrouter/deepseek/deepseek-chat-v3.1",
        top="openrouter/deepseek/deepseek-r1",
        entry="weak",
    ),
    "auto": TierLadder(
        weak="openrouter/qwen/qwen3-next-80b-a3b-instruct:free",
        mid="openrouter/deepseek/deepseek-chat-v3.1",
        top="openrouter/deepseek/deepseek-r1",
        entry="mid",
    ),
    "premium": TierLadder(
        weak="openrouter/deepseek/deepseek-chat-v3.1",
        mid="openrouter/openai/gpt-5.5",
        top="openrouter/anthropic/claude-opus-4-8",
        entry="mid",
    ),
}


class _TierSettings(Protocol):
    """The slice of Settings this resolver needs (duck-typed to avoid an import cycle)."""

    weak_model: str
    mid_model: str
    orchestrator_model: str
    cost_mode: str


def resolve_tiers(settings: _TierSettings) -> TierLadder:
    """Explicit override > cost mode preset > balanced default.

    An empty string in a tier field means "let the cost mode decide"; any
    non-empty value is the user's explicit choice and always wins.
    """
    mode = settings.cost_mode if settings.cost_mode in _PRESETS else "auto"
    preset = _PRESETS[mode]  # type: ignore[index]
    return TierLadder(
        weak=settings.weak_model or preset.weak,
        mid=settings.mid_model or preset.mid,
        top=settings.orchestrator_model or preset.top,
        entry=preset.entry,
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
