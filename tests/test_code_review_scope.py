"""``/code-review`` scope, request size, per-call limit and spend, with real stored conversations.

What is pinned: a new conversation reviews the server's own folder and no other; a conversation reviews
the folder it stores, and a request naming another is refused; a conversation with no folder stored is
not refused for the server's own folder (no false 409); a body over the cap is refused before it is parsed;
each model call carries the review's own limit; and a call that ends after the wall-clock limit is still
recorded as spend.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import chimera.api.commands_api as commands_api
from chimera.api import build_api_app
from chimera.config import Settings
from chimera.core.code_session import CodeSession, CodeSessionStore
from chimera.interface import ChatSession
from chimera.providers.gateway import CompletionResult, Message
from chimera.review.code_card import MAX_REVIEW_BODY, REVIEW_CALL_TIMEOUT_S
from chimera.review.family import choose_reviewer

EDIT = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,3 @@\n def f():\n+    x = 1\n     return 2\n"
ANSWER = '{"findings": []}'


class _Backend:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0

    def complete(self, messages: list[Message], **_: Any) -> CompletionResult:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return CompletionResult(content=ANSWER, model="fake/reviewer", prompt_tokens=10, completion_tokens=5)


class _Idle:
    def run(self, task: str, **_: Any) -> Any:  # pragma: no cover - the route never runs a turn
        raise AssertionError("/code-review must not start a turn")


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    default = tmp_path / "default"
    default.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    home = tmp_path / "home"
    monkeypatch.setattr(Settings, "can_answer", lambda self: True)
    monkeypatch.setattr(
        commands_api, "reviewer_for",
        lambda author, explicit="": choose_reviewer(author, explicit="other/reviewer"),
    )
    seen: dict[str, Any] = {"backend": _Backend(), "settings": []}

    def gateway(settings: Any, *_a: Any, **_k: Any) -> _Backend:
        seen["settings"].append(settings)
        return seen["backend"]

    monkeypatch.setattr(commands_api, "LLMGateway", gateway)
    settings = Settings(CHIMERA_HOME=str(home))
    app = build_api_app(lambda: ChatSession(_Idle()), workspace=default, settings=settings)  # type: ignore[arg-type]
    seen.update(client=TestClient(app), default=default, other=other, home=home)
    return seen


def _save_session(home: Path, workspace: str) -> str:
    session = CodeSession(agent=SimpleNamespace(), workspace=workspace)
    CodeSessionStore(home / "code_sessions").save(session)
    return session.session_id


def _post(server: dict[str, Any], body: dict[str, Any]) -> Any:
    return server["client"].post("/api/code/review", json=body)


def _edits() -> list[dict[str, str]]:
    return [{"path": "m.py", "patch": EDIT}]


# --- which folder ---------------------------------------------------------------------------------


def test_a_new_conversation_refuses_a_folder_that_is_not_the_projects(server: dict[str, Any]) -> None:
    response = _post(server, {"workspace": str(server["other"]), "edits": _edits()})
    assert response.status_code == 409
    assert server["backend"].calls == 0


def test_a_new_conversation_reviews_the_projects_folder(server: dict[str, Any]) -> None:
    response = _post(server, {"workspace": str(server["default"]), "edits": _edits()})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "no_findings"


def test_a_conversation_with_a_stored_folder_refuses_another_one(server: dict[str, Any]) -> None:
    sid = _save_session(server["home"], str(server["other"]))
    response = _post(server, {"workspace": str(server["default"]), "session_id": sid, "edits": _edits()})
    assert response.status_code == 409
    assert server["backend"].calls == 0


def test_a_conversation_with_its_stored_folder_is_reviewed(server: dict[str, Any]) -> None:
    sid = _save_session(server["home"], str(server["other"]))
    response = _post(server, {"workspace": str(server["other"]), "session_id": sid, "edits": _edits()})
    assert response.status_code == 200, response.text


def test_a_conversation_with_no_folder_stored_is_not_refused_for_the_projects_folder(server: dict[str, Any]) -> None:
    sid = _save_session(server["home"], "")
    response = _post(server, {"workspace": str(server["default"]), "session_id": sid, "edits": _edits()})
    assert response.status_code == 200, response.text


# --- size -----------------------------------------------------------------------------------------


def test_a_body_over_the_cap_is_refused_before_it_is_parsed(server: dict[str, Any]) -> None:
    huge = "x" * (MAX_REVIEW_BODY + 10)
    response = server["client"].post(
        "/api/code/review", content=json.dumps({"edits": [{"path": "m.py", "patch": huge}]}),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert server["backend"].calls == 0


# --- per call limit and spend ---------------------------------------------------------------------


def test_each_model_call_carries_the_reviews_own_limit(server: dict[str, Any]) -> None:
    _post(server, {"workspace": str(server["default"]), "edits": _edits()})
    (settings,) = server["settings"][:1]
    assert settings.request_timeout <= REVIEW_CALL_TIMEOUT_S


def test_a_call_that_ends_after_the_wall_clock_limit_is_still_recorded(
    server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands_api, "REVIEW_TIMEOUT_S", 0.05)
    server["backend"] = _Backend(delay=0.6)
    body = _post(server, {"workspace": str(server["default"]), "edits": _edits()}).json()
    assert body["status"] == "incomplete"
    assert body["reason"] == "the review ran out of time"
    usage = server["home"] / "usage.jsonl"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if usage.is_file() and "code-review:" in usage.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    assert "code-review:" in usage.read_text(encoding="utf-8")
