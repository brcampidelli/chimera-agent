"""SuccessReranker (#2): inference-time k-NN P(success) from the agent's own logged outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from chimera.evolution.reranker import SuccessReranker


def test_empty_history_returns_prior():
    r = SuccessReranker([], prior=0.5)
    assert r.score("merge intervals", "def merge(): ...") == 0.5


def test_no_similar_neighbor_returns_prior():
    r = SuccessReranker([("parse a duration string into seconds", True)], prior=0.5)
    # a totally unrelated task/candidate shares no tokens -> no evidence -> prior
    assert r.score("render an html template", "zzz qqq") == 0.5


def test_candidate_like_past_success_scores_high():
    history = [
        ("merge overlapping intervals sort by start return merged", True),
        ("merge overlapping intervals sort by start return merged list", True),
        ("flatten a nested list of arbitrary depth recursively", False),
    ]
    r = SuccessReranker(history, k=3)
    good = r.score("merge overlapping intervals", "sort intervals by start then merge overlapping")
    bad = r.score("merge overlapping intervals", "flatten nested list recursively by depth")
    assert good > bad
    assert good > 0.5  # neighbours were successes


def test_candidate_like_past_failure_scores_low():
    history = [
        ("flatten nested list depth recursion", False),
        ("flatten nested list depth recursion helper", False),
    ]
    r = SuccessReranker(history, k=3)
    assert r.score("flatten a nested list", "recursion over nested list by depth") < 0.5


def test_rerank_orders_candidates_and_best_picks_top():
    history = [("sort stable by key preserve order", True), ("mutate global state badly", False)]
    r = SuccessReranker(history, k=2)
    ranked = r.rerank("sort records", ["mutate global state in place", "stable sort by key preserving order"])
    assert [c for c, _ in ranked][0] == "stable sort by key preserving order"
    assert r.best("sort records", ["mutate global state in place", "stable sort by key preserving order"]) \
        == "stable sort by key preserving order"
    assert r.best("x", []) is None


def test_from_trajectories_labels_and_skips_unknown():
    @dataclass
    class Traj:
        prompt: str
        response: str
        outcome: str

    trajs = [
        Traj("sort by key", "stable sort keeping order", "success"),
        Traj("parse duration", "regex parse hms", "failure"),
        Traj("something", "half-done", "unknown"),  # no label -> skipped
    ]
    r = SuccessReranker.from_trajectories(trajs, k=3)
    # the unknown one is skipped, so only 2 labelled records inform the score
    assert r.score("sort a list by key", "stable sort keeping insertion order") > 0.5


def test_injectable_similarity_is_used():
    calls = {"n": 0}

    def always_one(a, b):  # every item is a perfect neighbour
        calls["n"] += 1
        return 1.0

    r = SuccessReranker([("x", True), ("y", False)], k=2, similarity=always_one)
    # both neighbours weighted equally (1 success, 1 failure) -> 0.5
    assert r.score("t", "c") == 0.5
    assert calls["n"] == 2


def _fake_embed(mapping):
    """A deterministic fake embedder: each text -> a fixed 2-D vector (unknown -> orthogonal)."""

    def embed(texts):
        return [mapping.get(t, [0.0, 1.0]) for t in texts]

    return embed


def test_semantic_mode_ranks_by_cosine_not_tokens():
    # 'alpha' and 'beta' share NO tokens but embed to the SAME direction (a paraphrase); lexical would
    # miss it, semantic should treat the beta-success record as the near neighbour of an alpha query.
    embed = _fake_embed({
        "alpha task\nalpha solution": [1.0, 0.0],  # the query embeds here
        "beta task\nbeta solution": [1.0, 0.0],     # a success, same direction (paraphrase)
        "gamma task\ngamma solution": [0.0, 1.0],   # a failure, orthogonal
    })
    r = SuccessReranker(
        [("beta task\nbeta solution", True), ("gamma task\ngamma solution", False)],
        k=2, embed=embed,
    )
    # query shares no TOKENS with 'beta' but is semantically identical -> should score high
    assert r.score("alpha task", "alpha solution") > 0.5


def test_semantic_empty_history_returns_prior():
    r = SuccessReranker([], embed=_fake_embed({}), prior=0.5)
    assert r.score("t", "c") == 0.5
