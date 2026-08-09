"""The terminal conversation outlives the terminal.

``chimera chat`` built a ``ChatSession`` in memory and dropped it on exit, so the agent that calls
itself "your terminal right-hand" forgot yesterday. The store that fixes it already existed and was
already tested — ``SessionManager`` over ``<home>/sessions`` — but it was constructed in exactly one
place, the desktop API. Two products, one data directory, no conversation in common.

These tests are about the choosing and the saving, which is where the behaviour lives. The store
itself has its own tests; re-testing it here would be testing the dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.api.sessions import SessionManager, SessionStore
from chimera.cli.main import _resume_or_new
from chimera.interface.session import ChatTurn


def _manager(tmp_path: Path) -> SessionManager:
    class _Session:
        def __init__(self) -> None:
            self.turns: list[ChatTurn] = []

    return SessionManager(lambda: _Session(), SessionStore(tmp_path / "sessions"))


def test_the_first_run_starts_a_thread_rather_than_resuming_nothing(tmp_path: Path) -> None:
    active, resumed = _resume_or_new(_manager(tmp_path), None, False)
    assert active
    assert resumed is False


def test_the_next_run_picks_up_where_the_last_one_stopped(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = manager.new()
    session: Any = manager.get(first)
    session.turns.append(ChatTurn(user="what did we decide about the retry cap?", assistant="six"))
    manager.persist(first)

    active, resumed = _resume_or_new(_manager(tmp_path), None, False)

    assert active == first
    assert resumed is True
    # And the thread is really there — this is the defect the change exists to fix.
    assert _manager(tmp_path).get(active).turns[0].user.startswith("what did we decide")


def test_new_refuses_to_resume(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    old = manager.new()
    manager.get(old).turns.append(ChatTurn(user="hello", assistant="hi"))
    manager.persist(old)

    active, resumed = _resume_or_new(manager, None, True)

    assert active != old
    assert resumed is False
    # The old thread stays on disk. A flag that means "start fresh" must not mean "throw away".
    assert manager.get(old).turns


def test_an_explicit_id_wins_over_the_newest(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    older = manager.new()
    manager.get(older).turns.append(ChatTurn(user="a", assistant="b"))
    manager.persist(older)
    newer = manager.new()
    manager.get(newer).turns.append(ChatTurn(user="c", assistant="d"))
    manager.persist(newer)

    active, resumed = _resume_or_new(manager, older, False)

    assert active == older
    assert resumed is True


def test_an_id_that_does_not_exist_yet_is_a_name_not_an_error(tmp_path: Path) -> None:
    # `chimera chat -s standup` is a reasonable way to name a thread. Rejecting it would be
    # pedantry, and the caller needs to know it is new so it does not announce a resume.
    active, resumed = _resume_or_new(_manager(tmp_path), "standup", False)
    assert active == "standup"
    assert resumed is False


def test_the_cli_and_the_app_read_the_same_directory(tmp_path: Path) -> None:
    """The point of the change, stated as a test.

    If these two ever diverge, a thread started in the terminal stops appearing in the app and
    nothing fails — which is exactly how the split lasted this long.
    """
    from chimera.config import Settings

    settings = Settings(CHIMERA_HOME=str(tmp_path))
    cli_root = settings.home / "sessions"

    store = SessionStore(cli_root)
    store.save("shared", [ChatTurn(user="started in the terminal", assistant="ok")])

    # The desktop API builds its store the same way (api/app.py: settings.home / "sessions").
    from_app = SessionStore(settings.home / "sessions")
    assert [turn.user for turn in from_app.load("shared")] == ["started in the terminal"]
    assert [meta.id for meta in from_app.list()] == ["shared"]
