"""A cut turn is recorded on every path that can carry one — not only the path that was tested first.

Study 30, item S30-09, after an adversarial review of the first commit. That commit made the step
log, the trace line and both receipts say how many steps the provider cut and how many tool calls
the gateway dropped. The review found four places the same defect still lived:

- the attempt a budget or spend cap cuts short is written through `_partial_attempt`, and replacing
  its line with `pass` left every test green — the path where a cut is MOST likely went unguarded;
- the completion cache keeps only content, model and tokens, so a cut answer it stored came back on
  the next hit as a finished one (and stayed cut for that key forever);
- the router's agreement path built a fresh result from its samples and dropped both fields;
- `_cut_counts` turned a step log without the fields into a clean zero, though `Attempt` promises
  None means "not recorded" (the §2t family: a docstring promising what the code does not do);
- the desktop MCP job summary curated its receipt keys and left both out.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core import Agent, AgentConfig, AutonomousAgent, AutonomousConfig
from chimera.core.autonomous import _cut_counts
from chimera.core.steplog import StepLog
from chimera.core.verify import VerificationResult
from chimera.orchestration.budget import BudgetExceeded
from chimera.providers.gateway import CompletionResult, Message, ToolCall
from chimera.tools import ToolRegistry

# ------------------------------------------------------------------ the attempt a cap cuts short


class _Passes:
    command = "true"

    def verify(self) -> VerificationResult:
        return VerificationResult(passed=True, output="ok")


class _CutThenBroke:
    """First call: a cut step that still asked for one tool and lost another. Second call: the
    ceiling. The loop records step 1 and then stops on budget — the `_partial_attempt` path."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *_a: Any, **_k: Any) -> CompletionResult:
        self.calls += 1
        if self.calls > 1:
            raise BudgetExceeded("delegation budget exhausted: 100/100 tokens")
        return CompletionResult(
            content="", model="fake/model", prompt_tokens=10, completion_tokens=32,
            finish_reason="length", truncated=True, dropped_tool_calls=1,
            tool_calls=[ToolCall(id="c1", name="nao_existe", arguments={})],
        )


def test_an_attempt_the_cap_cut_short_carries_its_cut_counts(tmp_path: Path) -> None:
    from chimera.api.runs import load_runs

    home = tmp_path / "home"
    model = _CutThenBroke()
    worker = Agent(
        model,
        ToolRegistry(),
        AgentConfig(inject_skill_context=False, prefix_nonce="", max_steps=4,
                    trace_path=home / "traces.jsonl"),
    )
    result = AutonomousAgent(
        worker,
        verifier=_Passes(),
        run_log=home / "runs.jsonl",
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
    ).run("escreva o arquivo")

    # The premise, checked rather than assumed: this went through the cap's path, not the verified one.
    assert result.stopped_reason == "spend"
    assert model.calls == 2
    (receipt,) = load_runs(home / "runs.jsonl")
    (attempt,) = receipt.attempts
    assert attempt.truncated_steps == 1
    assert attempt.dropped_tool_calls == 1


# ------------------------------------------------------------------ "not recorded" is not "clean"


def test_a_step_log_without_the_fields_reads_as_not_recorded() -> None:
    """A duck-typed step log that never carried the counts has measured nothing; a zero would say
    it measured a clean run."""
    bare = SimpleNamespace(steplog=SimpleNamespace(steps=[], system_sha=""))

    assert _cut_counts(bare) == (None, None)
    assert _cut_counts(SimpleNamespace()) == (None, None)


def test_a_real_clean_step_log_still_reads_as_zero() -> None:
    """The over-correction guard: a real step log with nothing cut is a measured zero."""
    assert _cut_counts(SimpleNamespace(steplog=StepLog())) == (0, 0)


# ------------------------------------------------------------------ the completion cache


def _response(content: str, finish_reason: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _cached_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, finish_reason: str
) -> tuple[Any, dict[str, int]]:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_CACHE", "1")
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    import litellm

    calls = {"n": 0}

    def fake(*, model: str, **_: Any) -> SimpleNamespace:
        calls["n"] += 1
        return _response("A resposta começa e", finish_reason)

    monkeypatch.setattr(litellm, "completion", fake)
    from chimera.providers import LLMGateway

    return LLMGateway(), calls


def test_a_cut_answer_is_not_served_from_cache_as_a_finished_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gateway, calls = _cached_gateway(monkeypatch, tmp_path, "length")
    msgs = [Message(role="user", content="explique")]

    first = gateway.complete(msgs, model="m", temperature=0.0)
    second = gateway.complete(msgs, model="m", temperature=0.0)

    assert first.truncated is True
    # Asked again, it went to the model again and came back marked cut — not a hit reading clean.
    assert calls["n"] == 2
    assert second.truncated is True


def test_a_finished_answer_is_still_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The guard against switching the cache off for everything."""
    gateway, calls = _cached_gateway(monkeypatch, tmp_path, "stop")
    msgs = [Message(role="user", content="explique")]

    gateway.complete(msgs, model="m", temperature=0.0)
    gateway.complete(msgs, model="m", temperature=0.0)

    assert calls["n"] == 1


# ------------------------------------------------------------------ the router's agreement path


class _Samples:
    def __init__(self, results: list[CompletionResult]) -> None:
        self.results = list(results)

    def complete(self, *_a: Any, **_k: Any) -> CompletionResult:
        return self.results.pop(0)


class _NoFusion:
    def complete(self, *_a: Any, **_k: Any) -> CompletionResult:
        raise AssertionError("the samples agreed; fusion must not run")


def test_the_agreement_result_keeps_a_cut_sample_on_record() -> None:
    from chimera.fusion.router import RoutedBackend

    samples = [
        CompletionResult(content="42", model="s", truncated=True, dropped_tool_calls=1),
        CompletionResult(content="42", model="s"),
        CompletionResult(content="42", model="s", dropped_tool_calls=2),
    ]
    result = RoutedBackend(_Samples(samples), _NoFusion(), agreement_k=3).complete(
        [Message(role="user", content="quanto")]
    )

    assert result.model == "agreement"
    assert result.truncated is True
    assert result.dropped_tool_calls == 3


def test_an_agreement_of_finished_samples_reads_clean() -> None:
    from chimera.fusion.router import RoutedBackend

    samples = [CompletionResult(content="42", model="s") for _ in range(3)]
    result = RoutedBackend(_Samples(samples), _NoFusion(), agreement_k=3).complete(
        [Message(role="user", content="quanto")]
    )

    assert result.truncated is False
    assert result.dropped_tool_calls == 0


# ------------------------------------------------------------------ the desktop MCP job summary


def test_the_desktop_job_summary_carries_the_cut_counts() -> None:
    from chimera.server.desktop_mcp import DesktopMCP

    token = "tok-" + "Q" * 40
    job = {
        "job_id": "j1", "done": True, "session_id": "s1", "turn_id": "t1", "next": 1, "events": [],
        "result": {"answer": "A resposta começa e", "model": "m/x", "system_sha": "abc123",
                   "truncated_steps": 1, "dropped_tool_calls": 2},
    }

    def http(method: str, url: str, tok: str, body: Any, timeout: float) -> tuple[int, Any]:
        return 200, {"status": 200, "job": job}

    def found() -> dict[str, Any]:
        return {"url": "http://127.0.0.1:65009", "token": token, "pid": 1, "version": "x",
                "full": False}

    text = DesktopMCP(discover=found, http=http).dispatch("desktop_send", {"message": "explique"})

    assert '"truncated_steps": 1' in text
    assert '"dropped_tool_calls": 2' in text
