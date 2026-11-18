"""Fusion receipts (M15-B3) — per-advisor cost attribution + persisted traces.

Hermes' MoA prices every panel member at its *own* model rate and keeps the full advisor trace;
this is the Chimera version, and the concrete substance behind the positioning "selective fusion
with receipts". Every fusion run can be turned into a :class:`FusionReceipt`: what each advisor,
the judge, and the synthesizer cost — priced at each model's own rate — plus whether selective mode
short-circuited the panel. Persist the receipts as JSONL and you can publish an honest cost×quality
curve instead of asserting fusion is worth it.

Honesty rules, on purpose:
- Tokens are **measured** (reported by the provider); dollars are **estimated** at public list price.
- An unknown model prices to ``None`` — the cost is *unknown*, never fabricated. A receipt with any
  unknown stage reports ``usd=None`` for the total, so a missing price can't masquerade as "free".
- The price table is approximate and overridable (:func:`set_price`) — it is an estimator, not a bill.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from chimera.build_info import CHIMERA_VERSION, chimera_git_sha
from chimera.fusion.engine import FusionTrace, StageUsage
from chimera.telemetry import get_logger

_log = get_logger("fusion.receipts")

_VERSION_DOT = re.compile(r"(?<=\d)\.(?=\d)")


@dataclass(frozen=True)
class ModelPrice:
    """Approximate public list price, US dollars per 1,000,000 tokens."""

    input_per_m: float
    output_per_m: float
    cache_read_per_m: float | None = None
    """What a prompt-cache HIT costs. None = nobody told us, and then the read is priced at
    ``input_per_m`` — the figure every receipt used before, never a multiplier made up here."""
    cache_write_per_m: float | None = None
    """What a prompt-cache WRITE costs (Anthropic: 1.25x input for the five-minute kind). None =
    unknown, priced at ``input_per_m``, which is a floor: no provider bills a write below input."""
    long_prompt_over: int | None = None
    """Prompt length, in tokens, ABOVE which ``long_prompt`` applies. None = one price at any length."""
    long_prompt: ModelPrice | None = None
    """The price of a request whose prompt is over ``long_prompt_over`` tokens. Claude Haiku 5.5 bills
    a long prompt at five times the short one, cache reads and writes included in the length, and a
    single flat row under-states every such call by 80%."""
    by_identity: bool = field(default=False, compare=False, repr=False)
    """Whether the ROW this price sits in names a Claude MODEL (the catalogue's own rows) and is matched
    on the model's identity, or names a ROUTE and is matched against the whole slug. It is a property of
    the row's price and not of the pattern text, so a `set_price` that happens to reuse a catalogue
    pattern is still a route-level override. Left out of equality: it says how a row is MATCHED, not
    what it costs."""
    reserve: bool = field(default=False, compare=False, repr=False)
    """Whether this price is a CATALOGUE fallback row (set by `register_catalog_prices`) rather than
    something a person typed with `set_price`. Only reserve rows compete on specificity in
    `resolve_price`; an explicit override wins by position, whatever slug it names."""


def tier_is_estimate(price: ModelPrice, prompt_tokens: int | None, *, aggregate: bool) -> bool:
    """Whether a figure priced from an AGGREGATE rests on the long tier's upper bound.

    True only for ``aggregate`` counts whose total is over the long-prompt threshold: there the true
    cost lies between the two tiers and the long one was charged. Under the threshold, or for one
    call, the figure is exact and this is False.
    """
    return (
        aggregate
        and price.long_prompt is not None
        and price.long_prompt_over is not None
        and (prompt_tokens or 0) > price.long_prompt_over
    )


def cost_usd(
    price: ModelPrice,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    *,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    calls: int | None = None,
    aggregate: bool = False,
) -> float:
    """Dollars for one call, with the prompt's cache share at its own rates.

    ``calls`` is how many API calls the token counts add up, when they are a SUM (a cascade's k
    samples of one prompt): a long-prompt tier is decided per request, so it is read off the prompt
    of ONE call (the sum divided by ``calls``) and not off the total, which would bill k short
    prompts at the long rate. None or 1 = the counts are one call's, as always.

    ``aggregate=True`` says the counts are the SUM of several calls of unknown, unequal sizes (a
    delegation's whole budget): no single prompt length exists. The tier is then read off the TOTAL,
    which is exact when the total is under the threshold (every call was shorter still) and an UPPER
    BOUND when it is over (the real figure is somewhere between the short and the long price). For a
    spend ceiling that is the safe side to err on: reading the short tier instead under-states the
    spend, and a ceiling that thinks it has money left is the failure it exists to prevent. Callers
    that can, mark such a figure as an estimate (:func:`tier_is_estimate`); callers that hold the
    exact per-call cost (an agent run's tally, ``AgentResult.usd``) must use that and never price
    the aggregate again.
    """
    prompt = max(0, prompt_tokens or 0)
    per_call = prompt / calls if calls and calls > 1 else prompt
    if (
        price.long_prompt is not None
        and price.long_prompt_over is not None
        and per_call > price.long_prompt_over
    ):
        price = price.long_prompt
    read = min(max(0, cache_read_tokens or 0), prompt)
    write = min(max(0, cache_write_tokens or 0), prompt - read)
    fresh = prompt - read - write
    read_rate = price.input_per_m if price.cache_read_per_m is None else price.cache_read_per_m
    write_rate = price.input_per_m if price.cache_write_per_m is None else price.cache_write_per_m
    usd = (
        fresh * price.input_per_m
        + read * read_rate
        + write * write_rate
        + max(0, completion_tokens or 0) * price.output_per_m
    ) / 1_000_000
    return round(usd, 6)


#: A free-tier slug bills nothing, and that is a measurement rather than a default.
#:
#: It used to be the first entry of the table below, which made the property "free beats its own
#: paid family" depend on nothing ever being prepended in front of it — and `set_price` prepends.
#: Folding the shipped catalogue in was enough to break it: `llama-3.3-70b-instruct:free` started
#: pricing at the paid family rate, because the catalogue's `llama-3.3-70b-instruct` is a substring
#: of it and now sat higher in the list. The rule was one `set_price` call away from breaking for
#: anybody, so it stops being a position in a list and becomes a check of its own.
_FREE_TIER = ModelPrice(0.0, 0.0)

# Approximate public list prices (USD / 1M tokens) as of mid-2026, for cost *estimation* only.
# Matched by substring against a normalized model id, longest/most-specific pattern first. Override
# or extend with set_price(); an unmatched model yields an unknown (None) cost rather than a guess.
_PRICES: list[tuple[str, ModelPrice]] = [
    ("deepseek-r1", ModelPrice(0.55, 2.19)),
    ("deepseek-reasoner", ModelPrice(0.55, 2.19)),
    # Anthropic publishes cache reads at 0.1x input and five-minute writes at 1.25x; the breakpoints
    # `providers.prompt_cache` sets are the default five-minute kind.
    # The 5.x generation, ABOVE the family rows below, because those are substrings of these ids:
    # `claude-haiku` priced `claude-haiku-5-5` at the Haiku 3.5 rate (8x its input, 8x its output) and
    # `claude-fable-5-2` had no row at all, which a spend cap reads as "price unknown, refuse".
    # Both spellings of each: Anthropic's dashed id and OpenRouter's dotted one. Read 2026-11-14 from
    # Anthropic's pricing page and OpenRouter's /api/v1/models; cache write is the five-minute kind.
    *[
        (f"claude-fable-5{sep}2", ModelPrice(8.0, 40.0, cache_read_per_m=0.20, cache_write_per_m=10.0))
        for sep in ("-", ".")
    ],
    *[
        (f"claude-opus-5{sep}5", ModelPrice(4.0, 20.0, cache_read_per_m=0.20, cache_write_per_m=5.0))
        for sep in ("-", ".")
    ],
    *[
        (f"claude-sonnet-5{sep}5", ModelPrice(2.0, 10.0, cache_read_per_m=0.10, cache_write_per_m=2.5))
        for sep in ("-", ".")
    ],
    *[
        (
            f"claude-haiku-5{sep}5",
            ModelPrice(
                0.10, 0.50, cache_read_per_m=0.01, cache_write_per_m=0.125,
                long_prompt_over=100_000,
                long_prompt=ModelPrice(0.50, 2.50, cache_read_per_m=0.05, cache_write_per_m=0.625),
            ),
        )
        for sep in ("-", ".")
    ],
    # OpenRouter's moving aliases, priced as the model each is today (same figures in the index,
    # 2026-11-14), so an alias neither falls through to a family row nor loses the Haiku long tier.
    ("claude-fable-latest", ModelPrice(8.0, 40.0, cache_read_per_m=0.20, cache_write_per_m=10.0)),
    ("claude-opus-latest", ModelPrice(4.0, 20.0, cache_read_per_m=0.20, cache_write_per_m=5.0)),
    ("claude-sonnet-latest", ModelPrice(2.0, 10.0, cache_read_per_m=0.10, cache_write_per_m=2.5)),
    (
        "claude-haiku-latest",
        ModelPrice(
            0.10, 0.50, cache_read_per_m=0.01, cache_write_per_m=0.125,
            long_prompt_over=100_000,
            long_prompt=ModelPrice(0.50, 2.50, cache_read_per_m=0.05, cache_write_per_m=0.625),
        ),
    ),
    # Haiku 4.5: $1 / $5, cache read 0.10, 5m write 1.25 (Anthropic's pricing page and OpenRouter's
    # index agree, 2026-11-14). The `claude-haiku` row below is the 3.5 price and used to price it.
    *[
        (f"claude-haiku-4{sep}5", ModelPrice(1.0, 5.0, cache_read_per_m=0.10, cache_write_per_m=1.25))
        for sep in ("-", ".")
    ],
    ("claude-sonnet", ModelPrice(3.0, 15.0, cache_read_per_m=0.30, cache_write_per_m=3.75)),
    ("claude-haiku", ModelPrice(0.80, 4.0, cache_read_per_m=0.08, cache_write_per_m=1.0)),
    ("gpt-4o-mini", ModelPrice(0.15, 0.60)),
    ("gpt-4o", ModelPrice(2.50, 10.0)),
    ("gemini-flash", ModelPrice(0.075, 0.30)),
    ("gemini-2.0-flash", ModelPrice(0.075, 0.30)),
    ("deepseek-chat", ModelPrice(0.14, 0.28)),
    ("deepseek-v3", ModelPrice(0.14, 0.28)),
    ("llama-3.1-8b", ModelPrice(0.05, 0.05)),
    ("llama-3.3-70b", ModelPrice(0.12, 0.30)),
    ("llama-3.1-70b", ModelPrice(0.12, 0.30)),
    ("qwen3-coder", ModelPrice(0.20, 0.80)),
    ("qwen-2.5", ModelPrice(0.20, 0.60)),
    ("ministral", ModelPrice(0.10, 0.10)),
    ("mistral", ModelPrice(0.10, 0.30)),
]


def _long_tier_without_publication(
    family: ModelPrice | None, live: tuple[float, float], read: float | None, write: float | None
) -> tuple[int | None, ModelPrice | None]:
    """The long-prompt tier of a live-priced slug whose index row publishes none.

    An index file written before ``pricing.overrides`` were parsed has no tier for ANY slug, and a
    variant (`claude-haiku-5.5:batch`, priced at half) must not fall to its short price above the
    threshold: that is a fifth of the real cost, and it lets a spend ceiling through. When the table
    knows a long tier for this family it is used, scaled by how far the variant's short price is
    from the family's (0.05 against 0.10 is half, so 0.25/1.25 against the table's 0.50/2.50), and
    never below the variant's own short price. A family with no tier has none to borrow.
    """
    if family is None or family.long_prompt is None or family.long_prompt_over is None:
        return None, None
    base = family.long_prompt
    ratio = live[0] / family.input_per_m if family.input_per_m > 0 else 1.0
    if ratio <= 0:
        ratio = 1.0
    long_in = max(base.input_per_m * ratio, live[0])
    long_out = max(base.output_per_m * ratio, live[1])
    scaled_read = None if base.cache_read_per_m is None else base.cache_read_per_m * ratio
    scaled_write = None if base.cache_write_per_m is None else base.cache_write_per_m * ratio
    return family.long_prompt_over, ModelPrice(
        long_in, long_out,
        cache_read_per_m=scaled_read if scaled_read is not None else read,
        cache_write_per_m=scaled_write if scaled_write is not None else write,
    )


def _pattern_in(pattern: str, norm: str, identity: str, price: ModelPrice) -> bool:
    """Whether a table ``pattern`` names this model.

    Most rows are a plain substring of the whole slug, as they always were (a person's `set_price`
    names a route and has to keep meaning that route).

    A catalogue Claude row (``price.by_identity``) is matched against the model's IDENTITY (:func:`chimera.providers.catalog.model_identity`:
    last path segment, vendor prefix, ``@snapshot`` and ``:variant`` removed, versions dashed), the
    same reading the capability table uses, so the two cannot disagree about which model a slug is. A
    row that ends in a version digit (``claude-opus-5``) must equal the identity, or the identity plus
    a dated snapshot: it is not a prefix of the NEXT release (``claude-opus-5-5``) nor of a longer
    name (``claude-fable-5-2-distill-x``). A family row (``claude-haiku``) is a prefix of the identity.
    """
    if not price.by_identity:
        return pattern in norm
    key = _VERSION_DOT.sub("-", pattern.rsplit("/", 1)[-1])
    if key[-1].isdigit():
        return re.fullmatch(rf"{re.escape(key)}(?:-\d{{8}})?", identity) is not None
    return identity.startswith(key)


# The rows this module ships for Claude models are matched on identity (`_pattern_in`). Marked on the
# row's price, once, here: everything registered later through `set_price` is a route-level override
# unless it says otherwise, whatever its pattern text.
_PRICES[:] = [
    (pattern, replace(price, by_identity=True) if "claude-" in pattern else price)
    for pattern, price in _PRICES
]


def set_price(pattern: str, price: ModelPrice) -> None:
    """Register/override a price for models whose id contains ``pattern`` (checked first).

    Matched against the whole slug, unless ``price.by_identity`` is set (only the catalogue's Claude
    rows do): a hand-typed override names a route, even when its text equals a catalogue pattern.

    Folds the catalogue in first, so an override typed BEFORE the first lookup is still in front of
    it: `register_catalog_prices` prepends too, and would otherwise sit on top of a person's number.
    """
    if not _catalog_registered:
        _ensure_catalog_registered()
    _PRICES.insert(0, (pattern.lower(), price))


#: Whether the shipped catalogue's own prices have been folded into the table above.
_catalog_registered = False


def _ensure_catalog_registered() -> None:
    """Fold the shipped catalogue's prices into the table, once, before the first lookup.

    `register_catalog_prices()` was written for exactly this and nothing called it, so nine of the
    fifteen models the app ships with resolved to *price unknown* — `claude-opus-5` at $5/$25 per
    1M among them. The six that did resolve included the default, `deepseek-chat-v3.1`, which is
    why it went unnoticed: the meter read correctly until somebody chose an expensive model, and
    then went quiet at precisely the moment the number mattered.

    **Called from the read, not from a startup hook.** A hook is a second code path that every new
    entrypoint has to remember, and the CLI, the API, the sidecar and every bench are separate
    entrypoints. `listing.remember_models` already states this rule about itself — refreshing as a
    side effect of the picker being used, so "there is no second code path that has to remember to
    run" — and this is the same rule applied to the same table from the other side.

    After the exact-match pass would be wrong: `register_catalog_prices` prepends, so a price a
    human typed with `set_price` must be registered *after* the catalogue to still win. Doing this
    first preserves that order.
    """
    global _catalog_registered
    if _catalog_registered:
        return
    # Set before the call, not after: `register_catalog_prices` calls `set_price`, and a future
    # version of it that priced by consulting `resolve_price` would recurse forever otherwise.
    _catalog_registered = True
    from chimera.providers.catalog import register_catalog_prices

    register_catalog_prices()


#: Priced at zero, and that is a measurement rather than a default. A model on `ollama/`, `vllm/` or
#: an LM Studio endpoint bills nothing to any provider — the cost is electricity, which is not what
#: a dollar figure in a receipt is about. Treating it as *unknown* would be the more cautious-looking
#: answer and the wrong one: it makes every local run unpriceable, and a spend cap that stops on
#: unknown prices would refuse to run the one configuration that cannot possibly overspend.
_LOCAL_ZERO = ModelPrice(0.0, 0.0)


_admission_prices: ContextVar[Mapping[str, ModelPrice] | None] = ContextVar(
    "admission_prices", default=None,
)


@contextmanager
def using_admission_prices(prices: Mapping[str, ModelPrice] | None) -> Iterator[None]:
    if prices is None:
        yield
        return
    token = _admission_prices.set(prices)
    try:
        yield
    finally:
        _admission_prices.reset(token)


def admission_prices_active() -> bool:
    return _admission_prices.get() is not None


def resolve_price(model: str, *, require_fresh_openrouter: bool = False) -> ModelPrice | None:
    """The list price for ``model``, or ``None`` if unknown (never guessed).

    Five sources, most specific first, and the ORDER is the whole design:

    1. **An exact match in the table.** ``set_price("openrouter/x/y", …)`` names one model, and
       somebody who named a model meant that model.
    2. **A `:free` slug**, which bills nothing. Above every substring pass because a free slug is a
       paid model's id with a suffix, so any family pattern for that model matches it too. This used
       to be the first row of the table and therefore depended on nothing ever being prepended in
       front of it — a property one `set_price` call away from breaking, and folding the shipped
       catalogue in is what broke it.
    3. **The provider's own published price**, for this exact slug, from the index the model picker
       fetches (see :func:`chimera.providers.listing.known_price`). This is why the default model
       stopped reporting "price unknown": the table below is hand-maintained and knew about twenty
       families, while the index knows four hundred models and is refreshed by using the app.
       Its cache rates win too; where it has none, the table's are used only from a row at the
       same input/output price (an index file written before cache rates were parsed has none).
    4. **The table by family substring** — the original behaviour, unchanged, and still the answer
       for every model the index has never seen (a local gateway, a vendor not on OpenRouter). The
       shipped catalogue is folded in here, once, on the first lookup.
    5. Local models, which bill nothing.

    With ``require_fresh_openrouter``, paid OpenRouter routes need a current published row to
    admit a request under a strict ceiling. Exact overrides and free routes are known up front.

    Why the index sits BETWEEN the two table lookups rather than after both: those patterns are
    substrings, and a substring is a guess about a family. ``deepseek-chat`` matched
    ``deepseek-chat-v3.1`` and priced it at the v3 rate — off by 3x on output — for as long as both
    existed. The provider's number for the exact slug is better evidence than our guess about its
    family, and worse evidence than a price a human typed for that one model.
    """
    from chimera.providers.gateway import _is_local_model
    from chimera.providers.listing import fresh_openrouter_price_record, known_model

    _ensure_catalog_registered()
    # Reasoning effort is a request option, not part of the provider's model identity. Receipts
    # retain the requested suffix, but catalogue and hand-maintained prices are keyed by the slug.
    from chimera.providers.catalog import base_slug

    price_model = base_slug(model)
    norm = price_model.lower()
    if not require_fresh_openrouter and (quotes := _admission_prices.get()) is not None:
        return quotes.get(norm)
    for pattern, price in _PRICES:
        if pattern == norm:
            return price

    # Before every substring pass, including the catalogue's: a `:free` slug is the paid model's id
    # with a suffix, so ANY family pattern for that model also matches it. An explicit exact-match
    # `set_price` for the free slug still wins, above.
    if ":free" in norm:
        return _FREE_TIER

    from chimera.providers.catalog import model_identity

    identity = model_identity(price_model)
    family = next(
        (price for pattern, price in _PRICES if _pattern_in(pattern, norm, identity, price)), None
    )
    # Among CATALOGUE rows only: one whose pattern IS the slug without its provider prefix names this
    # very model and beats a sibling that is merely a substring of it. `z-ai/glm-5.3` is inside
    # `z-ai/glm-5.3-flash`, and the later-registered flagship row used to price the flash model at
    # 1.40/4.40 — 39x its cost (planner A/B, 2026-11-16). A `set_price` override is never displaced:
    # if it came first, `family` is not a reserve row and this does not run.
    if family is not None and family.reserve:
        tail = norm.split("/", 1)[-1]
        family = next((p for pat, p in _PRICES if p.reserve and pat == tail), family)
    fresh_required = require_fresh_openrouter and norm.startswith("openrouter/")
    record = fresh_openrouter_price_record(price_model) if fresh_required else known_model(price_model)
    if record is not None and record.input_per_m is not None and record.output_per_m is not None:
        live = (record.input_per_m, record.output_per_m)
        read, write = record.cache_read_per_m, record.cache_write_per_m
        published = record.long_prompt
        # An index row with no cache rate is usually a file written before the parser read them
        # (`model-prices.json` outlives upgrades), not a provider saying "no cache". The table may
        # know the rate — but only borrow it from a row at the SAME input/output price: a family
        # substring at another price point is a guess about another model, and its cache rate
        # would be priced against an input rate it was never published beside.
        if family is not None and (family.input_per_m, family.output_per_m) == live:
            read = family.cache_read_per_m if read is None else read
            write = family.cache_write_per_m if write is None else write
        if published is not None:
            # The index publishes this variant's OWN long-prompt tier (`pricing.overrides`), which is
            # what a `:batch` slug needs: its short price differs from the table's, so the table's
            # tier would not be its tier.
            over, long_in, long_out, long_read, long_write = published
            return ModelPrice(
                live[0], live[1], cache_read_per_m=read, cache_write_per_m=write,
                long_prompt_over=over,
                long_prompt=ModelPrice(
                    long_in, long_out, cache_read_per_m=long_read, cache_write_per_m=long_write
                ),
            )
        borrowed_over, borrowed = _long_tier_without_publication(family, live, read, write)
        return ModelPrice(
            live[0], live[1], cache_read_per_m=read, cache_write_per_m=write,
            long_prompt_over=borrowed_over, long_prompt=borrowed,
        )

    if fresh_required:
        # A catalogue estimate cannot authorize a paid call with no current published price.
        return None
    if family is not None:
        return family
    # After the table, not before: an explicit `set_price` for a local model must still win.
    if _is_local_model(norm):
        return _LOCAL_ZERO
    return None


@dataclass
class StageCost:
    """One fusion stage priced at its own model's rate. ``usd`` is None if the price is unknown."""

    stage: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    usd: float | None
    usd_estimated: bool = False


def price_stage(usage: StageUsage) -> StageCost:
    """Turn a :class:`StageUsage` into a :class:`StageCost` at the stage model's own rate."""
    if usage.cost is not None:
        return StageCost(
            usage.stage, usage.model, usage.prompt_tokens, usage.completion_tokens,
            usage.cost.usd if usage.cost.unpriced is None else None, usage.cost.estimated,
        )
    price = resolve_price(usage.model)
    usd: float | None = None
    if price is not None:
        priced = price.input_per_m or price.output_per_m  # 0 for a genuinely-free model
        if priced and usage.prompt_tokens is None and usage.completion_tokens is None:
            # Priced model but the provider reported NO usage: the cost is UNKNOWN, not free.
            # Same honesty rule as an unknown model — a missing number never masquerades as $0.
            usd = None
        else:
            usd = cost_usd(
                price, usage.prompt_tokens, usage.completion_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
    return StageCost(usage.stage, usage.model, usage.prompt_tokens, usage.completion_tokens, usd)


@dataclass
class FusionReceipt:
    """The itemized cost of one fusion run — the 'receipt' behind selective fusion."""

    chimera_version: str = CHIMERA_VERSION
    chimera_git_sha: str = field(default_factory=chimera_git_sha)
    stages: list[StageCost] = field(default_factory=list)
    early_stopped: bool = False
    passed: bool | None = None  # optional quality signal (did the fused answer succeed?)
    aggregation: str = "synth"
    """How the answer was reached, as on the trace: synth | vote | fallback."""
    fallback_stage: str | None = None
    """On a fallback, the stage that failed (judge / synth): the answer is a panel answer."""

    @property
    def total_usd(self) -> float | None:
        """Sum of stage costs — None if ANY priced stage is unknown (no silent 'free')."""
        if not self.stages:
            return 0.0
        if any(s.usd is None for s in self.stages):
            return None
        return round(sum(s.usd or 0.0 for s in self.stages), 6)

    @property
    def total_tokens(self) -> int:
        return sum((s.prompt_tokens or 0) + (s.completion_tokens or 0) for s in self.stages)

    @property
    def advisor_costs(self) -> dict[str, float | None]:
        """Per-advisor (panel-stage) cost, keyed by model — Hermes' per-reference accounting."""
        return {s.model: s.usd for s in self.stages if s.stage == "panel"}

    def to_json(self) -> dict[str, object]:
        return {
            "chimera_version": self.chimera_version,
            "chimera_git_sha": self.chimera_git_sha,
            "stages": [asdict(s) for s in self.stages],
            "early_stopped": self.early_stopped,
            "aggregation": self.aggregation,
            "fallback_stage": self.fallback_stage,
            "passed": self.passed,
            "total_usd": self.total_usd,
            "total_tokens": self.total_tokens,
        }


def receipt_from_trace(trace: FusionTrace, *, passed: bool | None = None) -> FusionReceipt:
    """Build an itemized receipt from a fusion trace, pricing each stage at its own model rate."""
    return FusionReceipt(
        stages=[price_stage(u) for u in trace.usage],
        early_stopped=trace.early_stopped,
        passed=passed,
        aggregation=trace.aggregation,
        fallback_stage=trace.fallback_stage,
    )


def append_receipt(path: Path, receipt: FusionReceipt) -> None:
    """Append one receipt as a JSON line — the persisted trace for later cost×quality analysis."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt.to_json()) + "\n")


def load_receipts(path: Path) -> list[dict[str, object]]:
    """Load the persisted receipts (raw dicts) from a JSONL file."""
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(receipts: Sequence[dict[str, object]]) -> dict[str, object]:
    """Aggregate receipts into an honest cost×quality summary (the publishable curve).

    Reports the fusion rate (how often the full panel actually ran vs selective short-circuit),
    mean/total cost over the receipts that HAD a known cost, and — when receipts carry a pass/fail
    quality signal — the pass rate and the dollars spent per passing answer.
    """
    n = len(receipts)
    if n == 0:
        return {"n": 0}
    engaged = [r for r in receipts if not r.get("early_stopped")]
    priced = [r for r in receipts if r.get("total_usd") is not None]
    costs = [float(r["total_usd"]) for r in priced]  # type: ignore[arg-type]
    judged = [r for r in receipts if r.get("passed") is not None]
    passes = [r for r in judged if r.get("passed")]
    total_cost = round(sum(costs), 6) if costs else None
    out: dict[str, object] = {
        "n": n,
        "fusion_rate": round(len(engaged) / n, 4),
        "priced_n": len(priced),
        "mean_usd": round(sum(costs) / len(costs), 6) if costs else None,
        "total_usd": total_cost,
    }
    if judged:
        out["pass_rate"] = round(len(passes) / len(judged), 4)
        # Dollars per passing answer — the honest cost-of-quality number, when both are known.
        pass_costs = [float(r["total_usd"]) for r in passes if r.get("total_usd") is not None]  # type: ignore[arg-type]
        if pass_costs:
            out["usd_per_pass"] = round(sum(pass_costs) / len(pass_costs), 6)
    return out


def format_summary(summary: dict[str, object]) -> str:
    """A compact human-readable rendering of :func:`summarize` for the CLI."""
    if not summary.get("n"):
        return "no receipts yet"
    lines = [
        f"receipts: {summary['n']}  (priced: {summary.get('priced_n', 0)})",
        f"fusion engaged: {_pct(summary.get('fusion_rate'))}  "
        f"(selective short-circuited the rest)",
        f"mean cost: {_usd(summary.get('mean_usd'))}   total: {_usd(summary.get('total_usd'))}",
    ]
    if "pass_rate" in summary:
        lines.append(
            f"quality: {_pct(summary.get('pass_rate'))} pass   "
            f"cost/pass: {_usd(summary.get('usd_per_pass'))}"
        )
    return "\n".join(lines)


def _pct(value: object) -> str:
    return f"{float(value):.0%}" if isinstance(value, (int, float)) else "n/a"


def _usd(value: object) -> str:
    return f"${float(value):.4f}" if isinstance(value, (int, float)) else "unknown"


def receipts_from_traces(
    traces: Iterable[FusionTrace], passed: Iterable[bool | None] | None = None
) -> list[FusionReceipt]:
    """Convenience: build receipts for a batch of traces, optionally with per-trace quality."""
    flags = list(passed) if passed is not None else []
    out: list[FusionReceipt] = []
    for i, trace in enumerate(traces):
        out.append(receipt_from_trace(trace, passed=flags[i] if i < len(flags) else None))
    return out
