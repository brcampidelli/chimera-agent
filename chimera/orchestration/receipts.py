"""Delegation receipts (M16-A3): measured cost per delegation + the counterfactual.

The pxpipe salvage, applied to hierarchy instead of pixels: before any delegation,
a deterministic profitability estimate decides whether delegating is even worth it;
after the delegation, the receipt logs the MEASURED tokens/cost **and the
counterfactual estimate in the same row** — so "the hierarchy saved X" is a number
you can audit, never a claim.

Honesty rules (inherited from fusion receipts):
- Tokens are measured when the provider reports usage; a chars/4 fallback is
  always flagged ``tokens_estimated=True`` — estimates never masquerade as
  measurements.
- Dollars price at each model's own list rate via the shared price table;
  unknown model -> ``usd=None`` (never fabricated). Free tiers price to 0.0 via
  the catalog registration, so "free" is measured-zero, not unknown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from chimera.fusion.receipts import resolve_price
from chimera.orchestration.spec import TaskSpec
from chimera.telemetry import get_logger

_log = get_logger("orchestration.receipts")

TierName = Literal["weak", "mid", "top"]

#: Deterministic chars->tokens fallback used when a provider reports no usage.
CHARS_PER_TOKEN = 4


class DelegationReceipt(BaseModel):
    """One delegation, priced — with the counterfactual in the same row."""

    task_id: str
    tier: TierName
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    usd: float | None = None
    tokens_estimated: bool = False
    """True when any token count came from the chars/4 fallback, not the provider."""
    cache_read_tokens: int | None = None
    """Prompt-cache HIT tokens the provider billed cheap (M17). None = unknown/none."""
    cache_write_tokens: int | None = None
    """Prompt-cache WRITE tokens (M17). None = unknown/none."""
    # --- pxpipe salvage: the counterfactual, same row, so savings are measured ---
    counterfactual_tokens: int | None = None
    """Estimated tokens had the orchestrator done this work inline (no delegation)."""
    counterfactual_usd: float | None = None
    profitable_estimate: bool | None = None
    """The profitability gate's pre-decision, kept for audit."""

    @property
    def total_tokens(self) -> int:
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)


def estimate_tokens(text: str) -> int:
    """Deterministic chars/4 token estimate (flag downstream use as estimated)."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def price_delegation(
    model: str, prompt_tokens: int | None, completion_tokens: int | None
) -> float | None:
    """USD at the model's own list rate; None when the price is unknown (never guessed)."""
    price = resolve_price(model)
    if price is None:
        return None
    pt, ct = prompt_tokens or 0, completion_tokens or 0
    return round(pt / 1_000_000 * price.input_per_m + ct / 1_000_000 * price.output_per_m, 6)


@dataclass(frozen=True)
class ProfitEstimate:
    """The pre-delegation gate's arithmetic, kept explicit for audit."""

    delegate_est_tokens: int
    inline_est_tokens: int
    profitable: bool
    margin: float
    """(inline - delegate) / inline: positive = delegation looks cheaper."""


def estimate_profitability(
    spec: TaskSpec,
    *,
    orchestrator_context_chars: int,
    expected_output_chars: int = 4_000,
    overhead_tokens: int = 700,
) -> ProfitEstimate:
    """Deterministic pre-delegation estimate: is delegating cheaper than doing it inline?

    Inline cost model: the orchestrator carries its own (large) context plus the
    task and produces the output itself. Delegate cost model: a worker sees only
    the spec (small) and produces the output, the orchestrator later reads back a
    bounded summary; ``overhead_tokens`` covers the dispatch/synthesis framing.
    Both sides use the same chars/4 estimator — the comparison is apples-to-apples
    even though the absolute numbers are rough. The MEASURED truth lands in the
    receipt afterwards; this gate only stops obviously-unprofitable delegations.
    """
    spec_tokens = estimate_tokens(spec.render())
    output_tokens = max(1, expected_output_chars // CHARS_PER_TOKEN)
    summary_tokens = min(output_tokens, 2_000)

    delegate = spec_tokens + output_tokens + summary_tokens + overhead_tokens
    inline = estimate_tokens(" " * max(1, orchestrator_context_chars)) + output_tokens

    profitable = delegate < inline
    margin = (inline - delegate) / inline if inline > 0 else 0.0
    return ProfitEstimate(
        delegate_est_tokens=delegate,
        inline_est_tokens=inline,
        profitable=profitable,
        margin=round(margin, 4),
    )


def make_receipt(
    spec: TaskSpec,
    *,
    tier: TierName,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    tokens_estimated: bool = False,
    counterfactual_tokens: int | None = None,
    counterfactual_model: str | None = None,
    profitable_estimate: bool | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
) -> DelegationReceipt:
    """Assemble a priced receipt; the counterfactual is priced at the top model's rate."""
    counterfactual_usd: float | None = None
    if counterfactual_tokens is not None and counterfactual_model:
        # Rough split: assume the counterfactual is mostly prompt-side (context-heavy).
        counterfactual_usd = price_delegation(
            counterfactual_model, int(counterfactual_tokens * 0.8), int(counterfactual_tokens * 0.2)
        )
    return DelegationReceipt(
        task_id=spec.task_id,
        tier=tier,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=price_delegation(model, prompt_tokens, completion_tokens),
        tokens_estimated=tokens_estimated,
        counterfactual_tokens=counterfactual_tokens,
        counterfactual_usd=counterfactual_usd,
        profitable_estimate=profitable_estimate,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )


def append_delegation(path: Path, receipt: DelegationReceipt) -> None:
    """Append one delegation receipt as a JSON line (same discipline as fusion receipts)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(receipt.model_dump_json() + "\n")


def load_delegations(path: Path) -> list[DelegationReceipt]:
    """Load persisted delegation receipts; malformed lines are skipped with a log line."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[DelegationReceipt] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(DelegationReceipt.model_validate_json(line))
        except ValueError:  # pragma: no cover - defensive
            _log.warning("skipping malformed delegation receipt line")
    return out


def summarize_delegations(receipts: list[DelegationReceipt]) -> dict[str, object]:
    """Aggregate measured vs counterfactual — the honest 'what the hierarchy saved'.

    Reports measured totals, the counterfactual totals over the SAME receipts that
    have one (never mixing subsets silently), and the net saving. ``estimated_n``
    discloses how many rows relied on the chars/4 fallback.
    """
    n = len(receipts)
    if n == 0:
        return {"n": 0}
    by_tier: dict[str, int] = {}
    for r in receipts:
        by_tier[r.tier] = by_tier.get(r.tier, 0) + 1

    measured_tokens = sum(r.total_tokens for r in receipts)
    priced = [r for r in receipts if r.usd is not None]
    measured_usd = round(sum(r.usd or 0.0 for r in priced), 6) if priced else None

    with_cf = [r for r in receipts if r.counterfactual_tokens is not None]
    cf_tokens = sum(r.counterfactual_tokens or 0 for r in with_cf)
    paired_measured_tokens = sum(r.total_tokens for r in with_cf)
    cf_priced = [r for r in with_cf if r.counterfactual_usd is not None and r.usd is not None]
    cf_usd = round(sum(r.counterfactual_usd or 0.0 for r in cf_priced), 6) if cf_priced else None
    paired_usd = round(sum(r.usd or 0.0 for r in cf_priced), 6) if cf_priced else None

    out: dict[str, object] = {
        "n": n,
        "by_tier": by_tier,
        "measured_tokens": measured_tokens,
        "measured_usd": measured_usd,
        "priced_n": len(priced),
        "estimated_n": sum(1 for r in receipts if r.tokens_estimated),
    }
    if with_cf:
        out["counterfactual_n"] = len(with_cf)
        out["counterfactual_tokens"] = cf_tokens
        out["paired_measured_tokens"] = paired_measured_tokens
        out["token_saving"] = cf_tokens - paired_measured_tokens
        if cf_usd is not None and paired_usd is not None:
            out["counterfactual_usd"] = cf_usd
            out["paired_measured_usd"] = paired_usd
            out["usd_saving"] = round(cf_usd - paired_usd, 6)
    return out


def format_delegation_summary(summary: dict[str, object]) -> str:
    """Compact CLI rendering of :func:`summarize_delegations`."""
    if not summary.get("n"):
        return "no delegation receipts yet"
    lines = [
        f"delegations: {summary['n']}  by tier: "
        + ", ".join(f"{k}={v}" for k, v in sorted(dict(summary.get("by_tier", {})).items())),  # type: ignore[call-overload]
        f"measured: {summary['measured_tokens']} tokens"
        + (f", ${summary['measured_usd']}" if summary.get("measured_usd") is not None else ""),
    ]
    est = summary.get("estimated_n", 0)
    if est:
        lines.append(f"({est} receipt(s) used the chars/4 estimator — flagged, not measured)")
    if "counterfactual_tokens" in summary:
        lines.append(
            f"counterfactual (inline, same rows): {summary['counterfactual_tokens']} tokens"
            + (
                f", ${summary['counterfactual_usd']}"
                if summary.get("counterfactual_usd") is not None
                else ""
            )
        )
        saving_obj = summary.get("token_saving", 0)
        saving = saving_obj if isinstance(saving_obj, int) else 0
        sign = "saved" if saving >= 0 else "OVERSPENT"
        lines.append(
            f"net: {sign} {abs(saving)} tokens"
            + (
                f", ${summary['usd_saving']}"
                if summary.get("usd_saving") is not None
                else ""
            )
        )
    return "\n".join(lines)


def _load_json_lines(path: Path) -> list[dict[str, object]]:  # pragma: no cover - helper
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
