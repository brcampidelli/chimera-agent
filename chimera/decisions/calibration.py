"""Platt scaling — one logistic on ``logit(p)`` — fitted on labelled answers of OUR decision, and the
store that says which map applies to which instrument.

Why Platt and not isotonic: measured on 2026-09-19 (`bench/jev_decisions/RESULTS.md` §6, §7b), leave-
one-family-out on the governance corpus. On the vendor's ``p`` Platt took ECE 0.120 → 0.025 at the same
operating point; on the saturated local model's ``p`` (bin 1.00 → 0.91 correct) it took Brier 0.268 →
0.135 and ECE 0.299 → 0.085, at the floor. Isotonic regression made both worse — a step function fitted
in folds of two or three items has steps where the data has none. Two parameters cannot overfit fifty
rows; that is the whole argument.

Why a map is keyed on four things: the decision (a map from the governance corpus says nothing about
review findings — the same vendor's calibration read ECE 0.405 there), the backend, the model, and a
hash of the instrument — the fixed text around the state. Change the wording and the map is gone,
loudly (``calibrated=False`` on every answer), rather than applied to numbers it was never fitted on.

The fit is pure Python: Newton's method on the two-parameter log-likelihood with a small ridge
(``prior``) and a backtracking step so it never overshoots. A set the raw ``p`` separates perfectly is
refused rather than fitted (the ridge would only stop the slope at a step), and a deployment's refit
uses Platt's smoothed targets so a near-separation does not make a cliff either (see ``fit_platt``). Agrees with scikit-learn's ``LogisticRegression(C=1e6)`` on the bench rows to four
decimals (a = 0.7207, b = −2.6412 on the local arm) without the dependency.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LOGIT_EPS = 1e-4
"""``p`` is clamped to [ε, 1−ε] before the logit — the same clamp the bench's recalibration used, so
the shipped constants mean what the bench measured. Without it a route that answers exactly 1.0
(the saturated local model does, often) would send ``logit`` to infinity."""


def sigmoid(z: float) -> float:
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def logit(p: float, eps: float = LOGIT_EPS) -> float:
    p = min(max(p, eps), 1.0 - eps)
    return math.log(p / (1.0 - p))


def prompt_hash(backend: str, model: str, instrument: str) -> str:
    """Twelve hex characters over the backend, the model and the instrument text."""
    digest = hashlib.sha256(f"{backend}\n{model}\n{instrument}".encode()).hexdigest()
    return digest[:12]


def perfectly_separated(pairs: Sequence[tuple[float, int]]) -> bool:
    """True when a threshold on ``p`` puts every label on its own side (ties at the boundary allowed).

    On such a set the likelihood keeps rising as the slope grows, and the ridge in :func:`fit_platt`
    (prior 1e-4) is too weak to stop it: 25 + 25 separated rows gave ``a`` = 38, a map that sends
    p = 0.50 to 0.00001 and p = 0.60 to 0.988. The labels show the classes did not overlap in this
    sample; they cannot say how sure to be, so a fit on them would invent certainty. Compared on the
    clamped logit, the scale the fit reads. Every row at one value is not separation (no threshold
    splits it), and neither is a set with one label only.
    """
    pos = [logit(float(p)) for p, y in pairs if y]
    neg = [logit(float(p)) for p, y in pairs if not y]
    if not pos or not neg:
        return False
    if min(pos) == max(pos) == min(neg) == max(neg):
        return False
    return max(neg) <= min(pos) or max(pos) <= min(neg)


def fit_platt(
    pairs: Sequence[tuple[float, int]], *, prior: float = 1e-4, iterations: int = 200, smoothed: bool = False,
) -> tuple[float, float]:
    """``(a, b)`` such that ``sigmoid(a·logit(p) + b)`` is the calibrated probability.

    ``pairs`` are ``(p, label)`` with the label 1 for the event. Both labels must be present — a
    map fitted on one class is a constant, and a constant is not a calibration — and they must not be
    separated by ``p`` (:func:`perfectly_separated`): there the slope only stops where the ridge stops
    it, at a step the labels cannot support. Refused here, so no caller gets that step silently.

    ``smoothed`` fits Platt's own targets, ``(N₊+1)/(N₊+2)`` for a positive and ``1/(N₋+2)`` for a
    negative, instead of 1 and 0: they cap how sure a fit on N labels may be, and the cap scales with
    n. The plain fit stays the default (and the 1e-4 ridge stays as it is) because the shipped map was
    fitted that way and a refit on its rows must reproduce it to the ninth decimal — the refit loop's
    gate. :func:`target_sensitivity` uses the smoothed fit to catch the cliff the plain one makes.
    """
    if len(pairs) < 4:
        raise ValueError(f"at least four labelled answers are needed, got {len(pairs)}")
    xs = [logit(float(p)) for p, _ in pairs]
    ys = [1 if y else 0 for _, y in pairs]
    if all(ys) or not any(ys):
        raise ValueError("both labels are needed to fit a map")
    if perfectly_separated(pairs):
        raise ValueError(
            "the raw p separates the labels perfectly, so a Platt fit would run to a step at ~0 and ~1 — "
            "certainty these labels cannot support"
        )
    n_pos = sum(ys)
    n_neg = len(ys) - n_pos
    hi, lo = ((n_pos + 1) / (n_pos + 2), 1 / (n_neg + 2)) if smoothed else (1.0, 0.0)
    ts = [hi if y else lo for y in ys]

    def nll(a: float, b: float) -> float:
        total = 0.0
        for x, t in zip(xs, ts, strict=True):
            z = a * x + b
            total += (max(z, 0.0) + math.log1p(math.exp(-abs(z)))) - t * z
        return total + 0.5 * prior * (a * a + b * b)

    a, b = 1.0, 0.0
    for _ in range(iterations):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for x, t in zip(xs, ts, strict=True):
            s = sigmoid(a * x + b)
            d, w = s - t, s * (1.0 - s)
            g_a += d * x
            g_b += d
            h_aa += w * x * x
            h_ab += w * x
            h_bb += w
        g_a += prior * a
        g_b += prior * b
        h_aa += prior
        h_bb += prior
        det = h_aa * h_bb - h_ab * h_ab
        if det <= 0:
            break
        da = (h_bb * g_a - h_ab * g_b) / det
        db = (-h_ab * g_a + h_aa * g_b) / det
        before, step = nll(a, b), 1.0
        while step > 1e-8 and nll(a - step * da, b - step * db) > before:
            step /= 2.0
        a -= step * da
        b -= step * db
        if step * (abs(da) + abs(db)) < 1e-10:
            break
    return a, b


MAX_TARGET_SENSITIVITY = 0.15
"""How far the plain fit's probabilities may sit from the smoothed fit's before a refit is refused.

Exact separation is not the only cliff. 25 + 25 rows with one negative at p = 0.61, just above the
lowest positive (0.60), are not separated, and the plain fit gave a = 19.2 (p = 0.55 → 0.02, p = 0.62 →
0.85); with Platt's targets the same rows give a = 2.7, and the two maps differ by 0.40 at a row. That
gap is the certainty the plain fit takes from treating every label as exact. Measured on 2026-10-05:
the shipped bench rows 0.052, well-calibrated draws 0.076 (n = 20), 0.045 (n = 50), 0.009 (n = 200);
the near-separations 0.40 (overlap at 0.61) and 0.22 (at 0.70). 0.15 is twice the n = 20 noise."""


def target_sensitivity(pairs: Sequence[tuple[float, int]]) -> float:
    """The largest gap, over the rows' own ``p``, between the plain fit and Platt's smoothed-target fit.

    Raises ``ValueError`` where :func:`fit_platt` would."""
    a, b = fit_platt(pairs)
    c, d = fit_platt(pairs, smoothed=True)
    return max(abs(sigmoid(a * logit(float(p)) + b) - sigmoid(c * logit(float(p)) + d)) for p, _ in pairs)


@dataclass(frozen=True)
class PlattMap:
    """A fitted map and where it came from."""

    id: str
    decision: str
    backend: str
    model: str
    prompt_hash: str
    a: float
    b: float
    n: int
    positives: int
    fitted_at: str
    source: str = ""
    """The labelled rows it was fitted on — a results file, a corpus name."""
    note: str = ""
    """The held-out numbers that justified it; the shipped ones cite the bench section."""
    resolved_model: str = ""
    """The build the rows came from, as the route names it (``qwen3:4b@Q4_K_M``); empty when the
    rows did not record one. The Decider applies the map only to answers from the same build."""

    def apply(self, p: float) -> float:
        return sigmoid(self.a * logit(p) + self.b)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlattMap:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    @classmethod
    def fit(
        cls, pairs: Sequence[tuple[float, int]], *, decision: str, backend: str, model: str,
        prompt_hash: str, source: str = "", note: str = "", fitted_at: str = "", resolved_model: str = "",
        smoothed: bool = False,
    ) -> PlattMap:
        a, b = fit_platt(pairs, smoothed=smoothed)
        when = fitted_at or time.strftime("%Y-%m-%d")
        return cls(
            id=f"{decision}/{backend}/{model}/{prompt_hash}/{when}", decision=decision, backend=backend,
            model=model, prompt_hash=prompt_hash, a=a, b=b, n=len(pairs), positives=sum(1 for _, y in pairs if y),
            fitted_at=when, source=source, note=note, resolved_model=resolved_model,
        )


class CalibrationMaps:
    """The maps a Decider may apply, found by the four keys, or not found."""

    def __init__(self, maps: Iterable[PlattMap] = ()) -> None:
        self._maps: dict[tuple[str, str, str, str], PlattMap] = {}
        for m in maps:
            self.add(m)

    @staticmethod
    def _key(m: PlattMap) -> tuple[str, str, str, str]:
        return (m.decision, m.backend, m.model, m.prompt_hash)

    def add(self, m: PlattMap) -> None:
        """A later map for the same four keys replaces the earlier one — refits supersede."""
        self._maps[self._key(m)] = m

    def find(self, decision: str, backend: str, model: str, prompt_hash: str) -> PlattMap | None:
        return self._maps.get((decision, backend, model, prompt_hash))

    def __len__(self) -> int:
        return len(self._maps)

    def __iter__(self) -> Iterator[PlattMap]:
        return iter(self._maps.values())

    def to_list(self) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self._maps.values()]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"maps": self.to_list()}, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> CalibrationMaps:
        """The maps in a file; an absent file is an empty store, a malformed one raises."""
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(PlattMap.from_dict(m) for m in data.get("maps", []))

    @classmethod
    def shipped(cls) -> CalibrationMaps:
        """The maps this package ships, each fitted on a bench with the provenance in its note."""
        from chimera.decisions.maps import SHIPPED_MAPS

        return cls(SHIPPED_MAPS)

    def merged(self, other: CalibrationMaps) -> CalibrationMaps:
        """This store with ``other``'s maps on top — a user's refit beats the shipped one."""
        out = CalibrationMaps(self._maps.values())
        for m in other._maps.values():
            out.add(m)
        return out
