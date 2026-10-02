"""A call the route refuses for load waits longer before its retries, on the profile that asked for it.

The option-B run of `bench/useful_context` (glm-5.3-flash to 512k, 2026-09-30) was ended by its stop rule
after every error came back HTTP 429, "temporarily rate-limited upstream", three attempts each, 15 s and
30 s apart. The owner chose to relaunch pressing the route less: one worker, and a refused call waiting
60 s then 120 s. Only that profile changes; every other profile keeps the waits its runs were made with,
and any other error keeps them too.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

RUN = Path(__file__).resolve().parents[1] / "bench" / "useful_context" / "run.py"


class RateLimitError(Exception):
    """Named like litellm's, which is how the runner recognises a refusal."""


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location("useful_context_run_waits", RUN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "useful_context_run_waits", module)
    spec.loader.exec_module(module)
    module._spent = 0.0
    return module


def _waits(
    runner: Any, monkeypatch: pytest.MonkeyPatch, profile: str, error: Exception
) -> list[float]:
    # The profile also sets the filler's corpus on the shared `items` module; put it back afterwards.
    monkeypatch.setattr(runner.it, "CORPUS_ROOTS", runner.it.CORPUS_ROOTS)
    runner.use_profile(profile)
    slept: list[float] = []
    monkeypatch.setattr(runner.time, "sleep", slept.append)

    def refuse(request: dict[str, Any], timeout: int = 900) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(runner, "_call", refuse)
    item = runner.it.items("T", 1)[0]
    row = runner._one(item, 4_000, "4000", 4.0, 100.0)
    assert row["error"], "a call that never answers is an error, never a zero"
    return slept


@pytest.mark.parametrize("profile", ["glm53flash512", "glm53flash512_novita"])
def test_the_slow_profiles_wait_a_minute_then_two_after_a_refusal(
    runner: Any, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    refused = RateLimitError('{"error":{"code":429,"message":"temporarily rate-limited upstream"}}')
    assert _waits(runner, monkeypatch, profile, refused) == [60, 120]


def test_any_other_error_keeps_the_usual_waits_on_the_slow_profile(
    runner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _waits(runner, monkeypatch, "glm53flash512", TimeoutError("A Timeout Occurred")) == [
        15,
        30,
    ]


@pytest.mark.parametrize("profile", ["v4flash", "luna", "glm53flash", "glm53"])
def test_every_other_profile_waits_as_its_runs_did(
    runner: Any, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    # glm53flash's option-A top-rung rule is not reached: 4k is not its top rung, so it retries as usual.
    refused = RateLimitError('{"error":{"code":429}}')
    assert _waits(runner, monkeypatch, profile, refused) == [15, 30]
