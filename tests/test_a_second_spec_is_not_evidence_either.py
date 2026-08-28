"""One spec was excluded from its own scan. Two specs, and the project passed with nothing built.

The drift gate searches every `contains` regex across every text file in the workspace. A spec
repeats, in its `text` and `target` fields, the very words those regexes look for — so a spec
sitting in the folder it judges is evidence for itself. That was found and fixed by excluding
`spec.source`: the exact file being checked.

Measured on the shipped rc39, in an empty folder:

    one spec  ................. 0/5 satisfied, aligned=False   <- the fix working
    the same spec, copied ..... 5/5 satisfied, aligned=True    <- with no code written

Only the file being checked was skipped, so any *other* spec in the folder was still scanned and
still contained the answers. And the second file is not hypothetical: the drafting flow derives the
filename from the project slug, so redrafting a project with a slightly different name produces it
through the ordinary path — no unusual act required, just changing your mind about the name.

The rule is now "a file that is itself a spec is not evidence that a spec is satisfied", decided by
shape rather than filename, because a rule that trusts the name is a rule a rename defeats.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from chimera.governance.drift import Spec, check_drift, load_spec

_SPEC = {
    "name": "padaria aurora",
    "requirements": [
        {"id": "r1", "text": "a página inicial existe", "check": "contains", "target": "Padaria Aurora"},
        {"id": "r2", "text": "há um cardápio", "check": "contains", "target": "cardapio"},
        {"id": "r3", "text": "há um formulário de contato", "check": "contains", "target": "contato"},
    ],
}


def _write(path: Path, data: object) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_one_spec_does_not_satisfy_itself(tmp_path: Path) -> None:
    """The control, and the fix that already existed: an empty project satisfies nothing."""
    spec = load_spec(_write(tmp_path / "padaria.spec.yaml", _SPEC))
    report = check_drift(spec, tmp_path)
    assert report.aligned is False
    assert [r.satisfied for r in report.results] == [False, False, False]


def test_a_second_spec_does_not_satisfy_the_first(tmp_path: Path) -> None:
    """The defect. Same empty project, one extra file, and it used to report 3/3 aligned."""
    spec = load_spec(_write(tmp_path / "padaria.spec.yaml", _SPEC))
    _write(tmp_path / "padaria-aurora-doces.spec.yaml", _SPEC)  # what redrafting produces

    report = check_drift(spec, tmp_path)
    assert report.aligned is False, "a copy of the spec is still being read as the work"
    assert [r.satisfied for r in report.results] == [False, False, False]


def test_a_spec_under_any_name_is_still_a_spec(tmp_path: Path) -> None:
    """Shape, not filename. Excluding `*.spec.yaml` would have been the easy rule and an
    unlucky rename would walk straight through it."""
    spec = load_spec(_write(tmp_path / "padaria.spec.yaml", _SPEC))
    _write(tmp_path / "notes.yml", _SPEC)

    assert check_drift(spec, tmp_path).aligned is False


def test_the_spec_being_checked_need_not_live_in_the_workspace(tmp_path: Path) -> None:
    """The original guard still holds when the spec is kept outside the folder it judges."""
    elsewhere, project = tmp_path / "elsewhere", tmp_path / "project"
    elsewhere.mkdir()
    project.mkdir()
    spec = load_spec(_write(elsewhere / "s.yaml", _SPEC))

    assert check_drift(spec, project).aligned is False


def test_real_work_still_satisfies_the_spec(tmp_path: Path) -> None:
    """The control that matters most: a rule which excluded too much would make the gate
    unsatisfiable, and an unsatisfiable gate is not a stricter gate — it is a broken one."""
    spec = load_spec(_write(tmp_path / "padaria.spec.yaml", _SPEC))
    (tmp_path / "index.html").write_text(
        "<h1>Padaria Aurora</h1><a href='#cardapio'>cardapio</a><form id='contato'></form>",
        encoding="utf-8",
    )
    report = check_drift(spec, tmp_path)
    assert report.aligned is True
    assert all(r.satisfied for r in report.results)


def test_ordinary_project_yaml_is_still_scanned(tmp_path: Path) -> None:
    """The other side of the same risk. Only a document shaped like a spec leaves the scan; a
    config file that happens to be YAML is still evidence, or the gate would quietly stop
    reading half of a real project."""
    spec = Spec(
        name="s",
        requirements=[{"id": "r1", "check": "contains", "target": "postgres"}],  # type: ignore[list-item]
    )
    _write(tmp_path / "docker-compose.yml", {"services": {"db": {"image": "postgres:16"}}})

    assert check_drift(spec, tmp_path).aligned is True


def test_a_yaml_that_is_not_a_spec_is_not_mistaken_for_one(tmp_path: Path) -> None:
    """`name` plus a `requirements` list is not enough — the entries must carry what the gate
    reads. A python project's own metadata should never be silently excluded."""
    from chimera.governance.drift import _is_spec_shaped

    assert _is_spec_shaped({"name": "pkg", "requirements": ["fastapi>=0.1", "pydantic"]}) is False
    assert _is_spec_shaped({"name": "pkg", "requirements": []}) is False
    assert _is_spec_shaped({"requirements": [{"check": "contains", "target": "x"}]}) is False
    assert _is_spec_shaped({"name": "s", "requirements": [{"check": "contains", "target": "x"}]}) is True
