"""A question waiting for a person says which conversation and which project asked it.

Found reading the code on 2026-09-30 (R6 of the review of several conversations at once). The question
file held an action, a reason and a number, and nothing that said where it came from; the status bar's
list of waiting questions showed every conversation's questions in one dialog with no project and no
conversation on them. With two turns in two projects both asking to run a command, the wrong one could
be approved.

Now the question records the turn that asked it (`run_id`), and `GET /api/approvals` answers each with
the conversation and folder of that turn, read from the running turns (and from background works).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.governance.pending import ask_durably, pending
from chimera.interface import ChatSession

TIMEOUT = 10.0


def test_the_question_records_the_turn_that_asked_it(tmp_path: Path) -> None:
    done = threading.Event()
    threading.Thread(
        target=lambda: (ask_durably(tmp_path, "run_shell: rm -rf build", "outside the folder",
                                    facts={"run_id": "turn-1", "surface": "api:turn"},
                                    wait_seconds=2, poll_seconds=0.05), done.set()),
        daemon=True,
    ).start()
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and not pending(tmp_path):
        time.sleep(0.02)

    assert [q.run_id for q in pending(tmp_path)] == ["turn-1"]
    assert done.wait(TIMEOUT)


class _Held:
    entered = threading.Event()
    release = threading.Event()

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, **_kw: Any) -> AgentResult:
        type(self).entered.set()
        assert type(self).release.wait(TIMEOUT)
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


@pytest.fixture
def held() -> type[_Held]:
    _Held.entered.clear()
    _Held.release.clear()
    yield _Held
    _Held.release.set()


def test_the_waiting_list_names_the_conversation_and_folder_of_each_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: type[_Held]
) -> None:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", held, raising=True)
    project = tmp_path / "shop"
    project.mkdir()
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    client = TestClient(build_api_app(lambda: ChatSession(held()), workspace=tmp_path, settings=settings))
    turn = threading.Thread(
        target=lambda: client.post(
            "/api/code/turn", json={"message": "clean up", "workspace": str(project), "stream": False}
        ),
        daemon=True,
    )
    turn.start()
    assert held.entered.wait(TIMEOUT)
    running = client.get("/api/code/turns/running").json()[0]
    # The question this turn's tool would put, as the approver puts it: with the turn's run id.
    threading.Thread(
        target=lambda: ask_durably(home, "run_shell: rm -rf build", "outside the folder",
                                   facts={"run_id": running["turn_id"], "surface": "api:turn"},
                                   wait_seconds=3, poll_seconds=0.05),
        daemon=True,
    ).start()
    deadline = time.monotonic() + TIMEOUT
    listed: list[dict[str, Any]] = []
    while time.monotonic() < deadline and not listed:
        listed = client.get("/api/approvals").json()
        time.sleep(0.02)

    assert listed, "the question never reached the list"
    assert listed[0]["run_id"] == running["turn_id"]
    assert listed[0]["session_id"] == running["session_id"]
    assert Path(listed[0]["workspace"]).name == "shop"
    held.release.set()
    turn.join(TIMEOUT)
