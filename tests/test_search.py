"""Cross-file search (:mod:`chimera.core.search`).

Both engines are driven against the same real workspace, and every behavioural test runs twice —
once through ripgrep and once through the fallback. That is the point: the two are allowed to differ
in speed and in what they skip, and they are not allowed to differ in what they ANSWER. A fallback
that quietly returns different hits is worse than no fallback, because nobody checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.search import (
    MAX_HITS,
    SearchResult,
    _search_python,
    _search_ripgrep,
    ripgrep_available,
    search,
)

ENGINES = [
    pytest.param(
        _search_ripgrep,
        marks=pytest.mark.skipif(not ripgrep_available(), reason="ripgrep not installed"),
        id="ripgrep",
    ),
    pytest.param(_search_python, id="python"),
]


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A small workspace with the shapes a real one has: nesting, noise, and a binary."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "import os\n\n\ndef connect():\n    return os.environ['DSN']\n", encoding="utf-8"
    )
    (tmp_path / "src" / "util.ts").write_text(
        "export function connect() {\n  return 1;\n}\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Demo\n\nCall connect() first.\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("connect();\n" * 50, encoding="utf-8")
    # A real binary, NUL bytes included. ripgrep decides by CONTENT and the fallback by EXTENSION,
    # so a "binary" with no NUL in it is a file ripgrep is right to search — a fixture that would
    # have made the two engines disagree for a reason that is about neither of them.
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00" + b"connect\x00" * 10)
    return tmp_path


def _run(engine, query: str, repo: Path, **kwargs) -> SearchResult:
    return engine(query, repo, regex=kwargs.pop("regex", False),
                  case_sensitive=kwargs.pop("case_sensitive", False),
                  glob=kwargs.pop("glob", ""))


@pytest.mark.parametrize("engine", ENGINES)
def test_it_finds_the_thing_and_says_where(engine, repo: Path) -> None:
    result = _run(engine, "connect", repo)

    paths = {hit.path for hit in result.hits}
    assert "src/app.py" in paths
    assert "src/util.ts" in paths
    assert "README.md" in paths
    # Forward slashes on every platform: the same shape the file tree and the editor's URL use, so a
    # hit can be turned into "open this file" without a second normalisation nobody remembers.
    assert all("\\" not in hit.path for hit in result.hits)
    assert all(hit.line > 0 for hit in result.hits)


@pytest.mark.parametrize("engine", ENGINES)
def test_it_carries_the_line_and_where_the_match_sits_in_it(engine, repo: Path) -> None:
    # Offsets travel so the panel highlights what actually matched. Re-searching the line in the
    # browser is how a case-insensitive or regex query gets highlighted in the wrong place.
    hit = next(h for h in _run(engine, "connect", repo).hits if h.path == "src/util.ts")
    assert "connect" in hit.text
    assert hit.text[hit.start : hit.end].lower() == "connect"


@pytest.mark.parametrize("engine", ENGINES)
def test_it_ignores_case_by_default_and_obeys_when_told(engine, repo: Path) -> None:
    assert _run(engine, "CONNECT", repo).hits, "a default search should be case-insensitive"
    assert not _run(engine, "CONNECT", repo, case_sensitive=True).hits


@pytest.mark.parametrize("engine", ENGINES)
def test_a_plain_query_is_not_a_regex(engine, repo: Path) -> None:
    """`connect()` is what someone types when they mean `connect()`.

    Treated as a pattern it means "connect with an empty group" and matches every bare `connect` —
    so the naive reading returns MORE hits than the literal one, which reads as working.
    """
    literal = _run(engine, "connect()", repo)
    # `def connect():` contains the literal `connect()` too — an expectation that excluded it was
    # wrong about the fixture, not about the behaviour. What the literal search must NOT do is match
    # `os.environ` and the other bare words a naive regex reading of `()` would sweep in.
    assert {hit.path for hit in literal.hits} == {"src/app.py", "src/util.ts", "README.md"}
    assert all("connect()" in hit.text for hit in literal.hits)


@pytest.mark.parametrize("engine", ENGINES)
def test_a_regex_is_available_when_asked_for(engine, repo: Path) -> None:
    result = _run(engine, r"def \w+\(", repo, regex=True)
    assert {hit.path for hit in result.hits} == {"src/app.py"}


@pytest.mark.parametrize("engine", ENGINES)
def test_a_glob_narrows_the_search(engine, repo: Path) -> None:
    result = _run(engine, "connect", repo, glob="*.ts")
    assert {hit.path for hit in result.hits} == {"src/util.ts"}


@pytest.mark.parametrize("engine", ENGINES)
def test_binaries_are_not_searched(engine, repo: Path) -> None:
    # A PNG containing the bytes of the word is not a hit anybody wants, and printing its "line"
    # into a panel is how a terminal ends up full of control characters.
    assert "logo.png" not in {hit.path for hit in _run(engine, "connect", repo).hits}


@pytest.mark.parametrize("engine", ENGINES)
def test_the_noise_directory_is_skipped(engine, repo: Path) -> None:
    """`node_modules` alone would fill the result cap and push the real hits out.

    ripgrep normally gets this from `.gitignore` — and a workspace that is not a git repository has
    no such file, which is exactly the case this fixture is. Measured before the fix: fifty hits
    from vendored code. Both engines now exclude the same list explicitly.
    """
    assert not [h for h in _run(engine, "connect", repo).hits if h.path.startswith("node_modules")]


@pytest.mark.parametrize("engine", ENGINES)
def test_no_match_is_an_empty_answer_and_not_an_error(engine, repo: Path) -> None:
    result = _run(engine, "definitely-not-in-this-repository", repo)
    assert result.hits == []
    assert result.error == ""
    assert result.timed_out is False


@pytest.mark.parametrize("engine", ENGINES)
def test_a_flood_of_matches_is_capped_and_says_so(engine, tmp_path: Path) -> None:
    """A capped result that looks complete is how someone concludes a symbol is unused."""
    (tmp_path / "big.txt").write_text("needle\n" * (MAX_HITS + 200), encoding="utf-8")
    result = _run(engine, "needle", tmp_path)

    assert len(result.hits) <= MAX_HITS
    assert result.capped is True


@pytest.mark.parametrize("engine", ENGINES)
def test_one_hit_per_line(engine, repo: Path) -> None:
    # Both engines report a line once. Differing here would make the two engines disagree about how
    # many results a search has, which is the one thing they must not do.
    (repo / "src" / "twice.py").write_text("connect(); connect()\n", encoding="utf-8")
    hits = [h for h in _run(engine, "connect", repo).hits if h.path == "src/twice.py"]
    assert len(hits) == 1


def test_the_public_entry_point_names_the_engine_that_answered(repo: Path) -> None:
    """The whole reason a fallback is allowed to exist.

    Reporting "not available" and stopping is honest and useless; falling back silently is useful
    and dishonest. Naming the engine is what makes the third option possible — the caller can say
    the search was the simpler one.
    """
    result = search("connect", repo)
    assert result.engine in ("ripgrep", "python")
    assert result.engine == ("ripgrep" if ripgrep_available() else "python")
    assert result.hits


def test_an_empty_query_searches_for_nothing(repo: Path) -> None:
    # Rather than matching every line in the repository, which is what an empty pattern means to
    # both engines and what a search box sends on every keystroke before the first character.
    assert search("", repo).hits == []


def test_a_workspace_that_is_not_there_is_reported(tmp_path: Path) -> None:
    result = search("x", tmp_path / "nope")
    assert result.hits == []
    assert "workspace" in result.error


def test_an_invalid_regex_is_reported_rather_than_raised(repo: Path) -> None:
    result = search("(unclosed", repo, regex=True)
    assert result.hits == []
    assert result.error  # the message belongs to the user who typed it, not to a traceback


def test_a_long_line_is_clipped(tmp_path: Path) -> None:
    # A minified bundle is one line of two megabytes; a panel that renders it is a panel that hangs.
    (tmp_path / "bundle.js").write_text("x" * 5000 + "needle" + "y" * 5000, encoding="utf-8")
    hits = search("needle", tmp_path).hits
    assert hits and len(hits[0].text) <= 400
