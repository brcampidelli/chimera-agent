"""A full overwrite that does not parse is never what anyone meant.

Three write paths call `atomic_write_text` and none of them validated anything. The right scope is
narrower than "validate every write", and for a reason: a surgical edit may legitimately pass
through a state that does not parse — a half-applied rename, a function mid-move — while a COMPLETE
file that does not parse is a mistake by construction. Checking both would turn a normal editing
rhythm into a fight with the tool.

The cost of not checking is not cosmetic. A broken file is one the next step cannot import or test,
so the agent spends a whole verify cycle discovering something `ast.parse` knew for free — and on an
overwrite it has also destroyed the version that did parse.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.tools.files import WriteFileTool


def write(ws: Path, name: str, content: str, **kw: object) -> str:
    return WriteFileTool(ws).run(path=name, content=content, **kw)


def test_valid_python_is_written(tmp_path: Path) -> None:
    out = write(tmp_path, "ok.py", "def f() -> int:\n    return 1\n")
    assert "wrote" in out
    assert (tmp_path / "ok.py").exists()


def test_broken_python_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    out = write(tmp_path, "bad.py", "def f(:\n    return 1\n")
    assert out.startswith("error:")
    assert not (tmp_path / "bad.py").exists(), "the refusal must not leave a partial file"


def test_the_refusal_says_where(tmp_path: Path) -> None:
    """A message that only says "invalid" costs the agent a read to find out what.

    It is about to retry; the line number is the difference between a fix and a guess.
    """
    out = write(tmp_path, "bad.py", "x = 1\ny = (\n")
    assert "line" in out


def test_an_existing_good_file_survives_a_broken_overwrite(tmp_path: Path) -> None:
    """The expensive half. An overwrite destroys the version that worked.

    Without the check the agent replaces a parsing file with a non-parsing one, and the only way
    back is the checkpoint — if there is one.
    """
    target = tmp_path / "keep.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    write(tmp_path, "keep.py", "VALUE = (\n")
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_broken_json_is_refused_too(tmp_path: Path) -> None:
    out = write(tmp_path, "c.json", '{"a": 1,}')
    assert out.startswith("error:")
    assert not (tmp_path / "c.json").exists()


def test_valid_json_is_written(tmp_path: Path) -> None:
    write(tmp_path, "c.json", '{"a": 1}')
    assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8")) == {"a": 1}


def test_the_escape_hatch_writes_it_anyway(tmp_path: Path) -> None:
    """Not optional. Templates, fixtures and deliberately-broken examples are real files.

    A guard with no way past turns "the agent wrote something odd" into "the agent cannot do this
    at all", which is the more expensive failure of the two.
    """
    out = write(tmp_path, "fixture.py", "def broken(:\n", allow_invalid=True)
    assert "wrote" in out
    assert (tmp_path / "fixture.py").read_text(encoding="utf-8") == "def broken(:\n"


def test_formats_we_cannot_check_are_written_untouched(tmp_path: Path) -> None:
    """Only what the standard library checks for free. Everything else passes through.

    A guess at "valid" for Markdown, YAML or plain text would refuse real files, and a writer that
    refuses real files is worse than one that writes a broken one.
    """
    for name, content in (
        ("notes.md", "# Title\n\nnot { valid json ("),
        ("a.txt", "def f(:"),
        ("t.yaml", "a: [1, 2"),
        ("t.py.j2", "def {{ name }}(:\n"),
    ):
        assert "wrote" in write(tmp_path, name, content)
        assert (tmp_path / name).read_text(encoding="utf-8") == content


def test_an_empty_python_file_is_valid(tmp_path: Path) -> None:
    """`__init__.py` is the commonest file in a Python repository and it is usually empty."""
    assert "wrote" in write(tmp_path, "__init__.py", "")


def test_the_other_writers_are_deliberately_not_checked(tmp_path: Path) -> None:
    """`edit_file` may legitimately leave a file mid-change, and it stays that way.

    Pinned so the scope is a decision rather than an oversight: someone extending this to every
    write path would break the editing rhythm the tools exist to support.
    """
    from chimera.tools.edit import EditFileTool

    target = tmp_path / "m.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")
    out = EditFileTool(tmp_path).run(path="m.py", old="return 1", new="return (")
    assert "error" not in out.lower()
    assert "return (" in target.read_text(encoding="utf-8")
