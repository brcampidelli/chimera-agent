"""A design approval is answered as text in its conversation, never as a yes or no on the app's modal.

Found on 2026-10-28, on Chimera 0.79.0. A turn that called ``design submit_for_approval`` parked a
typed question (``ask_user_durably``, kind ``user_question``, tool ``design``). Two surfaces drew it:
the conversation's card, which answered through ``POST /api/questions/{id}`` and worked, and the
global "Chimera needs a decision" modal, which listed it from ``GET /api/approvals`` as a generic
yes or no. Its Allow button posted a boolean to ``POST /api/approvals/{id}``, which refuses a typed
question and, because that question was never given a code in memory, answered "another process
asked this, use chimera approve --code". The question was this process's own.

Now the approvals list leaves typed questions out, and a boolean click on one answers nothing and
claims nothing about who asked.
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
from chimera.governance.pending import ask_user_durably, pending
from chimera.interface import ChatSession

TIMEOUT = 10.0
QUESTION = "Review the design and choose approval or adjustments."


class _Idle:
    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, **_kw: Any) -> AgentResult:  # pragma: no cover - no turn runs here
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Idle, raising=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    return TestClient(build_api_app(lambda: ChatSession(_Idle()), workspace=tmp_path, settings=settings))


def _park_design_question(home: Path) -> tuple[threading.Thread, dict[str, Any]]:
    """Park the question as the design tool does: the same call, the same announce, a real wait."""
    seen: dict[str, Any] = {}
    result: dict[str, Any] = {}

    def announce(request_id: str, data: dict[str, Any]) -> None:
        seen["id"] = request_id
        seen["data"] = data

    def park() -> None:
        result["answer"] = ask_user_durably(
            home, QUESTION, ["approved", "adjustments"], True,
            announce=announce, wait_seconds=8.0, poll_seconds=0.02, run_id="turn-design", tool="design",
        )

    thread = threading.Thread(target=park, daemon=True)
    thread.start()
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and "id" not in seen:
        time.sleep(0.01)
    assert "id" in seen, "the design question was never announced"
    return thread, {**seen, "result": result}


def test_the_approvals_list_leaves_the_design_question_out(app_client: TestClient, tmp_path: Path) -> None:
    home = tmp_path / "home"
    thread, parked = _park_design_question(home)
    try:
        assert [q.id for q in pending(home)] == [parked["id"]], "the question is parked as a typed one"
        listed = app_client.get("/api/approvals").json()
        assert all(row["id"] != parked["id"] for row in listed), listed
    finally:
        app_client.post(f"/api/questions/{parked['id']}", json={"answer": "approved"})
        thread.join(TIMEOUT)


def test_the_conversation_answers_the_design_question_as_text(app_client: TestClient, tmp_path: Path) -> None:
    home = tmp_path / "home"
    thread, parked = _park_design_question(home)
    response = app_client.post(f"/api/questions/{parked['id']}", json={"answer": "approved"}).json()
    thread.join(TIMEOUT)

    assert response == {"ok": True}
    assert parked["result"]["answer"] == "approved", "the tool receives the answer the owner gave"


def test_a_yes_click_on_the_design_question_says_nothing_about_another_process(
    app_client: TestClient, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    thread, parked = _park_design_question(home)
    try:
        for approved in (True, False):
            body = app_client.post(f"/api/approvals/{parked['id']}", json={"approved": approved}).json()
            assert body == {"ok": False}, body
            assert "needs_code" not in str(body) and "other_process" not in str(body)
        assert [q.id for q in pending(home)] == [parked["id"]], "a boolean click leaves it waiting"
    finally:
        app_client.post(f"/api/questions/{parked['id']}", json={"answer": "adjustments: tighten the header"})
        thread.join(TIMEOUT)
