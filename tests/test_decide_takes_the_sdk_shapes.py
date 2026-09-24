"""The open interface takes the vendor SDK's documented request shapes — study 24, item A1 (item 4 of the plan).

`chimera/decisions/interface.py` refused four things `typesafe-sdk-python` (MIT) documents in
`src/typesafe_sdk/_schemas/models.py`:
- a `state` that is an object or a list;
- a missing or structured `instructions`;
- a criterion meaning that is an object, a list or `null`;
- a Score whose criteria are a *list* of level meanings.

JevBench hit the object state the morning of study 24. The interface's own round-trip test compared
us with our own client, so it could not see any of this. The fixtures below are the SDK schema's own
`examples=`, copied.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from chimera.decisions import Decider, Reading, as_choice
from chimera.decisions.interface import RequestError, decide, parse_request, render
from chimera.decisions.lint import errors
from chimera.decisions.openrouter import OpenRouterDecisionsBackend
from chimera.tools.decide import DecideTool

SDK_STATE_OBJECT = {"message": "Please help.", "subject": "Duplicate charge"}
SDK_NOUL = {"type": "noul", "instructions": "Is this message about billing?"}
SDK_NOUL_STRUCTURED = {
    "type": "noul",
    "instructions": {"task": "Identify unsolicited advertising."},
    "criteria": {"false": "A legitimate conversation", "true": "Unsolicited advertising"},
}
SDK_CHOICE = {
    "type": "choice",
    "instructions": "What is the tone of this message?",
    "criteria": {"angry": "An upset or hostile message", "calm": "A neutral or polite message", "excited": "An enthusiastic or eager message"},
}
SDK_SCORE = {"type": "score", "instructions": "How urgent is this message?", "criteria": ["Can wait", "Needs attention this week", "Needs attention today"]}


class _Backend:
    """0.8 on the first option; records the state text each question was asked about."""

    name = "fake"
    model = "fake-4b"

    def __init__(self) -> None:
        self.states: list[str] = []

    def instrument(self, question: Any) -> str:
        return json.dumps(OpenRouterDecisionsBackend.question_body(question), sort_keys=True)

    def ask(self, state: str, question: Any) -> Reading:
        self.states.append(state)
        choice = as_choice(question)
        rest = 0.2 / (len(choice.options) - 1)
        shares = {o: (0.8 if i == 0 else rest) for i, o in enumerate(choice.options)}
        p = sum(shares[o] for o in choice.event) if choice.event else None
        return Reading(choice=choice.options[0], shares=shares, p=p)


def test_an_object_state_is_accepted_and_reaches_the_model_as_compact_json() -> None:
    backend = _Backend()
    out = decide(Decider(backend), {"state": SDK_STATE_OBJECT, "questions": {"billing": SDK_NOUL}})
    assert out["answers"]["billing"]["type"] == "noul"
    assert backend.states == [json.dumps(SDK_STATE_OBJECT, ensure_ascii=False)]  # the JevBench adapters' convention
    list_state = [{"role": "user", "text": "refund?"}]
    state, _, _ = parse_request({"state": list_state, "questions": {"billing": SDK_NOUL}})
    assert state == render(list_state)


def test_a_structured_instruction_and_criteria_only_noul_are_accepted() -> None:
    _, questions, _ = parse_request({"state": "s", "questions": {"spam": SDK_NOUL_STRUCTURED}})
    noul = questions["spam"]
    assert noul.instructions == json.dumps({"task": "Identify unsolicited advertising."})
    assert noul.criteria == {"false": "A legitimate conversation", "true": "Unsolicited advertising"}
    _, questions, _ = parse_request({"state": "s", "questions": {"spam": {"type": "noul", "criteria": {"true": "advertising"}}}})
    assert questions["spam"].instructions == ""


def test_a_null_meaning_leaves_the_option_to_its_name_and_an_object_meaning_is_rendered() -> None:
    criteria = {"refund": {"what": "wants money back", "examples": ["charged twice"]}, "other": None}
    _, questions, _ = parse_request({"state": "s", "questions": {"intent": {"type": "choice", "instructions": "Which intent?", "criteria": criteria}}})
    choice = questions["intent"]
    assert choice.options == ("refund", "other")
    assert choice.criteria == {"refund": json.dumps(criteria["refund"], ensure_ascii=False)}


def test_the_sdk_score_list_becomes_levels_zero_up_and_is_not_refused_by_the_linter() -> None:
    _, questions, _ = parse_request({"state": "s", "questions": {"urgency": SDK_SCORE}})
    score = questions["urgency"]
    assert score.levels == ("0", "1", "2")
    assert score.criteria == {"0": "Can wait", "1": "Needs attention this week", "2": "Needs attention today"}
    assert errors(score) == []  # numbers WITH a meaning each are neutral identifiers (I3)


def test_the_answers_come_back_in_the_sdk_answer_shapes() -> None:
    out = decide(Decider(_Backend()), {
        "state": SDK_STATE_OBJECT,
        "questions": {"billing": SDK_NOUL, "tone": SDK_CHOICE, "urgency": SDK_SCORE},
    })
    billing, tone, urgency = (out["answers"][k] for k in ("billing", "tone", "urgency"))
    assert set(billing) == {"type", "noul"}
    assert set(tone) == {"type", "choice", "confidence", "probabilities"} and tone["choice"] == "angry"
    assert set(urgency) == {"type", "score", "confidence", "legend", "probabilities"}
    assert urgency["legend"] == {"0": "Can wait", "1": "Needs attention this week", "2": "Needs attention today"}
    assert set(urgency["probabilities"]) == set(urgency["legend"])  # "using the same keys as legend"
    assert urgency["score"] == pytest.approx(0 * 0.8 + 1 * 0.1 + 2 * 0.1)


def test_our_own_strict_client_reads_the_score_answer_back() -> None:
    out = decide(Decider(_Backend()), {"state": "s", "questions": {"urgency": SDK_SCORE}})
    _, questions, _ = parse_request({"state": "s", "questions": {"urgency": SDK_SCORE}})
    reading = OpenRouterDecisionsBackend("k").read({"answers": out["answers"]}, questions["urgency"])
    assert reading.choice == "0" and reading.shares == pytest.approx(out["answers"]["urgency"]["probabilities"])


@pytest.mark.parametrize(
    "body",
    [
        {"state": 5, "questions": {"b": SDK_NOUL}},
        {"state": {}, "questions": {"b": SDK_NOUL}},
        {"state": [], "questions": {"b": SDK_NOUL}},
        {"state": "s", "questions": {"b": {"type": "noul", "instructions": 5}}},
        {"state": "s", "questions": {"b": {"type": "noul"}}},  # neither instructions nor criteria
        {"state": "s", "questions": {"u": {"type": "score", "instructions": "How urgent?", "criteria": ["low", None]}}},
        {"state": "s", "questions": {"u": {"type": "score", "instructions": "How urgent?", "criteria": ["only one"]}}},
        {"state": "s", "questions": {"t": {"type": "choice", "instructions": "Tone?", "criteria": {"a": 3, "b": "x"}}}},
        {"state": "s", "questions": {"u": {"type": "score", "instructions": "Rate.", "criteria": {"1": "", "2": "", "3": ""}}}},  # numbers naming nothing
    ],
)
def test_what_the_sdk_does_not_allow_or_names_nothing_is_still_refused(body: dict[str, Any]) -> None:
    with pytest.raises(RequestError):
        parse_request(body)


def test_the_agent_tool_passes_an_object_state_through_instead_of_its_python_repr() -> None:
    backend = _Backend()
    out = json.loads(DecideTool(Decider(backend)).run(questions={"billing": SDK_NOUL}, state=SDK_STATE_OBJECT))
    assert out["answers"]["billing"]["type"] == "noul"
    assert backend.states == [json.dumps(SDK_STATE_OBJECT, ensure_ascii=False)]
    assert "'message'" not in backend.states[0]  # not str(dict)


def test_the_route_takes_the_sdk_shapes_too(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route validates its body with its own model (`DecideIn`) before the interface sees it, so the
    interface accepting a shape is not enough — the model has to."""
    from fastapi.testclient import TestClient

    import chimera.decisions.factory as factory
    from chimera.api import build_api_app
    from chimera.config import Settings

    monkeypatch.setattr(factory, "build_decider", lambda settings, **kw: Decider(_Backend()))
    client = TestClient(build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path))))  # type: ignore[arg-type, call-arg]
    body = {"state": SDK_STATE_OBJECT, "questions": {"spam": SDK_NOUL_STRUCTURED, "urgency": SDK_SCORE, "tone": SDK_CHOICE}}
    resp = client.post("/api/decide", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["answers"]["urgency"]["legend"]["2"] == "Needs attention today"
