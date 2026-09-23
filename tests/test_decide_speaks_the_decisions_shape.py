"""The open System One interface speaks the Decisions shape — study 22, phase 4.

The phase's gate was "round-trip tests in the SDK shape". This package already has a client for that
shape (`chimera/decisions/openrouter.py`), so the round trip is literal: the request our client sends
is one our interface accepts, and the response our interface returns is one our client reads back to
the same reading. Around it: a question the linter rejects is refused before any call; a backend that
fails on one question fails that question only; the route and the command say the same thing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chimera.config import Settings, get_settings
from chimera.decisions import Choice, Decider, Noul, Reading, as_choice
from chimera.decisions.interface import RequestError, decide, parse_request
from chimera.decisions.openrouter import OpenRouterDecisionsBackend


class _Backend:
    """Puts 0.8 on the first option of any question; raises on keys named 'broken'."""

    name = "fake"
    model = "fake-4b"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def instrument(self, question: Any) -> str:
        return json.dumps(OpenRouterDecisionsBackend.question_body(question), sort_keys=True)

    def ask(self, state: str, question: Any) -> Reading:
        self.calls.append(question.key)
        if question.key == "broken":
            raise ConnectionError("server is off")
        choice = as_choice(question)
        rest = 0.2 / (len(choice.options) - 1)
        shares = {o: (0.8 if i == 0 else rest) for i, o in enumerate(choice.options)}
        p = sum(shares[o] for o in choice.event) if choice.event else None
        return Reading(choice=choice.options[0], shares=shares, p=p, resolved_model="fake-4b@Q4")


NOUL = {"type": "noul", "instructions": "Does the log line report a failure?",
        "criteria": {"true": "an error or a crash", "false": "anything else"}}
CHOICE = {"type": "choice", "instructions": "Which subsystem does the line come from?",
          "criteria": {"database": "sql, migrations", "network": "sockets, http", "other": "anything else"}}
SCORE = {"type": "score", "instructions": "How urgent is the line?",
         "criteria": {"low": "informational", "medium": "degraded", "high": "outage"}}
REQUEST = {"state": "ERROR db: connection pool exhausted", "questions": {"failure": NOUL, "subsystem": CHOICE, "urgency": SCORE}}


def test_each_kind_comes_back_in_its_own_shape() -> None:
    out = decide(Decider(_Backend()), REQUEST)
    assert out["model"] == "fake-4b@Q4"
    assert out["answers"]["failure"] == {"noul": pytest.approx(0.8)}
    sub = out["answers"]["subsystem"]
    assert sub["choice"] == "database" and set(sub["probabilities"]) == {"database", "network", "other"}
    assert sub["confidence"] == pytest.approx((3 * 0.8 - 1) / 2)
    urg = out["answers"]["urgency"]
    assert urg["legend"] == ["low", "medium", "high"]
    assert urg["score"] == pytest.approx(0 * 0.8 + 1 * 0.1 + 2 * 0.1)
    assert set(out["receipts"]) == {"failure", "subsystem", "urgency"}
    assert out["receipts"]["failure"]["calibrated"] is False  # ad hoc: no map, and the receipt says so


def test_the_request_our_own_client_sends_is_accepted() -> None:
    client = OpenRouterDecisionsBackend("k")
    for question in (Noul("failure", NOUL["instructions"], criteria=dict(NOUL["criteria"])),
                     Choice("subsystem", CHOICE["instructions"], tuple(CHOICE["criteria"]), criteria=dict(CHOICE["criteria"]))):
        body = client.body("some state", question)
        state, parsed, _ = parse_request(body)
        assert state == "some state"
        assert as_choice(parsed[question.key]).options == as_choice(question).options
        assert as_choice(parsed[question.key]).criteria == as_choice(question).criteria


def test_the_response_our_own_client_reads_is_the_same_reading() -> None:
    out = decide(Decider(_Backend()), REQUEST)
    client = OpenRouterDecisionsBackend("k")
    noul = Noul("failure", NOUL["instructions"], criteria=dict(NOUL["criteria"]))
    reading = client.read({"answers": {"failure": out["answers"]["failure"]}, "model": out["model"]}, noul)
    assert reading.p == pytest.approx(0.8) and reading.choice == "yes" and reading.resolved_model == "fake-4b@Q4"
    choice = Choice("subsystem", CHOICE["instructions"], tuple(CHOICE["criteria"]), criteria=dict(CHOICE["criteria"]))
    reading = client.read({"answers": {"subsystem": out["answers"]["subsystem"]}}, choice)
    assert reading.choice == "database" and reading.shares == pytest.approx(out["answers"]["subsystem"]["probabilities"])


@pytest.mark.parametrize(
    "bad",
    [
        {"state": "", "questions": {"a": NOUL}},
        {"state": "s", "questions": {}},
        {"state": "s", "questions": {"a": {"type": "vote", "instructions": "x"}}},
        {"state": "s", "questions": {"a": {"type": "noul", "instructions": ""}}},
        {"state": "s", "questions": {"a": {"type": "noul", "instructions": "x", "criteria": {"yes": "y"}}}},
        {"state": "s", "questions": {"a": {"type": "choice", "instructions": "x", "criteria": {"only": "one"}}}},
    ],
)
def test_a_request_that_cannot_be_asked_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(RequestError):
        parse_request(bad)


def test_a_question_the_linter_rejects_is_refused_before_any_call() -> None:
    backend = _Backend()
    compound = {"type": "noul", "instructions": "Is the line an error and from the database?"}
    with pytest.raises(RequestError, match="joins two conditions"):
        decide(Decider(backend), {"state": "s", "questions": {"a": compound}})
    polar = {"type": "choice", "instructions": "Pick.", "criteria": {"yes": "y", "no": "n", "maybe": "m"}}
    with pytest.raises(RequestError, match="read as words"):
        decide(Decider(backend), {"state": "s", "questions": {"a": polar}})
    assert backend.calls == []


def test_a_backend_that_fails_on_one_question_fails_that_one_only() -> None:
    out = decide(Decider(_Backend()), {"state": "s", "questions": {"broken": NOUL, "failure": NOUL}})
    assert "error" in out["answers"]["broken"] and out["answers"]["failure"] == {"noul": pytest.approx(0.8)}


# --- the route and the command -------------------------------------------------------------------


@pytest.fixture
def fake_decider(monkeypatch: pytest.MonkeyPatch) -> _Backend:
    import chimera.decisions.factory as factory

    backend = _Backend()
    monkeypatch.setattr(factory, "build_decider", lambda settings, **kw: Decider(backend))
    return backend


def test_the_route_answers_in_the_same_shape_and_422s_a_bad_question(tmp_path: Path, fake_decider: _Backend) -> None:
    from chimera.api import build_api_app

    client = TestClient(build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path))))  # type: ignore[arg-type, call-arg]
    resp = client.post("/api/decide", json=REQUEST)
    assert resp.status_code == 200
    assert resp.json()["answers"] == decide(Decider(_Backend()), REQUEST)["answers"]
    bad = client.post("/api/decide", json={"state": "s", "questions": {"a": {"type": "noul", "instructions": "A and B?"}}})
    assert bad.status_code == 422 and "joins two conditions" in bad.json()["detail"]


def test_the_command_maps_a_question_over_every_line(tmp_path: Path, fake_decider: _Backend, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": {"failure": NOUL}}), encoding="utf-8")
    lines = tmp_path / "in.jsonl"
    lines.write_text(
        "\n".join([json.dumps({"id": "a", "state": "ERROR x"}), json.dumps({"id": "b", "other": 1}),
                   json.dumps({"id": "c", "state": "ok"})]) + "\n",
        encoding="utf-8",
    )
    result_path = tmp_path / "out.jsonl"
    result = CliRunner().invoke(app, ["decide", "-q", str(questions), "--jsonl", str(lines), "-o", str(result_path)])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    rows = [json.loads(x) for x in result_path.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == ["a", "c"]  # the line without a state is skipped, not guessed
    assert rows[0]["answers"]["failure"] == {"noul": pytest.approx(0.8)}
    assert CliRunner().invoke(app, ["decide", "-q", str(questions)]).exit_code == 2  # a state or a file
