"""A cut completion, or a tool call the gateway could not parse, is recorded — never a silent normal turn.

Study 30, item S30-09. The gateway has computed `CompletionResult.truncated` since the provider's
`finish_reason` was first read, and its docstring says why it matters: a cut response can carry a
half-written argument or lose the call entirely. Nothing in the agent loop read it. With a 32k
output ceiling a turn the provider cut mid-sentence went into the step log, the trace and the
receipt exactly like a finished one.

The gateway also DROPS a tool call whose argument string does not parse (that was a fix: serving it
as `arguments={}` ran the tool with nothing and blamed the model). But the drop left a log line and
nothing else, so a step whose every call was dropped arrived at the loop as a step with no call —
which the loop reads as the final answer. Nothing done, and nothing on record to say why.

This ships RECORD-ONLY: the step, the trace line and both receipts say how many steps were cut and
how many calls were dropped. Behaviour does not change — a "resend valid JSON" observation and a
"continue from the cut" resumption stay off until a census over real traces shows the rate is not
trivial and a paired run shows they help. A guard written only in a docstring is the failure this
closes (§2t); a behaviour nobody measured is not the fix for it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core import Agent, AgentConfig, AutonomousAgent, AutonomousConfig
from chimera.core.steplog import StepLog, StepRecord
from chimera.core.verify import VerificationResult
from chimera.interface import ChatSession
from chimera.providers.gateway import CompletionResult, LLMGateway
from chimera.tools import ToolRegistry

# ------------------------------------------------------------------ the gateway counts what it drops


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, name: str, arguments: str, id: str = "c1") -> None:
        self.id = id
        self.function = _Fn(name, arguments)


def _normalizar(finish_reason: str, calls: list[_Call]) -> CompletionResult:
    message = SimpleNamespace(content="", tool_calls=calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return LLMGateway._normalize(SimpleNamespace(choices=[choice], usage=None), "m")  # noqa: SLF001


def test_the_batch_path_counts_the_call_it_dropped() -> None:
    """The drop itself is right and stays. What was missing is that it happened: `tool_calls` is
    None either way, and None is also what a step that never asked for a tool returns."""
    cortado = _normalizar("length", [_Call("write_file", '{"path": "src/ap')])

    assert cortado.tool_calls is None
    assert cortado.dropped_tool_calls == 1


def test_a_partly_dropped_step_counts_only_the_broken_call() -> None:
    resultado = _normalizar(
        "length",
        [_Call("read_file", '{"path": "a.py"}', "c1"), _Call("write_file", '{"pa', "c2")],
    )

    assert [c.name for c in (resultado.tool_calls or [])] == ["read_file"]
    assert resultado.dropped_tool_calls == 1


def test_a_whole_call_drops_nothing() -> None:
    """The guard against counting every call as dropped."""
    assert _normalizar("tool_calls", [_Call("read_file", '{"path": "a.py"}')]).dropped_tool_calls == 0


def _tool_chunk(name: str | None, arguments: str) -> SimpleNamespace:
    fn = SimpleNamespace(name=name, arguments=arguments)
    delta = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(index=0, id="c", function=fn)])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)


def _end_chunk(reason: str) -> SimpleNamespace:
    delta = SimpleNamespace(content=None, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=reason)], usage=None)


def test_the_stream_path_counts_the_call_it_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The streaming path is the one the desktop uses, and it has its own copy of the drop."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    get_settings.cache_clear()
    gw = LLMGateway()

    def _stream(**_kwargs: Any) -> Any:
        yield _tool_chunk("write_file", '{"path": "src/ap')
        yield _end_chunk("length")

    monkeypatch.setattr(gw, "_stream_once", _stream, raising=False)
    result = gw.stream_complete([{"role": "user", "content": "oi"}], model="prov/m")

    assert result.tool_calls is None
    assert result.truncated is True
    assert result.dropped_tool_calls == 1


# ------------------------------------------------------------------ the loop records both


class _Scripted:
    """A model that returns the same result on every call — enough for the loop to run for real."""

    def __init__(self, result: CompletionResult) -> None:
        self.result = result

    def complete(self, *_a: Any, **_k: Any) -> CompletionResult:
        return self.result


def _cut() -> CompletionResult:
    return CompletionResult(
        content="A resposta começa e", model="fake/model", prompt_tokens=10,
        completion_tokens=32, finish_reason="length", truncated=True,
    )


def _all_dropped() -> CompletionResult:
    return CompletionResult(
        content="Pronto.", model="fake/model", prompt_tokens=10, completion_tokens=5,
        finish_reason="length", truncated=True, dropped_tool_calls=2,
    )


def _agent(model: Any, trace: Path | None = None) -> Agent:
    return Agent(
        model,
        ToolRegistry(),
        AgentConfig(
            inject_skill_context=False, prefix_nonce="", max_steps=1, trace_path=trace,
        ),
    )


def test_a_cut_step_is_recorded_as_cut(tmp_path: Path) -> None:
    trace = tmp_path / "traces.jsonl"
    result = _agent(_Scripted(_cut()), trace).run("explique o módulo")

    step = result.steplog.steps[0]
    assert step.truncated is True
    assert step.as_dict()["truncated"] is True
    assert result.steplog.truncated_steps == 1
    # In the trace line too, beside the context peak, so a census can be run over traces already
    # on disk without re-deriving anything from the step contents.
    line = json.loads(trace.read_text(encoding="utf-8").splitlines()[-1])
    assert line["truncated_steps"] == 1
    assert line["steps"][0]["truncated"] is True


def test_a_step_whose_every_call_was_dropped_says_so(tmp_path: Path) -> None:
    """The quiet case: no call reaches the loop, so the step reads as an answer. The run still ends
    the way it did — record-only — but the record now says two calls were lost on the way."""
    trace = tmp_path / "traces.jsonl"
    result = _agent(_Scripted(_all_dropped()), trace).run("escreva o arquivo")

    assert result.steplog.steps[0].dropped_tool_calls == 2
    assert result.steplog.dropped_tool_calls == 2
    line = json.loads(trace.read_text(encoding="utf-8").splitlines()[-1])
    assert line["dropped_tool_calls"] == 2
    assert line["steps"][0]["dropped_tool_calls"] == 2


def test_a_finished_step_is_not_flagged() -> None:
    """The over-correction guard: a healthy run must read as clean, or the census counts noise."""
    healthy = CompletionResult(content="pronto", model="fake/model", finish_reason="stop")
    result = _agent(_Scripted(healthy)).run("diga pronto")

    assert result.steplog.steps[0].truncated is False
    assert result.steplog.steps[0].dropped_tool_calls == 0
    assert result.steplog.truncated_steps == 0
    assert result.steplog.dropped_tool_calls == 0


def test_a_backend_without_the_fields_reads_as_clean() -> None:
    """Several backends here return duck-typed results. Missing fields are "nothing reported" and
    must not crash the loop that records them."""

    class _Bare:
        def complete(self, *_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(
                content="pronto", model="m", tool_calls=None, prompt_tokens=1,
                completion_tokens=1, cache_read_tokens=None, cache_write_tokens=None,
                route_meta=None,
            )

    result = _agent(_Bare()).run("diga pronto")
    assert result.steplog.truncated_steps == 0


def test_the_log_totals_count_steps_and_sum_calls() -> None:
    log = StepLog()
    log.add(StepRecord(index=1, prompt_tokens=1, completion_tokens=1, model="m", truncated=True,
                       dropped_tool_calls=1))
    log.add(StepRecord(index=2, prompt_tokens=1, completion_tokens=1, model="m"))
    log.add(StepRecord(index=3, prompt_tokens=1, completion_tokens=1, model="m", truncated=True,
                       dropped_tool_calls=2))

    assert log.truncated_steps == 2
    assert log.dropped_tool_calls == 3
    assert log.as_dict()["truncated_steps"] == 2
    assert log.as_dict()["dropped_tool_calls"] == 3


# ------------------------------------------------------------------ the receipts carry them


class _Passes:
    command = "true"

    def verify(self) -> VerificationResult:
        return VerificationResult(passed=True, output="ok")


def test_the_run_receipt_carries_the_counts_and_the_api_returns_them(tmp_path: Path) -> None:
    from chimera.api import build_api_app
    from chimera.api.runs import load_runs

    home = tmp_path / "home"
    worker = Agent(
        _Scripted(_all_dropped()),
        ToolRegistry(),
        AgentConfig(inject_skill_context=False, prefix_nonce="", max_steps=1,
                    trace_path=home / "traces.jsonl"),
    )
    AutonomousAgent(
        worker,
        verifier=_Passes(),
        run_log=home / "runs.jsonl",
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    ).run("escreva o arquivo")

    (receipt,) = load_runs(home / "runs.jsonl")
    assert receipt.attempts[0].truncated_steps == 1
    assert receipt.attempts[0].dropped_tool_calls == 2

    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(
        build_api_app(lambda: ChatSession(Agent(_Scripted(_cut()), ToolRegistry())),
                      settings=settings)
    )
    listed = client.get("/api/runs").json()
    assert listed[0]["attempts"][0]["truncated_steps"] == 1
    assert listed[0]["attempts"][0]["dropped_tool_calls"] == 2


def test_an_old_receipt_reads_as_not_recorded() -> None:
    """A row from before the fields has no count to show: None, not a clean zero it never measured."""
    from chimera.api.runs import RunReceipt

    old = RunReceipt.model_validate_json(
        '{"ts": "t", "task": "a", "attempts": [{"index": 1, "verified": true}]}'
    )
    assert old.attempts[0].truncated_steps is None
    assert old.attempts[0].dropped_tool_calls is None


def _frames(text: str) -> dict[str, dict[str, Any]]:
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


def test_the_code_turn_receipt_carries_the_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api import build_api_app

    class _Model(_Scripted):
        def __init__(self, *_a: Any, **_k: Any) -> None:
            super().__init__(_all_dropped())

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", _Model)
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(
        build_api_app(
            lambda: ChatSession(Agent(_Model(), ToolRegistry())), workspace=ws, settings=settings
        )
    )
    response = client.post("/api/code/turn", json={"message": "escreva", "stream": False})
    assert response.status_code == 200
    done = _frames(response.text)["done"]

    assert done["truncated_steps"] >= 1
    assert done["dropped_tool_calls"] >= 2

