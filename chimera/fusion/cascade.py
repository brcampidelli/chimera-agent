"""FrugalGPT-style cascade (M16-A6): weak -> gate -> mid -> gate -> fusion.

Evidence: cascade routing (generate cheap, score, escalate only when the cheap
answer fails a gate) is the best-documented cost lever in the literature —
FrugalGPT reports 50–98% cost reduction at matched quality. This backend is the
Chimera version, layered over the tier ladder from :mod:`chimera.providers.catalog`:

- Tool-calling turns go straight to the MID tier (free/weak models are unreliable
  tool callers — the same rule :class:`~chimera.fusion.router.RoutedBackend`
  applies to fusion applies here).
- Turns the :class:`~chimera.fusion.router.RoutingPolicy` flags (deep reasoning /
  error-sensitive) skip the weak tier and enter at MID; ``mode="always"`` goes
  straight to fusion.
- Everything else starts WEAK: k samples + majority agreement (self-consistency,
  free difficulty signal), then a deterministic gate; failure climbs to MID, then
  to FUSION.
- Every turn appends a :class:`~chimera.fusion.route_log.RouteRecord` — the
  dataset a learned router (RouteLLM) could be trained on later. Log only, by
  explicit anti-scope decision.

``RoutedBackend`` stays untouched — this is a sibling, not a replacement; ``--fuse``
keeps meaning what it meant.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.fusion.route_log import RouteRecord, append_route, prompt_fingerprint
from chimera.fusion.router import RoutingPolicy, _last_user_text
from chimera.providers.gateway import CompletionResult, MessageLike, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("fusion.cascade")

# A deterministic sanity check on a tier's answer: True = accept, False = escalate.
CheapGate = Callable[[CompletionResult], bool]

_REFUSAL_MARKERS = (
    "i can't", "i cannot", "i'm sorry", "i am unable", "as an ai",
    "não consigo", "não posso", "no puedo", "lo siento",
)


def _sum_usage(results: list[CompletionResult], field_name: str) -> int | None:
    """Sum a token field across sampled results (None if none reported)."""
    values = [getattr(r, field_name) for r in results if getattr(r, field_name) is not None]
    return sum(values) if values else None


def default_gate(result: CompletionResult) -> bool:
    """Free acceptance gate: non-empty and not an refusal/apology opener."""
    text = (result.content or "").strip()
    if not text:
        return False
    head = text.lower()[:160]
    return not any(marker in head for marker in _REFUSAL_MARKERS)


@dataclass
class CascadeConfig:
    """Tier models + weak-stage self-consistency + where decisions get logged."""

    weak: str
    mid: str
    entry: str = "weak"
    """Entry tier ("weak" or "mid") — cost_mode 'auto' enters at mid."""
    agreement_k: int = 2
    agreement_threshold: float = 0.85
    agreement_temperature: float = 0.7
    log_path: Path | None = None
    tags: dict[str, str] = field(default_factory=dict)


class CascadeBackend:
    """A ``SupportsComplete`` that climbs weak -> mid -> fusion, gating each hop.

    ``gateway`` answers single-model calls (any tier, via ``model=``);
    ``fusion`` is the top rung (typically the FusionEngine or a RoutedBackend
    pinned to fusion). Both are duck-typed — tests script them freely.
    """

    def __init__(
        self,
        gateway: SupportsComplete,
        fusion: SupportsComplete,
        config: CascadeConfig,
        *,
        gate: CheapGate | None = None,
        policy: RoutingPolicy | None = None,
    ) -> None:
        self.gateway = gateway
        self.fusion = fusion
        self.config = config
        self.gate = gate or default_gate
        self.policy = policy or RoutingPolicy()

    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        record = self._new_record(messages)
        result = self._route(
            record, messages, model=model, temperature=temperature,
            max_tokens=max_tokens, tools=tools,
        )
        # Single place that attaches the cascade trace, whatever tier accepted.
        cascade_meta: dict[str, Any] = {
            "kind": "cascade",
            "tiers_tried": record.tiers_tried,
            "accepted_tier": record.accepted_tier,
            "models": record.models,
            "tokens_by_tier": record.tokens_by_tier,
            "agreement": record.agreement,
            "fuse_reason": record.fuse_reason,
        }
        # When the cascade escalated to fusion, preserve the inner fusion trace by nesting.
        if result.route_meta and result.route_meta.get("kind") == "fusion":
            cascade_meta["fusion"] = result.route_meta
        result.route_meta = cascade_meta
        return result

    def _route(
        self,
        record: RouteRecord,
        messages: list[MessageLike],
        *,
        model: str | None,
        temperature: float,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
    ) -> CompletionResult:
        # Tool turns: straight to MID single-model (weak/free models are unreliable
        # tool callers; fusion doesn't tool-call at all).
        if tools:
            result = self.gateway.complete(
                messages, model=self.config.mid, temperature=temperature,
                max_tokens=max_tokens, tools=tools,
            )
            self._log_hop(record, "mid", self.config.mid, result)
            self._accept(record, "mid")
            return result

        reason = self.policy.fuse_reason(messages)
        record.fuse_reason = reason

        # mode="always": the caller explicitly wants fusion — don't climb.
        if reason == "mode":
            return self._fuse(record, messages, temperature)

        # Policy-flagged turns (deep reasoning / error-sensitive) skip the weak tier.
        start_at_mid = reason != "none" or self.config.entry == "mid"

        if not start_at_mid:
            weak_result = self._try_weak(record, messages, max_tokens)
            if weak_result is not None:
                return weak_result

        mid_result = self.gateway.complete(
            messages, model=self.config.mid, temperature=temperature, max_tokens=max_tokens
        )
        self._log_hop(record, "mid", self.config.mid, mid_result)
        if self.gate(mid_result):
            self._accept(record, "mid")
            return mid_result

        return self._fuse(record, messages, temperature)

    # ------------------------------------------------------------------ tiers

    def _try_weak(
        self, record: RouteRecord, messages: list[MessageLike], max_tokens: int | None
    ) -> CompletionResult | None:
        """Weak tier with k-sample majority; None = didn't stick, climb to mid."""
        from chimera.fusion.consistency import majority

        k = max(1, self.config.agreement_k)
        samples = [
            self.gateway.complete(
                messages, model=self.config.weak,
                temperature=self.config.agreement_temperature if k > 1 else 0.3,
                max_tokens=max_tokens,
            )
            for _ in range(k)
        ]
        for sample in samples:
            self._log_hop(record, "weak", self.config.weak, sample)
        if k > 1:
            winner = majority(
                [s.content for s in samples], threshold=self.config.agreement_threshold
            )
            record.agreement = 1.0 if winner is not None else 0.0
            if winner is None:
                _log.debug("weak tier disagreed over %d samples; climbing", k)
                return None
            consensus = next((s for s in samples if s.content == winner), samples[0])
            if self.gate(consensus):
                self._accept(record, "weak")
                # Report the usage of ALL k samples, not just the winning one: a caller that meters
                # the returned result (e.g. a BudgetedBackend) must see the real weak-tier spend, not
                # 1/k of it. (The route log already records every hop; this fixes the returned usage.)
                return consensus.model_copy(update={
                    "prompt_tokens": _sum_usage(samples, "prompt_tokens"),
                    "completion_tokens": _sum_usage(samples, "completion_tokens"),
                })
            return None
        if self.gate(samples[0]):
            self._accept(record, "weak")
            return samples[0]
        return None

    def _fuse(
        self, record: RouteRecord, messages: list[MessageLike], temperature: float
    ) -> CompletionResult:
        result = self.fusion.complete(messages, temperature=temperature)
        self._log_hop(record, "fusion", result.model or "fusion", result)
        self._accept(record, "fusion")
        return result

    # -------------------------------------------------------------- telemetry

    def _new_record(self, messages: list[MessageLike]) -> RouteRecord:
        chars, sha = prompt_fingerprint(_last_user_text(messages))
        return RouteRecord(prompt_chars=chars, prompt_sha=sha)

    def _log_hop(
        self, record: RouteRecord, tier: str, model: str, result: CompletionResult
    ) -> None:
        if tier not in record.tiers_tried:
            record.tiers_tried.append(tier)
        record.models[tier] = model
        spent = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        record.tokens_by_tier[tier] = record.tokens_by_tier.get(tier, 0) + spent

    def _accept(self, record: RouteRecord, tier: str) -> None:
        record.accepted_tier = tier
        if self.config.log_path is not None:
            append_route(self.config.log_path, record)
