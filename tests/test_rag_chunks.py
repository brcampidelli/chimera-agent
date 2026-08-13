"""Cutting a repository into retrievable pieces (:mod:`chimera.rag.chunks`).

The unit of retrieval decides what retrieval can answer, so these tests are about boundaries: that
a symbol arrives whole, that its decorators come with it, that a class is indexed both as itself and
as its methods, and that a file which cannot be parsed still yields something rather than nothing.
"""

from __future__ import annotations

from pathlib import Path

from chimera.rag.chunks import MAX_CHUNK_CHARS, chunk_source, walk

SAMPLE = '''\
"""Module docstring."""

import os


def connect(dsn: str) -> str:
    """Open the connection."""
    return os.environ[dsn]


class Gateway:
    """Talks to the provider."""

    TIMEOUT = 30

    def send(self, body: str) -> str:
        """Send one request."""
        return body

    async def stream(self, body: str) -> str:
        return body
'''


def test_a_function_arrives_whole() -> None:
    """A window cut through the middle of a body gives the model code it cannot attribute."""
    chunks = chunk_source("app.py", SAMPLE)
    found = next(c for c in chunks if c.symbol == "connect")

    assert found.kind == "function"
    assert "def connect(dsn: str) -> str:" in found.text
    assert "return os.environ[dsn]" in found.text
    assert found.start_line < found.end_line


def test_a_class_is_indexed_as_itself_and_as_its_methods() -> None:
    """Both, deliberately.

    A question about the class's purpose is answered by its header and docstring; a question about
    one behaviour is answered by one method. Indexing only the class buries the method in an
    average; indexing only the methods loses the thing that says what they are for.
    """
    symbols = {c.symbol: c for c in chunk_source("app.py", SAMPLE)}

    assert symbols["Gateway"].kind == "class"
    assert "Talks to the provider." in symbols["Gateway"].text
    assert "TIMEOUT = 30" in symbols["Gateway"].text
    assert "Gateway.send" in symbols
    assert "Gateway.stream" in symbols  # async counts


def test_the_class_header_stops_before_its_methods() -> None:
    # Otherwise the class chunk is the whole file again and its vector is the average of everything.
    header = next(c for c in chunk_source("app.py", SAMPLE) if c.symbol == "Gateway")
    assert "def send" not in header.text


def test_decorators_come_with_the_function() -> None:
    """`@app.post("/api/x")` is most of what makes the function below it findable by someone asking
    about routes — and it sits ABOVE the line the parser calls the definition."""
    source = '@app.post("/api/things")\ndef create_thing():\n    return 1\n'
    chunk = next(c for c in chunk_source("api.py", source) if c.symbol == "create_thing")

    assert '@app.post("/api/things")' in chunk.text
    assert chunk.start_line == 1


def test_a_file_that_does_not_parse_still_yields_content() -> None:
    """A syntax error is a fact about one file, not a reason to make it unfindable."""
    chunks = chunk_source("broken.py", "def oops(:\n    this is not python\n" * 5)

    assert chunks
    assert all(c.kind == "window" for c in chunks)


def test_a_module_with_no_symbols_is_windowed() -> None:
    # A settings file or a script: real content, no definitions.
    chunks = chunk_source("settings.py", "DEBUG = True\nHOSTS = ['a', 'b']\n")
    assert chunks and chunks[0].kind == "window"


def test_a_non_python_file_is_windowed_and_says_so() -> None:
    """A TypeScript chunk cut by line count is not the same kind of object as a Python chunk cut by
    span, and a caller that cannot tell them apart will reason about one as if it were the other."""
    chunks = chunk_source("app.ts", "export function hi() {\n  return 1;\n}\n")
    assert chunks and chunks[0].kind == "window"
    assert chunks[0].symbol == ""


def test_windows_overlap_so_a_straddling_match_survives_somewhere() -> None:
    lines = "".join(f"line {i}\n" for i in range(1, 200))
    chunks = chunk_source("big.txt.ts", lines)

    assert len(chunks) > 1
    # The second window starts before the first one ended.
    assert chunks[1].start_line <= chunks[0].end_line


def test_an_enormous_symbol_is_split_rather_than_stored_whole() -> None:
    """A three-thousand-line generated class is a symbol by the parser's reckoning and a haystack by
    every other measure."""
    body = "".join(f"    x{i} = {i}\n" for i in range(MAX_CHUNK_CHARS // 8))
    chunks = chunk_source("huge.py", f"def enormous():\n{body}")

    assert len(chunks) > 1
    assert all(len(c.text) <= MAX_CHUNK_CHARS * 2 for c in chunks)


def test_an_empty_file_yields_nothing() -> None:
    assert chunk_source("empty.py", "   \n\n") == []


def test_the_identifier_is_stable_across_runs() -> None:
    # The index reuses embeddings by id; an id that changed per run would re-embed everything on
    # every rebuild, which is the difference between a cache and a bill.
    first = chunk_source("app.py", SAMPLE)
    second = chunk_source("app.py", SAMPLE)
    assert [c.ident for c in first] == [c.ident for c in second]
    assert all(":" in c.ident and "-" in c.ident for c in first)


def test_the_label_carries_the_path_and_the_symbol() -> None:
    # It goes into the FTS text, so a query naming either one hits the chunk.
    chunk = next(c for c in chunk_source("src/app.py", SAMPLE) if c.symbol == "connect")
    assert "src/app.py" in chunk.label and "connect" in chunk.label


# --- walking a workspace ---------------------------------------------------------------------


def test_walk_indexes_source_and_skips_the_noise(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "README.md").write_text("# Title\n\nProse.\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("var x = 1;\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x00")

    paths = {c.path for c in walk(tmp_path)}

    assert "src/app.py" in paths
    assert "README.md" in paths
    assert not any(p.startswith("node_modules") for p in paths)
    assert "logo.png" not in paths


def test_walk_stops_at_its_file_budget(tmp_path: Path) -> None:
    """An index that takes minutes to build is an index nobody rebuilds, and a stale index answers
    confidently about code that has moved."""
    for i in range(20):
        (tmp_path / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")

    assert len({c.path for c in walk(tmp_path, max_files=5)}) == 5


def test_walk_skips_a_file_over_the_size_cap(tmp_path: Path) -> None:
    (tmp_path / "small.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "huge.py").write_text("x = 1\n" * 200_000, encoding="utf-8")

    paths = {c.path for c in walk(tmp_path, max_bytes=10_000)}

    assert paths == {"small.py"}
