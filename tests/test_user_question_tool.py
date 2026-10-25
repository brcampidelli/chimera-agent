"""The Code screen's typed question parks its turn without crossing the approval boundary."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Literal

from chimera.api.app import build_api_app
from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.governance.approval import ApprovalAnnouncer
from chimera.governance.pending import (
    answer,
    answer_user_question,
    approvals_directory,
    ask_user_durably,
)
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
    directory = approvals_directory(tmp_path)
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
    directory = approvals_directory(tmp_path)
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
    question_schema = tool.parameters["properties"]["questions"]
    assert question_schema["minItems"] == 1 and question_schema["maxItems"] == 4
    options_schema = question_schema["items"]["properties"]["options"]
    assert options_schema["minItems"] == 2 and options_schema["maxItems"] == 4
    assert tool.parameters["properties"]["allow_free_text"]["default"] is True
    assert tool.parameters["type"] == "object"
    assert not {"oneOf", "anyOf", "allOf"} & tool.parameters.keys()
    assert tool.run(question="legacy", questions=[]) == "error: provide exactly one of question or questions"
    assert tool.run() == "error: provide exactly one of question or questions"
    assert "timed_out" in tool.description
    question = {"label": "Pick", "description": "Choose an option.", "options": ["yes", "no"]}
    assert tool.run(questions=[question] * 5).startswith("error:")
    assert tool.run(questions=[{**question, "options": ["a", "b", "c", "d", "e"]}]).startswith("error:")
    assert tool.run(questions=[question]) == "No response."


def test_question_card_has_distinct_option_and_free_text_submission_paths() -> None:
    from pathlib import Path

    source = Path("apps/desktop/src/components/code/UserQuestionCard.tsx").read_text(encoding="utf-8")
    assert "question.options.map" in source
    # The premise changed on purpose (study 34, item 13): an option click used to call
    # `submit(option)` directly, which sent "Other (describe)" as an empty answer. It now goes through
    # `pick`, which holds back an "Other" until there is text and otherwise submits the option's
    # full text. Both halves are still asserted: the click path and the one place that submits it.
    assert "pick(option)" in source
    assert "submit(question.option_details?.[option] ?? option)" in source
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
    assert source.count('"code.question.sendAsNext"') == 10
    assert source.count('"code.question.expiredNote"') == 10
    assert source.count('"code.question.otherHint"') == 10


def _ask_in_background(tool: AskUserTool, **kwargs: Any) -> tuple[threading.Thread, list[str]]:
    out: list[str] = []
    worker = threading.Thread(target=lambda: out.append(tool.run(**kwargs)))
    worker.start()
    return worker, out

def test_ask_user_accepts_legacy_header_with_new_questions(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    def choose_first(request_id: str, data: dict[str, Any]) -> None:
        seen.append(data)
        assert answer_user_question(tmp_path, request_id, data["options"][0])

    tool = AskUserTool(tmp_path, choose_first, wait_seconds=0)
    question_list = [{"label": "Color", "description": "Choose a color.", "options": ["blue", "green"]}]
    for kwargs in (
        {"questions": question_list, "question": "Resolve this contradiction."},
        {"questions": question_list, "question": "Resolve this contradiction.", "options": []},
    ):
        assert json.loads(tool.run(**kwargs)) == {
            "answers": [{"label": "Color", "answer": "blue"}]
        }
    assert len(seen) == 2
    assert [entry["label"] for entry in seen] == ["Color", "Color"]


def test_ask_user_rejects_ambiguous_legacy_options_with_actionable_error(tmp_path: Path) -> None:
    tool = AskUserTool(tmp_path, lambda *_args: None, wait_seconds=0)
    result = tool.run(
        questions=[{"label": "Color", "description": "Choose.", "options": ["blue", "green"]}],
        question="Resolve this contradiction.",
        options=["old", "legacy"],
    )
    assert result.startswith("error:")
    assert "drop legacy `options`" in result


def test_ask_user_parses_questions_json_string_and_reports_invalid_json(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    def choose_first(request_id: str, data: dict[str, Any]) -> None:
        seen.append(data)
        assert answer_user_question(tmp_path, request_id, data["options"][0])

    tool = AskUserTool(tmp_path, choose_first, wait_seconds=0)
    questions = [{"label": "Color", "description": "Choose.", "options": ["blue", "green"]}]
    assert json.loads(tool.run(questions=json.dumps(questions))) == {
        "answers": [{"label": "Color", "answer": "blue"}]
    }
    assert seen[0]["label"] == "Color"
    invalid = tool.run(questions='[{"label":')
    assert invalid.startswith("error:")
    assert "valid JSON" in invalid


def test_ask_user_keeps_the_legacy_call_and_result_shape(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    tool = AskUserTool(
        tmp_path,
        lambda request_id, data: (
            seen.append(data), answer_user_question(tmp_path, request_id, "blue")
        ),
        wait_seconds=0,
    )
    result = tool.run(question="Choose a colour", options=["blue", "green"])
    assert json.loads(result) == {"answer": "blue"}
    assert seen[0]["label"] == "Choose a colour"

def test_ask_user_legacy_without_options_allows_free_text(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    def answer_freely(request_id: str, data: dict[str, Any]) -> None:
        seen.append(data)
        assert answer_user_question(tmp_path, request_id, "because it is safer")

    tool = AskUserTool(tmp_path, answer_freely, wait_seconds=0)
    assert json.loads(tool.run(question="Why this approach?")) == {"answer": "because it is safer"}
    assert seen[0]["options"] == []


def test_ask_user_legacy_accepts_six_options(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    def choose_sixth(request_id: str, data: dict[str, Any]) -> None:
        seen.append(data)
        assert answer_user_question(tmp_path, request_id, data["options"][5])

    options = ["one", "two", "three", "four", "five", "six"]
    tool = AskUserTool(tmp_path, choose_sixth, wait_seconds=0)
    assert json.loads(tool.run(question="Choose one", options=options)) == {"answer": "six"}
    assert seen[0]["options"] == options


def test_ask_user_truncates_label_and_description_with_visible_marker(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    label = "Heading " * 15
    description = "Long explanation " * 150
    tool = AskUserTool(
        tmp_path,
        lambda _id, data: seen.append(data),
        wait_seconds=0,
    )
    assert tool.run(questions=[{
        "label": label, "description": description, "options": ["one", "two"]
    }]) == "No response."
    assert len(seen[0]["label"]) == 80 and seen[0]["label"].endswith("…")
    assert len(seen[0]["description"]) == 2000 and seen[0]["description"].endswith("…")

def test_multiple_questions_collect_each_answer_in_order(tmp_path: Path) -> None:
    announced: list[dict[str, Any]] = []

    def answer_each(request_id: str, data: dict[str, Any]) -> None:
        announced.append(data)
        assert answer_user_question(tmp_path, request_id, data["options"][0])

    tool = AskUserTool(tmp_path, answer_each, wait_seconds=0)
    questions = [
        {"label": "First", "description": "First choice", "options": ["blue", "green"]},
        {"label": "Second", "description": "Second choice", "options": ["day", "night"]},
    ]
    assert json.loads(tool.run(questions=questions)) == {
        "answers": [{"label": "First", "answer": "blue"}, {"label": "Second", "answer": "day"}]
    }
    assert [item["label"] for item in announced] == ["First", "Second"]
def test_multiple_questions_timeout_on_first_question_stops_before_second(tmp_path: Path) -> None:
    calls = 0

    def announce(_request_id: str, _data: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1

    tool = AskUserTool(tmp_path, announce, wait_seconds=0)
    questions = [
        {"label": "First", "description": "First choice", "options": ["blue", "green"]},
        {"label": "Second", "description": "Second choice", "options": ["day", "night"]},
    ]
    assert tool.run(questions=questions) == "No response."
    assert calls == 1


def test_multiple_questions_return_completed_answers_as_partial_on_later_timeout(tmp_path: Path) -> None:
    calls = 0

    def announce(request_id: str, _data: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            assert answer_user_question(tmp_path, request_id, "blue")

    tool = AskUserTool(tmp_path, announce, wait_seconds=0,)
    questions = [
        {"label": "First", "description": "First choice", "options": ["blue", "green"]},
        {"label": "Second", "description": "Second choice", "options": ["day", "night"]},
    ]
    result = json.loads(tool.run(questions=questions))
    assert result == {"answers": [{"label": "First", "answer": "blue"}], "timed_out": True}
    assert calls == 2


def test_a_long_option_is_shortened_with_an_ellipsis_and_keeps_its_full_text(tmp_path: Path) -> None:
    """Study 34, item 12: an option over 120 characters was refused, so the agent asked the whole
    question again. It is cut for the button; the full text rides along and comes back as the answer."""
    seen: list[dict[str, Any]] = []
    tool = AskUserTool(tmp_path, lambda _id, data: seen.append(data), wait_seconds=2)
    long = "Reescrever o módulo inteiro " + "com todas as dependências e testes " * 6
    assert len(long) > 120
    question = {"label": "Qual caminho?", "description": "Escolha uma abordagem.", "options": [long, "curto"]}
    worker, out = _ask_in_background(tool, questions=[question])
    while not seen:
        threading.Event().wait(0.001)
    label = seen[0]["options"][0]
    assert len(label) <= 120 and label.endswith("…") and seen[0]["options"][1] == "curto"
    assert seen[0]["option_details"] == {label: " ".join(long.split())}
    assert answer_user_question(tmp_path, str(seen[0]["id"]), label)
    worker.join(timeout=10)
    assert out == ['{"answers": [{"label": "Qual caminho?", "answer": "' + " ".join(long.split()) + '"}]}']


def test_an_empty_option_is_still_refused_and_a_short_one_has_no_details(tmp_path: Path) -> None:
    tool = AskUserTool(tmp_path, lambda _id, _data: None, wait_seconds=0)
    base = {"label": "?", "description": "Choose.", "options": ["a", "  "]}
    assert tool.run(questions=[base]).startswith("error: each option must be a non-empty")
    seen: list[dict[str, Any]] = []
    quick = AskUserTool(tmp_path, lambda _id, data: seen.append(data), wait_seconds=0)
    assert quick.run(questions=[{**base, "options": ["a", "b"]}]) == "No response."
    assert seen[0]["option_details"] == {}
    assert seen[0]["wait_seconds"] == 0.0


def test_two_long_options_that_collide_when_shortened_are_refused_as_not_distinct(tmp_path: Path) -> None:
    tool = AskUserTool(tmp_path, lambda _id, _data: None, wait_seconds=0)
    prefix = "x" * 130
    question = {"label": "?", "description": "Choose.", "options": [prefix + "a", prefix + "b"]}
    assert "distinct" in tool.run(questions=[question])
