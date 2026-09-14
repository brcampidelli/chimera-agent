"""The registry of projects, and the two distinctions it exists to keep.

The first is **absent against empty**. Re-registering a project you have already named must not wipe
the name, and clearing a name must be possible — which are different requests that look identical in
JSON unless something keeps them apart. `register(path)` says nothing about the alias;
`register(path, "")` clears it. Collapsing those was how the client-side version would have lost every name the first
time a folder was re-added.

The second is **a bookmark against the work**. Forgetting a project here removes a row from a list.
It does not touch the conversations filed under that workspace and it does not touch the folder —
the sibling test `test_delete_code_project` covers the route that does remove transcripts, and the
whole reason these two live at different paths is that a person tidying a list must not lose work.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.core.code_projects import MAX_PROJECTS, CodeProjectRegistry

LOJA = "C:\\Users\\alguem\\loja"
BLOG = "C:\\Users\\alguem\\blog"


def _registry(tmp_path: Path) -> CodeProjectRegistry:
    return CodeProjectRegistry(tmp_path / "code_projects.json")


def test_an_empty_registry_is_an_empty_list_not_an_error(tmp_path: Path) -> None:
    """A fresh install has no file, and that is the normal case rather than a failure."""
    assert _registry(tmp_path).entries() == []


def test_projects_come_back_in_the_order_they_were_added(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    registry.register(BLOG)
    assert [row.path for row in registry.entries()] == [LOJA, BLOG]


def test_registering_the_same_path_twice_does_not_list_it_twice(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    rows = registry.register(LOJA)
    assert [row.path for row in rows] == [LOJA]


def test_re_registering_a_named_project_keeps_the_name(tmp_path: Path) -> None:
    """The distinction this file exists for: no alias in the request means *say nothing about it*."""
    registry = _registry(tmp_path)
    registry.register(LOJA, "a loja")
    rows = registry.register(LOJA)
    assert rows[0].alias == "a loja"


def test_an_empty_alias_clears_the_name(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA, "a loja")
    rows = registry.register(LOJA, "")
    assert rows[0].alias == ""


def test_forgetting_one_leaves_the_others(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    registry.register(BLOG)
    assert [row.path for row in registry.remove(LOJA)] == [BLOG]


def test_forgetting_something_never_registered_is_not_an_error(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    assert [row.path for row in registry.remove(BLOG)] == [LOJA]


def test_a_blank_path_is_not_a_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="path"):
        _registry(tmp_path).register("   ")


def test_the_number_of_projects_is_bounded(tmp_path: Path) -> None:
    """Not a judgement about how many checkouts anyone has — a bound on what a looping client can
    grow the file to, since nothing else here limits how often a client may POST."""
    registry = _registry(tmp_path)
    for index in range(MAX_PROJECTS):
        registry.register(f"C:\\p\\{index}")
    with pytest.raises(ValueError, match="at most"):
        registry.register("C:\\p\\one-too-many")


def test_an_unreadable_file_is_kept_rather_than_overwritten(tmp_path: Path) -> None:
    """A project list is the one thing here the machine cannot reconstruct — the person who typed it
    is the only copy. So a parse failure moves the bytes aside instead of reading as empty and
    letting the next write destroy them."""
    path = tmp_path / "code_projects.json"
    path.write_text("{not json at all", encoding="utf-8")
    registry = CodeProjectRegistry(path)

    assert registry.entries() == []
    assert path.with_suffix(".json.corrupt").read_text(encoding="utf-8") == "{not json at all"

    registry.register(LOJA)
    assert [row.path for row in registry.entries()] == [LOJA]


def test_a_shape_nobody_wrote_reads_as_empty(tmp_path: Path) -> None:
    """Valid JSON of the wrong shape is not corruption — it is a file someone edited by hand. Rows
    that are not rows are skipped, and the ones that are survive."""
    path = tmp_path / "code_projects.json"
    path.write_text(
        json.dumps({"projects": ["not a row", {"path": LOJA, "alias": 7}, {"alias": "x"}]}),
        encoding="utf-8",
    )
    rows = CodeProjectRegistry(path).entries()
    assert [(row.path, row.alias) for row in rows] == [(LOJA, "")]


def test_the_write_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["code_projects.json"]


def test_the_file_is_readable_by_a_person(tmp_path: Path) -> None:
    """Seeding this list by hand is a supported thing to do — it is how six projects reached an
    installation that had never been used — so the file stays indented JSON with the paths
    unescaped."""
    registry = _registry(tmp_path)
    registry.register("C:\\Users\\alguém\\Área de Trabalho\\loja", "a loja")
    written = json.loads((tmp_path / "code_projects.json").read_text(encoding="utf-8"))
    assert written == {"projects": [{"path": "C:\\Users\\alguém\\Área de Trabalho\\loja",
                                     "alias": "a loja"}]}
