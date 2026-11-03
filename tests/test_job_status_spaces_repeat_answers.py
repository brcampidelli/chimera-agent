"""A repeat `job_status` on a running job is held back, and the hold ends when the turn is cancelled.

Measured 2026-10-29 to 31: the agent called `job_status` again and again with `wait_seconds` 0, each
call answering at once, one model step per call. A repeat answer about a running job now waits until
MIN_REPEAT_WAIT_SECONDS after the previous answer, unless the job ends or the turn is cancelled first.

Every wait here runs on a fake clock: a sleep moves the clock by its length and returns at once, so
no test waits on real time. The jobs are real processes only so that `job_status` has something to
read; a test ends them by cancelling them, not by waiting for them.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from chimera.core.jobs import JobRegistry
from chimera.tools.base import Tool
from chimera.tools.jobs import (
    MIN_REPEAT_WAIT_SECONDS,
    JobStatusTool,
    answer_not_before,
    bind_turn_cancel,
)

PY = sys.executable


class FakeClock:
    """Time that moves only when something sleeps on it. Safe to share between threads."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []
        self._lock = threading.Lock()

    def clock(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.slept.append(seconds)
            self.now += seconds


def _running_job(tmp_path: Path, jobs: JobRegistry, name: str = "ws") -> tuple[Path, str]:
    ws = tmp_path / name
    ws.mkdir(exist_ok=True)
    job = jobs.start(
        "sleep-long", cwd=ws, env={}, argv=[PY, "-c", "import time; time.sleep(60)"], shell=False
    )
    return ws, job.id


def test_the_first_answer_is_not_held_back() -> None:
    assert answer_not_before(500.0, None) == 500.0


def test_a_repeat_is_held_until_the_minimum_after_the_previous_answer() -> None:
    assert answer_not_before(100.0, 100.0) == 100.0 + MIN_REPEAT_WAIT_SECONDS


def test_a_repeat_that_asks_for_longer_keeps_its_own_wait() -> None:
    assert answer_not_before(300.0, 100.0) == 300.0


def test_a_repeat_on_a_running_job_waits_the_minimum_on_the_fake_clock(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    ws, job_id = _running_job(tmp_path, jobs)
    try:
        fake = FakeClock()
        status = JobStatusTool(jobs, ws, clock=fake.clock, sleep=fake.sleep)
        first = status.run(job_id=job_id)
        assert "running" in first
        assert "Still running" not in first  # the first answer is never held or annotated
        assert fake.slept == []

        second = status.run(job_id=job_id)
        assert sum(fake.slept) == MIN_REPEAT_WAIT_SECONDS
        assert "Still running" in second
        assert "since the last answer" in second
        assert "does not help" in second
    finally:
        jobs.cancel(job_id)


def test_a_repeat_after_the_minimum_has_passed_is_not_held(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    ws, job_id = _running_job(tmp_path, jobs)
    try:
        fake = FakeClock()
        status = JobStatusTool(jobs, ws, clock=fake.clock, sleep=fake.sleep)
        status.run(job_id=job_id)
        fake.now += MIN_REPEAT_WAIT_SECONDS + 5  # the caller already spent that time itself
        status.run(job_id=job_id)
        assert fake.slept == []
    finally:
        jobs.cancel(job_id)


def test_a_repeat_on_a_job_that_ends_is_released_early(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    ws, job_id = _running_job(tmp_path, jobs)
    fake = FakeClock()
    sleeps = {"count": 0}

    def sleep_then_end(seconds: float) -> None:
        fake.sleep(seconds)
        sleeps["count"] += 1
        if sleeps["count"] == 2:
            jobs.cancel(job_id)  # the job ends inside the held window

    status = JobStatusTool(jobs, ws, clock=fake.clock, sleep=sleep_then_end)
    status.run(job_id=job_id)
    out = status.run(job_id=job_id)
    assert "cancelled" in out, out
    assert "Still running" not in out
    assert sum(fake.slept) < MIN_REPEAT_WAIT_SECONDS


def test_a_turn_cancelled_during_the_wait_stops_it_at_once(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    ws, job_id = _running_job(tmp_path, jobs)
    try:
        fake = FakeClock()
        cancelled = {"on": False}

        def sleep_then_cancel(seconds: float) -> None:
            fake.sleep(seconds)
            cancelled["on"] = True  # the person stops the turn during the first slice

        status = JobStatusTool(jobs, ws, clock=fake.clock, sleep=sleep_then_cancel)
        status.bind_turn_cancel(lambda: cancelled["on"])
        status.run(job_id=job_id)
        out = status.run(job_id=job_id)
        assert "turno cancelado" in out
        assert sum(fake.slept) < MIN_REPEAT_WAIT_SECONDS  # stopped after one slice, not the minimum
        still = jobs.get(job_id)
        assert still is not None and still.state == "running"
    finally:
        jobs.cancel(job_id)


def test_the_cancel_signal_reaches_a_job_status_behind_a_wrapper(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    ws = tmp_path / "ws"
    ws.mkdir()
    status = JobStatusTool(jobs, ws)

    class Wrapper(Tool):
        name = "wrapped"
        description = "a governance wrapper"
        parameters: dict[str, object] = {}

        def __init__(self, inner: Tool) -> None:
            self.inner = inner

        def run(self, **kwargs: object) -> str:
            return ""

    bound = bind_turn_cancel([Wrapper(status), Wrapper(Wrapper(status))], lambda: True)
    assert bound == 2
    assert status._cancelled is not None and status._cancelled()


def test_two_parallel_repeats_are_spaced_by_the_lock_and_the_reservation(tmp_path: Path) -> None:
    jobs = JobRegistry(tmp_path / "home")
    ws, job_id = _running_job(tmp_path, jobs)
    try:
        fake = FakeClock()
        status = JobStatusTool(jobs, ws, clock=fake.clock, sleep=fake.sleep)
        barrier_start = threading.Barrier(2)
        outputs: list[str] = []
        out_lock = threading.Lock()

        def call() -> None:
            barrier_start.wait()
            text = status.run(job_id=job_id)
            with out_lock:
                outputs.append(text)

        threads = [threading.Thread(target=call) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # Whichever call came second was held until the first one's reserved answer plus the
        # minimum, so the shared clock moved by at least the minimum, and neither call escaped.
        assert fake.now - 1000.0 >= MIN_REPEAT_WAIT_SECONDS
        assert len(outputs) == 2
    finally:
        jobs.cancel(job_id)


def test_the_default_registry_has_a_job_status_the_turn_cancel_reaches(tmp_path: Path) -> None:
    # The Code turn binds its Stop signal through this count; zero would mean the wait ignores Stop.
    from chimera.tools.builtin import default_registry

    registry = default_registry(tmp_path)
    assert bind_turn_cancel(registry.tools(), lambda: False) >= 1


class _PlainListAgent:
    """A code-turn agent whose `tools` is a plain list of names, as the test fakes have it.

    Before the fix, the turn's Stop wiring called `agent.tools.tools()` outside the turn's
    try/except: the AttributeError killed the turn thread, no `done` frame came, and the stream hung.
    """

    def __init__(self) -> None:
        from chimera.core.context_budget import RunState

        self.tools = ["read_file"]
        self.run_state = RunState()

    def run(self, task: str, **_: object) -> object:
        from chimera.core.agent import AgentResult

        return AgentResult(answer="ok", steps=1, stopped_reason="final", tool_names=[], model="m", usd=0.0)


def test_a_code_turn_whose_agent_has_a_plain_list_of_tools_still_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: the turn must reach its `done` frame even when Stop cannot be wired to the agent.
    pytest.importorskip("fastapi")
    pytest.importorskip("sse_starlette")
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.interface import ChatSession

    agent = _PlainListAgent()
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(_env_file=None, CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]

    class _Stub:
        def run(self, task: str, **_: object) -> object:
            raise AssertionError("the chat agent is not used by a code turn")

    client = TestClient(build_api_app(lambda: ChatSession(_Stub()), workspace=ws, settings=settings))
    response = client.post("/api/code/turn", json={"message": "hello"})

    events = [line[len("event: "):] for line in response.text.splitlines() if line.startswith("event: ")]
    assert "done" in events, response.text
