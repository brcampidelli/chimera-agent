"""A dollar ceiling belongs to the RUN, not to each attempt inside it.

``AgentConfig.max_usd`` reads as "this run may spend $X". ``Agent.run`` built a ``SpendBudget`` from
it, and ``AutonomousAgent`` calls ``run`` once per ATTEMPT — so the real limit was
``max_usd * max_attempts``, and the reviewer, which is a model call per attempt and lives outside
every ``Agent``, was under no ceiling at all.

Measured in the app before the fix: a run asking for **$0.000002** with three attempts spent
**$0.0129**. Attempts 2 and 3 each started again at zero, and each paid a reviewer to criticise a
worker whose whole answer was that it had hit a spending limit.

Everything here is free: no model call, no network.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import Agent, AgentConfig, AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.supervisor import Review
from chimera.orchestration.budget import SpendBudget, SpendCappedBackend, SpendExceeded
from chimera.providers.gateway import ToolCall
from chimera.tools.registry import Tool, ToolRegistry


class _Config:
    """Only the field the loop reads off a worker to size the ceiling."""

    def __init__(self, max_usd: float | None) -> None:
        self.max_usd = max_usd


class _BudgetWorker:
    """Records the budget it was handed on each attempt, and always fails so the loop retries.

    Failing matters: a worker that succeeds ends the run on attempt 1, and then every assertion
    about attempt 2 sharing a ceiling would pass without a second attempt ever happening.
    """

    def __init__(self, max_usd: float | None = 5.0) -> None:
        self.config = _Config(max_usd)
        self.seen: list[SpendBudget | None] = []

    def run(self, task: str, *, spend: SpendBudget | None = None, **kwargs: Any) -> AgentResult:
        self.seen.append(spend)
        return AgentResult(answer="not done", steps=1, stopped_reason="final")


class _StoppedOnSpendWorker:
    """A worker that hit the ceiling mid-attempt — what ``Agent.run`` returns in that case."""

    def __init__(self) -> None:
        self.config = _Config(5.0)
        self.calls = 0

    def run(self, task: str, **kwargs: Any) -> AgentResult:
        self.calls += 1
        return AgentResult(
            answer="spend cap reached: $0.0037 of $0.0000", steps=1, stopped_reason="spend"
        )


class _SpendingStoppedWorker:
    """Stopped on spend, and carrying the usage of the call that got it there."""

    def __init__(self) -> None:
        self.config = _Config(5.0)

    def run(self, task: str, **kwargs: Any) -> AgentResult:
        r = AgentResult(
            answer="spend cap reached: $0.0052 of $0.0000", steps=1, stopped_reason="spend"
        )
        r.usd = 0.005197
        r.prompt_tokens = 8870
        r.completion_tokens = 193
        r.model = "openrouter/deepseek/deepseek-chat-v3.1"
        return r


class _OldWorker:
    """Predates the parameter: its ``run`` takes no ``spend``, and must still be callable."""

    def __init__(self) -> None:
        self.config = _Config(5.0)
        self.calls = 0

    def run(self, task: str) -> AgentResult:
        self.calls += 1
        return AgentResult(answer="done", steps=1, stopped_reason="final")


class _ReviewResult:
    """The shape a backend returns, priced high enough that one call moves a budget."""

    content = "APPROVED"
    model = "openrouter/deepseek/deepseek-chat"
    prompt_tokens = 1_000_000
    completion_tokens = 1_000_000
    finish_reason = "stop"
    route_meta = None
    cache_read_tokens = 0
    cache_write_tokens = 0

    def __init__(self) -> None:
        self.tool_calls: list[Any] = []


class _CountingBackend:
    """Stands in for the reviewer's gateway."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> _ReviewResult:
        self.calls += 1
        return _ReviewResult()


class _CountingManager:
    """A reviewer that always rejects, so the loop uses every attempt it is given."""

    def __init__(self) -> None:
        self.calls = 0
        self.backend = _CountingBackend()
        self.model: str | None = None
        self.use_rubric = False
        self.rubric_threshold = 0.6

    def review(self, task: str, proposed: str, *, context: str = "") -> Review:
        self.calls += 1
        self.backend.complete([])
        return Review(approved=False, feedback="again")


def _config(**kw: Any) -> AutonomousConfig:
    return AutonomousConfig(use_planner=False, use_manager=True, **kw)


def _auto(worker: Any, manager: Any, attempts: int) -> AutonomousAgent:
    return AutonomousAgent(worker, manager=manager, config=_config(max_attempts=attempts))


# --- one ceiling, not one per attempt ----------------------------------------------------------


def test_every_attempt_draws_on_the_same_budget() -> None:
    """The whole defect in one assertion: three attempts, one budget object.

    Identity, not equality — two ``SpendBudget(5.0)`` objects compare as different but each would
    hold its own ``_spent``, which is precisely the bug.
    """
    worker = _BudgetWorker(max_usd=5.0)

    _auto(worker, _CountingManager(), 3).run("do the task")

    assert len(worker.seen) == 3, "the loop did not use all three attempts"
    assert worker.seen[0] is not None, "the worker was handed no budget at all"
    assert worker.seen[0] is worker.seen[1] is worker.seen[2], (
        "each attempt got its own ceiling — a $5 run can spend $15"
    )


def test_spending_carries_from_one_attempt_to_the_next() -> None:
    """What the shared object is FOR: money spent on attempt 1 is gone on attempt 2."""
    worker = _BudgetWorker(max_usd=5.0)

    _auto(worker, _CountingManager(), 2).run("do the task")

    first, second = worker.seen[0], worker.seen[1]
    assert first is not None and second is not None
    first.record("openrouter/deepseek/deepseek-chat", 1_000_000, 1_000_000)
    assert second.spent == first.spent > 0


def test_a_worker_without_a_cap_is_handed_nothing() -> None:
    """No ``max_usd`` is no ceiling, and must not become a zero-dollar one."""
    worker = _BudgetWorker(max_usd=None)

    _auto(worker, _CountingManager(), 2).run("do the task")

    assert worker.seen == [None, None]


def test_a_worker_that_predates_the_parameter_still_runs() -> None:
    """Duck-typed, like ``should_stop`` beside it: an old ``Worker`` keeps its old behaviour."""
    worker = _OldWorker()

    result = _auto(worker, _CountingManager(), 1).run("do the task")

    assert worker.calls == 1
    assert result.attempts


# --- reaching the ceiling ends the run ---------------------------------------------------------


def test_a_worker_stopped_on_money_is_not_reviewed_and_not_retried() -> None:
    """The measured waste: three attempts ran under a cap the first call had already passed, each
    paying a reviewer to criticise a worker that was only reporting the limit."""
    worker = _StoppedOnSpendWorker()
    manager = _CountingManager()

    result = _auto(worker, manager, 3).run("do the task")

    assert worker.calls == 1, "the run kept retrying after the money ran out"
    assert manager.calls == 0, "a reviewer was paid to judge a worker that never worked"
    assert result.success is False
    assert result.stopped_reason == "spend"


def test_the_run_that_hit_the_cap_records_what_it_spent() -> None:
    """A cap that hides its own spending is worse than no cap.

    Measured on the shipped fix: the run stopped correctly and its receipt read
    ``usd: null, attempts: []`` while the answer said *"spend cap reached: $0.0030"*. The attempt
    that reached the ceiling had called a model — that is HOW it reached it — and returning before
    the loop's bookkeeping dropped the only record of the money. The Cost screen, which reads those
    attempts, then showed a paid run as free.
    """
    worker = _StoppedOnSpendWorker()

    result = _auto(worker, _CountingManager(), 3).run("do the task")

    assert len(result.attempts) == 1, "the attempt that spent the money left no record"
    assert result.attempts[0].index == 1


def test_the_recorded_attempt_carries_the_tokens_it_used() -> None:
    """Not just a row: the row has to hold the numbers, or the receipt is a placeholder."""
    worker = _SpendingStoppedWorker()

    result = _auto(worker, _CountingManager(), 2).run("do the task")

    attempt = result.attempts[0]
    assert attempt.prompt_tokens == 8870
    assert attempt.completion_tokens == 193
    assert attempt.usd == 0.005197
    assert attempt.model == "openrouter/deepseek/deepseek-chat-v3.1"


def test_the_ending_says_which_ceiling_and_what_it_had_spent() -> None:
    """Not "the run failed". The worker's own message names the cap and the spend."""
    result = _auto(_StoppedOnSpendWorker(), _CountingManager(), 2).run("do the task")

    assert "spend cap reached" in result.answer


# --- the reviewer draws on the same money ------------------------------------------------------


def test_the_reviewer_is_inside_the_ceiling() -> None:
    """It is a model call per attempt, and it sat outside every budget in the app."""
    manager = _CountingManager()
    auto = _auto(_BudgetWorker(max_usd=5.0), manager, 1)

    capped = auto._capped_manager(SpendBudget(5.0))

    assert isinstance(capped.backend, SpendCappedBackend)
    assert capped.backend.inner is manager.backend


def test_capping_the_reviewer_keeps_its_class_and_its_review() -> None:
    """Rebuilding a ``Manager`` from its fields would keep the name and lose the behaviour.

    A subclass, or any duck-typed reviewer with its own ``review``, must survive being capped —
    a substitution nothing downstream could detect, and one that would quietly turn a custom
    verdict into the default APPROVED/REVISE parse of whatever the backend happened to say.
    """
    manager = _CountingManager()
    auto = _auto(_BudgetWorker(max_usd=5.0), manager, 1)

    capped = auto._capped_manager(SpendBudget(5.0))

    assert type(capped) is type(manager)
    assert capped.review("t", "a").approved is False, "the overridden verdict was lost"


def test_a_reviewer_with_no_backend_to_wrap_is_left_alone() -> None:
    class _NoBackend:
        def review(self, task: str, proposed: str, *, context: str = "") -> Review:
            return Review(approved=True)

    auto = _auto(_BudgetWorker(max_usd=5.0), _NoBackend(), 1)

    assert auto._capped_manager(SpendBudget(5.0)) is auto.manager


def test_wrapping_the_reviewer_never_mutates_the_shared_one() -> None:
    """A wrapper installed on ``self.manager`` would carry this run's spending into the next."""
    manager = _CountingManager()
    auto = _auto(_BudgetWorker(max_usd=5.0), manager, 1)

    auto._capped_manager(SpendBudget(5.0))

    assert not isinstance(manager.backend, SpendCappedBackend)


def test_no_budget_means_the_reviewer_is_left_exactly_as_it_was() -> None:
    auto = _auto(_BudgetWorker(max_usd=None), _CountingManager(), 1)

    assert auto._capped_manager(None) is auto.manager


# --- the real Agent, not a fake ----------------------------------------------------------------


class _PingTool(Tool):
    """Does nothing, so the loop keeps calling the model and only a cap can end it."""

    name = "ping"
    description = "does nothing"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        return "pong"


class _LoopingBackend:
    """Calls ``ping`` forever, priced so that one call is worth roughly a cent."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> Any:
        self.calls += 1
        result = _ReviewResult()
        result.tool_calls = [ToolCall(id=f"c{self.calls}", name="ping", arguments={})]
        return result


def _real_agent(backend: _LoopingBackend, max_usd: float) -> Agent:
    """Loop detection OFF, deliberately: a worker that calls the same tool forever is exactly what
    it exists to stop, and with it on both runs end at the same step for a reason that has nothing
    to do with money — which is how the first version of these tests passed AND failed for the
    wrong reason. ``max_steps`` is set high for the same reason: only the cap may end these runs."""
    registry = ToolRegistry()
    registry.register(_PingTool())
    return Agent(
        backend,
        registry,
        AgentConfig(max_usd=max_usd, max_steps=500, detect_tool_loops=False),
    )


def test_a_caller_owned_budget_is_the_one_the_agent_uses() -> None:
    """The join between the two halves: without it the loop hands a budget down and ``Agent.run``
    builds its own anyway, so nothing about a shared ceiling reaches the place that spends."""
    backend = _LoopingBackend()
    shared = SpendBudget(10.0)

    _real_agent(backend, max_usd=10.0).run("go", spend=shared)

    assert shared.spent > 0, "the agent spent on a budget of its own, not on the one it was given"


def test_a_second_run_on_the_same_budget_has_less_to_spend() -> None:
    """What a per-attempt ceiling could never do: the second run stops sooner than the first.

    Sizing matters — the cap is set so the first run cannot exhaust it on one call, otherwise both
    runs would make exactly one call and the assertion would hold for the wrong reason.
    """
    backend = _LoopingBackend()
    shared = SpendBudget(10.0)
    agent = _real_agent(backend, max_usd=10.0)

    agent.run("go", spend=shared)
    first = backend.calls
    agent.run("go again", spend=shared)
    second = backend.calls - first

    assert first > 1, "the first run ended on one call — the cap is too small to measure carry-over"
    assert second < first, "the second run started again at zero"


def test_without_a_caller_budget_the_agent_still_builds_its_own() -> None:
    """The standalone case the original comment defends: two runs, two allowances."""
    backend = _LoopingBackend()
    agent = _real_agent(backend, max_usd=10.0)

    agent.run("go")
    first = backend.calls
    agent.run("go again")

    assert backend.calls - first == first, "a standalone Agent lost its per-run allowance"


class _RefusingManager(_CountingManager):
    """A reviewer whose call the ceiling refuses — what the wrapped backend does once blocked."""

    def review(self, task: str, proposed: str, *, context: str = "") -> Review:
        self.calls += 1
        raise SpendExceeded("spend cap reached: $5.0000 of $5.0000")


def test_a_reviewer_refused_by_the_ceiling_abstains_it_does_not_veto() -> None:
    """The direction matters, and only one of the two is safe.

    A refused reviewer has not judged. Reading its silence as REVISE would revert work the money
    had already bought — under verify-or-revert, deleting a correct patch because the budget ran
    out while nobody was looking at it.
    """
    manager = _RefusingManager()
    auto = _auto(_BudgetWorker(max_usd=5.0), manager, 1)

    approved, feedback, abstained = auto._review("t", "a", "", manager=manager)

    assert manager.calls == 1, "the reviewer was never reached — this proves nothing"
    assert abstained is True, "a reviewer that was never allowed to look was recorded as a verdict"
    assert approved is True, "silence became a veto, and verify-or-revert would delete the work"
    assert feedback == ""
