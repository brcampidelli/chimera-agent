"""The loop knew exactly why it stopped, and the receipt threw the answer away.

`AgentResult.stopped_reason` is a real taxonomy of six values, each assigned at one place in the
loop, and `SpendExceeded` is a subclass of `BudgetExceeded` purely so the two ceilings can be told
apart. It reaches `traces.jsonl`, the SSE `done` frame, and a badge on screen.

It does not reach `runs.jsonl`. `AutonomousResult` carries it in memory and `build_receipt` never
reads it, so a run cancelled by the user, a run stopped by the dollar ceiling, and a run whose work
was rejected by the verifier all persist the same `success: false`. The one file kept as the durable
record of what happened cannot answer "how many runs stopped at the cap this month" — not because
the number is hard, but because the field was dropped at the boundary.

Empty string is the default, and it means "a receipt written before this field existed". That is the
same shape `verify_source` and `profile` already use here, and for the same reason: filling old rows
with a plausible value would put invented evidence into the one view whose job is to say what
happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.api.runs import RunReceipt, build_receipt


@dataclass
class _Tentativa:
    index: int = 1
    answer: str = ""
    verified: bool = False
    reverted: bool = False


@dataclass
class _Resultado:
    """The shape `build_receipt` consumes, with only the fields it reads."""

    answer: str = ""
    success: bool = False
    paused: bool = False
    attempts: list[Any] = field(default_factory=list)
    stopped_reason: str = ""


def _recibo(**kwargs: Any) -> RunReceipt:
    return build_receipt(_Resultado(**kwargs), "arrume o build", None, "2026-09-01T00:00:00Z")  # type: ignore[arg-type]


def test_the_dollar_ceiling_is_recorded() -> None:
    """The question that had no answer: which of these failures cost money and stopped early."""
    assert _recibo(stopped_reason="spend").stopped_reason == "spend"


def test_a_cancelled_run_is_not_a_failed_one() -> None:
    """Both persist `success: false`; only one of them means something went wrong."""
    cancelado = _recibo(stopped_reason="cancelled")
    esgotado = _recibo(stopped_reason="max_steps")

    assert cancelado.success is False and esgotado.success is False
    assert cancelado.stopped_reason != esgotado.stopped_reason


def test_a_finished_run_says_final() -> None:
    """The value must survive whatever it is, not only the interesting ones."""
    assert _recibo(success=True, stopped_reason="final").stopped_reason == "final"


def test_a_result_without_the_field_still_builds() -> None:
    """`build_receipt` reads duck-typed results from several call sites, and one of them is a test
    double written before this field existed. A required read here would turn a new field into a
    crash on the path that persists the run — after the work was already paid for."""

    class _Antigo:
        answer = "pronto"
        success = True
        paused = False
        attempts: list[Any] = []

    assert build_receipt(_Antigo(), "t", None, "2026-09-01T00:00:00Z").stopped_reason == ""  # type: ignore[arg-type]


def test_an_old_receipt_on_disk_still_parses() -> None:
    """The upgrade path: `runs.jsonl` holds rows written before the field, and they must load."""
    antigo = {
        "ts": "2026-01-01T00:00:00Z",
        "task": "t",
        "success": True,
        "paused": False,
        "verify_command": None,
        "answer": "pronto",
        "attempts": [],
    }

    assert RunReceipt.model_validate(antigo).stopped_reason == ""
