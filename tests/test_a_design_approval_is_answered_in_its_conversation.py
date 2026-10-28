"""A design approval is answered as text, by the owner's screen, and it shows in one place.

Found on 2026-10-28, on Chimera 0.79.0. A turn that called ``design submit_for_approval`` parked a
typed question (``ask_user_durably``, kind ``user_question``, tool ``design``). Two surfaces drew it:
the conversation's card, which answered through ``POST /api/questions/{id}`` and worked, and the
global "Chimera needs a decision" modal, which listed it from ``GET /api/approvals`` as a generic
yes or no. Its Allow button posted a boolean to ``POST /api/approvals/{id}``, which refuses a typed
question and answered "another process asked this, use chimera approve --code". The question was
this process's own.

The approvals list leaves typed questions out, and a boolean click on one answers nothing. The typed
list (``GET /api/questions``) carries the design card, and the design is approved only from the
owner's screen: a bearer token on its own (readable by the agent's shell), a paired device, or a
bridge without full control is refused with 403. Writing the answer is one step, so two answers at
once cannot both be recorded.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.governance.pending import answer_user_question, ask_user_durably, pending
from chimera.interface import ChatSession

TIMEOUT = 10.0
QUESTION = "Review the design and choose approval or adjustments."
TOKEN = "owner-bearer-for-the-design-tests"


class _Idle:
    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, **_kw: Any) -> AgentResult:  # pragma: no cover - no turn runs here
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The app with an owner token, and its own screen credential, as the desktop shell runs it."""
    import chimera.core
    from chimera.api import build_api_app
    from chimera.api import screen_credential as sc

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Idle, raising=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=TOKEN)
    return TestClient(
        build_api_app(
            lambda: ChatSession(_Idle()), workspace=tmp_path, settings=settings,
            screen=sc.for_mode("app", server_token=TOKEN),
        )
    )


def _bearer() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}"}


def _screen(client: TestClient) -> dict[str, str]:
    """What the desktop card sends: the owner's bearer and the screen credential beside it."""
    from chimera.api.screen_credential import HEADER_NAME

    return {**_bearer(), HEADER_NAME: str(client.app.state.screen_credential.value)}


def _park_design_question(
    home: Path, *, tool: str = "design", **card: Any
) -> tuple[threading.Thread, dict[str, Any]]:
    """Park the question as the design tool does: the same call, the same announce, a real wait."""
    seen: dict[str, Any] = {}
    result: dict[str, Any] = {}

    def announce(request_id: str, data: dict[str, Any]) -> None:
        seen["id"] = request_id
        seen["data"] = data

    def park() -> None:
        result["answer"] = ask_user_durably(
            home, QUESTION, ["approved", "adjustments"], True,
            announce=announce, wait_seconds=8.0, poll_seconds=0.02, run_id="turn-design", tool=tool,
            **card,
        )

    thread = threading.Thread(target=park, daemon=True)
    thread.start()
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and "id" not in seen:
        time.sleep(0.01)
    assert "id" in seen, "the question was never announced"
    return thread, {**seen, "result": result}


def _finish(client: TestClient, thread: threading.Thread, request_id: str) -> None:
    """Answer a parked question from the screen so its thread ends, then wait for it."""
    client.post(f"/api/questions/{request_id}", json={"answer": "approved"}, headers=_screen(client))
    thread.join(TIMEOUT)


def test_the_approvals_list_leaves_the_design_question_out(app_client: TestClient, tmp_path: Path) -> None:
    home = tmp_path / "home"
    thread, parked = _park_design_question(home)
    try:
        assert [q.id for q in pending(home)] == [parked["id"]], "the question is parked as a typed one"
        listed = app_client.get("/api/approvals", headers=_bearer()).json()
        assert all(row["id"] != parked["id"] for row in listed), listed
    finally:
        _finish(app_client, thread, parked["id"])


def test_the_owners_screen_answers_the_design_as_text(app_client: TestClient, tmp_path: Path) -> None:
    home = tmp_path / "home"
    thread, parked = _park_design_question(home)
    response = app_client.post(
        f"/api/questions/{parked['id']}", json={"answer": "approved"}, headers=_screen(app_client)
    )
    thread.join(TIMEOUT)

    assert response.status_code == 200 and response.json() == {"ok": True}
    assert parked["result"]["answer"] == "approved", "the tool receives the answer the owner gave"


def test_a_yes_click_on_the_design_question_says_nothing_about_another_process(
    app_client: TestClient, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    thread, parked = _park_design_question(home)
    try:
        for approved in (True, False):
            body = app_client.post(
                f"/api/approvals/{parked['id']}", json={"approved": approved}, headers=_screen(app_client)
            ).json()
            assert body == {"ok": False}, body
            assert "needs_code" not in str(body) and "other_process" not in str(body)
        assert [q.id for q in pending(home)] == [parked["id"]], "a boolean click leaves it waiting"
    finally:
        _finish(app_client, thread, parked["id"])


def test_a_bearer_token_alone_cannot_approve_the_design(app_client: TestClient, tmp_path: Path) -> None:
    home = tmp_path / "home"
    thread, parked = _park_design_question(home)
    try:
        refused = app_client.post(
            f"/api/questions/{parked['id']}", json={"answer": "approved"}, headers=_bearer()
        )
        assert refused.status_code == 403, refused.text
        assert not (home / "approvals" / f"{parked['id']}.user-answer.json").exists()
        assert [q.id for q in pending(home)] == [parked["id"]], "still waiting for the owner's screen"
    finally:
        _finish(app_client, thread, parked["id"])


def test_an_ordinary_question_is_still_answered_with_the_bearer_token(
    app_client: TestClient, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    thread, parked = _park_design_question(home, tool="ask_user")
    response = app_client.post(f"/api/questions/{parked['id']}", json={"answer": "blue"}, headers=_bearer())
    thread.join(TIMEOUT)

    assert response.status_code == 200 and response.json() == {"ok": True}
    assert parked["result"]["answer"] == "blue"


def test_the_typed_list_draws_the_design_card_and_the_count_is_the_status_bar_s(
    app_client: TestClient, tmp_path: Path
) -> None:
    from chimera.governance.pending import visible_to_owner

    home = tmp_path / "home"
    thread, parked = _park_design_question(
        home, description="Launch screen, three variants", presentation="design_approval",
        document_id="doc-7", title="Fornalha", revision=12,
    )
    try:
        rows = app_client.get("/api/questions", headers=_bearer()).json()
        row = next(r for r in rows if r["id"] == parked["id"])
        assert row["presentation"] == "design_approval"
        assert (row["document_id"], row["title"], row["revision"]) == ("doc-7", "Fornalha", 12)
        assert row["description"] == "Launch screen, three variants"
        assert row["options"] == ["approved", "adjustments"]
        # The governance count and the status bar read the same set: this question counts once.
        assert [q.id for q in visible_to_owner(home)] == [parked["id"]]
    finally:
        _finish(app_client, thread, parked["id"])


def _write_waiting_question(home: Path, request_id: str) -> None:
    directory = home / "approvals"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{request_id}.ask.json").write_text(
        json.dumps({
            "id": request_id, "kind": "user_question", "tool": "ask_user", "question": "q",
            "options": ["a", "b"], "allow_free_text": True,
            "asked_at": time.time(), "wait_seconds": 300.0,
        }),
        encoding="utf-8",
    )


def _answer_together(home: Path, request_id: str) -> list[bool]:
    """Two answers, released at the same moment, to one question."""
    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def answerer(text: str) -> Callable[[], None]:
        def run() -> None:
            barrier.wait()
            outcome = answer_user_question(home, request_id, text)
            with lock:
                results.append(outcome)

        return run

    threads = [threading.Thread(target=answerer(text)) for text in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT)
    return results


def test_two_answers_at_once_write_exactly_one(tmp_path: Path) -> None:
    """The answer file is created only if absent, in one step: a race writes one answer, never two."""
    home = tmp_path / "home"
    directory = home / "approvals"
    for n in range(40):
        request_id = f"{n:012x}"
        _write_waiting_question(home, request_id)
        results = _answer_together(home, request_id)

        assert sorted(results) == [False, True], f"question {request_id}: {results}"
        written = json.loads((directory / f"{request_id}.user-answer.json").read_text(encoding="utf-8"))
        assert written["answer"] in {"a", "b"}
    assert not [p for p in directory.iterdir() if p.name.endswith(".tmp")], "no staging file left behind"


def test_the_stored_title_and_summary_are_bounded(tmp_path: Path) -> None:
    home = tmp_path / "home"
    thread, parked = _park_design_question(
        home, description="S" * 3000, presentation="design_approval",
        document_id="d" * 500, title="T" * 500, revision=4,
    )
    try:
        stored = json.loads((home / "approvals" / f"{parked['id']}.ask.json").read_text(encoding="utf-8"))
        assert len(stored["title"]) <= 200
        assert len(stored["description"]) <= 1000
        assert len(stored["document_id"]) <= 64
    finally:
        answer_user_question(home, parked["id"], "approved")
        thread.join(TIMEOUT)
