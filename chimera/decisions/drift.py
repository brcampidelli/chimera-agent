"""Drift alerts computed from the decision log alone — study 27, phase 3.

Tiltmeter's three alarms, none of which needs a label:

* **the serving build changed** — the same instrument (decision, backend, model, instrument hash)
  answered on two different ``resolved_model`` values. An alias moves under a fixed configured name,
  and every calibration map is keyed on the build, so this is the alarm that says a refit is due;
* **the answer distribution moved** — the last window of raw numbers against everything before it:
  a population-stability index over ten equal bins, and a chi-square test of homogeneity on the same
  counts. Both must fire: PSI alone flags a thin window that wandered, the test alone flags a large
  window that moved by a hair;
* **answers sit on a threshold** — a large share of the recent calibrated numbers within ``EPS`` of
  the REVIEW or ALLOW cut. That region flaps: two near-identical questions land on either side.

**These annotate and gate nothing.** They surface on the Decisions screen and in
``chimera decisions report``. A refit that follows an alarm can then be justified by the log rather
than a hunch, and that is all the alarm is for.

Pure Python on purpose. There is no scipy in this project (numpy is an optional extra), and the
chi-square survival function is the regularised upper incomplete gamma, a dozen lines with `math`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from chimera.decisions.log import Row

#: How many of the latest answers are "now", and how many each side needs before a test means
#: anything. Below this a window is a handful of cases and any distribution looks like drift.
WINDOW = 50
MIN_SIDE = 30
BINS = 10
#: The conventional reading of a population-stability index: under 0.1 stable, 0.1-0.25 shifted,
#: over 0.25 shifted a lot. The alarm fires at the middle of the middle band, and only together
#: with the significance test below.
PSI_ALARM = 0.2
ALPHA = 0.01
#: A recent answer is "on a threshold" within this distance of a cut, and the region flaps when this
#: share of the last window is.
EPS = 0.03
NEAR_SHARE = 0.25
MIN_NEAR = 20

Instrument = tuple[str, str, str, str]
"""(decision, backend, model, prompt_hash) — a group_key without the build."""


@dataclass(frozen=True)
class Alert:
    kind: str
    """``model_changed`` | ``answer_drift`` | ``near_threshold``."""
    decision: str
    backend: str
    model: str
    prompt_hash: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "decision": self.decision, "backend": self.backend,
            "model": self.model, "prompt_hash": self.prompt_hash, "detail": self.detail,
        }


def gamma_q(a: float, x: float) -> float:
    """The regularised upper incomplete gamma function Q(a, x), for a > 0 (Numerical Recipes 6.2:
    the series below a+1, the continued fraction above)."""
    if x <= 0.0:
        return 1.0
    log_prefix = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1.0:
        term = total = 1.0 / a
        ap = a
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(log_prefix))
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return min(1.0, math.exp(log_prefix) * h)


def chi2_sf(statistic: float, df: int) -> float:
    """P(X >= statistic) for X ~ chi-square with ``df`` degrees of freedom."""
    if df < 1:
        raise ValueError("df must be at least 1")
    return gamma_q(df / 2.0, max(0.0, statistic) / 2.0)


def _histogram(values: Sequence[float], bins: int = BINS) -> list[int]:
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, max(0, int(v * bins)))] += 1
    return counts


def psi(reference: Sequence[int], recent: Sequence[int], floor: float = 1e-4) -> float:
    """Population-stability index of two histograms over the same bins. An empty bin is floored, so
    a bin that appeared or vanished counts heavily and never divides by zero."""
    n_ref, n_new = sum(reference), sum(recent)
    if not n_ref or not n_new:
        return 0.0
    total = 0.0
    for r, n in zip(reference, recent, strict=True):
        e = max(r / n_ref, floor)
        a = max(n / n_new, floor)
        total += (a - e) * math.log(a / e)
    return total


def homogeneity(reference: Sequence[int], recent: Sequence[int]) -> tuple[float, int] | None:
    """Chi-square statistic and degrees of freedom for two count vectors drawn from one population,
    or None when there are fewer than two occupied bins to compare."""
    cols = [(r, n) for r, n in zip(reference, recent, strict=True) if r + n > 0]
    if len(cols) < 2:
        return None
    n_ref, n_new = sum(r for r, _ in cols), sum(n for _, n in cols)
    grand = n_ref + n_new
    statistic = 0.0
    for r, n in cols:
        col = r + n
        for observed, side in ((r, n_ref), (n, n_new)):
            expected = col * side / grand
            statistic += (observed - expected) ** 2 / expected
    return statistic, len(cols) - 1


def _instrument(answer: dict[str, Any]) -> Instrument:
    return (
        str(answer.get("decision") or ""), str(answer.get("backend") or ""),
        str(answer.get("model") or ""), str(answer.get("prompt_hash") or ""),
    )


def _by_instrument(rows: Iterable[Row]) -> dict[Instrument, list[Row]]:
    grouped: dict[Instrument, list[Row]] = {}
    for row in rows:
        grouped.setdefault(_instrument(row.answer), []).append(row)
    for members in grouped.values():
        members.sort(key=lambda r: float(r.answer.get("at") or 0.0))
    return grouped


def model_changed(rows: Iterable[Row]) -> list[Alert]:
    """One alert per instrument that answered on more than one serving build, oldest first."""
    out: list[Alert] = []
    for key, members in sorted(_by_instrument(rows).items()):
        seen: dict[str, float] = {}
        for row in members:
            build = str(row.answer.get("resolved_model") or "")
            if build and build not in seen:
                seen[build] = float(row.answer.get("at") or 0.0)
        if len(seen) > 1:
            builds = [{"build": b, "first_seen": t} for b, t in sorted(seen.items(), key=lambda kv: kv[1])]
            out.append(Alert("model_changed", *key, detail={"builds": builds}))
    return out


def answer_drift(rows: Iterable[Row]) -> list[Alert]:
    """One alert per instrument whose latest window of raw numbers moved away from the rest."""
    out: list[Alert] = []
    for key, members in sorted(_by_instrument(rows).items()):
        numbers = [r.raw_p for r in members if r.raw_p is not None and not r.answer.get("halt")]
        if len(numbers) < 2 * MIN_SIDE:
            continue
        recent, reference = numbers[-WINDOW:], numbers[:-WINDOW]
        if len(reference) < MIN_SIDE or len(recent) < MIN_SIDE:
            continue
        ref_h, new_h = _histogram(reference), _histogram(recent)
        index = psi(ref_h, new_h)
        test = homogeneity(ref_h, new_h)
        if test is None:
            continue
        p_value = chi2_sf(*test)
        if index >= PSI_ALARM and p_value < ALPHA:
            out.append(Alert(
                "answer_drift", *key,
                detail={"psi": round(index, 3), "p_value": p_value, "reference": len(reference), "recent": len(recent)},
            ))
    return out


def near_threshold(rows: Iterable[Row], *, review_at: float, allow_below: float) -> list[Alert]:
    """One alert per instrument whose recent calibrated numbers crowd a cut of the band."""
    out: list[Alert] = []
    for key, members in sorted(_by_instrument(rows).items()):
        recent = [
            float(r.answer["p"]) for r in members
            if r.answer.get("calibrated") and isinstance(r.answer.get("p"), (int, float))
        ][-WINDOW:]
        if len(recent) < MIN_NEAR:
            continue
        for name, cut in (("review_at", review_at), ("allow_below", allow_below)):
            near = sum(1 for p in recent if abs(p - cut) <= EPS)
            if near / len(recent) >= NEAR_SHARE:
                out.append(Alert(
                    "near_threshold", *key,
                    detail={"cut": name, "value": cut, "near": near, "of": len(recent), "eps": EPS},
                ))
    return out


def describe(alert: Alert) -> str:
    """One line for the terminal. The desktop translates from the same fields."""
    d = alert.detail
    where = f"{alert.decision} ({alert.backend}/{alert.model})"
    if alert.kind == "model_changed":
        builds = " -> ".join(str(b["build"]) for b in d["builds"])
        return f"{where}: the serving build changed ({builds}); the calibration map is keyed on the build"
    if alert.kind == "answer_drift":
        return (
            f"{where}: the last {d['recent']} answers moved away from the {d['reference']} before them "
            f"(PSI {d['psi']}, p = {d['p_value']:.2g})"
        )
    return (
        f"{where}: {d['near']} of the last {d['of']} answers sit within {d['eps']} of {d['cut']} "
        f"({d['value']}); that region flaps"
    )


def alerts(rows: Iterable[Row], *, review_at: float, allow_below: float) -> list[Alert]:
    """Every alarm over the log, in a stable order: build changes first, then distribution drift,
    then thresholds under crowding."""
    materialised = list(rows)
    return [
        *model_changed(materialised),
        *answer_drift(materialised),
        *near_threshold(materialised, review_at=review_at, allow_below=allow_below),
    ]
