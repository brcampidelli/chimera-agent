"""The ecosystem's compat spec (jevcompat: 48 requirements, each cited to the vendor docs) found 2
of 8 open System One servers out of conformance. This is the subset that applies to POST /api/decide
and `chimera.decisions.interface.decide`, written as tests so the wire contract cannot regress
silently and a client written against the ecosystem's format ports to ours. Study 27, phase 0.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.decisions import Decider, Reading, as_choice
from chimera.decisions.interface import RequestError, decide
from chimera.decisions.openrouter import OpenRouterDecisionsBackend

STATE = "ERROR db: connection pool exhausted"
NOUL = {"type": "noul", "instructions": "Does the log report a failure?"}
CHOICE = {"type": "choice", "instructions": "Which colour?",
          "criteria": {"red": "warm", "green": "cool", "blue": "cold"}}
SCORE = {"type": "score", "instructions": "How urgent?", "criteria": ["can wait", "today", "now"]}


def _weight(state: str, instructions: str, option: str) -> int:
    digest = hashlib.sha256(f"{state}|{instructions}|{option}".encode()).hexdigest()
    return int(digest, 16) % 97 + 1


def _choice(n: int) -> dict[str, Any]:
    return {"type": "choice", "instructions": "Pick.",
            "criteria": {f"o{i:03d}": f"meaning {i}" for i in range(n)}}


class _Deterministic:
    """Weights each option by a hash of the state and the question's content — never its key."""

    name = "fake"
    model = "fake-4b"

    def instrument(self, question: Any) -> str:
        return json.dumps(OpenRouterDecisionsBackend.question_body(question), sort_keys=True)

    def ask(self, state: str, question: Any) -> Reading:
        choice = as_choice(question)
        weights = {o: _weight(state, choice.instructions, o) for o in choice.options}
        total = sum(weights.values())
        shares = {o: w / total for o, w in weights.items()}
        chosen = max(shares, key=lambda o: shares[o])
        p = sum(shares[o] for o in choice.event) if choice.event else None
        return Reading(choice=chosen, shares=shares, p=p, resolved_model="fake-4b@Q4")


class _Down:
    """The backend is off: every ask is a halt."""

    name = "fake"
    model = "fake-4b"

    def instrument(self, question: Any) -> str:
        return json.dumps(OpenRouterDecisionsBackend.question_body(question), sort_keys=True)

    def ask(self, state: str, question: Any) -> Reading:
        raise ConnectionError("server is off")


def test_a_distribution_covers_every_option_and_sums_to_one() -> None:
    out = decide(Decider(_Deterministic()), {"state": STATE, "questions": {"c": CHOICE, "u": SCORE}})
    probs = out["answers"]["c"]["probabilities"]
    assert set(probs) == {"red", "green", "blue"} and abs(sum(probs.values()) - 1.0) <= 1e-6
    levels = out["answers"]["u"]["probabilities"]
    assert set(levels) == {"0", "1", "2"} and abs(sum(levels.values()) - 1.0) <= 1e-6


def test_a_score_is_the_expected_level_index() -> None:
    out = decide(Decider(_Deterministic()), {"state": "s", "questions": {"u": SCORE}})
    answer = out["answers"]["u"]
    mine = sum(i * answer["probabilities"][level] for i, level in enumerate(("0", "1", "2")))
    assert answer["score"] == pytest.approx(mine)


def test_a_choice_takes_two_to_255_options() -> None:
    with pytest.raises(RequestError, match="2–255"):
        decide(Decider(_Deterministic()), {"state": "s", "questions": {"k": _choice(1)}})
    with pytest.raises(RequestError, match="255"):
        decide(Decider(_Deterministic()), {"state": "s", "questions": {"k": _choice(256)}})
    for n in (2, 255):
        out = decide(Decider(_Deterministic()), {"state": "s", "questions": {"k": _choice(n)}})
        assert set(out["answers"]["k"]["probabilities"]) == {f"o{i:03d}" for i in range(n)}


def test_answers_do_not_depend_on_question_order_or_ids() -> None:
    a = decide(Decider(_Deterministic()), {"state": STATE, "questions": {"a": CHOICE, "b": SCORE}})
    b = decide(Decider(_Deterministic()), {"state": STATE, "questions": {"y": SCORE, "x": CHOICE}})
    assert a["answers"]["a"] == b["answers"]["x"] and a["answers"]["b"] == b["answers"]["y"]


def test_a_halt_is_an_error_never_a_number() -> None:
    out = decide(Decider(_Down()), {"state": "s", "questions": {"y": NOUL, "c": CHOICE}})
    noul = out["answers"]["y"]
    assert "error" in noul and "noul" not in noul
    choice = out["answers"]["c"]
    assert "error" in choice and "probabilities" not in choice


def test_the_route_refuses_an_out_of_range_choice_with_a_422_and_a_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api import build_api_app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    import chimera.decisions.factory as factory

    monkeypatch.setattr(factory, "build_decider", lambda settings, **kw: Decider(_Deterministic()))
    client = TestClient(build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path))))  # type: ignore[arg-type, call-arg]
    resp = client.post("/api/decide", json={"state": "s", "questions": {"k": _choice(256)}})
    get_settings.cache_clear()
    assert resp.status_code == 422 and "255" in resp.json()["detail"]