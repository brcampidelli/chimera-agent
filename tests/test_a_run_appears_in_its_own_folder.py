"""A run must be listed under the folder it ran in, however that folder was spelled.

The filter was ``receipt.workspace == workspace`` — raw string equality. On Windows one folder has
several spellings, so a run started at ``C:/Users/.../rc43-teste`` returned nothing for
``C:\\Users\\...\\rc43-teste``: measured in the app, two runs were recorded correctly and were
invisible in the list for their own project, which reads as the app having lost them.

Everything here is free: no model call, no network, and no filesystem lookup — `same_folder` is
deliberately pure string work, so a receipt for a folder that has since been deleted still matches.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from chimera.api.runs import load_runs, same_folder

WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="separator and case folding are Windows path semantics"
)


def _write(home: Path, *workspaces: str) -> Path:
    path = home / "runs.jsonl"
    rows: list[dict[str, Any]] = [
        {"ts": f"2026-08-30T0{i}:00:00+00:00", "task": "t", "success": True,
         "workspace": ws, "attempts": []}
        for i, ws in enumerate(workspaces)
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# --- the measured bug --------------------------------------------------------------------------


@WINDOWS_ONLY
def test_forward_slashes_find_a_run_recorded_with_backslashes(tmp_path: Path) -> None:
    """The exact failure seen in the app: 0 runs one way, 2 the other, same folder."""
    log = _write(tmp_path, r"C:\Users\brcam\Desktop\chimera-sim\rc43-teste")

    found = load_runs(log, workspace="C:/Users/brcam/Desktop/chimera-sim/rc43-teste")

    assert len(found) == 1


@WINDOWS_ONLY
def test_case_does_not_hide_a_run_on_windows(tmp_path: Path) -> None:
    log = _write(tmp_path, r"C:\Users\brcam\Projects\Site")

    assert len(load_runs(log, workspace=r"c:\users\brcam\projects\site")) == 1


def test_a_trailing_separator_is_the_same_folder(tmp_path: Path) -> None:
    log = _write(tmp_path, str(tmp_path / "proj"))

    assert len(load_runs(log, workspace=str(tmp_path / "proj") + os.sep)) == 1


def test_a_redundant_step_is_the_same_folder(tmp_path: Path) -> None:
    log = _write(tmp_path, str(tmp_path / "proj"))

    assert len(load_runs(log, workspace=str(tmp_path / "sub" / ".." / "proj"))) == 1


# --- what must still NOT match -----------------------------------------------------------------


def test_a_different_folder_is_still_a_different_folder(tmp_path: Path) -> None:
    """The normalisation must not turn the filter into "everything"."""
    log = _write(tmp_path, str(tmp_path / "a"), str(tmp_path / "b"))

    assert len(load_runs(log, workspace=str(tmp_path / "a"))) == 1


def test_a_sibling_whose_name_is_a_prefix_does_not_match(tmp_path: Path) -> None:
    """``site`` must not collect ``site-old`` — a substring check would, and reads as correct."""
    log = _write(tmp_path, str(tmp_path / "site-old"))

    assert load_runs(log, workspace=str(tmp_path / "site")) == []


def test_a_receipt_with_no_workspace_is_never_placed_in_one(tmp_path: Path) -> None:
    """Documented rule, and now load-bearing: ``normpath("")`` is ``"."``, so an unattributed
    receipt WOULD answer to a filter of ``.`` if it were normalised like any other path."""
    log = _write(tmp_path, "")

    assert load_runs(log, workspace=".") == []
    assert load_runs(log, workspace=os.getcwd()) == []


def test_asking_for_no_project_still_finds_the_unattributed_runs(tmp_path: Path) -> None:
    """``""`` is a query, not "no filter" — the only way to reach a receipt written before the
    workspace field existed. Normalising it as a path would have silently removed that door, which
    is how the path comparison first broke this."""
    log = _write(tmp_path, "", str(tmp_path / "proj"))

    found = load_runs(log, workspace="")

    assert len(found) == 1
    assert found[0].workspace == ""


def test_no_filter_still_returns_everything(tmp_path: Path) -> None:
    log = _write(tmp_path, str(tmp_path / "a"), "", str(tmp_path / "b"))

    assert len(load_runs(log)) == 3


# --- the comparison itself ---------------------------------------------------------------------


def test_it_does_not_read_the_filesystem(tmp_path: Path) -> None:
    """A receipt for a folder that has since been deleted must still be findable — `resolve()`
    would make the list depend on which projects still exist on disk."""
    gone = str(tmp_path / "deleted-long-ago")

    assert same_folder(gone, gone)
    assert len(load_runs(_write(tmp_path, gone), workspace=gone)) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX keeps case significant")
def test_case_still_separates_two_folders_on_posix() -> None:
    """``normcase`` is the identity on POSIX, and it must stay that way: there, ``/tmp/Site`` and
    ``/tmp/site`` really are two folders and folding them would merge two projects' runs."""
    assert not same_folder("/tmp/Site", "/tmp/site")
