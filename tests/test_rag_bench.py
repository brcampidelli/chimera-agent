"""The pre-registered A/B for retrieval (:mod:`chimera.eval.rag_bench`).

A bench is a measuring instrument, and an instrument that cannot be wrong is not one. Two of the
three properties below were added because the first two runs of this bench produced numbers that
were artifacts rather than findings:

* recall@10 of **0.005**, which read as "keyword search is useless" and was a broken FTS query
  (the whole phrase quoted, so every multi-word search was an exact-phrase match);
* recall@10 of **1.00**, which read as "keyword search is perfect" and was the corpus containing
  the question — the docstring the probe is made from sat inside the chunk it pointed at.

The third run measured 0.4925 over 400 probes on 2,691 chunks of this repository. These tests are
what stop the instrument drifting back to either artifact.
"""

from __future__ import annotations

from pathlib import Path

from chimera.eval.rag_bench import _without_docstring, build_probes, run_rag_bench
from chimera.rag.chunks import Chunk, chunk_source

SOURCE = '''\
def resolve_verify(root):
    """The verify command actually used, and where it came from."""
    return "pytest -q"


def spend(budget):
    """Count the tokens a run is permitted to consume before compaction."""
    return budget * 2
'''


def test_a_probe_does_not_contain_the_name_it_is_looking_for() -> None:
    """Otherwise it measures whether a retriever can match a string to itself."""
    probes = build_probes(chunk_source("core/verify.py", SOURCE))

    probe = next(p for p in probes if "resolve_verify" in p.target or "spend" not in p.query)
    assert "resolve" not in probe.query.lower()
    assert "verify" not in probe.query.lower()


def test_the_probe_keeps_enough_words_to_be_a_question() -> None:
    probes = build_probes(chunk_source("core/verify.py", SOURCE))
    assert probes and all(len(p.query.split()) >= 4 for p in probes)


def test_an_undocumented_symbol_produces_no_probe() -> None:
    # There is nothing to ask about it that is not its own name.
    assert build_probes(chunk_source("x.py", "def bare():\n    return 1\n")) == []


def test_the_indexed_text_loses_the_docstring() -> None:
    """The fix for the 1.00 run: what is indexed is the CODE.

    Somebody describes a behaviour and the implementation does not contain their description — that
    is the real shape of the task, and leaving the docstring in makes the corpus hold the question.
    """
    chunk = next(c for c in chunk_source("core/verify.py", SOURCE) if c.symbol == "resolve_verify")

    stripped = _without_docstring(chunk)

    assert "actually used" not in stripped.text
    assert "def resolve_verify" in stripped.text
    assert 'return "pytest -q"' in stripped.text
    # Identity and span are untouched: the chunk has to stay the row it was stored under.
    assert stripped.ident == chunk.ident


def test_stripping_a_one_line_docstring_leaves_the_body() -> None:
    chunk = Chunk("a.py", "f", "function", 1, 3, 'def f():\n    """One line."""\n    return 1\n')
    assert "One line" not in _without_docstring(chunk).text
    assert "return 1" in _without_docstring(chunk).text


def test_a_chunk_that_is_only_a_docstring_keeps_its_text() -> None:
    # Removing everything would index an empty document, which is worse than indexing prose: an
    # empty row can never be retrieved and silently shrinks the corpus.
    chunk = Chunk("a.py", "f", "function", 1, 2, 'def f():\n    """Only this."""\n')
    assert _without_docstring(chunk).text.strip()


def test_the_bench_reports_a_ceiling_without_an_embedder(tmp_path: Path) -> None:
    """`headroom` needs no provider and bounds everything a semantic layer could add.

    Measuring it first is what makes this bench able to say the whole layer is not worth its cost.
    """
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "verify.py").write_text(SOURCE, encoding="utf-8")

    report = run_rag_bench(tmp_path, index_path=tmp_path / "i.sqlite3")

    assert report.probes > 0
    assert 0.0 <= report.keyword_recall <= 1.0
    assert report.headroom == 1.0 - report.keyword_recall
    # Absent, not zero: an embedder that was never called did not fail.
    assert report.vector_recall is None
    assert report.hybrid_recall is None
    assert any("no embedder" in note for note in report.notes)


def test_the_bench_measures_the_semantic_side_when_given_an_embedder(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "verify.py").write_text(SOURCE, encoding="utf-8")
    vocabulary = ["command", "tokens", "run", "compaction", "permitted", "consume", "count"]

    def embed(texts: list[str]) -> list[list[float]]:
        return [[float(t.lower().count(w)) for w in vocabulary] for t in texts]

    report = run_rag_bench(tmp_path, index_path=tmp_path / "i.sqlite3", embed=embed)

    assert report.vector_recall is not None
    assert report.hybrid_recall is not None
    # The fusion may not beat either side on a two-function corpus; what it must never do is lose
    # everything both retrievers found.
    assert report.hybrid_recall >= min(report.keyword_recall, report.vector_recall)


def test_an_empty_repository_says_so_rather_than_dividing_by_zero(tmp_path: Path) -> None:
    report = run_rag_bench(tmp_path, index_path=tmp_path / "i.sqlite3")
    assert report.probes == 0
    assert any("no documented symbols" in note for note in report.notes)
