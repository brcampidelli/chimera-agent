"""A question is in this process's memory before it is on disk.

A screen lists waiting questions from their files. ``ask_durably`` used to write the file and only
then remember the question's code, so a click in between — the owner seeing the card appear and
answering it at once — was told the question was asked by another process and needed a code.
``test_only_the_screen_approves_without_a_code.py`` hit the same window on CI: it waits for the file
and then checks :func:`pending.approvable_here`, and on one run the check came first.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from chimera.governance import pending


def _ask(home: Path) -> bool:
    return pending.ask_durably(
        home, "run_shell\ngit push --force", "policy: force push",
        on_asked=lambda *_: None, wait_seconds=0.2, poll_seconds=0.05,
    )


def test_the_code_is_held_when_the_question_file_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []
    real = Path.write_text

    def spy(self: Path, *args: Any, **kwargs: Any) -> int:
        if self.name.endswith(".ask.json"):
            seen.append(pending.approvable_here(self.name.removesuffix(".ask.json")))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    _ask(tmp_path / "home")
    assert seen == [True], "the question reached disk before this process held its code"


def test_a_question_that_cannot_be_written_leaves_no_code_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    real = Path.write_text

    def refuse(self: Path, *args: Any, **kwargs: Any) -> int:
        if self.name.endswith(".ask.json"):
            asked.append(self.name.removesuffix(".ask.json"))
            raise OSError("disk full")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", refuse)
    assert _ask(tmp_path / "home") is False
    assert len(asked) == 1
    assert not pending.approvable_here(asked[0]), "a question nobody can see kept its code"


def test_a_screen_that_answers_the_moment_the_card_appears_needs_no_code(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result: list[bool] = []
    thread = threading.Thread(
        target=lambda: result.append(
            pending.ask_durably(
                home, "run_shell\ngit push --force", "policy: force push",
                on_asked=lambda *_: None, wait_seconds=10.0, poll_seconds=0.02,
            )
        ),
        daemon=True,
    )
    thread.start()
    while not pending.pending(home):
        pass
    request_id = pending.pending(home)[0].id
    assert pending.approvable_here(request_id)
    pending.answer(home, request_id, False)
    thread.join(10.0)
    assert result == [False]
