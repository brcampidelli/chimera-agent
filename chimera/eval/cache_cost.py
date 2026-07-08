"""Caching-aware dollar model for the hierarchy sweep (M16 companion).

Every hierarchy RESULTS file carries the same caveat: token counts are real, but a
provider with prompt caching bills the single agent's repeated document prefix at ~0.1x,
so the *dollar* gap is narrower than the *token* gap. This module turns that caveat into
an explicit, auditable number — and surfaces a finding worth stating out loud.

This is a MODEL, not a measurement. Its assumptions (stated so they can be argued with):

- Docs dominate the cost; we count in units of "one document".
- **Single-context (baseline):** the D documents form a stable prefix re-sent on every
  one of the Q turns. With caching they are paid in full ONCE (write) and at the read
  multiplier on the other Q-1 turns.
- **Scoped (hierarchy):** each of the Q worker steps loads its one document in a fresh,
  cache-cold worker context (independent calls). Conservative for the hierarchy — it
  forfeits the cache the single agent enjoys.

The naive token model reproduces the measured sweep exactly (token saving = 1 - 1/D at
Q=D), which is why the same function reports both.

Honest headline it produces: **prompt caching does not just narrow the win — with many
turns it can ERASE or invert it**, because the single agent caches its repeated context
while independent workers re-pay theirs cold. The token win is real; whether it is a
*dollar* win depends entirely on the provider's caching and the conversation length.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CacheModel:
    """Prompt-caching price multipliers (relative to a normal input token = 1.0)."""

    read_mult: float = 0.1
    """Cached-prefix reads (Anthropic bills cache hits at ~0.1x input)."""
    write_mult: float = 1.0
    """First occurrence. Use 1.25 for Anthropic's 5-min-TTL cache-write premium; 1.0
    is the conservative no-premium default."""


@dataclass(frozen=True)
class CostComparison:
    d: int
    q: int
    token_reduction: float
    """Naive token saving — what the sweep measures (= 1 - 1/D at Q=D)."""
    dollar_reduction: float
    """Saving once the single agent's repeated docs are billed at the cache read rate.
    Can be < token_reduction, and can go NEGATIVE (hierarchy costs more) at high Q."""


def compare_under_caching(
    d: int, q: int | None = None, *, model: CacheModel | None = None
) -> CostComparison:
    """Naive token saving vs cache-aware dollar saving for a D-doc, Q-turn task."""
    if d < 1:
        raise ValueError("d must be >= 1")
    q = q if q is not None else d
    if q < 1:
        raise ValueError("q must be >= 1")
    model = model or CacheModel()

    # Naive (no caching): both arms measured in "document loads".
    baseline_naive = q * d          # all D docs, every one of Q turns
    scoped_naive = q                # one doc per step
    token_reduction = 1.0 - scoped_naive / baseline_naive

    # Cache-aware dollars: baseline's stable doc prefix caches; scoped is cold per worker.
    baseline_dollar = d * (model.write_mult + model.read_mult * (q - 1))
    scoped_dollar = q * model.write_mult
    dollar_reduction = 1.0 - scoped_dollar / baseline_dollar

    return CostComparison(
        d=d, q=q,
        token_reduction=round(token_reduction, 4),
        dollar_reduction=round(dollar_reduction, 4),
    )


def dollar_cost(
    *,
    regular_input: int,
    output: int,
    input_per_m: float,
    output_per_m: float,
    cache_read: int = 0,
    cache_write: int = 0,
    model: CacheModel | None = None,
) -> float:
    """MEASURED dollar cost from a real usage breakdown (cache tokens billed at their
    multipliers). This is the bridge that turns the analytic model into a measurement
    when the provider actually reports cache accounting (see gateway ``cache_*_tokens``)."""
    model = model or CacheModel()
    input_cost = (
        regular_input
        + cache_read * model.read_mult
        + cache_write * model.write_mult
    ) / 1_000_000 * input_per_m
    return round(input_cost + output / 1_000_000 * output_per_m, 6)


def measured_dollar_reduction(baseline_cost: float, scoped_cost: float) -> float:
    """Real dollar saving from two measured :func:`dollar_cost` values (1 - scoped/base)."""
    if baseline_cost <= 0:
        return 0.0
    return round(1.0 - scoped_cost / baseline_cost, 4)


def format_table(comparisons: list[CostComparison], *, model: CacheModel) -> str:
    """Render the token-vs-dollar comparison as a compact table."""
    lines = [
        f"caching model: read={model.read_mult}x  write={model.write_mult}x  (a MODEL, not measured)",
        f"{'D':>3} {'Q':>3}  {'token cut':>9}  {'$ cut (cached)':>14}",
    ]
    for c in comparisons:
        lines.append(f"{c.d:>3} {c.q:>3}  {c.token_reduction:>+8.1%}  {c.dollar_reduction:>+13.1%}")
    lines.append(
        "note: $ cut < token cut because the single agent caches its repeated docs; "
        "at high Q it can go negative (workers re-pay cold context the single agent caches)."
    )
    return "\n".join(lines)
