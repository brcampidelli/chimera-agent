"""reranker_ab: leave-one-out AUC of the success reranker, lexical vs semantic (measure #2)."""

from __future__ import annotations

from chimera.eval.reranker_ab import auc, run_reranker_ab


def test_auc_perfect_and_chance():
    assert auc([(0.9, True), (0.8, True), (0.1, False)]) == 1.0  # every success outranks the failure
    assert auc([(0.5, True), (0.5, False)]) == 0.5  # a tie is chance
    assert auc([(0.1, True), (0.9, False)]) == 0.0  # inverted
    assert auc([(0.5, True)]) == 0.5  # no pos/neg pair -> chance


def test_lexical_auc_discriminates_when_tokens_carry_the_signal():
    # Successes talk about "sort stable key"; failures about "mutate global state". A held-out record
    # scored by its lexical neighbours should rank successes above failures.
    corpus = [
        ("sort records", "stable sort by key preserving order", True),
        ("sort rows", "stable sort keyed preserving input order", True),
        ("update config", "mutate global state in place badly", False),
        ("update settings", "mutate the global state directly", False),
    ]
    report = run_reranker_ab(corpus, k=3)
    assert report.n == 4 and report.successes == 2
    assert report.lexical_auc > 0.5  # lexical neighbours carry the signal here
    assert report.semantic_auc is None  # no embedder supplied


def test_semantic_beats_lexical_on_paraphrases():
    # Tokens are deliberately DISJOINT across records, so lexical recall is blind (AUC ~0.5), but the
    # fake embedder groups successes together and failures together -> semantic separates them.
    corpus = [
        ("t1", "aaa", True),
        ("t2", "bbb", True),
        ("t3", "ccc", False),
        ("t4", "ddd", False),
    ]
    vecs = {
        "t1\naaa": [1.0, 0.0], "t2\nbbb": [0.96, 0.02],   # successes cluster
        "t3\nccc": [0.0, 1.0], "t4\nddd": [0.02, 0.96],   # failures cluster
    }

    def embed(texts):
        return [vecs[t] for t in texts]

    report = run_reranker_ab(corpus, k=1, embed=embed)
    assert abs(report.lexical_auc - 0.5) < 1e-9  # disjoint tokens -> lexical is a coin
    assert report.semantic_auc == 1.0  # embeddings separate success from failure perfectly
    assert report.gap is not None and report.gap > 0.4
