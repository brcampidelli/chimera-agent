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
(``prior``) so a perfectly separable set still converges, and a backtracking step so it never
overshoots. Agrees with scikit-learn's ``LogisticRegression(C=1e6)`` on the bench rows to four
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


def fit_platt(pairs: Sequence[tuple[float, int]], *, prior: float = 1e-4, iterations: int = 200) -> tuple[float, float]:
    """``(a, b)`` such that ``sigmoid(a·logit(p) + b)`` is the calibrated probability.

    ``pairs`` are ``(p, label)`` with the label 1 for the event. Both labels must be present — a
    map fitted on one class is a constant, and a constant is not a calibration.
    """
    if len(pairs) < 4:
        raise ValueError(f"at least four labelled answers are needed, got {len(pairs)}")
    xs = [logit(float(p)) for p, _ in pairs]
    ys = [1 if y else 0 for _, y in pairs]
    if all(ys) or not any(ys):
        raise ValueError("both labels are needed to fit a map")

    def nll(a: float, b: float) -> float:
        total = 0.0
        for x, y in zip(xs, ys, strict=True):
            z = a * x + b
            total += (max(z, 0.0) + math.log1p(math.exp(-abs(z)))) - y * z
        return total + 0.5 * prior * (a * a + b * b)

    a, b = 1.0, 0.0
    for _ in range(iterations):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for x, y in zip(xs, ys, strict=True):
            s = sigmoid(a * x + b)
            d, w = s - y, s * (1.0 - s)
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
    ) -> PlattMap:
        a, b = fit_platt(pairs)
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
