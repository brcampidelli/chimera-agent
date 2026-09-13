"""The four arms of PREREGISTRATION §4. Standard library only — no numpy, no scikit-learn.

That constraint is the point, not thrift: a gate that needs scikit-learn cannot ship inside the
desktop app, so a number obtained with one would not be a number about a gate we could build.

Every arm exposes the same two methods, so the scorer cannot treat one specially:

    arm.fit(solves)      # trained on OTHER tasks only (leave-one-task-out); may be a no-op
    arm.score(solve)     # higher = more suspicious (more likely a false success)
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from corpus import Solve

_WORD = re.compile(r"[a-z0-9_]+")


def tokens(text: str) -> list[str]:
    """Lowercase word unigrams + bigrams. Deliberately dull: this is the cheap arm."""
    words = _WORD.findall(text.lower())
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]


class Arm:
    """An arm scores a solve. Higher means 'more likely a false success'."""

    name = "arm"

    def fit(self, solves: list[Solve]) -> None:
        return None

    def score(self, solve: Solve) -> float:
        raise NotImplementedError


class SelfReport(Arm):
    """The gate Chimera ships today. Fits nothing; it is the agent's own boolean.

    Within claimed successes this is constant by construction, so the primary DV reads 0.500 for it
    — and that constant 0.500 is the finding, not a defect: the shipped gate cannot rank one claimed
    success above another, which is exactly why a detector was proposed.
    """

    name = "self_report"

    def score(self, solve: Solve) -> float:
        return 0.0 if solve.self_report else 1.0


class Length(Arm):
    """Control. Characters in the claim, nothing else.

    If TF-IDF ties this, whatever signal exists is verbosity, and calling it a lexical detector
    names it wrong.
    """

    name = "length"

    def __init__(self, *, sign: float = 1.0) -> None:
        self.sign = sign

    def fit(self, solves: list[Solve]) -> None:
        """Learn only the direction, from the training tasks — never the held-out one."""
        short = [len(s.claim) for s in solves if not s.passed]
        good = [len(s.claim) for s in solves if s.passed]
        if short and good:
            self.sign = 1.0 if (sum(short) / len(short)) >= (sum(good) / len(good)) else -1.0

    def score(self, solve: Solve) -> float:
        return self.sign * len(solve.claim)


class LengthCeiling(Length):
    """`Length` with the direction pinned to whichever one wins — a CEILING, not a deployable arm.

    It is reported because `Length` learns its sign from the training tasks and that sign turns out
    not to transfer, which drags the deployable arm below 0.5. Separating the two says which half of
    the problem is "no signal" and which is "signal that does not carry across tasks". A number
    chosen with knowledge of the answer can never be read as performance.
    """

    name = "length_ceiling"

    def __init__(self) -> None:
        super().__init__(sign=-1.0)

    def fit(self, solves: list[Solve]) -> None:
        return None


class HarnessSignals(Arm):
    """Not the paper's arm: five numbers the receipt already carries, no text at all.

    Cheap, deterministic, and if it wins it is what should be built instead of a text model.
    """

    name = "harness"

    def __init__(self) -> None:
        self.model = DenseLogistic()

    @staticmethod
    def features(solve: Solve) -> list[float]:
        # `delivered_matches_verified` gets three levels, not two. It is None when there was no
        # winning attempt to compare a digest against, and folding that into False would encode
        # "we could not look" as "it did not match" — a different claim, and the one that made this
        # field look like a second copy of `success` on the first pass.
        digest = {True: 1.0, False: -1.0, None: 0.0}[solve.delivered_matches_verified]
        return [
            float(solve.rounds),
            float(solve.attempts),
            1.0 if solve.reverted else 0.0,
            1.0 if solve.stagnant else 0.0,
            digest,
        ]

    def fit(self, solves: list[Solve]) -> None:
        rows = [self.features(s) for s in solves]
        labels = [0.0 if s.passed else 1.0 for s in solves]
        self.model.fit(rows, labels)

    def score(self, solve: Solve) -> float:
        return self.model.decision(self.features(solve))


class Oracle(Arm):
    """Positive control (PREREGISTRATION §8): fed the label itself, it must score exactly 1.000.

    A scorer that cannot rank a perfect predictor at 1.000 is broken, and then no other number in
    RESULTS.md means anything. This arm exists to make that failure loud.
    """

    name = "oracle_control"

    def score(self, solve: Solve) -> float:
        return -solve.oracle


@dataclass
class DenseLogistic:
    """Plain L2 logistic regression by gradient descent, with per-feature standardisation.

    For the handful of harness features, whose scales differ by orders of magnitude. TF-IDF uses
    `SparseLogistic` instead: centring a TF-IDF row would destroy the sparsity that makes a
    pure-Python fit finish at all, and the rows are already L2-normalised.

    Deterministic: fixed iteration count, zero-initialised weights, no shuffling. Two runs on the
    same input give the same bytes, so a difference between runs is never the optimiser.
    """

    iterations: int = 400
    learning_rate: float = 0.5
    l2: float = 1.0
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    centre: list[float] = field(default_factory=list)
    scale: list[float] = field(default_factory=list)

    def fit(self, rows: list[list[float]], labels: list[float]) -> None:
        if not rows:
            return
        width = len(rows[0])
        self.centre = [sum(r[j] for r in rows) / len(rows) for j in range(width)]
        variance = [
            sum((r[j] - self.centre[j]) ** 2 for r in rows) / max(len(rows) - 1, 1)
            for j in range(width)
        ]
        self.scale = [math.sqrt(v) if v > 1e-12 else 1.0 for v in variance]
        standard = [self._standardise(r) for r in rows]

        self.weights = [0.0] * width
        self.bias = 0.0
        for _ in range(self.iterations):
            grad = [0.0] * width
            grad_bias = 0.0
            for row, label in zip(standard, labels, strict=True):
                error = _sigmoid(self._raw(row)) - label
                grad_bias += error
                for j, value in enumerate(row):
                    grad[j] += error * value
            n = len(standard)
            for j in range(width):
                self.weights[j] -= self.learning_rate * (grad[j] / n + self.l2 * self.weights[j] / n)
            self.bias -= self.learning_rate * grad_bias / n

    def _standardise(self, row: list[float]) -> list[float]:
        return [(v - c) / s for v, c, s in zip(row, self.centre, self.scale, strict=True)]

    def _raw(self, standard_row: list[float]) -> float:
        return self.bias + sum(w * v for w, v in zip(self.weights, standard_row, strict=True))

    def decision(self, row: list[float]) -> float:
        if not self.weights:
            return 0.0
        return self._raw(self._standardise(row))


@dataclass
class SparseLogistic:
    """The same regression over sparse rows: {feature index: value}, no centring.

    A TF-IDF document touches a few hundred of several thousand terms, so iterating the non-zeros is
    what makes a stdlib fit take seconds instead of an hour. Same determinism guarantee.
    """

    iterations: int = 300
    learning_rate: float = 1.0
    l2: float = 2.0
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0

    def fit(self, rows: list[dict[int, float]], labels: list[float], width: int) -> None:
        self.weights = [0.0] * width
        self.bias = 0.0
        if not rows:
            return
        n = len(rows)
        for _ in range(self.iterations):
            grad: dict[int, float] = {}
            grad_bias = 0.0
            for row, label in zip(rows, labels, strict=True):
                error = _sigmoid(self.decision(row)) - label
                grad_bias += error
                for index, value in row.items():
                    grad[index] = grad.get(index, 0.0) + error * value
            decay = self.learning_rate * self.l2 / n
            for index, value in grad.items():
                self.weights[index] -= self.learning_rate * value / n
            if self.l2:
                self.weights = [w - decay * w for w in self.weights]
            self.bias -= self.learning_rate * grad_bias / n

    def decision(self, row: dict[int, float]) -> float:
        if not self.weights:
            return 0.0
        return self.bias + sum(self.weights[i] * v for i, v in row.items())


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-min(x, 60.0)))
    exp = math.exp(max(x, -60.0))
    return exp / (1.0 + exp)


class Tfidf(Arm):
    """The paper's arm: TF-IDF over word 1-2 grams of the claim, then logistic regression.

    Sparse by hand — the vocabulary is a dict and each document is a dict of the terms it has, so a
    287-item corpus fits without a matrix library.
    """

    name = "tfidf"

    def __init__(self, *, min_df: int = 3, max_features: int = 4000) -> None:
        self.min_df = min_df
        self.max_features = max_features
        self.vocabulary: dict[str, int] = {}
        self.idf: list[float] = []
        self.model = SparseLogistic()

    def _vectorise(self, text: str) -> dict[int, float]:
        counts = Counter(t for t in tokens(text) if t in self.vocabulary)
        if not counts:
            return {}
        vector = {
            self.vocabulary[term]: (1.0 + math.log(n)) * self.idf[self.vocabulary[term]]
            for term, n in counts.items()
        }
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
        return {k: v / norm for k, v in vector.items()}

    def fit(self, solves: list[Solve]) -> None:
        document_frequency: Counter[str] = Counter()
        for solve in solves:
            document_frequency.update(set(tokens(solve.claim)))
        kept = [t for t, n in document_frequency.items() if n >= self.min_df]
        kept.sort(key=lambda t: (-document_frequency[t], t))
        kept = kept[: self.max_features]
        self.vocabulary = {term: i for i, term in enumerate(sorted(kept))}
        total = len(solves)
        self.idf = [0.0] * len(self.vocabulary)
        for term, index in self.vocabulary.items():
            self.idf[index] = math.log((1 + total) / (1 + document_frequency[term])) + 1.0

        rows = [self._vectorise(s.claim) for s in solves]
        labels = [0.0 if s.passed else 1.0 for s in solves]
        self.model.fit(rows, labels, len(self.vocabulary))

    def score(self, solve: Solve) -> float:
        if not self.vocabulary:
            return 0.0
        return self.model.decision(self._vectorise(solve.claim))


def arms() -> list[Arm]:
    """The registered arms, in the order RESULTS.md reports them."""
    return [SelfReport(), Length(), Tfidf(), HarnessSignals()]


def ceilings() -> list[Arm]:
    """Reported beside the arms, never as performance: each one knows something a gate would not."""
    return [LengthCeiling()]
