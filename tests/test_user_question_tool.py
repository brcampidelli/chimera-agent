"""The Code screen's typed question parks its turn without crossing the approval boundary."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Literal

from chimera.api.app import build_api_app
from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.governance.approval import ApprovalAnnouncer
from chimera.governance.pending import answer, answer_user_question, ask_user_durably
from chimera.interface import ChatSession
from chimera.tools.ask_user import AskUserTool


def test_a_user_question_pauses_until_a_choice_or_free_text_arrives(tmp_path: Path) -> None:
    for response in ("blue", "because it is safer"):
        seen: list[dict[str, Any]] = []
        result: list[str | None] = []
        def ask(result: list[str | None] = result, seen: list[dict[str, Any]] = seen) -> None:
            result.append(
                ask_user_durably(
                    tmp_path, "Which approach?", ["blue", "green"], True,
                    announce=lambda _id, data: seen.append(data), wait_seconds=2, poll_seconds=0.001,
                )
            )

        worker = threading.Thread(target=ask)
        worker.start()
        while not seen:
            threading.Event().wait(0.001)
        request_id = str(seen[0]["id"])
        assert seen[0]["kind"] == "user_question"
        assert answer_user_question(tmp_path, request_id, response)
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert result == [response]


def test_a_question_timeout_returns_no_response_and_never_fabricates_an_answer(tmp_path: Path) -> None:
    announcements: list[dict[str, Any]] = []
    result = ask_user_durably(
        tmp_path, "Which approach?", [], True, announce=lambda _id, data: announcements.append(data),
        wait_seconds=0, poll_seconds=0,
    )
    assert result is None
    assert announcements[0]["kind"] == "user_question"


def test_question_answers_and_approval_answers_cannot_cross_types(tmp_path: Path) -> None:
    from chimera.governance.pending import _dir

    directory = _dir(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    approval_id = "a" * 12
    question_id = "b" * 12
    (directory / f"{approval_id}.ask.json").write_text(
        '{"id":"' + approval_id + '","kind":"approval"}', encoding="utf-8"
    )
    (directory / f"{question_id}.ask.json").write_text(
        '{"id":"' + question_id + '","kind":"user_question","options":[],"allow_free_text":true}',
        encoding="utf-8",
    )
    assert not answer_user_question(tmp_path, approval_id, "approve")
    assert not answer(tmp_path, question_id, True)
    assert answer_user_question(tmp_path, question_id, "ordinary user data")
    assert not answer(tmp_path, question_id, False)
    assert not answer_user_question(tmp_path, approval_id, "not an approval")


def test_only_an_attended_code_turn_registers_ask_user(tmp_path: Path) -> None:
    settings = Settings(CHIMERA_HOME=str(tmp_path))
    seams = CodeSeams()
    attended, _ = assemble_registry(
        seams, tmp_path, settings, object(), steps=1, surface="api:turn", approval_sink=ApprovalAnnouncer()
    )
    headless, _ = assemble_registry(seams, tmp_path, settings, object(), steps=1, surface="terminal")
    other_surface, _ = assemble_registry(
        seams, tmp_path, settings, object(), steps=1, surface="chat", approval_sink=ApprovalAnnouncer()
    )
    assert "ask_user" in attended.names()
    assert "ask_user" not in headless.names()
    assert "ask_user" not in other_surface.names()
    assert "ask_user" not in assemble_registry(
        seams, tmp_path, settings, object(), steps=1, surface="api:turn"
    )[0].names()
    assert "ask_user" not in assemble_registry(
        seams, tmp_path, settings, object(), steps=1, surface="api:turn", guest=True,
        approval_sink=ApprovalAnnouncer(),
    )[0].names()


def test_user_question_route_requires_the_protected_api_and_typed_answer(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """With a server token set, the question routes are behind it like the approval routes.

    Without one the local API is open by design (the desktop's own sidecar), so the property worth
    pinning is the token case: no bearer, no list and no answer. The guard reads the token from the
    live settings on every call (so a token set at runtime applies at once), hence the environment.
    """
    from fastapi.testclient import TestClient

    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "question-token")
    get_settings.cache_clear()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]  # runtime-only init kwarg
    client = TestClient(build_api_app(lambda: ChatSession(object()), settings=settings))
    bearer = {"Authorization": "Bearer question-token"}

    assert client.get("/api/questions").status_code == 401
    assert client.post("/api/questions/aaaaaaaaaaaa", json={"answer": "yes"}).status_code == 401
    assert client.get("/api/questions", headers=bearer).status_code == 200
    unknown = client.post("/api/questions/aaaaaaaaaaaa", json={"answer": "yes"}, headers=bearer)
    assert unknown.status_code == 200 and unknown.json() == {"ok": False}
    get_settings.cache_clear()


def test_question_response_validation_rejects_unlisted_answer_without_free_text(tmp_path: Path) -> None:
    from chimera.governance.pending import _dir

    directory = _dir(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    request_id = "c" * 12
    (directory / f"{request_id}.ask.json").write_text(
        '{"id":"' + request_id + '","kind":"user_question","options":["blue"],'
        '"allow_free_text":false}', encoding="utf-8"
    )
    assert not answer_user_question(tmp_path, request_id, "green")
    assert answer_user_question(tmp_path, request_id, "blue")


def test_user_question_schema_is_distinct_from_approval_schema() -> None:
    from chimera.api.schemas import UserQuestionAnswerIn, UserQuestionOut

    assert UserQuestionAnswerIn.model_fields.keys() == {"answer"}
    assert UserQuestionOut.model_fields["kind"].default == "user_question"
    assert UserQuestionOut.model_fields["kind"].annotation == Literal["user_question"]
    assert "approved" not in UserQuestionAnswerIn.model_fields


def test_tool_schema_bounds_choices_and_timeout_is_explicit(tmp_path: Path) -> None:
    tool = AskUserTool(tmp_path, lambda *_args: None, wait_seconds=0)
    assert tool.parameters["properties"]["options"]["maxItems"] == 6
    assert tool.parameters["properties"]["allow_free_text"]["default"] is True
    assert tool.run(question="Pick", options=["yes"] * 7).startswith("error:")
    assert tool.run(question="Pick") == "No response."


def test_question_card_has_distinct_option_and_free_text_submission_paths() -> None:
    from pathlib import Path

    source = Path("apps/desktop/src/components/code/UserQuestionCard.tsx").read_text(encoding="utf-8")
    assert "question.options.map" in source
    assert "submit(option)" in source
    assert "submit(freeText)" in source
    assert "answerUserQuestion(question.id, answer)" in source
    assert "data-card=\"user-question\"" in source
    assert "ApprovalCard" not in source


def test_user_question_copy_has_all_ten_locales() -> None:
    from pathlib import Path

    source = Path("apps/desktop/src/lib/i18n.tsx").read_text(encoding="utf-8")
    assert source.count('"code.question.title"') == 10
    assert source.count('"code.question.freeText"') == 10
    assert source.count('"code.question.submit"') == 10
    assert source.count('"code.question.answerFailed"') == 10
    assert source.count('"code.question.expired"') == 10
