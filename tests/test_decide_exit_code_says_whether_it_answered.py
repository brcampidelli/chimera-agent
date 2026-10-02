"""`chimera decide` exited 0 even when a question halted (the backend was down, the state
overflowed), so a CI step or a shell script could not tell "answered" from "failed" — an
infrastructure error read as a verdict. The contract: 0 every question answered, 1 at least one
question failed (the output is still printed in full first), 2 usage or a question the linter
refuses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.config import get_settings
from chimera.decisions import Decider, Reading, as_choice
from chimera.decisions.openrouter import OpenRouterDecisionsBackend


class _Backend:
    """Puts 0.8 on the first option; a question keyed 'broken' or a state holding 'ERROR' halts."""

    name = "fake"
    model = "fake-4b"

    def instrument(self, question: Any) -> str:
        return json.dumps(OpenRouterDecisionsBackend.question_body(question), sort_keys=True)

    def ask(self, state: str, question: Any) -> Reading:
        if question.key == "broken" or "ERROR" in state:
            raise ConnectionError("server is off")
        choice = as_choice(question)
        rest = 0.2 / (len(choice.options) - 1)
        shares = {o: (0.8 if i == 0 else rest) for i, o in enumerate(choice.options)}
        p = sum(shares[o] for o in choice.event) if choice.event else None
        return Reading(choice=choice.options[0], shares=shares, p=p, resolved_model="fake-4b@Q4")


NOUL = {"type": "noul", "instructions": "Does the log line report a failure?",
        "criteria": {"true": "an error or a crash", "false": "anything else"}}


@pytest.fixture
def fake_decider(monkeypatch: pytest.MonkeyPatch) -> _Backend:
    import chimera.decisions.factory as factory

    backend = _Backend()
    monkeypatch.setattr(factory, "build_decider", lambda settings, **kw: Decider(backend))
    return backend


def test_every_answer_is_exit_zero(
    tmp_path: Path, fake_decider: _Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": {"failure": NOUL}}), encoding="utf-8")
    result = CliRunner().invoke(app, ["decide", "-q", str(questions), "--state", "all good"])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output


def test_a_halted_question_is_exit_one_and_still_prints(
    tmp_path: Path, fake_decider: _Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": {"broken": NOUL}}), encoding="utf-8")
    result = CliRunner().invoke(app, ["decide", "-q", str(questions), "--state", "all good"])
    get_settings.cache_clear()
    assert result.exit_code == 1, result.output
    assert '"broken"' in result.output and "ConnectionError: server is off" in result.output


def test_a_halt_in_any_jsonl_line_is_exit_one(
    tmp_path: Path, fake_decider: _Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": {"failure": NOUL}}), encoding="utf-8")
    lines = tmp_path / "in.jsonl"
    lines.write_text(
        "\n".join([json.dumps({"id": "a", "state": "ERROR pool exhausted"}),
                   json.dumps({"id": "b", "state": "all good"})]) + "\n",
        encoding="utf-8",
    )
    result_path = tmp_path / "out.jsonl"
    result = CliRunner().invoke(
        app, ["decide", "-q", str(questions), "--jsonl", str(lines), "-o", str(result_path)]
    )
    get_settings.cache_clear()
    assert result.exit_code == 1, result.output
    rows = [json.loads(x) for x in result_path.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == ["a", "b"]  # both lines were written, the halt included
    assert "ConnectionError" in rows[0]["answers"]["failure"]["error"]
    assert rows[1]["answers"]["failure"] == {"type": "noul", "noul": pytest.approx(0.8)}