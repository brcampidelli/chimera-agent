"""The loop had one ending, no price per unit of work, and no way to ask it to stop at a number.

Three holes, one shape: the mechanism was built, tested and shipped, and the last wire was missing.

1. **`--max-usd`.** ``SpendBudget``, ``SpendCappedBackend`` and the whole ``stopped_reason="spend"``
   path exist and are covered by ``test_the_dollar_cap_is_the_runs.py`` — and ``chimera solve`` had
   twenty-nine flags, not one of them about money, so ``_run_budget`` returned ``None`` on every
   terminal invocation and none of it could run.
2. **One ending.** ``stopped_reason`` is written at two sites, so a run that used up its attempts, a
   run whose answer a person refused, and a run that succeeded while changing nothing on disk all
   left the same blank in ``runs.jsonl``. "How many runs stopped at the cap this month" had no
   answer, and neither did "how many of our successes were no-ops".
3. **Cost per accepted change.** Every term — per-attempt ``usd``, ``verified``, ``reverted``,
   ``diff_productive`` — has been on the receipt for releases, and nothing divided one by the other.

Each assertion below was reverted on disk and confirmed to go red. Everything here is free: no model
call, no network.

Two states from the loop-engineering literature are deliberately NOT implemented, and the test that
would have to exist for them is the reason: nothing in this loop can set ``blocked`` or ``stalled``
truthfully. The stagnation detector injects a pivot and re-plans — it has never stopped a run — so
an ending named ``stalled`` would assert a cause the code does not have. It is reported as a fact
beside the ending instead, and ``None`` there means *nothing looked*, not *it did not happen*.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

from chimera.api.runs import AttemptReceipt, build_receipt, cost_per_accepted_change
from chimera.core import AutonomousAgent, AutonomousConfig, WorkspaceGuard
from chimera.core.agent import AgentConfig, AgentResult
from chimera.core.verify import VerificationResult
from chimera.evolution import StagnationDetector

_SOURCE = pathlib.Path(AutonomousAgent.__module__.replace(".", "/") + ".py")


class _Worker:
    """Answers, and optionally writes a file so the guard measures a real change."""

    def __init__(self, *, workspace: pathlib.Path | None = None, writes: str | None = None) -> None:
        self.config = AgentConfig(model="m")
        self.workspace = workspace
        self.writes = writes
        self.runs = 0

    def run(self, task: str, **kw: Any) -> AgentResult:
        self.runs += 1
        if self.workspace is not None and self.writes:
            (self.workspace / self.writes).write_text(f"run {self.runs}\n", encoding="utf-8")
        return AgentResult(answer="done", steps=1, stopped_reason="final")


class _Pass:
    def verify(self) -> VerificationResult:
        return VerificationResult(True, "ok")


class _Fail:
    def verify(self) -> VerificationResult:
        return VerificationResult(False, "tests failed")


def _auto(worker: Any, **kw: Any) -> AutonomousAgent:
    cfg = AutonomousConfig(
        max_attempts=kw.pop("max_attempts", 1), use_planner=False, use_manager=False
    )
    return AutonomousAgent(worker, config=cfg, **kw)


# --- 1. every return names its ending -------------------------------------------------------------


def test_every_construction_of_a_result_names_its_ending() -> None:
    """The guard that outlives this change: a seventh return site cannot forget.

    Behavioural tests below cover the endings a free test can reach. This one covers the two that
    need a checkpoint round trip (``paused``, ``denied``) and, more to the point, every ending
    somebody adds next year. A field that is "always set" by convention is set until it isn't.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AutonomousResult"
    ]
    assert len(calls) >= 6, f"expected the known return sites, found {len(calls)}"

    missing = [
        node.lineno for node in calls if not any(k.arg == "ending" for k in node.keywords)
    ]
    assert not missing, f"AutonomousResult built without an ending at line(s) {missing}"


def test_the_default_is_unknown_and_not_a_plausible_guess() -> None:
    """A result built somewhere that does not set it says so, rather than claiming an ending."""
    from chimera.core.autonomous import AutonomousResult

    assert AutonomousResult(answer="", success=False).ending == "unknown"


# --- 2. the endings a free test can reach ---------------------------------------------------------


def test_running_out_of_attempts_says_exhausted() -> None:
    result = _auto(_Worker(), verifier=_Fail(), max_attempts=2).run("do it")

    assert result.success is False
    assert result.ending == "exhausted"
    assert result.stopped_reason == "", "exhaustion is not an early stop; it must not borrow one"


def test_a_success_that_changed_a_file_says_success(tmp_path: pathlib.Path) -> None:
    worker = _Worker(workspace=tmp_path, writes="out.txt")
    result = _auto(worker, verifier=_Pass(), guard=WorkspaceGuard(tmp_path)).run("do it")

    assert result.success is True and result.ending == "success"


def test_a_success_that_changed_nothing_says_no_op(tmp_path: pathlib.Path) -> None:
    """The ending that did not exist, and the one worth having.

    A verified success with an empty diff is right when the task was a question and a defect when it
    was not — and until now both wrote ``success: true`` with nothing to tell them apart. The diff
    gate already computed this to block hollow learning; the receipt just never carried the verdict.
    """
    result = _auto(_Worker(), verifier=_Pass(), guard=WorkspaceGuard(tmp_path)).run("answer me")

    assert result.success is True, "a no-op is still a success — this is not a failure rename"
    assert result.ending == "no_op"


def test_an_unmeasured_diff_is_not_called_a_no_op() -> None:
    """No guard means nobody measured the tree. That is ``success``, never ``no_op``.

    ``no_op`` is a claim that nothing changed. Making it whenever we failed to look would put an
    assertion nobody checked into the record — the same reason ``stagnant`` is ``None`` below.
    """
    result = _auto(_Worker(), verifier=_Pass()).run("answer me")

    assert result.success is True and result.ending == "success"


def test_a_cancelled_run_says_cancelled() -> None:
    worker = _Worker()
    auto = _auto(worker, verifier=_Fail(), should_stop=lambda: worker.runs >= 1, max_attempts=3)
    result = auto.run("cancel me")

    assert worker.runs == 1
    assert result.ending == "cancelled" and result.stopped_reason == "cancelled"


def test_a_run_stopped_on_money_says_spend() -> None:
    """The ending that ``--max-usd`` makes reachable from a terminal for the first time."""

    class _StoppedOnSpend:
        def __init__(self) -> None:
            self.config = AgentConfig(model="m", max_usd=0.001)

        def run(self, task: str, **kw: Any) -> AgentResult:
            return AgentResult(
                answer="spend cap reached: $0.0030", steps=1, stopped_reason="spend"
            )

    result = _auto(_StoppedOnSpend(), max_attempts=3).run("do it")

    assert result.ending == "spend" and result.stopped_reason == "spend"


# --- 3. stagnant is a fact beside the ending, and None is not False -------------------------------


def test_without_a_detector_stagnation_is_unknown_not_absent() -> None:
    """``None`` means nothing watched. Reporting ``False`` would claim a measurement nobody made."""
    result = _auto(_Worker(), verifier=_Fail(), max_attempts=2).run("do it")

    assert result.stagnant is None


def test_with_a_detector_the_repeated_failure_is_recorded() -> None:
    """Two attempts failing the same way is what the detector is for — and it is still ``exhausted``.

    The ending stays ``exhausted`` on purpose: the detector injected a pivot and the loop kept
    going. Naming the ending ``stalled`` would say stagnation stopped the run, and it never has.
    """
    auto = _auto(
        _Worker(),
        verifier=_Fail(),
        stagnation=StagnationDetector(window=2, signature_similarity=1.0),
        max_attempts=3,
    )
    result = auto.run("do it")

    assert result.ending == "exhausted"
    assert result.stagnant is True, "identical failures three times running is the whole signal"


# --- 4. the flag that makes the ceiling reachable -------------------------------------------------


def test_solve_offers_a_flag_for_money() -> None:
    """Twenty-nine flags and none about spending — the gap that made a built mechanism unreachable."""
    import inspect

    from chimera.cli.main import solve

    assert "max_usd" in inspect.signature(solve).parameters


def test_the_flag_is_handed_to_the_worker_it_is_supposed_to_cap() -> None:
    """The wire between the two tests around this one, and the only one nothing was watching.

    Found while sabotaging: deleting ``max_usd=max_usd`` from ``solve``'s ``AgentConfig`` left every
    other assertion green. The flag would have existed, the budget would have been buildable, and
    the two would not have been connected — a setting that accepts a number and ignores it, which
    is the same shape as the approval mode that asked nobody.
    """
    import ast as _ast
    import inspect

    from chimera.cli import main

    tree = _ast.parse(inspect.getsource(main.solve))
    configs = [
        node
        for node in _ast.walk(tree)
        if isinstance(node, _ast.Call)
        and isinstance(node.func, _ast.Name)
        and node.func.id == "AgentConfig"
    ]
    assert configs, "solve no longer builds an AgentConfig; this guard needs rewriting"
    assert any(
        kw.arg == "max_usd" and isinstance(kw.value, _ast.Name) and kw.value.id == "max_usd"
        for cfg in configs
        for kw in cfg.keywords
    ), "solve takes --max-usd and never hands it to the worker"


def test_a_cap_on_the_worker_becomes_a_budget_for_the_run() -> None:
    """The wire itself: without ``max_usd`` this returns ``None`` and nothing is ever capped."""
    assert _auto(_Worker())._run_budget() is None

    capped = _Worker()
    capped.config = AgentConfig(model="m", max_usd=2.50)
    budget = _auto(capped)._run_budget()

    assert budget is not None and budget.max_usd == 2.50


# --- 5. money over delivered work -----------------------------------------------------------------


def _leg(**kw: Any) -> AttemptReceipt:
    base: dict[str, Any] = {
        "verified": True,
        "reverted": False,
        "diff_productive": True,
        "usd": 0.10,
    }
    return AttemptReceipt(**{**base, **kw})


def test_the_ratio_is_money_over_what_survived_the_gate() -> None:
    assert cost_per_accepted_change([_leg(), _leg()]) == (2, 0.2, 0.1)


def test_reverted_work_is_paid_for_and_not_counted() -> None:
    """It cost money — that is why it stays in the numerator — and it delivered nothing."""
    cost = cost_per_accepted_change([_leg(), _leg(reverted=True)])

    assert cost.accepted == 1 and cost.usd == 0.2 and cost.per_change == 0.2


def test_a_verified_attempt_with_an_empty_diff_is_not_an_accepted_change() -> None:
    """The hollow success the diff gate exists to catch does not get to flatter this ratio."""
    assert cost_per_accepted_change([_leg(diff_productive=False)]).accepted == 0


def test_an_unmeasured_diff_does_not_count_either() -> None:
    """``None`` is unknown. Counting it would make the rows nobody measured the cheap ones."""
    assert cost_per_accepted_change([_leg(diff_productive=None)]).accepted == 0


def test_bought_nothing_and_cannot_be_priced_are_different_answers() -> None:
    """Both leave ``per_change`` empty and they are opposite verdicts; the other two fields say which.

    Collapsing them is the failure this shape exists to prevent: one run wasted its money, the other
    may have spent it perfectly and used a model whose price we do not know.
    """
    nothing_accepted = cost_per_accepted_change([_leg(diff_productive=False)])
    unpriced = cost_per_accepted_change([_leg(usd=None)])

    assert nothing_accepted.per_change is None and unpriced.per_change is None
    assert nothing_accepted.accepted == 0 and nothing_accepted.usd == 0.10
    assert unpriced.accepted == 1 and unpriced.usd is None


def test_one_unpriced_leg_refuses_the_whole_sum() -> None:
    """Same all-or-nothing rule as ``total_usd``, because it IS ``total_usd`` — not a second copy.

    A partial sum is not conservative: it is wrong in the one direction that makes whichever
    configuration used a free tier look like the cheap one.
    """
    assert cost_per_accepted_change([_leg(), _leg(usd=None)]).usd is None


# --- 6. it reaches the durable record -------------------------------------------------------------


def test_the_receipt_carries_the_ending_and_the_stagnation(tmp_path: pathlib.Path) -> None:
    """Persisted, not just returned: ``runs.jsonl`` is what a month-later question is asked of."""
    result = _auto(_Worker(), verifier=_Fail(), max_attempts=2).run("do it")
    receipt = build_receipt(result, "do it", None, "2026-09-06T00:00:00Z")

    assert receipt.ending == "exhausted"
    assert receipt.stagnant is None
    assert '"ending":"exhausted"' in receipt.model_dump_json().replace(" ", "")


def test_the_ending_survives_the_wire_to_the_screen(tmp_path: pathlib.Path) -> None:
    """``RunReceiptOut`` is a curated projection, so a field on the receipt is not a field on the API.

    That boundary is the reason this test exists rather than an assertion about the model's fields:
    what can break is FastAPI's coercion dropping a name the DTO does not declare, and the Runs list
    then rendering the same blank it rendered before — the whole defect, moved one layer out.
    """
    from chimera.api.runs import append_run

    log = tmp_path / "runs.jsonl"
    result = _auto(_Worker(), verifier=_Fail(), max_attempts=2).run("do it")
    append_run(log, build_receipt(result, "do it", None, "2026-09-06T00:00:00Z"))

    from chimera.api.schemas import RunReceiptOut

    on_the_wire = RunReceiptOut.model_validate(
        build_receipt(result, "do it", None, "2026-09-06T00:00:00Z").model_dump()
    )

    assert on_the_wire.ending == "exhausted"
    assert on_the_wire.stagnant is None


def test_a_receipt_from_a_result_that_predates_the_field_reads_unknown() -> None:
    """``build_receipt`` is duck-typed by design; an old result must degrade, never raise."""

    class _Old:
        answer, success, paused, attempts, plan = "x", True, False, [], None

    receipt = build_receipt(_Old(), "t", None, "2026-09-06T00:00:00Z")  # type: ignore[arg-type]

    assert receipt.ending == "unknown" and receipt.stagnant is None
