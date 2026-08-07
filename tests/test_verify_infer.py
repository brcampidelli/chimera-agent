"""Finding the command that already judges a project.

The verify field is being removed from the screen, and the capability must not leave with it: without
an executable verdict every run silently drops to "a model read the answer and approved it", while
the panel counting passes keeps using the same word. So the command is read off the project instead
of typed — which is only safe if the reading is a fact about files, never a guess.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.core.verify_infer import infer_verify


def test_an_empty_directory_infers_nothing(tmp_path: Path) -> None:
    """The honest outcome, and the one the UI has to say out loud."""
    assert infer_verify(tmp_path) is None


def test_a_package_json_test_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")

    found = infer_verify(tmp_path)

    assert found is not None
    assert found.command == "npm test"
    assert found.source_file == "package.json"


def test_the_npm_scaffold_script_is_refused(tmp_path: Path) -> None:
    """`npm init` writes a test script that exits 1. Adopting it would fail every run of every
    scaffolded project — the one way this inference could be actively harmful rather than absent."""
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}), encoding="utf-8"
    )

    assert infer_verify(tmp_path) is None


def test_the_lockfile_picks_the_runner(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")

    found = infer_verify(tmp_path)
    assert found is not None and found.command == "pnpm test"


def test_a_broken_package_json_is_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")

    assert infer_verify(tmp_path) is None


def test_a_makefile_test_target(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("build:\n\tcc x.c\n\ntest:\n\t./run\n", encoding="utf-8")

    found = infer_verify(tmp_path)
    assert found is not None and found.command == "make test"


def test_the_word_test_inside_a_recipe_is_not_a_target(tmp_path: Path) -> None:
    """A command that does nothing is worse than no command: it produces `evidence == "verifier"`
    for a verifier that verified nothing."""
    (tmp_path / "Makefile").write_text("build:\n\techo run the test suite\n", encoding="utf-8")

    assert infer_verify(tmp_path) is None


def test_pytest_configured_in_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-q"\n', encoding="utf-8"
    )

    found = infer_verify(tmp_path)
    assert found is not None
    assert found.command == "python -m pytest -q"
    assert found.source_file == "pyproject.toml"


def test_a_tests_directory_with_real_test_files(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text("def test_x(): pass\n", encoding="utf-8")

    found = infer_verify(tmp_path)
    assert found is not None and found.source_file == "tests/"


def test_an_empty_tests_directory_infers_nothing(tmp_path: Path) -> None:
    """A folder named `tests` with nothing in it is a layout, not a test suite."""
    (tmp_path / "tests").mkdir()

    assert infer_verify(tmp_path) is None


def test_cargo_and_go(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert infer_verify(tmp_path).command == "cargo test"  # type: ignore[union-attr]

    go = tmp_path / "go"
    go.mkdir()
    (go / "go.mod").write_text("module x\n", encoding="utf-8")
    assert infer_verify(go).command == "go test ./..."  # type: ignore[union-attr]


def test_a_project_statement_beats_an_observation_about_layout(tmp_path: Path) -> None:
    """`package.json` naming a test script is this project saying how it is tested. A `tests/`
    directory is something we noticed. When both exist, the statement wins."""
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("", encoding="utf-8")

    found = infer_verify(tmp_path)
    assert found is not None and found.command == "npm test"


def test_it_never_runs_anything(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A "validated" command would be a second definition of verified, and inference that executes
    project files is a way to run untrusted code by opening a folder."""
    import subprocess

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("inference must not execute anything")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    assert infer_verify(tmp_path) is not None


# --- how a request turns into a command, and what the receipt remembers about it ----------------


def test_an_explicit_command_is_never_overridden_by_inference(tmp_path: Path) -> None:
    from chimera.api.app import resolve_verify

    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    assert resolve_verify("make check", tmp_path) == ("make check", "user")


def test_an_empty_string_means_no_verifier_not_go_looking(tmp_path: Path) -> None:
    """Clearing the field is a choice. Inferring over the top of it would override a decision with a
    guess — and the guess would then be recorded as a verified run."""
    from chimera.api.app import resolve_verify

    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    assert resolve_verify("", tmp_path) == (None, "none")


def test_absent_means_look_at_the_project(tmp_path: Path) -> None:
    from chimera.api.app import resolve_verify

    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    command, source = resolve_verify(None, tmp_path)
    assert command == "python -m pytest -q"
    assert source == "inferred:pytest.ini"


def test_a_project_with_nothing_to_run_says_so_rather_than_inventing(tmp_path: Path) -> None:
    from chimera.api.app import resolve_verify

    assert resolve_verify(None, tmp_path) == (None, "none")


def test_the_receipt_keeps_the_source_so_it_cannot_be_read_as_a_choice() -> None:
    """The whole reason the source is carried. A receipt that cannot tell a typed command from an
    inferred one will eventually be cited as evidence of a decision nobody made."""
    from chimera.api.runs import RunReceipt

    assert RunReceipt(task="t").verify_source == "user"  # every receipt predating the field
    assert RunReceipt(task="t", verify_source="inferred:pyproject.toml").verify_source.startswith(
        "inferred:"
    )
