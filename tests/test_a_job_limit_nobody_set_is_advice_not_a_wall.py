"""The job limits are advice until the owner sets them.

The owner decided on 2026-09-27 that "3 at once and 6 hours" stop being reasons for the agent to
stop: a job that is refused, or killed at hour six, reads as the agent giving up on something it
was handed on purpose. `CHIMERA_JOBS_MAX_RUNNING` and `CHIMERA_JOBS_MAX_RUNTIME` are still real
limits the moment somebody sets them, so this file holds both halves against the real tool.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from chimera.core.jobs import DEFAULT_MAX_RUNNING, JobRegistry
from chimera.sandbox import LocalSandbox
from chimera.tools.shell import RunShellTool

PY = sys.executable
SLEEPER = "import time\ntime.sleep(60)\n"


def _wait(predicate: Any, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def _tool(tmp_path: Path, jobs: JobRegistry) -> tuple[RunShellTool, str]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "s.py").write_text(SLEEPER, encoding="utf-8")
    return RunShellTool(ws, LocalSandbox(), confirm=None, jobs=jobs), f'"{PY}" s.py'


def _stop_all(jobs: JobRegistry) -> None:
    for job in jobs.all():
        with suppress(Exception):
            jobs.cancel(job.id)


def test_with_nothing_set_a_fourth_job_starts_and_the_start_says_it_is_past_the_usual(
    tmp_path: Path,
) -> None:
    jobs = JobRegistry(tmp_path / "home")
    jobs.configure_from_settings(None, None)
    tool, command = _tool(tmp_path, jobs)
    try:
        outs = [tool.run(command=command, background=True) for _ in range(DEFAULT_MAX_RUNNING + 1)]

        assert all(o.startswith("job ") for o in outs), outs  # none refused
        assert "Note:" not in outs[0]
        assert f"more than the usual {DEFAULT_MAX_RUNNING}" in outs[-1]
        assert "not a limit" in outs[-1]
    finally:
        _stop_all(jobs)


def test_with_nothing_set_a_job_is_not_given_a_deadline(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    jobs.configure_from_settings(None, None)
    tool, command = _tool(tmp_path, jobs)
    try:
        started = tool.run(command=command, background=True)

        assert "stopped after" not in started
        job = jobs.get(started.split()[1])
        assert job is not None and job.max_runtime == 0.0
    finally:
        _stop_all(jobs)


def test_a_cap_the_owner_set_still_refuses_the_next_job(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    jobs.configure_from_settings(2, None)
    tool, command = _tool(tmp_path, jobs)
    try:
        for _ in range(2):
            assert tool.run(command=command, background=True).startswith("job ")

        refused = tool.run(command=command, background=True)

        assert refused.startswith("error: 2 background jobs are already running"), refused
        assert "CHIMERA_JOBS_MAX_RUNNING" in refused
    finally:
        _stop_all(jobs)


def test_a_runtime_the_owner_set_still_kills_the_job(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    jobs.configure_from_settings(None, 1)
    tool, command = _tool(tmp_path, jobs)
    try:
        started = tool.run(command=command, background=True)
        assert "stopped after 1s at most" in started
        job_id = started.split()[1]

        _wait(lambda: (j := jobs.get(job_id)) is not None and j.state == "timed_out")
    finally:
        _stop_all(jobs)


def test_a_value_below_one_is_still_the_default_and_so_still_advice(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    jobs.configure_from_settings(0, 0)

    assert (jobs.hard_running, jobs.hard_runtime) == (False, False)
    assert jobs.max_running == DEFAULT_MAX_RUNNING


def test_a_registry_built_with_numbers_is_a_caller_that_meant_them(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home", max_running=2, max_runtime=1)

    assert (jobs.hard_running, jobs.hard_runtime) == (True, True)


def test_the_settings_reach_the_registry_as_unset_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.config import get_settings
    from chimera.core.jobs import jobs_for
    from chimera.tools.builtin import default_registry

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CHIMERA_JOBS_MAX_RUNNING", raising=False)
    monkeypatch.delenv("CHIMERA_JOBS_MAX_RUNTIME", raising=False)
    get_settings.cache_clear()
    try:
        ws = tmp_path / "ws"
        ws.mkdir()
        default_registry(ws, host_exec_confirm=None)
        registry = jobs_for(tmp_path / "home")
        assert (registry.hard_running, registry.hard_runtime) == (False, False)

        monkeypatch.setenv("CHIMERA_JOBS_MAX_RUNNING", "5")
        get_settings.cache_clear()
        default_registry(ws, host_exec_confirm=None)
        assert (registry.hard_running, registry.max_running) == (True, 5)
    finally:
        get_settings.cache_clear()
        os.environ.pop("CHIMERA_JOBS_MAX_RUNNING", None)
