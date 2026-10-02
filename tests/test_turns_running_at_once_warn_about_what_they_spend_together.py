"""Turns running at once warn about what they spend together.

Found reading the code on 2026-09-30 (R14 of the review of several conversations at once). Each
coding turn warns when IT has spent US$ 1 (`spend_warn`), and a ceiling exists only when the person
types one. With five conversations running at once, five turns could spend US$ 0.90 each — US$ 4.50
together — and not one of them would say anything.

The owner's rule of 2026-09-27 holds: a limit where a person is waiting is a warning, not a stop.
So this is a warning. Every turn's meter reports what it has spent to one tally, and when two or more
turns are running and their sum crosses a multiple of the warning amount, each of them says so
(`combined_spend`), once per multiple. Nothing is stopped, and a ceiling the person typed is still
that turn's own.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.combined_spend import CombinedSpend
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

TIMEOUT = 10.0


def _codes(notes: list[tuple[str, str, dict[str, Any]]]) -> list[str]:
    return [code for code, _text, _data in notes]


def test_two_turns_that_cross_the_amount_together_both_hear_it_once() -> None:
    tally = CombinedSpend()
    a = tally.budget("a", max_usd=None, warn_usd=1.0)
    b = tally.budget("b", max_usd=None, warn_usd=1.0)

    a.charge(0.6)
    assert _codes(a.take_notices()) == [], "one turn under the amount warned"
    b.charge(0.6)
    [(code, text, data)] = b.take_notices()
    assert code == "combined_spend" and data == {"usd": 1.2, "turns": 2}
    assert "US$ 1.20" in text
    assert _codes(a.take_notices()) == ["combined_spend"], "the other running turn was not told"
    assert _codes(a.take_notices()) == [] and _codes(b.take_notices()) == [], "said more than once"


def test_the_next_multiple_is_said_again() -> None:
    tally = CombinedSpend()
    a = tally.budget("a", max_usd=None, warn_usd=1.0)
    b = tally.budget("b", max_usd=None, warn_usd=1.0)
    a.charge(0.6)
    b.charge(0.6)
    a.take_notices()
    b.take_notices()

    a.charge(1.0)  # 2.20 together; this turn alone is now over the amount too

    assert _codes(a.take_notices()) == ["spend_warn", "combined_spend"]
    assert _codes(b.take_notices()) == ["combined_spend"]


def test_one_turn_alone_hears_only_its_own_warning() -> None:
    tally = CombinedSpend()
    a = tally.budget("a", max_usd=None, warn_usd=1.0)

    a.charge(1.5)

    assert _codes(a.take_notices()) == ["spend_warn"], "a turn alone was told about a sum of one"


def test_a_finished_turn_leaves_the_sum() -> None:
    tally = CombinedSpend()
    a = tally.budget("a", max_usd=None, warn_usd=1.0)
    a.charge(0.9)
    a.take_notices()
    tally.close("a")
    b = tally.budget("b", max_usd=None, warn_usd=1.0)

    b.charge(0.5)

    assert _codes(b.take_notices()) == [], "a finished turn's spend still counted"


def test_a_typed_ceiling_is_still_the_turns_own() -> None:
    tally = CombinedSpend()
    capped = tally.budget("a", max_usd=0.5, warn_usd=1.0)
    other = tally.budget("b", max_usd=None, warn_usd=1.0)
    other.charge(3.0)

    capped.charge(0.4)

    assert capped.blocked() is None, "another turn's spend reached this turn's ceiling"
    capped.charge(0.2)
    assert capped.blocked() is not None


# ------------------------------------------------------------------ through the app


class _Spending:
    """Charges its turn's meter and reads its notices the way the real loop does once per step."""

    entered: list[threading.Event] = []
    release = threading.Event()
    heard: dict[str, list[str]] = {}

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, spend: Any = None, on_notice: Any = None, **_kw: Any) -> AgentResult:
        if task.startswith("spend"):
            assert spend is not None, "the turn was run without the shared meter"
            spend.charge(0.6)
            heard = type(self).heard.setdefault(task, [])
            heard.extend(_codes(spend.take_notices()))
            type(self).entered.pop(0).set()
            type(self).release.wait(TIMEOUT)
            heard.extend(_codes(spend.take_notices()))
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


def test_two_conversations_running_at_once_share_one_meter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Spending, raising=True)
    first, second = threading.Event(), threading.Event()
    _Spending.entered = [first, second]
    _Spending.release.clear()
    _Spending.heard = {}
    ws_a, ws_b = tmp_path / "a", tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    client = TestClient(build_api_app(lambda: ChatSession(_Spending()), workspace=ws_a,
                                      settings=Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")))

    def turn(message: str, ws: Path) -> threading.Thread:
        thread = threading.Thread(
            target=lambda: client.post("/api/code/turn", json={"message": message, "workspace": str(ws)}),
            daemon=True,
        )
        thread.start()
        return thread

    one = turn("spend one", ws_a)
    assert first.wait(TIMEOUT)
    two = turn("spend two", ws_b)
    assert second.wait(TIMEOUT)
    _Spending.release.set()
    one.join(TIMEOUT)
    two.join(TIMEOUT)

    assert _Spending.heard["spend two"] == ["combined_spend"], _Spending.heard
    assert _Spending.heard["spend one"] == ["combined_spend"], "the turn that started first was not told"
