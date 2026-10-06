"""`job_status` can wait for a job to end, so following one is one call and not a polling loop.

Measured 2026-10-06 through the desktop bridge: an agent that started a 20-minute test run in the
background called `job_status` 75 times in 2.5 minutes — one model step each — varying `tail_lines`,
so the identical-call loop detector never fired. The tool had no way to wait.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from chimera.core.jobs import JobRegistry
from chimera.sandbox import LocalSandbox
from chimera.tools.jobs import MAX_WAIT_SECONDS, JobStatusTool
from chimera.tools.shell import RunShellTool

PY = sys.executable


def _start(tmp_path: Path, seconds: float) -> tuple[JobStatusTool, str]:
    jobs = JobRegistry(tmp_path / "home")
    ws = tmp_path / "ws"
    ws.mkdir()
    shell = RunShellTool(ws, LocalSandbox(), confirm=None, jobs=jobs)
    out = shell.run(
        command=f'"{PY}" -c "import time; time.sleep({seconds}); print(\'done\')"', background=True
    )
    assert out.startswith("job "), out
    assert "wait_seconds" in out  # the start message tells the model how to follow the job
    return JobStatusTool(jobs, ws), out.split()[1]


def test_one_call_with_a_wait_returns_the_finished_job(tmp_path: Path) -> None:
    status, job_id = _start(tmp_path, 0.5)
    began = time.monotonic()
    out = status.run(job_id=job_id, wait_seconds=20)
    assert "finished" in out and "(exit 0)" in out, out
    assert time.monotonic() - began < 15  # returned when the job ended, not at the deadline


def test_without_a_wait_the_answer_is_immediate(tmp_path: Path) -> None:
    status, job_id = _start(tmp_path, 5)
    began = time.monotonic()
    out = status.run(job_id=job_id)
    assert "running" in out
    assert time.monotonic() - began < 2


def test_the_wait_is_bounded(tmp_path: Path) -> None:
    status, job_id = _start(tmp_path, 5)
    began = time.monotonic()
    out = status.run(job_id=job_id, wait_seconds=1)
    assert "running" in out  # the wait ran out first and said so
    assert 0.8 <= time.monotonic() - began < 4
    assert MAX_WAIT_SECONDS <= 120
