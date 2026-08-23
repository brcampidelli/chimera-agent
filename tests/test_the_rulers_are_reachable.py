"""The measurements this project tells you to take, from a place you can take them.

`chimera/rag/__init__.py` points at `run_rag_bench` to say the retriever's existence is not a claim
that it helps. `chimera/evolution/reranker.py` says to measure with `run_reranker_ab` BEFORE putting
the reranker in a hot path. Both sentences were **prose**: neither module was exported from
`chimera.eval`, neither had a caller outside its own test, and no command ran either one. The advice
pointed at something you could not reach from the package that gave it — which is the pattern this
codebase keeps finding in itself, applied to its own rulers.

`selftest` is listed here too and was NOT orphaned: `bench/learning_lift` and `bench/local_lift`
both import it. It had no caller inside `chimera/`, which is a different and much weaker statement,
and saying so is the point — an audit that folds the two together produces a number nobody can act
on.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from chimera.cli.main import app

runner = CliRunner()


def test_the_rulers_import_from_the_package_that_recommends_them() -> None:
    # `chimera.eval` lazily exports twenty-odd siblings and exported none of these. Someone reading
    # the advice could not follow it without knowing the private module path.
    from chimera.eval import (
        RagReport,
        RerankerABReport,
        assert_discriminating,
        auc,
        run_rag_bench,
        run_reranker_ab,
    )

    assert callable(run_rag_bench)
    assert callable(run_reranker_ab)
    assert callable(assert_discriminating)
    assert callable(auc)
    assert RagReport is not None and RerankerABReport is not None


def test_the_rag_bench_runs_from_the_command_line(tmp_path: Path) -> None:
    corpus = tmp_path / "src"
    corpus.mkdir()
    (corpus / "a.py").write_text(
        'def add(a, b):\n    """Return the sum of two numbers."""\n    return a + b\n',
        encoding="utf-8",
    )
    (corpus / "b.py").write_text(
        'def slugify(text):\n    """Turn a title into a url-safe slug."""\n    return text.lower()\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["measure", "rag", str(corpus), "--max-probes", "5"])

    assert result.exit_code == 0, result.output
    assert "keyword" in result.output


def test_it_says_not_measured_rather_than_zero_without_an_embedder(tmp_path: Path) -> None:
    """The distinction the report's own comment insists on, carried through to the screen.

    `vector_recall` is None when no embedder was supplied — never 0.0, "which would read as the
    vectors failed". A CLI that formatted None as 0.000 would undo that at the last step, which is
    where a number stops being data and becomes a conclusion.
    """
    corpus = tmp_path / "src"
    corpus.mkdir()
    (corpus / "a.py").write_text(
        'def add(a, b):\n    """Return the sum of two numbers."""\n    return a + b\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["measure", "rag", str(corpus), "--max-probes", "3"])

    assert "not measured" in result.output
    assert "0.000" not in result.output.split("hybrid")[-1]


def test_the_reranker_ab_runs_from_the_command_line(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "\n".join(
            json.dumps({"query": q, "text": t, "success": s})
            for q, t, s in [
                ("fix the parser", "parser tokeniser rewrite", True),
                ("fix the parser", "unrelated css tweak", False),
                ("add a cache", "lru cache for lookups", True),
                ("add a cache", "rename a variable", False),
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["measure", "reranker", str(corpus)])

    assert result.exit_code == 0, result.output
    assert "auc" in result.output.lower()


def test_an_empty_corpus_refuses_rather_than_reporting_a_number(tmp_path: Path) -> None:
    # An AUC over nothing is 0.5 by construction, which is exactly the value that means "this
    # reranker is a coin flip". Printing it for an empty file would be the worst possible answer.
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("\n", encoding="utf-8")

    result = runner.invoke(app, ["measure", "reranker", str(corpus)])

    assert result.exit_code == 1
    assert "empty" in result.output.lower()
