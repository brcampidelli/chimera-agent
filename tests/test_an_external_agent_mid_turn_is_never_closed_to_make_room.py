"""An external agent in the middle of a turn is never closed to make room, nor for being idle.

Found reading the code on 2026-09-30 (R11 of the review of several conversations at once). The ACP
registry keeps at most four agents alive and closed the least recently used when a fifth started,
without asking whether it was mid-turn: a fifth Claude Code or Gemini conversation killed the oldest
agent process in the middle of its work. The idle sweep had the same blind spot for a turn longer than
an hour.

Now a turn marks itself busy while it prompts, and the registry closes only idle agents. When every
agent is busy it goes over the limit for a while (and says so in the log) rather than killing work.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

import chimera.acp.registry as registry_module
from chimera.acp.registry import AcpRegistry, SessionKey


class _FakeTurn:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        self.busy = False
        self.closed = False

    def start(self) -> _FakeTurn:
        return self

    def is_alive(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True

    def rebind(self, **_kw: Any) -> None:
        return None

    def cancel(self) -> None:
        return None


@pytest.fixture(autouse=True)
def fake_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "AcpTurn", _FakeTurn)


def _key(n: int) -> SessionKey:
    return SessionKey(session_id=f"s{n}", provider="claude", workspace=str(Path.cwd()))


def test_a_new_agent_closes_the_oldest_idle_one_never_a_busy_one() -> None:
    reg = AcpRegistry(max_live=2)
    first = reg.get(_key(1), None)  # type: ignore[arg-type]
    first.busy = True  # mid-turn
    second = reg.get(_key(2), None)  # type: ignore[arg-type]

    reg.get(_key(3), None)  # type: ignore[arg-type]

    assert not first.closed, "the oldest agent was closed in the middle of its turn"
    assert second.closed, "the oldest IDLE agent is the one that makes room"


def test_when_every_agent_is_busy_the_registry_goes_over_rather_than_killing_work() -> None:
    reg = AcpRegistry(max_live=1)
    first = reg.get(_key(1), None)  # type: ignore[arg-type]
    first.busy = True

    reg.get(_key(2), None)  # type: ignore[arg-type]

    assert not first.closed
    assert reg.live() == 2


def test_the_idle_sweep_leaves_a_long_turn_alone() -> None:
    reg = AcpRegistry(idle_seconds=0.01)
    working = reg.get(_key(1), None)  # type: ignore[arg-type]
    working.busy = True
    time.sleep(0.05)

    reg.evict_idle()

    assert not working.closed, "a turn longer than the idle limit was closed mid-turn"
    working.busy = False
    reg.evict_idle()
    assert working.closed, "once it is idle, the sweep still closes it"


def test_a_turn_is_busy_exactly_while_it_prompts() -> None:
    from chimera.acp.turn import AcpTurn

    turn = AcpTurn.__new__(AcpTurn)
    seen: list[bool] = []

    class _Conn:
        exit_code = None

        def request(self, *_a: Any, **_kw: Any) -> dict[str, Any]:
            seen.append(turn.busy)
            return {"stopReason": "end_turn"}

    turn._conn = _Conn()  # type: ignore[assignment]
    turn._session_id = "sid"
    turn.turn_timeout = 5.0
    turn.busy = False

    turn.prompt("hello")

    assert seen == [True]
    assert turn.busy is False
