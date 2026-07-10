"""PROBE — best-arm identification with a cheap proxy (M18-5, arXiv 2607.06879).

The cost-aware router's job — "which model/config is best for this task-class?" — is a best-arm
identification problem where each *expensive* reward (a real grade, a paid-model judge) is paired
with a *cheap* proxy (a weak judge's score, a heuristic) of unknown correlation ρ. PROBE uses the
proxy as a **control variate**: it draws the cheap proxy often and the expensive reward rarely, and
adjusts the reward estimate by the proxy so the estimator's variance scales by **(1 − ρ²)** — fewer
expensive draws when the proxy is good, and, crucially, **still correct when the proxy is bad**
(β → 0, it degrades to the plain reward mean with a wider interval, never a biased one).

This module is the pure estimator + selection rule (no I/O, no model calls), so it's unit-testable
and can be fed recorded (proxy, reward) observations from `route_log` or a bench. Observations are
``(proxy, reward)`` pairs; ``reward`` is ``None`` for a cheap-only draw.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

# Two-sided normal quantiles for common confidence levels; δ is snapped to the nearest tabulated level,
# rounding toward the more conservative (larger) z. A dependency-free stand-in for norm.ppf.
_Z_TABLE = [(0.20, 1.2816), (0.10, 1.6449), (0.05, 1.9600), (0.02, 2.3263), (0.01, 2.5758)]

Observation = tuple[float, float | None]  # (cheap proxy score, expensive reward or None)


def _z_for(delta: float) -> float:
    for d, z in _Z_TABLE:
        if delta >= d:
            return z
    return _Z_TABLE[-1][1]


@dataclass
class ArmEstimate:
    """A control-variate estimate of one arm's mean reward, with a confidence half-width."""

    arm: str
    mean: float
    half_width: float
    n_reward: int
    n_proxy: int
    beta: float
    rho: float


@dataclass
class ProbeDecision:
    """The best-arm verdict + the next arm to sample when not yet confident."""

    best: str | None
    confident: bool
    next_arm: str | None
    estimates: list[ArmEstimate] = field(default_factory=list)


class ProbeBestArm:
    """Best-arm identification with a cheap-proxy control variate (fixed-confidence)."""

    def __init__(self, *, delta: float = 0.1, min_reward: int = 2) -> None:
        self.z = _z_for(delta)
        self.min_reward = max(1, min_reward)

    def estimate(self, arm: str, observations: list[Observation]) -> ArmEstimate:
        proxies = [p for p, _ in observations]
        paired = [(p, r) for p, r in observations if r is not None]
        n_proxy, n_reward = len(proxies), len(paired)
        if n_reward == 0:
            return ArmEstimate(arm, 0.0, math.inf, 0, n_proxy, 0.0, 0.0)
        pp = [p for p, _ in paired]
        rr = [float(r) for _, r in paired]  # type: ignore[arg-type]
        reward_mean = statistics.fmean(rr)
        beta = rho = 0.0
        residual_var = statistics.variance(rr) if n_reward > 1 else 0.0
        mean = reward_mean
        var_p_paired = statistics.variance(pp) if n_reward > 1 else 0.0
        if n_reward > 1 and var_p_paired > 0:
            mp, mr = statistics.fmean(pp), reward_mean
            # Sample covariance (÷ n−1), consistent with statistics.variance — so β and ρ are exact.
            cov = sum((p - mp) * (r - mr) for p, r in paired) / (n_reward - 1)
            beta = cov / var_p_paired
            sp, sr = math.sqrt(var_p_paired), math.sqrt(residual_var)
            rho = cov / (sp * sr) if sr > 0 else 0.0
            # Control variate: adjust by the proxy's deviation from its (cheaply, precisely) known mean.
            proxy_mean_all = statistics.fmean(proxies)
            mean = statistics.fmean([r - beta * (p - proxy_mean_all) for p, r in paired])
            # Residual variance ≈ Var(reward)·(1−ρ²) — the reduction the proxy buys.
            residual_var = statistics.variance([r - beta * p for p, r in paired])
        proxy_var_all = statistics.variance(proxies) if n_proxy > 1 else 0.0
        # SE: reward-residual term (shrinks with ρ) + the small proxy-mean-uncertainty term.
        se = math.sqrt(residual_var / n_reward + (beta**2) * proxy_var_all / max(n_proxy, 1))
        return ArmEstimate(arm, mean, self.z * se, n_reward, n_proxy, beta, rho)

    def select(self, arms: dict[str, list[Observation]]) -> ProbeDecision:
        """Pick the best arm; if not yet δ-confident, name the arm to sample next (the challenger)."""
        estimates = [self.estimate(a, obs) for a, obs in arms.items()]
        order = sorted(estimates, key=lambda e: e.mean, reverse=True)
        if not order:
            return ProbeDecision(best=None, confident=False, next_arm=None, estimates=[])
        under = [e for e in estimates if e.n_reward < self.min_reward]
        if under:  # explore the least-sampled arm before trusting any comparison
            nxt = min(under, key=lambda e: e.n_reward).arm
            return ProbeDecision(best=order[0].arm, confident=False, next_arm=nxt, estimates=order)
        best, rivals = order[0], order[1:]
        best_lcb = best.mean - best.half_width
        confident = all(best_lcb > r.mean + r.half_width for r in rivals)
        next_arm = None if confident else (rivals[0].arm if rivals else best.arm)
        return ProbeDecision(best=best.arm, confident=confident, next_arm=next_arm, estimates=order)
