from __future__ import annotations

import pytest

from bench.rag_rerank.run import _cross_encoder_rank
from chimera.eval.paired import compare_paired
from chimera.rag.chunks import Chunk


class Hit:
    def __init__(self, ident: str, text: str) -> None:
        self.chunk = Chunk("fixture.py", ident, "function", 1, 1, text)


def test_fake_cross_encoder_scores_same_paired_candidates_and_reorders_by_score() -> None:
    candidates = [Hit("first", "counter += 1"), Hit("second", "return counter")]
    called: list[list[tuple[str, str]]] = []

    def fake_score(pairs: list[tuple[str, str]]) -> list[float]:
        called.append(pairs)
        return [0.9, 0.1] if pairs[0][0].endswith("first target") else [0.1, 0.9]

    cases = [("find a counter — first target", candidates[0]), ("find a counter — second target", candidates[1])]
    baseline_targets: list[bool] = []
    treatment_targets: list[bool] = []
    rankings = []
    for case_query, target in cases:
        reranked = _cross_encoder_rank(case_query, candidates, fake_score)
        rankings.append(reranked)
        baseline_targets.append(target in candidates[:1])
        treatment_targets.append(target in reranked[:1])
    paired = compare_paired(
        baseline_targets,
        treatment_targets,
        baseline_name="hybrid30",
        treatment_name="cross_encoder",
    )

    assert called[0] == [
        (cases[0][0], "Description: find a counter — first target\n\nChunk: fixture.py :: first [function]\n```\ncounter += 1\n```"),
        (cases[0][0], "Description: find a counter — first target\n\nChunk: fixture.py :: second [function]\n```\nreturn counter\n```"),
    ]
    assert called[1][0][0] == cases[1][0] and len(called[1]) == 2
    assert rankings == [[candidates[0], candidates[1]], [candidates[1], candidates[0]]]
    assert baseline_targets == [True, False]
    assert treatment_targets == [True, True]
    assert paired.n == 2
    assert paired.baseline_only == 0
    assert paired.treatment_only == 1
    assert paired.delta == 0.5


def test_fake_cross_encoder_rejects_unpaired_candidate_scores() -> None:
    with pytest.raises(ValueError, match="returned 1 scores for 2 candidates"):
        _cross_encoder_rank("q", [Hit("a", "a"), Hit("b", "b")], lambda pairs: [0.5])
