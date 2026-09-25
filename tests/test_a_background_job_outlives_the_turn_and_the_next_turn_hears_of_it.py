"""A `run_shell(background=true)` starts a job the turn does not wait for, and the next turn hears
how it ended.

Item 6(b) of the list audited on 2026-09-16. The loop ran one tool at a time and `run_shell` waited,
so "download the images and, while that runs, draft the post" was a sentence the agent could not act
on. A job is the same command — the kernel, the taint ledger and the host-exec gate see `run_shell`
with the command string unchanged, one wrapper out — started detached with its output on disk.

What is pinned: the job runs after the tool returned; its output is on disk; `job_status` reads the
state and the tail; `job_cancel` kills the tree; a job that ended is handed to exactly one later
turn; the API lists and cancels; a registry without a home refuses the parameter; an isolated
sandbox refuses it; and a record whose process this backend did not start is `lost`, not
`finished`.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from chimera.core.jobs import JobRegistry, jobs_for
from chimera.sandbox import LocalSandbox
from chimera.tools.jobs import JobCancelTool, JobStatusTool
from chimera.tools.shell import RunShellTool

PY = sys.executable


def _wait(predicate: Any, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def _shell(tmp_path: Path, jobs: JobRegistry | None) -> RunShellTool:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return RunShellTool(ws, LocalSandbox(), confirm=None, jobs=jobs)


# ------------------------------------------------------------------ the job itself


def test_the_tool_returns_at_once_and_the_job_finishes_on_its_own(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    tool = _shell(tmp_path, jobs)
    command = f'"{PY}" -c "import time; time.sleep(0.4); print(\'done at last\')"'

    began = time.monotonic()
    out = tool.run(command=command, background=True)
    elapsed = time.monotonic() - began

    assert out.startswith("job ") and "started in the background" in out, out
    assert "NOT stopped by cancelling the turn" in out
    assert elapsed < 0.3, f"the tool waited for the job ({elapsed:.2f} s)"
    job_id = out.split()[1]
    assert jobs.get(job_id) is not None and jobs.get(job_id).state == "running"  # type: ignore[union-attr]

    _wait(lambda: jobs.get(job_id).state == "finished")  # type: ignore[union-attr]
    job = jobs.get(job_id)
    assert job is not None and job.exit_code == 0 and job.finished_at is not None
    assert "done at last" in Path(job.log).read_text(encoding="utf-8")
    record = json.loads((tmp_path / "home" / "jobs" / f"{job_id}.json").read_text(encoding="utf-8"))
    assert record["state"] == "finished" and record["exit_code"] == 0


def test_the_job_runs_in_the_resolved_cwd_with_the_scrubbed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-reach-the-child")
    jobs = JobRegistry(tmp_path / "home")
    tool = _shell(tmp_path, jobs)
    (tmp_path / "ws" / "sub").mkdir()
    command = f"\"{PY}\" -c \"import os; print(os.getcwd()); print(os.environ.get('OPENAI_API_KEY', 'scrubbed'))\""

    out = tool.run(command=command, cwd="sub", background=True)
    job_id = out.split()[1]
    _wait(lambda: jobs.get(job_id).state == "finished")  # type: ignore[union-attr]

    log = Path(jobs.get(job_id).log).read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert str((tmp_path / "ws" / "sub").resolve()).lower() in log.lower()
    assert "scrubbed" in log and "sk-should-not-reach" not in log


def test_status_reads_the_state_and_the_tail_and_cancel_kills_the_tree(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    tool = _shell(tmp_path, jobs)
    command = f'"{PY}" -c "import time,sys; print(\'ticking\', flush=True); time.sleep(30)"'
    job_id = tool.run(command=command, background=True).split()[1]
    _wait(lambda: "ticking" in jobs.tail(job_id))

    status = JobStatusTool(jobs).run(job_id=job_id)
    assert status.startswith(f"job {job_id}: running") and "ticking" in status
    listed = JobStatusTool(jobs).run()
    assert job_id in listed and "running" in listed

    began = time.monotonic()
    cancelled = JobCancelTool(jobs).run(job_id=job_id)
    assert time.monotonic() - began < 5.0
    assert cancelled.startswith(f"job {job_id}: cancelled")
    job = jobs.get(job_id)
    assert job is not None and job.state == "cancelled" and job.finished_at is not None
    assert JobCancelTool(jobs).run(job_id=job_id).startswith(f"job {job_id}: cancelled"), (
        "twice is idempotent"
    )
    assert JobStatusTool(jobs).run(job_id="nope") == "error: no such job 'nope'"


def test_a_job_that_ended_is_handed_to_exactly_one_later_turn(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    tool = _shell(tmp_path, jobs)
    job_id = tool.run(command=f'"{PY}" -c "print(1)"', background=True).split()[1]
    _wait(lambda: jobs.get(job_id).state == "finished")  # type: ignore[union-attr]

    first = jobs.finished_unreported()
    assert [j.id for j in first] == [job_id]
    assert jobs.finished_unreported() == [], "reported once, never again"
    assert jobs.get(job_id).reported is True  # type: ignore[union-attr]


# ------------------------------------------------------------------ what is refused, and what is lost


def test_without_a_job_store_the_parameter_is_refused_with_a_sentence(tmp_path: Path) -> None:
    out = _shell(tmp_path, None).run(command="echo hi", background=True)
    assert out.startswith("error: background jobs are not available here")
    assert "foreground" in out


def test_an_isolated_sandbox_refuses_a_background_job(tmp_path: Path) -> None:
    class _Isolated(LocalSandbox):
        @staticmethod
        def is_isolated() -> bool:
            return True

    ws = tmp_path / "ws"
    ws.mkdir()
    tool = RunShellTool(ws, _Isolated(), confirm=None, jobs=JobRegistry(tmp_path / "home"))
    out = tool.run(command="echo hi", background=True)
    assert out.startswith("error: background jobs run on the host sandbox only")
    assert not (tmp_path / "home" / "jobs").exists() or not list(
        (tmp_path / "home" / "jobs").glob("*.json")
    )


def test_the_host_exec_gate_is_consulted_before_a_job_starts(tmp_path: Path) -> None:
    asked: list[str] = []

    def decline(command: str) -> bool:
        asked.append(command)
        return False

    ws = tmp_path / "ws"
    ws.mkdir()
    tool = RunShellTool(ws, LocalSandbox(), confirm=decline, jobs=JobRegistry(tmp_path / "home"))
    out = tool.run(command="echo hi", background=True)
    assert asked == ["echo hi"]
    assert out == "error: host execution declined (CHIMERA_HOST_EXEC). Not run."


def test_a_record_from_a_previous_process_is_lost_not_finished(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "jobs").mkdir(parents=True)
    dead_pid = 2_000_000_000  # no such process on any machine this runs on
    (home / "jobs" / "abc123.json").write_text(
        json.dumps(
            {
                "id": "abc123",
                "command": "sleep 99",
                "cwd": str(tmp_path),
                "pid": dead_pid,
                "started_at": time.time() - 60,
                "log": str(home / "jobs" / "abc123.log"),
                "state": "running",
            }
        ),
        encoding="utf-8",
    )
    jobs = JobRegistry(home)
    job = jobs.get("abc123")
    assert job is not None and job.state == "lost" and job.exit_code is None
    assert "lost" in JobStatusTool(jobs).run(job_id="abc123")
    assert jobs.cancel("abc123").state == "lost"  # type: ignore[union-attr]


# ------------------------------------------------------------------ the registry is one per home per process


def test_one_registry_per_home(tmp_path: Path) -> None:
    assert jobs_for(tmp_path / "a") is jobs_for(tmp_path / "a")
    assert jobs_for(tmp_path / "a") is not jobs_for(tmp_path / "b")


# ------------------------------------------------------------------ the API and the next turn


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: Any) -> Any:
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(build_api_app(lambda: ChatSession(agent), workspace=ws, settings=settings))


def test_the_api_lists_and_cancels_and_the_next_turn_is_told(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core

    seen_prompts: list[str] = []
    seen_systems: list[str] = []

    class _Spy:
        def __init__(self, *a: Any, **kw: Any) -> None:
            # `build_agent` passes the config third and positional: Agent(gateway, registry, config)
            config = a[2] if len(a) > 2 else kw.get("config")
            # The job note travels in the turn notes since study 25 wave 2: true for this turn,
            # absent from the stored transcript, and out of the system message a provider caches.
            seen_prompts.append(str(getattr(config, "turn_notes", "") or ""))
            seen_systems.append(str(getattr(config, "system_prompt", "") or ""))

        def run(self, task: str, **_: Any) -> Any:
            from chimera.core.agent import AgentResult

            return AgentResult(
                answer="ok",
                steps=1,
                stopped_reason="final",
                transcript=[
                    {"role": "user", "content": task},
                    {"role": "assistant", "content": "ok"},
                ],
                model="test/model",
            )

    client = _client(tmp_path, monkeypatch, _Spy())
    monkeypatch.setattr(chimera.core, "Agent", lambda *a, **kw: _Spy(*a, **kw), raising=True)

    jobs = jobs_for(tmp_path / "home")
    tool = _shell(tmp_path, jobs)
    long_id = tool.run(command=f'"{PY}" -c "import time; time.sleep(30)"', background=True).split()[
        1
    ]
    short_id = tool.run(command=f'"{PY}" -c "print(\'short\')"', background=True).split()[1]
    _wait(lambda: jobs.get(short_id).state == "finished")  # type: ignore[union-attr]

    listed = client.get("/api/jobs").json()["jobs"]
    by_id = {j["id"]: j for j in listed}
    assert by_id[long_id]["state"] == "running" and by_id[short_id]["state"] == "finished"
    assert by_id[short_id]["exit_code"] == 0 and "short" in by_id[short_id]["tail"]

    cancelled = client.post(f"/api/jobs/{long_id}/cancel").json()
    assert cancelled["state"] == "cancelled"
    assert client.post("/api/jobs/nope/cancel").status_code == 404

    # The next turn is told about BOTH endings — once.
    client.post("/api/code/turn", json={"message": "oi"})
    assert seen_prompts, "the turn built no agent"
    prompt = seen_prompts[-1]
    assert "Background jobs that finished since your last turn" in prompt
    assert f"job {short_id} finished (exit 0)" in prompt and f"job {long_id} cancelled" in prompt
    assert "read the log with read_file" in prompt
    assert "Background jobs" not in seen_systems[-1], "the note is back in the cached system message"

    client.post("/api/code/turn", json={"message": "de novo"})
    assert "Background jobs that finished" not in seen_prompts[-1], "told twice"
