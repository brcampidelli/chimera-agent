"""A turn's receipt learns what the account was actually charged, and never shows less than that.

The receipt's `usd` is an estimate: the catalogue row of the model id, while one id is served by
several routes at different prices. The router's generation record carries `total_cost`, the charge,
~10 s after the call (`chimera.providers.generation`). A turn is billed once per call, so its bill is
the sum over every id. A partial sum would show a cheaper turn than the one that happened, so the
answers found so far are kept between reopens and `billed_usd` is written only when all are in.
"""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from chimera.providers import generation


def _receipt(ids: list[str], model: str = "openrouter/deepseek/x", **extra: Any) -> dict[str, Any]:
    return {"model": model, "generation_ids": ids, "provider": "", "usd": 0.01, **extra}


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self._raw = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_: object) -> None:
        return None


# ------------------------------------------------------------------ reading the record


def test_the_charge_is_read_off_the_record() -> None:
    opener = lambda *_a, **_k: _Resp({"data": {"total_cost": 0.00042, "provider_name": "X"}})  # noqa: E731
    assert generation.lookup_cost("gen-1", api_key="sk-x", opener=opener) == 0.00042


@pytest.mark.parametrize("data", [{}, {"total_cost": None}, {"total_cost": "0.1"}, {"total_cost": True}])
def test_a_record_without_a_number_is_unknown_never_free(data: dict[str, Any]) -> None:
    opener = lambda *_a, **_k: _Resp({"data": data})  # noqa: E731
    assert generation.lookup_cost("gen-1", api_key="sk-x", opener=opener) is None


def test_not_there_yet_is_unknown() -> None:
    def opener(*_a: Any, **_k: Any) -> Any:
        raise urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO())  # type: ignore[arg-type]

    assert generation.lookup_cost("gen-1", api_key="sk-x", opener=opener) is None


# ------------------------------------------------------------------ the pass


def test_every_id_answered_writes_the_sum_and_drops_the_parts() -> None:
    receipts = [_receipt(["a", "b", "c"])]
    costs = {"a": 0.001, "b": 0.002, "c": 0.0005}
    changed = generation.resolve_missing_bills(receipts, api_key="sk-x", lookup=lambda i, **_: costs[i])
    assert changed == 1
    assert receipts[0]["billed_usd"] == 0.0035
    assert "billed_parts" not in receipts[0]


def test_an_id_not_answered_keeps_what_was_found_and_writes_no_total() -> None:
    receipts = [_receipt(["a", "b"])]
    generation.resolve_missing_bills(
        receipts, api_key="sk-x", lookup=lambda i, **_: 0.001 if i == "a" else None
    )
    assert receipts[0].get("billed_usd") is None, "a partial sum would show a cheaper turn"
    assert receipts[0]["billed_parts"] == {"a": 0.001}

    asked: list[str] = []

    def second(i: str, **_: Any) -> float:
        asked.append(i)
        return 0.004

    generation.resolve_missing_bills(receipts, api_key="sk-x", lookup=second)
    assert asked == ["b"], "the next reopen asks only for what is missing"
    assert receipts[0]["billed_usd"] == 0.005


def test_the_pass_stops_at_its_budget_and_resumes_later() -> None:
    receipts = [_receipt(["a", "b", "c", "d"])]
    ticks = iter([0.0, 0.0, 0.5, 1.0, 3.0, 3.0, 3.0])
    generation.resolve_missing_bills(
        receipts, api_key="sk-x", budget_seconds=2.5, lookup=lambda *_a, **_k: 0.001,
        clock=lambda: next(ticks),
    )
    assert receipts[0].get("billed_usd") is None
    assert 0 < len(receipts[0]["billed_parts"]) < 4


def test_newest_receipt_first() -> None:
    receipts = [_receipt(["old"]), _receipt(["new"])]
    asked: list[str] = []

    def lookup(i: str, **_: Any) -> float:
        asked.append(i)
        return 0.001

    generation.resolve_missing_bills(receipts, api_key="sk-x", lookup=lookup)
    assert asked == ["new", "old"]


def test_what_is_not_asked_about() -> None:
    def refuse(*_a: Any, **_k: Any) -> float:
        raise AssertionError("asked")

    receipts = [
        _receipt(["a"], model="anthropic/claude-x"),  # not through the router
        _receipt(["b"], billed_usd=0.002),  # already known
        _receipt([]),  # nothing to ask about
    ]
    assert generation.resolve_missing_bills(receipts, api_key="sk-x", lookup=refuse) == 0
    assert generation.resolve_missing_bills([_receipt(["a"])], api_key="", lookup=refuse) == 0
