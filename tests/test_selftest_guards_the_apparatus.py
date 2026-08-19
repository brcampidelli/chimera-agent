"""The guard that runs before the spend, and the two failures it refuses to confuse.

A benchmark task claims two things. That its test passes once the work is done is measured on every
run; that the test FAILS before anyone touches anything is assumed — and that assumption is how a
suite ends up carrying tasks that score a hit for an arm that did nothing.

These tests use a real subprocess and a real temporary workspace, because the whole value of this
guard is that it executes. A mocked `subprocess.run` here would test that the module calls a function
this project already trusts, and would have passed just as happily against the version of this rule
that lived only in prose.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chimera.eval.selftest import (
    TaskCheck,
    assert_discriminating,
    check_discriminates,
    run_selftest,
)

PY = f'"{sys.executable}"'


def _workspace(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    ws = tmp_path / name
    ws.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return ws


def _check(tmp_path: Path, name: str, files: dict[str, str], test: str = "test_it.py") -> TaskCheck:
    return TaskCheck(
        task_id=name,
        setup=lambda: _workspace(tmp_path, name, files),
        verify=f"{PY} -m pytest -q {test}",
    )


# --- the healthy case ----------------------------------------------------------------------------


def test_a_task_whose_test_fails_first_is_the_healthy_one(tmp_path: Path) -> None:
    # Nothing implements `add` yet, so collection fails — which is exactly what a task should look
    # like before the work starts.
    verdict = check_discriminates(
        _check(tmp_path, "missing_impl", {"test_it.py": "from impl import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"})
    )

    assert verdict.discriminates is True
    assert verdict.runnable is True
    assert verdict.ok is True


def test_a_buggy_source_that_the_test_catches_discriminates(tmp_path: Path) -> None:
    # The other shape: the file exists and is WRONG. This is the case the rule was written for —
    # `bench/learning_lift` ships buggy sources and asserts the committed test catches them.
    verdict = check_discriminates(
        _check(
            tmp_path,
            "off_by_one",
            {
                "impl.py": "def add(a, b):\n    return a + b + 1\n",
                "test_it.py": "from impl import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
            },
        )
    )

    assert verdict.ok is True


# --- the failure this exists to catch --------------------------------------------------------------


def test_a_test_that_already_passes_is_named_vacuous(tmp_path: Path) -> None:
    """The task that measures nothing: it scores a hit for every arm, including one that did nothing,
    and it does it silently."""
    verdict = check_discriminates(
        _check(
            tmp_path,
            "already_done",
            {
                "impl.py": "def add(a, b):\n    return a + b\n",
                "test_it.py": "from impl import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
            },
        )
    )

    assert verdict.discriminates is False
    assert verdict.runnable is True  # it ran fine — that is the problem
    assert "PASSES" in verdict.detail


def test_a_vacuous_task_stops_the_run_before_any_model_is_called(tmp_path: Path) -> None:
    lines: list[str] = []
    checks = [
        _check(tmp_path, "good", {"test_it.py": "def test_x():\n    assert False\n"}),
        _check(tmp_path, "vacuous", {"test_it.py": "def test_x():\n    assert True\n"}),
    ]

    with pytest.raises(SystemExit) as raised:
        assert_discriminating(checks, report=lines.append)

    # The ids, not just a count: "some task is broken" is not actionable.
    assert "vacuous" in "\n".join(lines)
    assert "1 vacuous" in str(raised.value)


def test_every_task_is_checked_rather_than_stopping_at_the_first(tmp_path: Path) -> None:
    # One vacuous task rarely travels alone, and aborting at the first hides the rest behind two more
    # reruns of a suite that costs money to run.
    checks = [
        _check(tmp_path, "vacuous_a", {"test_it.py": "def test_x():\n    assert True\n"}),
        _check(tmp_path, "vacuous_b", {"test_it.py": "def test_y():\n    assert True\n"}),
    ]

    verdicts = run_selftest(checks)

    assert [v.task_id for v in verdicts] == ["vacuous_a", "vacuous_b"]
    assert all(not v.discriminates for v in verdicts)


# --- the failure that is NOT the same failure ------------------------------------------------------


def test_an_unrunnable_command_is_reported_as_broken_not_vacuous(tmp_path: Path) -> None:
    """No pytest, a bad interpreter, a timeout — an environment problem. Calling it "vacuous" would
    train everyone to ignore both messages."""
    check = TaskCheck(
        task_id="no_such_binary",
        setup=lambda: _workspace(tmp_path, "no_such_binary", {}),
        verify="definitely-not-a-real-command-9d3f --version",
    )

    verdict = check_discriminates(check)

    # Written expecting `ok is False` and it came back True: a missing command exits 127, and
    # non-zero is the answer this guard calls healthy. Left as the assertion that caught it, because
    # the consequence is the guard's own failure mode — an environment with no pytest would have
    # every task "discriminate" and the suite would be certified having measured nothing.
    assert verdict.runnable is False
    assert verdict.discriminates is False
    assert "could not run" in verdict.detail


def test_a_workspace_that_cannot_be_built_is_broken_not_vacuous(tmp_path: Path) -> None:
    def explode() -> Path:
        raise RuntimeError("disk full")

    verdict = check_discriminates(TaskCheck(task_id="bad_setup", setup=explode, verify="true"))

    assert verdict.runnable is False
    assert verdict.discriminates is False
    assert "disk full" in verdict.detail


def test_a_hanging_verify_times_out_instead_of_stalling_the_guard(tmp_path: Path) -> None:
    check = TaskCheck(
        task_id="hangs",
        setup=lambda: _workspace(tmp_path, "hangs", {}),
        verify=f"{PY} -c \"import time; time.sleep(30)\"",
    )

    verdict = check_discriminates(check, timeout=2)

    assert verdict.runnable is False
    assert "did not finish" in verdict.detail


def test_a_broken_environment_also_stops_the_run(tmp_path: Path) -> None:
    lines: list[str] = []

    with pytest.raises(SystemExit) as raised:
        assert_discriminating(
            [TaskCheck(task_id="bad", setup=lambda: (_ for _ in ()).throw(OSError("nope")), verify="true")],
            report=lines.append,
        )

    assert "BROKEN" in "\n".join(lines)
    assert "1 unrunnable" in str(raised.value)


def test_a_clean_suite_says_so_and_returns(tmp_path: Path) -> None:
    lines: list[str] = []

    verdicts = assert_discriminating(
        [_check(tmp_path, "good", {"test_it.py": "def test_x():\n    assert False\n"})],
        report=lines.append,
    )

    assert [v.ok for v in verdicts] == [True]
    assert "1 tasks discriminate" in "\n".join(lines)
