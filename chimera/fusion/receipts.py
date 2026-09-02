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
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chimera.fusion.engine import FusionTrace, StageUsage
from chimera.telemetry import get_logger

_log = get_logger("fusion.receipts")


@dataclass(frozen=True)
class ModelPrice:
    """Approximate public list price, US dollars per 1,000,000 tokens."""

    input_per_m: float
    output_per_m: float


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
    ("claude-sonnet", ModelPrice(3.0, 15.0)),
    ("claude-haiku", ModelPrice(0.80, 4.0)),
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


def set_price(pattern: str, price: ModelPrice) -> None:
    """Register/override a price for models whose id contains ``pattern`` (checked first)."""
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


def resolve_price(model: str) -> ModelPrice | None:
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
    4. **The table by family substring** — the original behaviour, unchanged, and still the answer
       for every model the index has never seen (a local gateway, a vendor not on OpenRouter). The
       shipped catalogue is folded in here, once, on the first lookup.
    5. Local models, which bill nothing.

    Why the index sits BETWEEN the two table lookups rather than after both: those patterns are
    substrings, and a substring is a guess about a family. ``deepseek-chat`` matched
    ``deepseek-chat-v3.1`` and priced it at the v3 rate — off by 3x on output — for as long as both
    existed. The provider's number for the exact slug is better evidence than our guess about its
    family, and worse evidence than a price a human typed for that one model.
    """
    from chimera.providers.gateway import _is_local_model
    from chimera.providers.listing import known_price

    _ensure_catalog_registered()
    norm = model.lower()
    for pattern, price in _PRICES:
        if pattern == norm:
            return price

    # Before every substring pass, including the catalogue's: a `:free` slug is the paid model's id
    # with a suffix, so ANY family pattern for that model also matches it. An explicit exact-match
    # `set_price` for the free slug still wins, above.
    if ":free" in norm:
        return _FREE_TIER

    live = known_price(model)
    if live is not None:
        return ModelPrice(live[0], live[1])

    for pattern, price in _PRICES:
        if pattern in norm:
            return price
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


def price_stage(usage: StageUsage) -> StageCost:
    """Turn a :class:`StageUsage` into a :class:`StageCost` at the stage model's own rate."""
    price = resolve_price(usage.model)
    usd: float | None = None
    if price is not None:
        priced = price.input_per_m or price.output_per_m  # 0 for a genuinely-free model
        if priced and usage.prompt_tokens is None and usage.completion_tokens is None:
            # Priced model but the provider reported NO usage: the cost is UNKNOWN, not free.
            # Same honesty rule as an unknown model — a missing number never masquerades as $0.
            usd = None
        else:
            pt, ct = usage.prompt_tokens or 0, usage.completion_tokens or 0
            usd = round(pt / 1_000_000 * price.input_per_m + ct / 1_000_000 * price.output_per_m, 6)
    return StageCost(usage.stage, usage.model, usage.prompt_tokens, usage.completion_tokens, usd)


@dataclass
class FusionReceipt:
    """The itemized cost of one fusion run — the 'receipt' behind selective fusion."""

    stages: list[StageCost] = field(default_factory=list)
    early_stopped: bool = False
    passed: bool | None = None  # optional quality signal (did the fused answer succeed?)

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
            "stages": [asdict(s) for s in self.stages],
            "early_stopped": self.early_stopped,
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
