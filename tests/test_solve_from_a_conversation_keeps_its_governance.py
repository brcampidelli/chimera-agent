"""``/solve`` from ``chat`` or ``assist`` runs under the conversation's governance, not without it.

The conversation is governed (`chimera/cli/right_hand.py`): a trust kernel, a taint ledger that
narrows dangerous tools once untrusted text arrives, an approver that asks the person at the
keyboard, and the owner's ``CHIMERA_REACH`` floor. ``/solve`` hands the task to ``chimera solve``,
and ``_solve_from_conversation`` overrode only task, workspace, model, write region and ceiling —
so the loop ran on ``solve``'s defaults, ``guard=False`` and ``taint=False``, with the reach floor
never applied (``_maybe_defer``'s docstring already admitted the floor does not reach ``solve``).
A governed conversation had an ungoverned exit one slash command away, and the handoff test
asserted everything about the call except that.

Plain ``chimera solve`` is untouched: its defaults are the command's own, and the last test pins it.

Free: the worker ``Agent`` is replaced by a recorder that stops the run before any model call.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chimera.cli.main import _solve_from_conversation
from chimera.cli.right_hand import build_right_hand
from chimera.config import get_settings


class _Stop(Exception):
    """Raised by the recording Agent once it has seen the registry the loop would run on."""


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_CASCADE", "CHIMERA_AUTO_FUSE", "CHIMERA_REACH", "CHIMERA_GOVERNANCE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("chimera.sandbox._warned", False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _record_worker(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    seen: list[Any] = []

    class Recorder:
        def __init__(self, _backend: Any, registry: Any, *_a: Any, **_k: Any) -> None:
            seen.append(registry)
            raise _Stop

    monkeypatch.setattr("chimera.core.Agent", Recorder)
    return seen


def _wrappers(tool: Any) -> list[Any]:
    chain = [tool]
    while hasattr(chain[-1], "inner"):
        chain.append(chain[-1].inner)
    return chain


def _hand(tmp_path: Path) -> Any:
    ws = tmp_path / "repo"
    ws.mkdir(exist_ok=True)
    return build_right_hand(ws, settings=get_settings(), surface="chat")


def _run(tmp_path: Path, hand: Any) -> None:
    with pytest.raises(_Stop):
        _solve_from_conversation(
            "fix the parser",
            workspace=str(tmp_path / "repo"),
            model=None,
            write_region=None,
            hand=hand,
        )


def test_the_conversations_ledger_and_approver_reach_the_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _record_worker(monkeypatch)
    hand = _hand(tmp_path)
    _run(tmp_path, hand)

    chain = _wrappers(seen[0].get("write_file"))
    ledgered = [w for w in chain if hasattr(w, "ledger") and hasattr(w, "approve")]
    assert ledgered, "the loop ran with no taint ledger"
    assert ledgered[0].ledger is hand.ledger, "the loop got a fresh ledger, not the conversation's"
    assert ledgered[0].approve is hand.approve, "the loop asks someone other than this person"


def _kernel_layer(tool: Any) -> Any:
    return next((w for w in _wrappers(tool) if hasattr(w, "kernel")), None)


@pytest.mark.parametrize("mode", ["off", "observe", "enforce"])
def test_the_loop_has_a_trust_kernel_exactly_when_the_conversation_does(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """Inherit, not escalate. This test used to assert a kernel on the loop unconditionally, under
    the default ``CHIMERA_GOVERNANCE=off`` — where the conversation has NO kernel (``govern_step``
    returns the registry unwrapped). So ``/solve`` was stricter than the posture it claimed to
    inherit: BLOCK/REVIEW in front of actions the conversation runs unasked, and under a pipe the
    deny approver turned them into refusals that existed only inside ``/solve``."""
    monkeypatch.setenv("CHIMERA_GOVERNANCE", mode)
    get_settings.cache_clear()
    seen = _record_worker(monkeypatch)
    hand = _hand(tmp_path)
    in_conversation = _kernel_layer(hand.registry.get("write_file"))

    _run(tmp_path, hand)
    in_loop = _kernel_layer(seen[0].get("write_file"))

    assert (in_loop is None) == (in_conversation is None)


def test_under_observe_the_loops_kernel_records_instead_of_asking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``observe`` measures: its kernel's approver says yes and writes it down. The loop's kernel
    used to receive the conversation's ledger approver — the REPL prompt, a deny under a pipe — which
    is enforcement wearing observe's name. It must also read the conversation's ledger lineage."""
    monkeypatch.setenv("CHIMERA_GOVERNANCE", "observe")
    get_settings.cache_clear()
    seen = _record_worker(monkeypatch)
    hand = _hand(tmp_path)

    _run(tmp_path, hand)
    kernel = _kernel_layer(seen[0].get("write_file"))

    assert kernel is not None
    assert kernel.approve is not hand.approve
    assert kernel.approve("review", "write a file") is True
    assert kernel.lineage == hand.ledger.lineage


def test_the_owners_reach_floor_reaches_the_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``CHIMERA_REACH=read_only`` takes the shell away from the conversation; the loop the
    conversation hands its task to must not give it back."""
    monkeypatch.setenv("CHIMERA_REACH", "read_only")
    get_settings.cache_clear()
    seen = _record_worker(monkeypatch)
    hand = _hand(tmp_path)
    assert "run_shell" not in hand.registry, "precondition: the floor removes the shell here"

    _run(tmp_path, hand)

    assert "run_shell" not in seen[0]


def test_plain_solve_keeps_its_own_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without a conversation there is nothing to inherit: ``chimera solve`` stays as it was."""
    seen = _record_worker(monkeypatch)
    (tmp_path / "repo").mkdir()
    with pytest.raises(_Stop):
        _solve_from_conversation(
            "fix the parser", workspace=str(tmp_path / "repo"), model=None, write_region=None
        )

    assert not any(hasattr(w, "ledger") for w in _wrappers(seen[0].get("write_file")))


@pytest.mark.parametrize(("command", "mode"), [("chat", "off"), ("assist", "off"), ("chat", "enforce"), ("assist", "observe")])
def test_the_slash_command_switches_the_loops_gates_on(
    monkeypatch: pytest.MonkeyPatch, command: str, mode: str, tmp_path: Path
) -> None:
    """Through the REPL itself: the ledger always (the conversation always has one), the kernel
    exactly when the conversation's governance mode installs one."""
    monkeypatch.setenv("CHIMERA_GOVERNANCE", mode)
    get_settings.cache_clear()
    from typer.testing import CliRunner

    from chimera.cli.main import app
    from chimera.interface.session import ChatTurn, TurnReport

    class Session:
        max_history = 6

        def __init__(self, agent: Any = None, **_k: Any) -> None:
            self.agent = agent
            self.turns: list[ChatTurn] = []
            self.profile = ""

        def send_verbose(self, message: str, **_k: Any) -> TurnReport:
            return TurnReport(answer="ok", model="fake/model")

    seen: list[dict[str, Any]] = []

    def fake_solve(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return None

    monkeypatch.setattr("chimera.interface.ChatSession", Session)
    monkeypatch.setattr("chimera.cli.main.solve", fake_solve)
    result = CliRunner().invoke(
        app, [command, "--no-memory", "-w", str(tmp_path)], input="/solve fix it\n/exit\n"
    )

    assert result.exit_code == 0, result.output
    assert seen and seen[0]["taint"] is True
    assert seen[0]["guard"] is (mode != "off")
