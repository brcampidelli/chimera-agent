"""P0-09: a retry that says it escalated has to ask for something different, and never fuse.

The defect these tests pin: the retry of an autonomous run was "escalated to fusion" on an agent whose
every model call carries tools. The router sends a turn with tools to ONE model, so the retry ran on the
same model at the same effort, and the receipt said it had escalated. Nothing errored. The turns that
carry no `tools` (the closing call, the empty-reply retry) were the only ones that reached fusion, which
is the opposite of "fusion stays out of a run with tools".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.runs import build_receipt
from chimera.config import Settings
from chimera.core import Agent, AgentConfig, AutonomousAgent, AutonomousConfig
from chimera.core.agent import AgentResult
from chimera.core.escalation import Escalation, build_escalation, ladder_of, next_step
from chimera.core.verify import VerificationResult
from chimera.fusion.cascade import CascadeBackend, CascadeConfig
from chimera.fusion.router import RoutedBackend, RoutingPolicy
from chimera.providers import CompletionResult, ToolCall
from chimera.providers.fusion_guard import fusion_forbidden
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool

LADDER = ["vendor/weak", "vendor/mid", "vendor/top"]


# --------------------------------------------------------------------------- the step itself


def test_effort_is_raised_before_the_model_changes() -> None:
    assert next_step("vendor/mid#low", LADDER) == "vendor/mid#medium"
    assert next_step("vendor/mid#medium", LADDER) == "vendor/mid#high"
    # An unsuffixed slug is the provider's default, read as medium.
    assert next_step("vendor/mid", LADDER) == "vendor/mid#high"


def test_with_effort_exhausted_the_next_single_model_up_starts_at_medium() -> None:
    assert next_step("vendor/weak#high", LADDER) == "vendor/mid#medium"
    assert next_step("vendor/mid#high", LADDER) == "vendor/top#medium"


def test_the_ceiling_returns_the_slug_unchanged_instead_of_inventing_a_step() -> None:
    assert next_step("vendor/top#high", LADDER) == "vendor/top#high"


def test_a_model_outside_the_ladder_climbs_straight_to_the_top() -> None:
    assert next_step("custom/one#high", LADDER) == "vendor/top#medium"


def test_a_route_that_drops_the_effort_variant_skips_to_the_next_model() -> None:
    # Raising effort on a route that never sends it would be a retry identical to the first, with a
    # slug in the receipt that never reached the wire.
    nowhere = lambda _slug: False  # noqa: E731
    assert next_step("vendor/mid", LADDER, takes_effort=nowhere) == "vendor/top"


def test_the_ladder_is_weak_then_default_then_top_without_repeats(tmp_path: Path) -> None:
    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_DEFAULT_MODEL="openrouter/vendor/mid",
        CHIMERA_WEAK_MODEL="openrouter/vendor/weak",
        CHIMERA_ORCHESTRATOR_MODEL="openrouter/vendor/mid",  # top == default: one rung, not two
    )
    assert ladder_of(settings) == ["openrouter/vendor/weak", "openrouter/vendor/mid"]


# --------------------------------------------------------------- the run: what each attempt asks for


class _Spy:
    """A gateway that answers at once and remembers which model each call asked for."""

    def __init__(self) -> None:
        self.models: list[str | None] = []

    def complete(self, messages: list[Any], *, model: str | None = None, **kwargs: Any) -> CompletionResult:
        self.models.append(model)
        return CompletionResult(content="done", model=model or "unset")

    def takes_effort(self, model: str | None, *, thinking: bool | None = None) -> bool:
        from chimera.providers.gateway import thinking_overrides_effort

        return not thinking_overrides_effort(model or "", thinking)


class _FailsFirst:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def verify(self) -> VerificationResult:
        self.calls += 1
        ok = self.calls > self.fail_times
        return VerificationResult(passed=ok, output="" if ok else "tests failed")


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


def _run(tmp_path: Path, fail_times: int, attempts: int) -> tuple[Any, _Spy]:
    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_DEFAULT_MODEL="openrouter/vendor/mid",
        CHIMERA_WEAK_MODEL="openrouter/vendor/weak",
        CHIMERA_ORCHESTRATOR_MODEL="openrouter/vendor/top",
    )
    spy = _Spy()
    cfg = AgentConfig(model="openrouter/vendor/mid", max_steps=2)
    registry = _echo_registry()
    auto = AutonomousAgent(
        Agent(spy, registry, cfg),
        escalation=build_escalation(spy, settings, registry, cfg),
        verifier=_FailsFirst(fail_times),
        config=AutonomousConfig(max_attempts=attempts, use_planner=False, use_manager=False),
    )
    return auto.run("do the thing"), spy


def test_the_escalated_attempt_asks_for_a_different_effort_than_the_first(tmp_path: Path) -> None:
    result, spy = _run(tmp_path, fail_times=1, attempts=2)

    first, second = result.attempts
    assert (first.requested_model, first.effort) == ("openrouter/vendor/mid", "")
    assert (second.requested_model, second.effort) == ("openrouter/vendor/mid#high", "high")
    # And the call really carried it: the record is of the request, not a label.
    assert spy.models == ["openrouter/vendor/mid", "openrouter/vendor/mid#high"]


def test_a_second_retry_climbs_to_the_next_single_model(tmp_path: Path) -> None:
    result, spy = _run(tmp_path, fail_times=2, attempts=3)

    assert [a.requested_model for a in result.attempts] == [
        "openrouter/vendor/mid",
        "openrouter/vendor/mid#high",
        "openrouter/vendor/top#medium",
    ]
    assert spy.models[-1] == "openrouter/vendor/top#medium"


def test_the_receipt_keeps_the_model_and_effort_of_every_attempt(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, fail_times=1, attempts=2)

    receipt = build_receipt(result, "do the thing", None, "2026-11-14T00:00:00+00:00")

    assert [(a.requested_model, a.effort) for a in receipt.attempts] == [
        ("openrouter/vendor/mid", ""),
        ("openrouter/vendor/mid#high", "high"),
    ]


def test_a_run_that_never_retries_builds_no_escalated_worker() -> None:
    built: list[str] = []
    ladder = Escalation("vendor/mid", LADDER, lambda slug: built.append(slug) or object())

    assert ladder.model_for(1) == "vendor/mid"
    assert built == []  # asking what attempt 1 uses does not build anything
    ladder.worker_for(2)
    ladder.worker_for(2)
    assert built == ["vendor/mid#high"]  # built once, on the first retry, and reused


# ----------------------------------------------------- fusion never joins a run that carries tools


class _SingleSpy:
    """One model. Narrates on the first call (which makes the loop nudge), then calls a tool while
    tools are offered, and answers a tool-less call (the closing one) with text."""

    def __init__(self) -> None:
        self.calls: list[bool] = []  # True when the call carried tools
        self._first = True

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        self.calls.append(bool(tools))
        if tools is None:
            return CompletionResult(content="closing answer", model="single")
        if self._first:
            self._first = False
            return CompletionResult(content="You should run `git merge x` yourself.", model="single")
        return CompletionResult(
            content="", model="single",
            tool_calls=[ToolCall(id="c", name="echo", arguments={"text": "x"})],
        )


class _FusionSpy:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content="fused", model="fusion")


def _routed(fusion: _FusionSpy, single: _SingleSpy) -> Any:
    return RoutedBackend(single, fusion, RoutingPolicy(mode="always"))  # type: ignore[arg-type]


def _cascade(fusion: _FusionSpy, single: _SingleSpy) -> Any:
    return CascadeBackend(
        single, fusion, CascadeConfig(weak="vendor/weak", mid="vendor/mid"),  # type: ignore[arg-type]
        policy=RoutingPolicy(mode="always"),
    )


@pytest.mark.parametrize("build", [_routed, _cascade], ids=["router", "cascade"])
def test_a_tool_run_makes_zero_fusion_calls_including_the_nudge_and_the_closing_turn(build: Any) -> None:
    fusion, single = _FusionSpy(), _SingleSpy()
    # max_steps=3: narration (nudged), a tool call, a tool call, then the step ceiling forces a
    # tool-less closing call — the turn that used to be routed to fusion by the absence of `tools`.
    agent = Agent(
        build(fusion, single), _echo_registry(), AgentConfig(max_steps=3, insist_on_action=True)
    )

    result = agent.run("fix the merge")

    assert result.stopped_reason == "max_steps"
    assert False in single.calls  # the closing call happened and was answered by the one model
    assert result.answer == "closing answer"
    assert fusion.calls == 0


def test_outside_a_tool_run_the_router_still_fuses() -> None:
    # The guard is scoped to agents that carry tools; a pure-reasoning turn keeps its behaviour.
    fusion, single = _FusionSpy(), _SingleSpy()
    backend = _routed(fusion, single)

    backend.complete([{"role": "user", "content": "compare these designs"}])
    assert fusion.calls == 1

    with fusion_forbidden():
        backend.complete([{"role": "user", "content": "compare these designs"}])
    assert fusion.calls == 1  # unchanged: the second call went to the single model


def test_an_agent_without_tools_is_not_fenced_off_from_fusion() -> None:
    fusion, single = _FusionSpy(), _SingleSpy()
    agent = Agent(_routed(fusion, single), ToolRegistry(), AgentConfig(max_steps=2))

    agent.run("compare these designs")

    assert fusion.calls == 1


# ---------------------------------------------------------------- at the ceiling there is no escalation


def _auto(
    first: str, tmp_path: Path, attempts: int, fail_times: int, thinking: bool | None = None
) -> tuple[AutonomousAgent, _Spy]:
    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_DEFAULT_MODEL="openrouter/vendor/mid",
        CHIMERA_WEAK_MODEL="openrouter/vendor/weak",
        CHIMERA_ORCHESTRATOR_MODEL="openrouter/vendor/top",
    )
    spy = _Spy()
    cfg = AgentConfig(model=first, max_steps=2, thinking=thinking)
    registry = _echo_registry()
    auto = AutonomousAgent(
        Agent(spy, registry, cfg),
        escalation=build_escalation(spy, settings, registry, cfg),
        verifier=_FailsFirst(fail_times),
        config=AutonomousConfig(max_attempts=attempts, use_planner=False, use_manager=False),
    )
    return auto, spy


def test_at_the_ceiling_the_retry_reuses_the_worker_and_is_not_announced_as_an_escalation(
    tmp_path: Path,
) -> None:
    import logging

    class _Records(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=logging.DEBUG)
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())

    # The `chimera` logger does not propagate once its own handler is installed, so caplog sees nothing.
    logger = logging.getLogger("chimera.core.autonomous")
    records, before = _Records(), logger.level
    logger.addHandler(records)
    logger.setLevel(logging.DEBUG)
    try:
        auto, spy = _auto("openrouter/vendor/top#high", tmp_path, attempts=2, fail_times=1)
        result = auto.run("do the thing")
    finally:
        logger.removeHandler(records)
        logger.setLevel(before)

    assert spy.models == ["openrouter/vendor/top#high"] * 2
    assert auto._worker_for(2) is auto.worker  # no fresh agent with the same slug
    text = " | ".join(records.messages)
    assert "no escalation left" in text
    assert "escalating retry" not in text
    assert [a.requested_model for a in result.attempts] == ["openrouter/vendor/top#high"] * 2


def test_at_the_ceiling_the_probe_arm_is_not_labelled_escalate(tmp_path: Path) -> None:
    arms: list[str] = []

    class _Log:
        def record(self, *, arm: str, proxy: float, reward: float) -> None:
            arms.append(arm)

    class _Approves:
        def review(self, task: str, answer: str, context: str) -> Any:
            from chimera.core.supervisor import Review

            return Review(approved=True, feedback="")

    auto, _ = _auto("openrouter/vendor/top#high", tmp_path, attempts=2, fail_times=1)
    auto.probe_log = _Log()  # type: ignore[assignment]
    auto.manager = _Approves()  # type: ignore[assignment]
    auto.config.use_manager = True
    auto.run("do the thing")

    assert arms and "escalate" not in arms


# ------------------------------------------- a retry never steps down from a model that already ran


def test_a_retry_never_descends_below_the_highest_rung_already_used() -> None:
    ladder = Escalation("vendor/weak", LADDER, lambda slug: slug)
    assert ladder.model_for(2) == "vendor/weak#high"
    # `--escalate-on-tool-loop` ran vendor/top inside attempt 2: attempt 3 may not go back to mid.
    ladder.observe("vendor/top")
    assert ladder.model_for(3) == "vendor/top#high"
    # And what was already asked is not rewritten by a later observation.
    assert ladder.model_for(2) == "vendor/weak#high"


def test_the_floor_reaches_the_retry_through_the_autonomous_loop(tmp_path: Path) -> None:
    class _Jumps(_Spy):
        def complete(self, messages: list[Any], *, model: str | None = None, **kw: Any) -> CompletionResult:
            self.models.append(model)
            # The first attempt ends on the top model, as if the tool-loop breaker escalated it.
            return CompletionResult(
                content="done", model="openrouter/vendor/top" if len(self.models) == 1 else (model or "")
            )

    auto, _ = _auto("openrouter/vendor/weak", tmp_path, attempts=2, fail_times=1)
    spy = _Jumps()
    auto.worker.backend = spy  # type: ignore[attr-defined]
    assert auto.escalation is not None
    result = auto.run("do the thing")

    assert result.attempts[1].requested_model == "openrouter/vendor/top#high"


# ----------------------------------------------- thinking off: what the wire carries, not the slug


def test_with_thinking_off_the_retry_changes_model_because_effort_would_not_reach_the_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import httpx

    from chimera.config import get_settings
    from chimera.providers import LLMGateway

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "openrouter/vendor/mid")
    monkeypatch.setenv("CHIMERA_WEAK_MODEL", "openrouter/vendor/weak")
    monkeypatch.setenv("CHIMERA_ORCHESTRATOR_MODEL", "openrouter/vendor/top")
    get_settings.cache_clear()
    bodies: list[dict[str, Any]] = []
    ok = {
        "id": "x", "object": "chat.completion", "created": 0, "model": "vendor/mid",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    def send(self: httpx.Client, request: httpx.Request, *_a: Any, **_k: Any) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, request=request)
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=ok, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    settings = get_settings()
    gateway = LLMGateway()
    cfg = AgentConfig(model="openrouter/vendor/mid", max_steps=2, thinking=False)
    registry = _echo_registry()
    auto = AutonomousAgent(
        Agent(gateway, registry, cfg),
        escalation=build_escalation(gateway, settings, registry, cfg),
        verifier=_FailsFirst(1),
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
    )

    result = auto.run("do the thing")

    assert len(bodies) == 2
    # The wire: thinking stays off on both, and the only thing that moved is the model.
    assert all(b["reasoning"] == {"enabled": False} for b in bodies)
    assert bodies[0]["model"] != bodies[1]["model"]
    # And the receipt names what was sent: no effort claimed that the wire never carried.
    assert [a.effort for a in result.attempts] == ["", ""]
    assert result.attempts[1].requested_model == "openrouter/vendor/top"


# ------------------------------------------------ the compaction summariser is fenced too


def test_compact_history_summariser_does_not_reach_fusion_in_always_mode() -> None:
    fusion, single = _FusionSpy(), _SingleSpy()
    agent = Agent(
        _routed(fusion, single), _echo_registry(),
        AgentConfig(keep_recent=2, summarise_compaction=True),
    )
    messages: list[Any] = [{"role": "system", "content": "s"}]
    for i in range(12):
        messages.append({"role": "user", "content": f"question {i} " + "x" * 400})
        messages.append({"role": "assistant", "content": f"answer {i} " + "y" * 400})

    _, changed = agent.compact_history(messages)

    assert changed
    assert single.calls, "the summariser was never called, so this proves nothing"
    assert fusion.calls == 0


# --------------------------------------- the ladder never turns the reviewer's veto into a success


class _Rejects:
    """A reviewer on its own model that always rejects."""

    def __init__(self, model: str) -> None:
        self.model = model

    def review(self, task: str, answer: str, context: str) -> Any:
        from chimera.core.supervisor import Review

        return Review(approved=False, feedback="not good enough")


@pytest.mark.parametrize("thinking", [False, None], ids=["no-thinking", "thinking"])
def test_escalation_never_lands_the_editor_on_the_reviewer_so_a_rejection_stays_a_failure(
    tmp_path: Path, thinking: bool | None
) -> None:
    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_DEFAULT_MODEL="openrouter/vendor/mid",
        CHIMERA_WEAK_MODEL="openrouter/vendor/weak",
        CHIMERA_ORCHESTRATOR_MODEL="openrouter/vendor/top",
    )
    spy = _Spy()
    cfg = AgentConfig(model="openrouter/vendor/mid", max_steps=2, thinking=thinking)
    registry = _echo_registry()
    auto = AutonomousAgent(
        Agent(spy, registry, cfg),
        escalation=build_escalation(spy, settings, registry, cfg),
        manager=_Rejects("openrouter/vendor/top"),  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=3, use_planner=False, use_manager=True),
    )

    result = auto.run("do the thing")

    # No verifier: the reviewer is the only gate. Had the editor reached its model, its rejection would
    # have been demoted to advisory and the attempt marked a success.
    assert result.success is False
    assert all(not a.success for a in result.attempts)
    assert all("vendor/top" not in a.requested_model for a in result.attempts)
    assert {a.evaluator for a in result.attempts} == {"other"}


def test_the_same_model_at_another_effort_is_still_the_reviewers_own_model() -> None:
    from chimera.core.supervisor import Review

    class _Reviewer:
        model = "vendor/top"

        def review(self, task: str, answer: str, context: str) -> Review:
            return Review(approved=True, feedback="")

    class _W:
        config = AgentConfig(model="vendor/top#medium")

        def run(self, task: str) -> AgentResult:
            return AgentResult(answer="x", steps=1, stopped_reason="final")

    auto = AutonomousAgent(
        _W(), manager=_Reviewer(),  # type: ignore[arg-type]
        config=AutonomousConfig(use_planner=False, use_manager=True),
    )
    assert auto._judge_independence(auto.worker) == "self"


# ------------------------------------------------ the receipt names the effort that was sent


def test_the_receipt_records_no_effort_when_thinking_is_off(tmp_path: Path) -> None:
    auto, _ = _auto("openrouter/vendor/top#high", tmp_path, attempts=1, fail_times=0, thinking=False)
    result = auto.run("do the thing")

    assert result.attempts[0].effort == ""
    assert result.attempts[0].requested_model == "openrouter/vendor/top"


def test_the_receipt_of_a_plain_worker_records_no_effort_when_thinking_is_off() -> None:
    worker = Agent(_Spy(), _echo_registry(), AgentConfig(model="openrouter/vendor/top#high", thinking=False))
    auto = AutonomousAgent(
        worker, verifier=_FailsFirst(0),
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    attempt = auto.run("do the thing").attempts[0]

    assert (attempt.requested_model, attempt.effort) == ("openrouter/vendor/top", "")


# ----------------------- an escalated worker's tool-loop target can only go up from where it stands


def test_an_escalated_worker_never_gets_a_tool_loop_target_at_or_below_its_own_rung(tmp_path: Path) -> None:
    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_DEFAULT_MODEL="openrouter/vendor/mid",
        CHIMERA_WEAK_MODEL="openrouter/vendor/weak",
        CHIMERA_ORCHESTRATOR_MODEL="openrouter/vendor/top",
    )
    cfg = AgentConfig(
        model="openrouter/vendor/mid", escalate_on_tool_loop="openrouter/vendor/top"
    )
    escalation = build_escalation(_Spy(), settings, _echo_registry(), cfg)

    # mid#high -> top is a step up: the trigger stays.
    assert escalation.worker_for(2).config.escalate_on_tool_loop == "openrouter/vendor/top"
    # top#medium -> top would be a step down in effort (and no step up at all): the trigger is off.
    assert escalation.model_for(3) == "openrouter/vendor/top#medium"
    assert escalation.worker_for(3).config.escalate_on_tool_loop is None


# ------------------------------------------------------ a resumed run keeps the floor it had


def test_a_resumed_run_rebuilds_the_floor_from_the_attempts_it_restored(tmp_path: Path) -> None:
    from dataclasses import asdict

    from chimera.core.autonomous import Attempt
    from chimera.core.runstate import RunCheckpointer

    store = RunCheckpointer(tmp_path / "runs.db")
    first = Attempt(
        1, "a", False, False, True,
        requested_model="openrouter/vendor/weak",
        # attempt 1 ended on the top model: `--escalate-on-tool-loop` swapped inside it.
        model="openrouter/vendor/top",
    )
    store.save(
        "job",
        {
            "task": "do the thing", "attempts": [asdict(first)], "next_index": 2,
            "feedback": "attempt 1 failed", "plan_steps": None, "plan_raw": "",
        },
    )
    auto, _ = _auto("openrouter/vendor/weak", tmp_path, attempts=2, fail_times=0)
    auto.checkpointer = store

    result = auto.run("do the thing", thread_id="job")

    # From the floor (top), not from the first model (weak): never a step down from what already ran.
    assert result.attempts[-1].requested_model == "openrouter/vendor/top#high"


# ------------------- the floor and the effort step cannot bypass the reviewer's exclusion


def test_a_floor_on_the_reviewers_model_does_not_make_the_retry_run_on_it(tmp_path: Path) -> None:
    """Editor on mid, reviewer on top, no verifier; the tool-loop breaker ends attempt 1 on top.

    The retry used to start from that floor and raise its effort (`top#high`), so the editor became the
    reviewer, the reviewer's rejection turned advisory and the attempt a success.
    """

    class _EndsOnTop(_Spy):
        def complete(self, messages: list[Any], *, model: str | None = None, **kw: Any) -> CompletionResult:
            self.models.append(model)
            return CompletionResult(
                content="done", model="openrouter/vendor/top" if len(self.models) == 1 else (model or "")
            )

    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_DEFAULT_MODEL="openrouter/vendor/mid",
        CHIMERA_WEAK_MODEL="openrouter/vendor/weak",
        CHIMERA_ORCHESTRATOR_MODEL="openrouter/vendor/top",
    )
    spy = _EndsOnTop()
    cfg = AgentConfig(model="openrouter/vendor/mid", max_steps=2)
    registry = _echo_registry()
    auto = AutonomousAgent(
        Agent(spy, registry, cfg),
        escalation=build_escalation(spy, settings, registry, cfg),
        manager=_Rejects("openrouter/vendor/top"),  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=True),
    )

    result = auto.run("do the thing")

    assert result.success is False
    assert all(not a.success for a in result.attempts)
    assert all(not a.requested_model.startswith("openrouter/vendor/top") for a in result.attempts)
    assert {a.evaluator for a in result.attempts} == {"other"}


def test_the_effort_step_is_refused_on_the_reviewers_own_model() -> None:
    assert next_step("vendor/top", LADDER, avoid="vendor/top#low") == "vendor/top"
    assert next_step("vendor/mid", LADDER, avoid="vendor/top") == "vendor/mid#high"


# ----------------- thinking off cancels the effort only where the gateway really cancels it


def _wire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, default_model: str) -> list[dict[str, Any]]:
    import json

    import httpx

    from chimera.config import get_settings

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for name in ("OPENAI_API_BASE", "OPENAI_BASE_URL", "CHIMERA_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", default_model)
    monkeypatch.setenv("CHIMERA_WEAK_MODEL", default_model.replace("mid", "weak"))
    monkeypatch.setenv("CHIMERA_ORCHESTRATOR_MODEL", default_model.replace("mid", "top"))
    get_settings.cache_clear()
    bodies: list[dict[str, Any]] = []
    ok = {
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    def send(self: httpx.Client, request: httpx.Request, *_a: Any, **_k: Any) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, request=request)
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=ok, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    return bodies


def _retry_run(first: str) -> Any:
    from chimera.config import get_settings
    from chimera.providers import LLMGateway

    gateway = LLMGateway()
    cfg = AgentConfig(model=first, max_steps=2, thinking=False)
    registry = _echo_registry()
    auto = AutonomousAgent(
        Agent(gateway, registry, cfg),
        escalation=build_escalation(gateway, get_settings(), registry, cfg),
        verifier=_FailsFirst(1),
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
    )
    return auto.run("do the thing")


def test_on_an_openai_route_thinking_off_keeps_the_effort_the_wire_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies = _wire(monkeypatch, tmp_path, "openai/vendor-mid")

    result = _retry_run("openai/vendor-mid#medium")

    # The wire: OpenAI's `reasoning_effort` is sent, whatever `thinking` says.
    assert [b.get("reasoning_effort") for b in bodies] == ["medium", "high"]
    assert all("reasoning" not in b for b in bodies)
    # The receipt names what was sent, and the ladder DID raise effort (the same model, one step up).
    assert [(a.requested_model, a.effort) for a in result.attempts] == [
        ("openai/vendor-mid#medium", "medium"),
        ("openai/vendor-mid#high", "high"),
    ]


def test_on_an_openrouter_route_thinking_off_overwrites_the_effort_and_the_receipt_says_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies = _wire(monkeypatch, tmp_path, "openrouter/vendor/mid")

    result = _retry_run("openrouter/vendor/mid#medium")

    assert all(b["reasoning"] == {"enabled": False} for b in bodies)
    assert all("reasoning_effort" not in b for b in bodies)
    assert [a.effort for a in result.attempts] == ["", ""]
    assert result.attempts[1].requested_model == "openrouter/vendor/top"  # only the model could move


def test_a_plain_worker_on_an_openai_route_records_the_effort_it_sent_with_thinking_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.providers import LLMGateway

    bodies = _wire(monkeypatch, tmp_path, "openai/vendor-mid")
    worker = Agent(
        LLMGateway(), _echo_registry(), AgentConfig(model="openai/vendor-mid#high", max_steps=2, thinking=False)
    )
    auto = AutonomousAgent(
        worker, verifier=_FailsFirst(0),
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )

    attempt = auto.run("do the thing").attempts[0]

    assert [b.get("reasoning_effort") for b in bodies] == ["high"]
    assert (attempt.requested_model, attempt.effort) == ("openai/vendor-mid#high", "high")
