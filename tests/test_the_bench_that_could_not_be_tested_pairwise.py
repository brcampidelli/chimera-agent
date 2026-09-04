"""Three things the RAG bench could not do, found while pre-registering a run against it.

`bench/rag/PREREGISTRATION.md` fixed a paired decision rule — hybrid against keyword, McNemar, over
the same probes — and then the harness turned out not to be able to answer it. `RagReport` carried
three totals and nothing per probe, so the pairs the test needs were computed and thrown away inside
the loop that computed them.

The other two are quieter. `embed_missing` was called without `embedder=`, which leaves
`ChunkStore._align_embedder` inert: the guard that zeroes every vector when the model identity or
width changes was off in exactly the run that introduces vectors, and `store.py` describes what that
costs — `_cosine` returns 0.0 on a dimension mismatch, the `score > 0` filter drops the lot, and the
index reports healthy while returning nothing. And the query side embedded one probe per call,
inside the loop, for the same money as one batch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.eval.paired import compare_paired
from chimera.eval.rag_bench import run_rag_bench

SOURCE = '''
def verify(command: str) -> bool:
    """Run the project's own check and say whether it passed."""
    return True


def budget(tokens: int) -> int:
    """How many tokens a run is permitted to consume before compaction."""
    return tokens


def rank(items: list[str]) -> list[str]:
    """Order results so the most relevant one is read first."""
    return items
'''

VOCAB = ["command", "tokens", "run", "compaction", "permitted", "consume", "count", "order"]


def _corpus(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir(exist_ok=True)
    (tmp_path / "core" / "verify.py").write_text(SOURCE, encoding="utf-8")
    return tmp_path


def _embed(texts: list[str]) -> list[list[float]]:
    return [[float(t.lower().count(w)) for w in VOCAB] for t in texts]


# --- the pairs the decision rule needs ------------------------------------------------------------


def test_every_arm_reports_a_result_per_probe(tmp_path: Path) -> None:
    """Totals cannot be tested against each other; the arms answer the SAME probes."""
    report = run_rag_bench(_corpus(tmp_path), index_path=tmp_path / "i.sqlite3", embed=_embed)

    assert len(report.keyword_hit) == report.probes
    assert len(report.vector_hit) == report.probes
    assert len(report.hybrid_hit) == report.probes


def test_the_per_probe_lists_agree_with_the_totals(tmp_path: Path) -> None:
    """Two ways of counting the same thing, which is what makes either believable."""
    report = run_rag_bench(_corpus(tmp_path), index_path=tmp_path / "i.sqlite3", embed=_embed)

    assert report.keyword_recall == sum(report.keyword_hit) / report.probes
    assert report.vector_recall == sum(report.vector_hit) / report.probes
    assert report.hybrid_recall == sum(report.hybrid_hit) / report.probes


def test_the_lists_feed_the_projects_own_paired_test(tmp_path: Path) -> None:
    """The rule is McNemar via `chimera/eval/paired.py`, which takes two aligned pass/fail lists."""
    report = run_rag_bench(_corpus(tmp_path), index_path=tmp_path / "i.sqlite3", embed=_embed)

    result = compare_paired(report.keyword_hit, report.hybrid_hit)

    assert result.n == report.probes
    assert result.baseline_rate == report.keyword_recall
    assert result.treatment_rate == report.hybrid_recall


def test_without_an_embedder_only_the_keyword_arm_has_pairs(tmp_path: Path) -> None:
    """Absent, not a list of False. A probe the vector arm never answered did not miss it."""
    report = run_rag_bench(_corpus(tmp_path), index_path=tmp_path / "i.sqlite3")

    assert len(report.keyword_hit) == report.probes
    assert report.vector_hit == []
    assert report.hybrid_hit == []


# --- the signature guard --------------------------------------------------------------------------


def test_the_embedder_name_reaches_the_store(tmp_path: Path, monkeypatch: Any) -> None:
    """`_align_embedder` is inert without it, in the one run that first writes vectors."""
    from chimera.rag.store import ChunkStore

    seen: dict[str, Any] = {}
    real = ChunkStore.embed_missing

    def spy(self: Any, embed: Any, *args: Any, **kwargs: Any) -> int:
        seen.update(kwargs)
        return real(self, embed, *args, **kwargs)

    monkeypatch.setattr(ChunkStore, "embed_missing", spy, raising=True)
    run_rag_bench(
        _corpus(tmp_path), index_path=tmp_path / "i.sqlite3", embed=_embed, embedder="fake/model-x"
    )

    assert seen.get("embedder") == "fake/model-x"


def test_the_embedder_and_width_land_in_the_report(tmp_path: Path) -> None:
    """A recall figure without the model that produced it is a number about nothing in particular."""
    report = run_rag_bench(
        _corpus(tmp_path), index_path=tmp_path / "i.sqlite3", embed=_embed, embedder="fake/model-x"
    )

    assert report.embedder == "fake/model-x"
    assert report.dimensions == len(VOCAB)
    assert report.as_dict()["embedder"] == "fake/model-x"


# --- batching -------------------------------------------------------------------------------------


def test_the_queries_are_embedded_in_batches(tmp_path: Path) -> None:
    """One call per probe is the same money and several minutes of avoidable latency."""
    calls: list[int] = []

    def counting(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return _embed(texts)

    report = run_rag_bench(_corpus(tmp_path), index_path=tmp_path / "i.sqlite3", embed=counting)

    # One batch for the corpus, one for the queries — never one per probe.
    assert len(calls) < report.probes
    assert max(calls) > 1


def test_a_short_batch_voids_the_semantic_arms_rather_than_misaligning_them(
    tmp_path: Path,
) -> None:
    """A provider that returns fewer vectors than it was given must not shift every later probe.

    Silently zipping a short list against the probes would pair each query with the NEXT one's
    vector — a run that completes, reports a plausible recall, and is measuring nothing.
    """
    state = {"first": True}

    def short(texts: list[str]) -> list[list[float]]:
        vectors = _embed(texts)
        if state["first"]:
            state["first"] = False
            return vectors
        return vectors[:-1]  # the query batch comes back one short

    report = run_rag_bench(_corpus(tmp_path), index_path=tmp_path / "i.sqlite3", embed=short)

    assert report.vector_recall is None
    assert report.hybrid_recall is None
    assert any("vectors for" in note for note in report.notes)
