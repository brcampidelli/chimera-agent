"""One test suite at a time per folder, and only test suites.

Measured 2026-10-29 to 31: pytest and vitest were started in the same folder at once. They shared a
database and build outputs, and timed out for each other. The rule is pinned at four levels: which
commands are suites (the recogniser), which folder a suite runs in (the effective folder), the lock
that makes check-and-mark one step (the register), and the real `run_shell` with real foreground and
background runs. Nothing here waits on the clock.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from chimera.core.jobs import JobRegistry
from chimera.sandbox import LocalSandbox
from chimera.tools.shell import RunShellTool
from chimera.tools.suite_gate import SuiteJob, SuiteSlots, suite_folder_key, suite_label

PY = sys.executable

#: Commands that run a suite, with the label each one gets.
SUITES: list[tuple[str, str]] = [
    ("pytest -q", "pytest"),
    ("uv run --extra dev --extra desktop pytest -q", "pytest"),
    ("python -m pytest tests -k shell", "pytest"),
    (r".venv\Scripts\pytest.exe -q", "pytest"),
    ("npx vitest run --maxWorkers=2", "vitest"),
    ("cd apps/desktop && npx vitest run", "vitest"),
    ("(cd sub && pytest)", "pytest"),
    ("pushd sub && pytest", "pytest"),
    ("npx vitest run --dir tests/unit", "vitest"),
    ("jest --ci", "jest"),
    ("npm test", "npm test"),
    ("npm run test", "npm test"),
    ("npm -w apps/web run test", "npm test"),
    ("npm --workspace apps/web test", "npm test"),
    ("npm --prefix apps/desktop run test", "npm test"),
    ("pnpm test", "pnpm test"),
    ("pnpm --filter web test", "pnpm test"),
    ("npx playwright test", "playwright test"),
    ("dotnet test", "dotnet test"),
    ("python -m unittest", "unittest"),
    ("cargo test --workspace", "cargo test"),
    ("go test ./...", "go test"),
]

#: Commands that do not run a suite: a set-up, a query, a script that only has the name `test`
#: as a prefix, a reader, or a build.
NOT_SUITES: list[str] = [
    "pip install pytest",
    "uv add --dev pytest",
    "npm install vitest",
    "npm i -D jest",
    "pnpm add -D vitest",
    "python -m pytest --version",
    "pytest --help",
    "npx vitest --version",
    "npm run test:e2e",
    "npm run test:seed",
    "npm run test:db-reset",
    "npm test:unit",
    "echo pytest",
    "grep -r vitest src",
    "cat pytest.log",
    "npm run build",
    "npm install",
    "npm run lint && npm run build",
    "ruff check chimera tests",
    "mypy chimera",
    "cargo build",
    "go build ./...",
    "dotnet build",
    "git status",
    "python scripts/prompt_snapshots.py",
    "",
]


@pytest.mark.parametrize(("command", "label"), SUITES)
def test_a_suite_command_is_recognised(command: str, label: str) -> None:
    assert suite_label(command) == label


@pytest.mark.parametrize("command", NOT_SUITES)
def test_a_command_that_is_not_a_suite_is_not_recognised(command: str) -> None:
    assert suite_label(command) is None


def test_cd_moves_the_folder_a_suite_runs_in(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    assert suite_folder_key("cd sub && pytest", tmp_path) == suite_folder_key(
        "pytest", tmp_path / "sub"
    )
    assert suite_folder_key("cd sub; pytest", tmp_path) == suite_folder_key(
        "pytest", tmp_path / "sub"
    )


def test_prefix_moves_the_folder_relative_to_the_cd(tmp_path: Path) -> None:
    (tmp_path / "apps" / "desktop").mkdir(parents=True)
    by_prefix = suite_folder_key("npm --prefix apps/desktop run test", tmp_path)
    by_cwd = suite_folder_key("npx vitest run", tmp_path / "apps" / "desktop")
    assert by_prefix == by_cwd
    via_cd = suite_folder_key("cd apps && npm --prefix desktop run test", tmp_path)
    assert via_cd == by_cwd


def test_the_folder_flags_all_move_the_folder(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    expected = suite_folder_key("pytest", tmp_path / "x")
    assert suite_folder_key("pytest -C x", tmp_path) == expected
    assert suite_folder_key("npm --prefix=x run test", tmp_path) == expected
    assert suite_folder_key("pnpm --dir x test", tmp_path) == expected


def test_a_parenthesised_cd_and_pushd_move_the_folder(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    expected = suite_folder_key("pytest", tmp_path / "sub")
    assert suite_folder_key("(cd sub && pytest)", tmp_path) == expected
    assert suite_folder_key("pushd sub && pytest", tmp_path) == expected


def test_vitest_dir_is_a_filter_so_two_filters_in_one_folder_collide(tmp_path: Path) -> None:
    slots = SuiteSlots()
    first = _claim(slots, "npx vitest run --dir tests/unit", tmp_path)
    assert first.ticket is not None
    second = _claim(slots, "npx vitest run --dir tests/e2e", tmp_path)
    assert second.ticket is None and second.refusal is not None
    assert "already running in this folder" in second.refusal


def test_pnpm_dir_is_a_folder_so_two_folders_do_not_collide(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    slots = SuiteSlots()
    assert _claim(slots, "pnpm --dir a test", tmp_path).ticket is not None
    assert _claim(slots, "pnpm --dir b test", tmp_path).ticket is not None


def test_different_folders_have_different_keys(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    assert suite_folder_key("pytest", tmp_path) != suite_folder_key("pytest", tmp_path / "sub")


def test_the_same_folder_is_the_same_key_however_it_is_spelled(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    assert suite_folder_key("pytest", ws) == suite_folder_key("pytest", ws / ".." / "ws")


def test_an_unresolvable_folder_falls_back_to_normalised_text(tmp_path: Path) -> None:
    same = suite_folder_key("npm --prefix $DIR run test", tmp_path)
    assert same.startswith("text:")
    assert same == suite_folder_key("npm --prefix $DIR run test", tmp_path)
    assert same != suite_folder_key("npm --prefix $OTHER run test", tmp_path)


def _claim(slots: SuiteSlots, command: str, cwd: Path, jobs: list[SuiteJob] | None = None):  # type: ignore[no-untyped-def]
    return slots.claim(
        command,
        cwd,
        is_running=lambda _job_id: True,
        running_jobs=lambda: list(jobs or []),
    )


def test_a_second_suite_in_the_same_folder_is_refused_while_the_first_holds_it(tmp_path: Path) -> None:
    slots = SuiteSlots()
    first = _claim(slots, "pytest -q", tmp_path)
    assert first.ticket is not None
    second = _claim(slots, "npx vitest run", tmp_path)
    assert second.ticket is None
    assert second.refusal is not None
    assert "already running in this folder" in second.refusal
    assert "started in this app" in second.refusal
    assert "do not start another" in second.refusal.lower()


def test_a_suite_in_another_folder_is_not_blocked(tmp_path: Path) -> None:
    (tmp_path / "other").mkdir()
    slots = SuiteSlots()
    assert _claim(slots, "pytest -q", tmp_path).ticket is not None
    assert _claim(slots, "pytest -q", tmp_path / "other").ticket is not None


def test_a_non_suite_is_never_claimed_or_refused(tmp_path: Path) -> None:
    slots = SuiteSlots()
    assert _claim(slots, "pytest -q", tmp_path).ticket is not None
    claim = _claim(slots, "npm run build", tmp_path)
    assert claim.ticket is None and claim.refusal is None


def test_a_running_build_does_not_block_a_suite(tmp_path: Path) -> None:
    build = [SuiteJob("b1", "npm run build", str(tmp_path))]
    assert _claim(SuiteSlots(), "pytest -q", tmp_path, build).ticket is not None


def test_a_running_suite_job_in_the_folder_blocks_by_its_job_id(tmp_path: Path) -> None:
    running = [SuiteJob("abc123", "uv run pytest -q", str(tmp_path))]
    claim = _claim(SuiteSlots(), "npx vitest run", tmp_path, running)
    assert claim.refusal is not None
    assert "job abc123" in claim.refusal
    assert "job_status(job_id='abc123'" in claim.refusal


def test_a_suite_waiting_for_approval_holds_nothing(tmp_path: Path) -> None:
    # A slow owner who says no. While the first suite waits, a second one in the same folder is
    # asked about too, not refused as "still going": the first one has not taken the folder.
    slots = SuiteSlots()
    asked: list[str] = []
    seen: dict[str, str] = {}
    command = f'"{PY}" -m pytest --co -q'

    def confirm(action: str) -> bool:
        asked.append(action)
        if len(asked) == 1:
            seen["second"] = shell.run(command=command)
        return False

    shell = RunShellTool(tmp_path, LocalSandbox(), confirm=confirm, slots=slots)
    first = shell.run(command=command)
    assert "not run" in first or "declined" in first, first
    assert len(asked) == 2  # the second one reached the owner: it was not refused in advance
    assert "still going" not in seen["second"]
    assert "already running" not in seen["second"]
    assert not slots.is_held(command, tmp_path)


def test_the_folder_is_marked_after_the_owner_says_yes(tmp_path: Path) -> None:
    slots = SuiteSlots()
    command = f'"{PY}" -m pytest --co -q'
    held_during_run: list[bool] = []

    class Probe(LocalSandbox):
        def run(self, command: str, *args: object, **kwargs: object) -> object:
            held_during_run.append(slots.is_held(command, tmp_path))
            return super().run(command, *args, **kwargs)  # type: ignore[arg-type]

    shell = RunShellTool(tmp_path, Probe(), confirm=lambda _action: True, slots=slots)
    out = shell.run(command=command)
    assert out.startswith("[exit "), out
    assert held_during_run == [True]  # marked by the time the process starts
    assert not slots.is_held(command, tmp_path)  # and released when it returns


def test_a_finished_foreground_run_releases_its_folder(tmp_path: Path) -> None:
    slots = SuiteSlots()
    first = _claim(slots, "pytest -q", tmp_path)
    assert first.ticket is not None
    slots.finish(first.ticket)
    assert _claim(slots, "pytest -q", tmp_path).ticket is not None


def test_two_threads_at_the_same_moment_only_one_passes(tmp_path: Path) -> None:
    slots = SuiteSlots()
    barrier = threading.Barrier(2)
    results: list[object] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait()
        claim = _claim(slots, "pytest -q", tmp_path)
        with lock:
            results.append(claim.ticket)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for ticket in results if ticket is not None) == 1


def test_a_background_slot_is_freed_when_its_job_is_no_longer_running(tmp_path: Path) -> None:
    slots = SuiteSlots()
    first = _claim(slots, "pytest -q", tmp_path)
    assert first.ticket is not None
    slots.attach(first.ticket, "job1")
    running = {"job1": True}
    # Still running: refused by the held slot.
    held = slots.claim(
        "pytest -q", tmp_path, is_running=lambda j: running[j], running_jobs=lambda: []
    )
    assert held.refusal is not None and "job job1" in held.refusal
    # The job ended: the next claim prunes the slot and passes.
    running["job1"] = False
    after = slots.claim(
        "pytest -q", tmp_path, is_running=lambda j: running[j], running_jobs=lambda: []
    )
    assert after.ticket is not None


def test_a_foreground_suite_in_progress_blocks_run_shell_until_it_returns(tmp_path: Path) -> None:
    slots = SuiteSlots()
    shell = RunShellTool(tmp_path, LocalSandbox(), confirm=None, slots=slots)
    held = _claim(slots, "pytest -q", tmp_path)
    assert held.ticket is not None
    command = f'"{PY}" -m pytest --co -q'
    refused = shell.run(command=command)
    assert refused.startswith("error: a test suite is already running"), refused
    slots.finish(held.ticket)
    # Released: the same command now runs (pytest collects nothing in an empty folder, exit 5).
    ran = shell.run(command=command)
    assert ran.startswith("[exit "), ran
    assert "already running" not in ran


def test_run_shell_refuses_a_suite_beside_a_running_background_job(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    jobs = JobRegistry(tmp_path / "home")
    # A real registered job whose command text is a suite; the process it runs is only a sleep.
    first = jobs.start(
        "uv run pytest -q",
        cwd=ws,
        env={},
        argv=[PY, "-c", "import time; time.sleep(60)"],
        shell=False,
    )
    try:
        shell = RunShellTool(ws, LocalSandbox(), confirm=None, jobs=jobs, slots=SuiteSlots())
        out = shell.run(command="npx vitest run", background=True)
        assert out.startswith("error: a test suite is already running"), out
        assert f"job {first.id}" in out
        assert [j.id for j in jobs.all() if j.state == "running"] == [first.id]
        assert shell.run(command="pytest -q").startswith("error: a test suite is already running")
    finally:
        jobs.cancel(first.id)


def test_run_shell_does_not_refuse_a_suite_in_another_folder(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    other = tmp_path / "other"
    ws.mkdir()
    other.mkdir()
    jobs = JobRegistry(tmp_path / "home")
    first = jobs.start(
        "uv run pytest -q",
        cwd=ws,
        env={},
        argv=[PY, "-c", "import time; time.sleep(60)"],
        shell=False,
    )
    try:
        shell = RunShellTool(other, LocalSandbox(), confirm=None, jobs=jobs, slots=SuiteSlots())
        out = shell.run(command=f'"{PY}" -c "print(1)"', timeout=30)
        assert "already running" not in out
        assert out.startswith("[exit 0]"), out
    finally:
        jobs.cancel(first.id)
