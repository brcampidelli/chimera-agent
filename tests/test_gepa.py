"""Tests for GEPA reflective Pareto prompt evolution (M14 C1)."""

from __future__ import annotations

import pytest

from chimera.evolution.gepa import (
    BackendExecutor,
    BackendReflector,
    Candidate,
    GEPAOptimizer,
    TaskInstance,
    _dominates,
    _frontier,
    _pareto_pool,
    evolve_skill,
    optimize_template,
)
from chimera.evolution.learned_skill import LearnedSkill
from chimera.providers.gateway import CompletionResult

# --- fakes: the template's own text IS the signal (no network) ---------------------------


class _EchoExecutor:
    """Output = the template verbatim, so a scorer can grade template quality directly."""

    def run(self, template: str, task_input: dict[str, str]) -> str:
        return template


class _AddKeyword:
    """Reflector that repairs a template by inserting the missing keyword once."""

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword
        self.calls = 0

    def propose(self, template: str, feedback: str) -> str:
        self.calls += 1
        return template if self.keyword in template else f"{template} {self.keyword}"


class _AlwaysNewButBad:
    """Reflector that always proposes a fresh — but still failing — template (never converges)."""

    def __init__(self) -> None:
        self.calls = 0

    def propose(self, template: str, feedback: str) -> str:
        self.calls += 1
        return f"still bad {self.calls}"


def _has(keyword: str):
    return lambda out: 1.0 if keyword in out else 0.2


def _instances(n: int, keyword: str = "GOOD") -> list[TaskInstance]:
    return [TaskInstance(input={"x": str(i)}, scorer=_has(keyword)) for i in range(n)]


# --- optimize ----------------------------------------------------------------------------


def test_optimize_improves_seed() -> None:
    reflector = _AddKeyword("GOOD")
    opt = GEPAOptimizer(_EchoExecutor(), reflector)
    result = opt.optimize("do the task", _instances(3), budget=20)
    assert result.improved is True
    assert result.best_mean == 1.0
    assert result.seed_mean == pytest.approx(0.2)
    assert reflector.calls >= 1
    assert "GOOD" in result.best_template


def test_perfect_seed_wastes_no_rollouts() -> None:
    reflector = _AddKeyword("GOOD")
    opt = GEPAOptimizer(_EchoExecutor(), reflector)
    result = opt.optimize("already GOOD", _instances(4), budget=40)
    assert result.best_mean == 1.0
    assert result.improved is False
    assert result.rollouts == 4  # only the seed evaluation; never reflected on a perfect seed
    assert reflector.calls == 0


def test_budget_is_respected() -> None:
    opt = GEPAOptimizer(_EchoExecutor(), _AlwaysNewButBad())
    result = opt.optimize("do the task", _instances(5), budget=17)
    # Each evaluation costs 5 rollouts; the loop stops before it would exceed 17.
    assert result.rollouts <= 17
    assert result.best_mean == 0.2  # nothing improved


def test_stall_stops_on_duplicate_proposals() -> None:
    class _SameEveryTime:
        def __init__(self) -> None:
            self.calls = 0

        def propose(self, template: str, feedback: str) -> str:
            self.calls += 1
            return "do the task also-bad"  # new once, then a duplicate forever

    reflector = _SameEveryTime()
    opt = GEPAOptimizer(_EchoExecutor(), reflector, max_stall=3)
    result = opt.optimize("do the task", _instances(2), budget=1000)
    # Big budget, but the duplicate proposal stalls the search instead of looping forever.
    assert result.rollouts < 1000
    assert reflector.calls >= 3


# --- Pareto machinery --------------------------------------------------------------------


def test_pareto_pool_keeps_instance_winners() -> None:
    a = Candidate("A", (0.9, 0.1))  # best on instance 0
    b = Candidate("B", (0.1, 0.9))  # best on instance 1
    pool = _pareto_pool([a, b])
    assert set(pool) == {0, 1}  # both survive — neither dominates the other


def test_dominates() -> None:
    strong = Candidate("s", (0.9, 0.9))
    weak = Candidate("w", (0.5, 0.5))
    assert _dominates(strong, weak) is True
    assert _dominates(weak, strong) is False
    assert _dominates(strong, strong) is False  # equal, not strictly better


def test_frontier_excludes_dominated() -> None:
    best = Candidate("best", (0.9, 0.9))
    dominated = Candidate("dom", (0.4, 0.4))
    tradeoff = Candidate("trade", (1.0, 0.2))
    frontier = _frontier([best, dominated, tradeoff])
    assert 0 in frontier and 2 in frontier  # best + the trade-off are non-dominated
    assert 1 not in frontier  # fully dominated by best


def test_scores_are_clamped() -> None:
    inst = [TaskInstance(input={}, scorer=lambda out: 1.5), TaskInstance(input={}, scorer=lambda out: -0.3)]
    opt = GEPAOptimizer(_EchoExecutor(), _AddKeyword("GOOD"))
    result = opt.optimize("seed", inst, budget=2)
    assert result.candidates[0].scores == (1.0, 0.0)  # clamped into [0, 1]


# --- default backend seams ---------------------------------------------------------------


class _FakeBackend:
    """Distinguishes executor from reflector by system prompt; reflector returns a fixed rewrite."""

    def complete(self, messages: object, **kwargs: object) -> CompletionResult:
        system = messages[0].content  # type: ignore[index]
        if "improve an instruction template" in system:
            return CompletionResult(content="do the task GOOD", model="fake")
        return CompletionResult(content=messages[1].content, model="fake")  # type: ignore[index]


def test_backend_executor_fills_and_calls() -> None:
    out = BackendExecutor(_FakeBackend()).run("hello {x}", {"x": "world"})
    assert out == "hello world"


def test_backend_executor_missing_variable_degrades() -> None:
    out = BackendExecutor(_FakeBackend()).run("needs {missing}", {"x": "1"})
    assert out.startswith("[template error:")  # no crash, scores low


def test_backend_reflector_falls_back_on_empty() -> None:
    class _Empty:
        def complete(self, messages: object, **kwargs: object) -> CompletionResult:
            return CompletionResult(content="   ", model="fake")

    assert BackendReflector(_Empty()).propose("orig", "fb") == "orig"


def test_optimize_template_end_to_end() -> None:
    result = optimize_template(_FakeBackend(), "do the task {x}", _instances(2), budget=20)
    assert result.improved is True and result.best_mean == 1.0


def test_evolve_skill_returns_improved_copy() -> None:
    skill = LearnedSkill(name="s", description="d", prompt_template="do the task {x}", version="0.1.0")
    improved, result = evolve_skill(_FakeBackend(), skill, _instances(2), budget=20)
    assert result.improved is True
    assert improved is not skill
    assert "GOOD" in improved.prompt_template
    assert improved.version == "0.1.1"  # bumped


def test_evolve_skill_keeps_original_when_no_lift() -> None:
    skill = LearnedSkill(name="s", description="d", prompt_template="already GOOD", version="0.1.0")
    same, result = evolve_skill(_FakeBackend(), skill, _instances(2), budget=20)
    assert result.improved is False
    assert same is skill  # unchanged: a mutation that does not help is not adopted
