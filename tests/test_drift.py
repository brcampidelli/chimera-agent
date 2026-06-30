"""Tests for the spec<->code drift gate (Spec Growth Engine)."""

from __future__ import annotations

from pathlib import Path

from chimera.governance.drift import Requirement, Spec, check_drift, load_spec


def test_defines_and_absent_aligned(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
    spec = Spec(
        name="s",
        requirements=[
            Requirement(id="has-greet", check="defines", target="greet"),
            Requirement(id="no-todo", check="absent", target="TODO"),
        ],
    )
    report = check_drift(spec, tmp_path)
    assert report.aligned is True
    assert all(r.satisfied for r in report.results)


def test_missing_definition_is_drift(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    spec = Spec(name="s", requirements=[Requirement(id="g", check="defines", target="greet")])
    report = check_drift(spec, tmp_path)
    assert report.aligned is False
    assert report.results[0].satisfied is False


def test_absent_fails_when_marker_present(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("# TODO: fix this\n", encoding="utf-8")
    spec = Spec(name="s", requirements=[Requirement(id="no-todo", check="absent", target="TODO")])
    assert check_drift(spec, tmp_path).aligned is False


def test_contains_check(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("API_VERSION = 'v2'\n", encoding="utf-8")
    spec = Spec(name="s", requirements=[Requirement(id="v", check="contains", target="API_VERSION")])
    assert check_drift(spec, tmp_path).aligned is True


def test_command_check(tmp_path: Path) -> None:
    ok = Spec(name="s", requirements=[Requirement(id="ok", check="command", target='python -c "exit(0)"')])
    assert check_drift(ok, tmp_path).aligned is True
    bad = Spec(
        name="s",
        requirements=[Requirement(id="bad", check="command", target='python -c "import sys; sys.exit(2)"')],
    )
    assert check_drift(bad, tmp_path).aligned is False


def test_non_required_drift_keeps_alignment(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    spec = Spec(
        name="s",
        requirements=[Requirement(id="opt", check="defines", target="missing", required=False)],
    )
    assert check_drift(spec, tmp_path).aligned is True


def test_load_spec(tmp_path: Path) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text(
        "name: demo\nrequirements:\n  - id: a\n    check: defines\n    target: foo\n",
        encoding="utf-8",
    )
    spec = load_spec(path)
    assert spec.name == "demo"
    assert spec.requirements[0].target == "foo"
