"""A reply the route sent without reading the prompt is a failed call, never a wrong answer.

In the Novita pilot of `bench/useful_context` (2026-09-30), one 512k call came back after 23.6 s with
finish_reason "stop", no content, 0 prompt tokens, 0 completion tokens and no cost, and the grader scored
it `empty`: a route failure counted as the model forgetting. The runner now retries such a reply like any
failed call, and records an error if it persists, so it can never reach the grade.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

RUN = Path(__file__).resolve().parents[1] / "bench" / "useful_context" / "run.py"


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location("useful_context_run_unread", RUN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "useful_context_run_unread", module)
    spec.loader.exec_module(module)
    module._spent = 0.0
    monkeypatch.setattr(module.time, "sleep", lambda _s: None)
    return module


def _reply(prompt_tokens: int, content: str) -> dict[str, Any]:
    return {
        "provider": "DeepInfra",
        "content": content,
        "reasoning_head": "",
        "reasoning_chars": 0,
        "tool_calls": 0,
        "finish_reason": "stop",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": 3 if prompt_tokens else 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "cost_reported": None,
        "cost": 0.0,
    }


def _run(
    runner: Any, monkeypatch: pytest.MonkeyPatch, replies: list[dict[str, Any]]
) -> tuple[dict[str, Any], int]:
    calls = {"n": 0}

    def call(request: dict[str, Any], timeout: int = 900) -> dict[str, Any]:
        calls["n"] += 1
        return replies[min(calls["n"], len(replies)) - 1]

    monkeypatch.setattr(runner, "_call", call)
    item = runner.it.items("T", 1)[0]
    return runner._one(item, 4_000, "4000", 4.0, 100.0), calls["n"]


def test_an_unread_prompt_that_persists_is_an_error_and_is_not_graded(
    runner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    row, calls = _run(runner, monkeypatch, [_reply(0, "")])

    assert calls == 3, "retried like any failed call"
    assert row["error"].startswith("EmptyResponse"), "an error, never a zero"
    assert "grade" not in row


def test_an_unread_prompt_once_is_retried_and_the_real_answer_is_graded(
    runner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = runner.it.items("T", 1)[0]
    row, calls = _run(runner, monkeypatch, [_reply(0, ""), _reply(3_900, item.expected)])

    assert calls == 2
    assert row["error"] == ""
    assert row["prompt_tokens"] == 3_900
    assert "grade" in row
