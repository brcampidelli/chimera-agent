"""`edit_batch` — counted, multi-file, all-or-nothing.

The test that matters most here is `test_the_write_region_is_checked_per_path`. Every other writer
in this module takes a single `path` argument and the guards read that argument; a multi-file payload
has no top-level `path`, so a guard written outside the loop would simply not run and a fenced tool
would silently become an unfenced one. That is not hypothetical — it is the specific trap flagged
when this tool was proposed, and it is why the check lives inside phase 1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.tools.edit import EditBatchTool
from chimera.tools.workspace import PathEscapesWorkspaceError
from chimera.tools.write_region import WriteRegion


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\ny = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("from pkg.a import x\n", encoding="utf-8")
    (tmp_path / "pkg" / "c.py").write_text("from pkg.a import x\n", encoding="utf-8")
    return tmp_path


def test_one_call_edits_several_files(ws: Path) -> None:
    out = EditBatchTool(ws).run(edits=[
        {"path": "pkg/b.py", "old": "import x", "new": "import z"},
        {"path": "pkg/c.py", "old": "import x", "new": "import z"},
    ])
    assert "2 file(s)" in out
    assert (ws / "pkg" / "b.py").read_text(encoding="utf-8") == "from pkg.a import z\n"
    assert (ws / "pkg" / "c.py").read_text(encoding="utf-8") == "from pkg.a import z\n"


def test_a_wrong_count_aborts_the_whole_batch(ws: Path) -> None:
    """The counted half. `edit_file`'s replace_all reports the number *after* replacing; this
    refuses before writing anything, in any file."""
    out = EditBatchTool(ws).run(edits=[
        {"path": "pkg/b.py", "old": "import x", "new": "import z"},
        {"path": "pkg/a.py", "old": "= 1", "new": "= 2", "count": 1},  # actually occurs twice
    ])
    assert "NOTHING was written" in out
    assert "occurs 2 time(s), expected 1" in out
    assert (ws / "pkg" / "b.py").read_text(encoding="utf-8") == "from pkg.a import x\n", (
        "the first edit was valid and must NOT have landed — that is what all-or-nothing means"
    )


def test_the_expected_count_is_honoured_when_it_is_right(ws: Path) -> None:
    out = EditBatchTool(ws).run(edits=[{"path": "pkg/a.py", "old": "= 1", "new": "= 2", "count": 2}])
    assert "1 file(s)" in out
    assert (ws / "pkg" / "a.py").read_text(encoding="utf-8") == "x = 2\ny = 2\n"


def test_the_write_region_is_checked_per_path(ws: Path) -> None:
    """The trap this tool was warned about, pinned.

    Move the guard out of the loop and this goes red while everything else stays green.
    """
    region = WriteRegion(["pkg/b.py"], ws)
    out = EditBatchTool(ws, write_region=region).run(edits=[
        {"path": "pkg/b.py", "old": "import x", "new": "import z"},
        {"path": "pkg/c.py", "old": "import x", "new": "import z"},  # outside the region
    ])
    assert "batch aborted" in out and "pkg/c.py" in out
    assert (ws / "pkg" / "b.py").read_text(encoding="utf-8") == "from pkg.a import x\n"
    assert (ws / "pkg" / "c.py").read_text(encoding="utf-8") == "from pkg.a import x\n"


def test_the_never_writable_set_holds_with_no_region_declared(ws: Path) -> None:
    """`.git` is denied even when nobody declared a region — the default, and the case that matters."""
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    out = EditBatchTool(ws).run(edits=[{"path": ".git/config", "old": "[core]", "new": "[evil]"}])
    assert "batch aborted" in out
    assert (ws / ".git" / "config").read_text(encoding="utf-8") == "[core]\n"


def test_two_edits_to_one_file_compose(ws: Path) -> None:
    """Counting the second against the ORIGINAL would refuse a legitimate pair."""
    out = EditBatchTool(ws).run(edits=[
        {"path": "pkg/a.py", "old": "x = 1", "new": "x = 9"},
        {"path": "pkg/a.py", "old": "y = 1", "new": "y = 8"},
    ])
    assert "1 file(s)" in out
    assert (ws / "pkg" / "a.py").read_text(encoding="utf-8") == "x = 9\ny = 8\n"


def test_a_missing_file_aborts_before_any_write(ws: Path) -> None:
    out = EditBatchTool(ws).run(edits=[
        {"path": "pkg/b.py", "old": "import x", "new": "import z"},
        {"path": "pkg/nope.py", "old": "a", "new": "b"},
    ])
    assert "file not found" in out
    assert (ws / "pkg" / "b.py").read_text(encoding="utf-8") == "from pkg.a import x\n"


def test_empty_and_malformed_payloads_are_refused(ws: Path) -> None:
    tool = EditBatchTool(ws)
    assert "non-empty array" in tool.run(edits=[])
    assert "no 'path'" in tool.run(edits=[{"old": "a", "new": "b"}])
    assert "empty 'old'" in tool.run(edits=[{"path": "pkg/a.py", "old": "", "new": "b"}])
    assert "nothing to change" in tool.run(edits=[{"path": "pkg/a.py", "old": "a", "new": "a"}])
    assert "must be >= 1" in tool.run(
        edits=[{"path": "pkg/a.py", "old": "x", "new": "y", "count": 0}]
    )


def test_a_path_outside_the_workspace_raises_and_writes_nothing(ws: Path) -> None:
    """Escaping the workspace raises rather than returning a string — the same as every other
    writer here, and the agent loop turns tool exceptions into observations (`agent.py:597`).

    What matters for a *batch* is the invariant, not the channel: the jail is checked in phase 1,
    which performs no writes, so a valid earlier edit in the same payload cannot have landed.
    """
    with pytest.raises(PathEscapesWorkspaceError):
        EditBatchTool(ws).run(edits=[
            {"path": "pkg/b.py", "old": "import x", "new": "import z"},
            {"path": "../escape.py", "old": "a", "new": "b"},
        ])
    assert not (ws.parent / "escape.py").exists()
    assert (ws / "pkg" / "b.py").read_text(encoding="utf-8") == "from pkg.a import x\n"
