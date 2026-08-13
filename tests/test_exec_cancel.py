"""Stopping a command, and stopping it properly.

The command runner has always been able to START things. What it could not do was stop them: closing
the panel on a `npm run dev` left a dev server holding a port until the run's timeout, which for a
long command is an hour. These hold the two halves of the fix — an explicit Stop, and the stream
ending for any other reason — and they check the process is GONE rather than that a function
returned True.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.api.exec_stream import cancel, run_streamed, running_count  # noqa: E402
from chimera.config import Settings  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _Idle:
    run_state = RunState()

    def run(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("these tests never run the agent")


@pytest.fixture()
def client(tmp_path: Path):
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(build_api_app(lambda: ChatSession(_Idle()), workspace=ws, settings=settings)), ws


def test_cancel_kills_a_command_that_would_have_run_for_minutes(tmp_path: Path) -> None:
    started = threading.Event()
    result: dict[str, int] = {}

    def work() -> None:
        result["code"] = run_streamed(
            f'"{sys.executable}" -c "print(chr(120), flush=True); import time; time.sleep(300)"',
            workspace=tmp_path,
            timeout=300,
            on_line=lambda _line: started.set(),
            run_id="r1",
        )

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    assert started.wait(20), "the command never produced its first line"

    assert cancel("r1") is True

    thread.join(timeout=20)
    assert not thread.is_alive(), "run_streamed did not return after the kill"
    assert running_count() == 0


def test_cancelling_something_that_already_finished_says_so(tmp_path: Path) -> None:
    """False, not True. A Stop button that reports success for a command that ended a minute ago
    teaches you to distrust the one case where the answer matters."""
    run_streamed(
        f'"{sys.executable}" -c "pass"',
        workspace=tmp_path,
        timeout=30,
        on_line=lambda _line: None,
        run_id="r2",
    )

    assert cancel("r2") is False
    assert cancel("never-existed") is False


def test_the_endpoint_reports_honestly_for_an_unknown_run(client) -> None:
    api, _ = client

    body = api.post("/api/fs/exec/cancel", json={"id": "nope"}).json()

    assert body["cancelled"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="process-group kill; taskkill /T is the win32 path")
def test_the_whole_tree_dies_not_just_the_shell(tmp_path: Path) -> None:
    """The reason `kill_tree` exists rather than `proc.kill()`.

    A shell command is usually a launcher: `npm` starts node, node starts workers. Killing the one
    process we hold leaves the ones doing the work, and the port stays busy while the panel reports
    the command stopped — the exact failure that makes people reboot.
    """
    marker = tmp_path / "grandchild.pid"
    child = (
        f'"{sys.executable}" -c "'
        f"import os, time, pathlib; pathlib.Path(r'{marker}').write_text(str(os.getpid())); "
        'time.sleep(300)"'
    )
    ready = threading.Event()

    def work() -> None:
        run_streamed(
            f"{child} & wait",  # a shell that FORKS: the grandchild is what must not survive
            workspace=tmp_path,
            timeout=300,
            on_line=lambda _line: None,
            run_id="r3",
        )

    threading.Thread(target=work, daemon=True).start()
    for _ in range(200):
        if marker.exists():
            ready.set()
            break
        time.sleep(0.1)
    assert ready.is_set(), "the grandchild never started"
    pid = int(marker.read_text())

    assert cancel("r3") is True

    for _ in range(100):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid), f"the grandchild {pid} outlived the command it was started by"


def _alive(pid: int) -> bool:
    return (
        subprocess.run(["ps", "-p", str(pid)], capture_output=True, check=False).returncode == 0
    )
