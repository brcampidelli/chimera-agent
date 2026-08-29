"""The most expensive route in the app reported nothing on the Cost screen.

`_record_run_spend` reads `outcome.receipts` and returns immediately when the list is empty.
`IsolatedCrewResult` had `transcript`, `conflicts`, `merged`, `failures`, `rejected` and `summary`
— and no `receipts`. So `getattr(outcome, "receipts", None)` answered `None` on every crew run, the
recorder took its early return, and the screen that answers "what has this cost me" showed zero for
the one route that starts N tool-using agents at once.

Two things kept it alive. The comment beside the call said the opposite — that the route now
appears on the Cost screen — so anyone reading the code was told the fix was in. And the test that
covered the recorder used a stand-in class **with** a `receipts` attribute, so it exercised a
contract the shipped object did not fulfil: a fake that is more capable than the real thing tests
the fake.

The fix is per-worker metering rather than one crew total. A crew is justified by "N attempts, the
test picks the winner", and that trade is only accountable if you can see what each attempt cost —
including the ones that were rejected or crashed, which cost exactly as much as the one that won.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.orchestration.crew import IsolatedCrew, IsolatedCrewResult, IsolatedWorker
from chimera.orchestration.roles import Role
from chimera.tools.registry import ToolRegistry


class _Result:
    def __init__(self, content: str, model: str, prompt: int, completion: int) -> None:
        self.content = content
        self.model = model
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.tool_calls: list[Any] = []


class _Backend:
    """Answers once per call, reporting usage the way a provider does."""

    def __init__(self, model: str = "openai/gpt-4o-mini") -> None:
        self.model = model
        self.calls = 0

    def complete(self, messages: Any, **kwargs: Any) -> _Result:
        self.calls += 1
        return _Result("done", self.model, 100, 20)


def _worker(name: str, backend: Any = None) -> IsolatedWorker:
    return IsolatedWorker(
        role=Role(name=name, system_prompt=f"you are {name}"),
        tools=lambda _ws: ToolRegistry(),
        backend=backend,
        max_steps=1,
    )


# --------------------------------------------------------------------------- the contract


def test_the_result_type_carries_receipts() -> None:
    """Pinned on the type itself, because the whole defect was a field that was not there while
    everything around it assumed it was."""
    assert "receipts" in IsolatedCrewResult.__dataclass_fields__
    assert IsolatedCrewResult().receipts == []


def test_a_crew_run_produces_one_receipt_per_worker(tmp_path: Path) -> None:
    """Mechanism to wiring. Declaring the field changes nothing until the run fills it."""
    crew = IsolatedCrew(_Backend(), [_worker("alice"), _worker("bob")], max_workers=2)

    result = crew.run("do the thing", tmp_path)

    assert {r.task_id for r in result.receipts} == {"alice", "bob"}
    assert all(r.prompt_tokens == 100 and r.completion_tokens == 20 for r in result.receipts)
    assert all(r.model == "openai/gpt-4o-mini" for r in result.receipts)
    assert all(r.usd is not None and r.usd > 0 for r in result.receipts)


def test_each_worker_is_billed_to_itself(tmp_path: Path) -> None:
    """One shared meter would total the crew correctly and lose which worker spent what — which is
    the number that makes "N attempts, the test picks the winner" an accountable trade."""
    cheap, dear = _Backend("openai/gpt-4o-mini"), _Backend("openai/gpt-4o")
    crew = IsolatedCrew(
        _Backend(), [_worker("cheap", cheap), _worker("dear", dear)], max_workers=2
    )

    result = crew.run("do the thing", tmp_path)
    by_name = {r.task_id: r for r in result.receipts}

    assert by_name["cheap"].model == "openai/gpt-4o-mini"
    assert by_name["dear"].model == "openai/gpt-4o"
    assert by_name["dear"].usd is not None and by_name["cheap"].usd is not None
    assert by_name["dear"].usd > by_name["cheap"].usd


def test_an_unpriced_worker_costs_unknown_not_zero(tmp_path: Path) -> None:
    """The dangerous direction, and a sabotage run found nothing was holding it.

    `usd or 0.0` reads as a harmless default and is not one: it turns "we cannot price this model"
    into "this model was free", which understates the total in exactly the direction that flatters
    whichever configuration used the unpriced one. The recorder downstream already refuses to sum a
    partial total — it can only do that if the unknown arrives as an unknown.
    """
    crew = IsolatedCrew(
        _Backend(), [_worker("mystery", _Backend("nobody/prices-this-one"))], max_workers=1
    )

    result = crew.run("do the thing", tmp_path)

    assert len(result.receipts) == 1
    assert result.receipts[0].usd is None
    assert result.receipts[0].prompt_tokens == 100  # the tokens are known even when the price is not


def test_a_worker_that_never_called_a_model_is_not_billed_zero(tmp_path: Path) -> None:
    """Left out rather than filed at 0.00. A zero-dollar row invites the reader to conclude a
    worker ran for free; absence says it did not run."""
    from chimera.orchestration.crew import _receipts_from
    from chimera.orchestration.metering import MeteredBackend

    idle = MeteredBackend(_Backend(), label="idle")

    assert _receipts_from([("idle", idle)]) == []


# --------------------------------------------------------------------------- the wiring


def test_the_bill_reaches_the_cost_screen(tmp_path: Path) -> None:
    """End to end over the real objects: a crew run, its real result, the real recorder, the real
    log. Every hop between them was individually fine, and the sum of them recorded nothing."""
    from chimera.api.orchestration_api import _record_run_spend
    from chimera.api.usage import load_usage

    result = IsolatedCrew(_Backend(), [_worker("alice")], max_workers=1).run("go", tmp_path)
    _record_run_spend(tmp_path, "crew_run_1", result)

    rows = load_usage(tmp_path / "usage.jsonl")
    assert len(rows) == 1, "the crew route is still invisible to the Cost screen"
    assert rows[0].session_id == "orchestration:crew_run_1"
    assert rows[0].prompt_tokens == 100 and rows[0].completion_tokens == 20
    assert rows[0].usd is not None and rows[0].usd > 0
