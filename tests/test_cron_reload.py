"""Tests for the daemon-safe scheduler behaviour: live reload of jobs.json,
clobber-safe run stamps, a configurable shell tool, and cron-run delivery records.

These cover the fixes that make a running ``chimera serve --cron`` honour
out-of-band CLI edits (the rollback primitive), run slow scripts, and leave a
delivery-confirmation trail.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from chimera.sandbox.base import SandboxResult
from chimera.scheduler import CronStore, Scheduler
from chimera.tools.shell import RunShellTool

NOW = 1_000_000.0


# --- live reload / rollback primitive ----------------------------------------

def test_reload_if_changed_picks_up_external_disable(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    daemon_view = Scheduler(CronStore(path))
    job = daemon_view.schedule_cron("j", "* * * * *", "x", now=NOW)

    # A separate process (the CLI) disables the job on disk.
    cli_view = Scheduler(CronStore(path))
    cli_view.disable(job.id)

    assert daemon_view.store.get(job.id).enabled is True  # stale in-memory copy
    assert daemon_view.store.reload_if_changed() is True
    assert daemon_view.store.get(job.id).enabled is False  # now honoured


def test_run_due_honours_external_disable_without_restart(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    daemon_view = Scheduler(CronStore(path))
    job = daemon_view.schedule_cron("j", "* * * * *", "x", now=NOW)
    due_at = job.next_run
    assert due_at is not None

    Scheduler(CronStore(path)).disable(job.id)  # CLI disables it out-of-band

    fired: list[str] = []
    ran = daemon_view.run_due(due_at, lambda j: fired.append(j.id))
    assert fired == []  # rollback primitive works: the disabled job does not fire
    assert ran == []


def test_mark_ran_does_not_clobber_external_disable(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    daemon_view = Scheduler(CronStore(path))
    job = daemon_view.schedule_cron("j", "* * * * *", "x", now=NOW)

    Scheduler(CronStore(path)).disable(job.id)  # disabled mid-tick by the CLI

    daemon_view.mark_ran(job, NOW)  # daemon stamps the run it just executed
    assert CronStore(path).get(job.id).enabled is False  # disable survived
    assert CronStore(path).get(job.id).last_run == NOW  # run was still recorded


def test_save_is_atomic_and_leaves_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = Scheduler(CronStore(path)).schedule_cron  # trigger a save via schedule
    store("j", "* * * * *", "x", now=NOW)
    assert path.exists()
    json.loads(path.read_text(encoding="utf-8"))  # valid, complete JSON
    assert not path.with_suffix(path.suffix + ".tmp").exists()


# --- configurable shell tool -------------------------------------------------

class _FakeSandbox:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        self.calls.append({"command": command, "timeout": timeout, "cwd": cwd})
        return SandboxResult(exit_code=0, stdout="ok")


def test_shell_default_timeout_is_raised(tmp_path: Path) -> None:
    fake = _FakeSandbox()
    tool = RunShellTool(workspace=tmp_path, sandbox=fake)
    tool.run(command="echo hi")  # no explicit timeout
    assert fake.calls[0]["timeout"] >= 180  # slow scripts (e.g. 170s) survive


def test_shell_accepts_cwd_argument(tmp_path: Path) -> None:
    fake = _FakeSandbox()
    tool = RunShellTool(workspace=tmp_path, sandbox=fake)
    target = tmp_path / "sub"
    target.mkdir()
    tool.run(command="ls", cwd=str(target))
    assert fake.calls[0]["cwd"] == target.resolve()


def test_shell_cwd_defaults_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHIMERA_SHELL_CWD", str(tmp_path))
    fake = _FakeSandbox()
    tool = RunShellTool(sandbox=fake)  # no explicit workspace -> env
    assert tool.workspace == tmp_path.resolve()


# --- cron-run delivery record ------------------------------------------------

def test_record_cron_run_writes_confirmation_line(tmp_path: Path) -> None:
    from chimera.cli.main import _record_cron_run

    runs_log = tmp_path / "cron_runs.jsonl"
    job = SimpleNamespace(id="abc123", name="etoro-heartbeat")
    _record_cron_run(runs_log, job, "line one\nline two", webhook_url=None)

    lines = runs_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["name"] == "etoro-heartbeat"
    assert rec["preview"] == "line one line two"
    assert rec["delivered"] is None  # no webhook configured -> confirmation only
