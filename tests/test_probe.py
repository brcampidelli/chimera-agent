"""Tests for PROBE best-arm identification with a cheap-proxy control variate (M18-5)."""

from __future__ import annotations

import statistics

from chimera.eval.probe import ProbeBestArm


def test_good_proxy_tightens_the_interval() -> None:
    rewards = [0.2, 0.9, 0.4, 0.8, 0.3, 0.7]
    probe = ProbeBestArm(delta=0.1)
    # Strong arm: proxy == reward (ρ≈1) PLUS many cheap proxy-only draws that precisely pin the proxy
    # mean — this is where the (1−ρ²) reduction actually materialises.
    strong_obs = [(r, r) for r in rewards] + [(rewards[i % len(rewards)], None) for i in range(60)]
    strong = probe.estimate("strong", strong_obs)
    # Weak arm: a constant (useless) proxy — no reduction, falls back to the plain reward mean.
    weak = probe.estimate("weak", [(0.5, r) for r in rewards])
    assert strong.half_width < weak.half_width  # the good proxy buys a tighter interval
    assert abs(strong.mean - statistics.fmean(rewards)) < 0.1  # and stays unbiased
    assert abs(weak.mean - statistics.fmean(rewards)) < 1e-9
    assert strong.rho > 0.9 and abs(weak.beta) < 1e-9


def test_selects_the_best_arm_confidently() -> None:
    probe = ProbeBestArm(delta=0.1, min_reward=3)
    arms = {
        "good": [(0.9, 0.9), (0.85, 0.85), (0.95, 0.95), (0.9, 0.9)],
        "bad": [(0.1, 0.1), (0.15, 0.15), (0.05, 0.05), (0.1, 0.1)],
    }
    decision = probe.select(arms)
    assert decision.best == "good" and decision.confident is True and decision.next_arm is None


def test_unbiased_even_with_a_useless_proxy() -> None:
    probe = ProbeBestArm(delta=0.1, min_reward=3)
    # Proxy is uncorrelated noise; the estimate must still recover the right best arm + honest mean.
    arms = {
        "good": [(0.1, 0.8), (0.9, 0.9), (0.5, 0.7), (0.2, 0.85)],
        "bad": [(0.9, 0.2), (0.1, 0.1), (0.5, 0.3), (0.2, 0.15)],
    }
    decision = probe.select(arms)
    good = next(e for e in decision.estimates if e.arm == "good")
    assert decision.best == "good"
    assert abs(good.mean - statistics.fmean([0.8, 0.9, 0.7, 0.85])) < 0.2  # control variate is unbiased


def test_explores_the_under_sampled_arm_first() -> None:
    probe = ProbeBestArm(delta=0.1, min_reward=2)
    arms = {
        "a": [(0.5, 0.5)],  # only 1 reward -> under min_reward
        "b": [(0.4, 0.4), (0.6, 0.6), (0.5, 0.5)],
    }
    decision = probe.select(arms)
    assert decision.confident is False and decision.next_arm == "a"


def test_no_rewards_yields_infinite_uncertainty() -> None:
    probe = ProbeBestArm()
    est = probe.estimate("cheap-only", [(0.5, None), (0.6, None)])
    assert est.n_reward == 0 and est.half_width == float("inf")
