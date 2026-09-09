"""A thread that came back off disk says so — quietly, under the reply, and not inside it.

``ChatTurn.provenance`` has existed since the transcript learned to persist, ``SessionStore.load``
stamps every restored turn, and ``_replay`` puts the ones that are not known clean inside the
``<<external-data>>`` fence. All of that was invisible: ``chimera/interface/render.py`` mentioned
provenance **zero** times, so the person could not tell a conversation the model had just had from
one reassembled out of a file whose cleanliness nobody ever measured.

Two properties are worth more than the wording:

* it is **under** the reply, never inside it. The reply text is replayed into every later prompt,
  so a marker written into it would put words in the model's mouth for the rest of the thread and
  be saved back that way, one layer deeper on each reopen;
* a live turn says nothing. A line printed after every ordinary turn is a line nobody reads by the
  third one, and this exists to be noticed.

Everything here is free: no model call, no network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings
from chimera.interface import render
from chimera.interface.session import CLEAN, TAINTED, UNKNOWN, ChatTurn, TurnReport

runner = CliRunner()


def squashed(text: str) -> str:
    """Rich hard-wraps at the console width; join the pieces so a phrase survives the wrap."""
    return "".join(text.split())


def shows(output: str, phrase: str) -> bool:
    return squashed(phrase) in squashed(output)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_CHAT_MEMORY", "CHIMERA_CASCADE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("chimera.sandbox._warned", False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# -- the formatter --------------------------------------------------------------------------------


def _report(**kwargs: Any) -> TurnReport:
    return TurnReport(answer="ok", model="fake/model", **kwargs)


def test_a_live_clean_turn_says_nothing() -> None:
    assert render.provenance_line(_report(provenance=CLEAN), restored=[]) == ""


def test_restored_turns_whose_provenance_was_never_recorded_are_counted() -> None:
    line = render.provenance_line(_report(provenance=CLEAN), restored=[UNKNOWN, UNKNOWN])
    assert "2 restored turn(s)" in line
    assert "2 never measured" in line
    assert "data fence" in line


def test_a_restored_turn_recorded_clean_is_not_dressed_as_a_risk() -> None:
    """'Nothing untrusted entered' and 'nobody was in a position to say' are different facts, and a
    line that renders them identically publishes the more alarming of the two."""
    line = render.provenance_line(_report(provenance=CLEAN), restored=[CLEAN, CLEAN])
    assert "2 recorded clean" in line
    assert "data fence" not in line


def test_a_tainted_turn_says_the_thread_is_downstream_of_it() -> None:
    line = render.provenance_line(_report(provenance=TAINTED), restored=[])
    assert "read external content" in line


def test_the_counts_are_separated_by_kind() -> None:
    line = render.provenance_line(
        _report(provenance=CLEAN), restored=[UNKNOWN, TAINTED, CLEAN, UNKNOWN]
    )
    assert "4 restored turn(s)" in line
    assert "2 never measured" in line and "1 tainted" in line and "1 recorded clean" in line


# -- the surface ----------------------------------------------------------------------------------


def _install(monkeypatch: pytest.MonkeyPatch, provenance: str = CLEAN) -> list[Any]:
    made: list[Any] = []

    class Fake:
        max_history = 6

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.turns: list[ChatTurn] = []
            self.profile = ""
            made.append(self)

        def send_verbose(self, message: str, **_kw: Any) -> TurnReport:
            self.turns.append(ChatTurn(user=message, assistant="a live answer"))
            return TurnReport(answer="a live answer", model="fake/model", provenance=provenance)

        def set_model(self, _slug: str | None) -> bool:
            return True

        def reset(self) -> None:
            self.turns.clear()

    monkeypatch.setattr("chimera.interface.ChatSession", Fake)
    return made


def _saved_thread(session_id: str, turns: list[dict[str, str]]) -> None:
    """Write a thread the shipped ``SessionStore`` will hydrate — not a hand-built session."""
    home = Path(get_settings().home) / "sessions"
    home.mkdir(parents=True, exist_ok=True)
    (home / f"{session_id}.json").write_text(
        json.dumps({"id": session_id, "title": "t", "turns": turns}), encoding="utf-8"
    )


def test_resuming_a_thread_of_unmeasured_turns_says_so_under_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)
    # No `provenance` key at all: every turn written before the field existed. It reads as
    # `unknown`, which is what the store promises and what the fence is built from.
    _saved_thread("old", [{"user": "q1", "assistant": "a1"}, {"user": "q2", "assistant": "a2"}])
    result = runner.invoke(app, ["chat", "--no-memory", "-s", "old"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    assert shows(result.stdout, "2 restored turn(s)")
    assert shows(result.stdout, "never measured")


def test_the_marker_is_not_inside_the_model_s_own_words(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _install(monkeypatch)
    _saved_thread("old", [{"user": "q1", "assistant": "a1"}])
    result = runner.invoke(app, ["chat", "--no-memory", "-s", "old"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    # What the session recorded for this turn is the reply and nothing else — the line the person
    # read is the surface's, and it is not going back into the next prompt.
    assert made[0].turns[-1].assistant == "a live answer"
    assert "restored" not in made[0].turns[-1].assistant


def test_a_fresh_thread_prints_no_provenance_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)
    result = runner.invoke(app, ["chat", "--no-memory", "--new"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "restored turn(s)" not in squashed(result.stdout)


def test_the_window_read_is_the_window_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the turns that reach the prompt are counted.

    ``_replay`` renders ``turns[-max_history:]``; a count over the whole file would tell somebody
    with a two-hundred-turn thread that two hundred restored turns fed a prompt that saw six.
    """
    from chimera.cli.main import _replayed_provenance

    class Session:
        max_history = 2
        turns = [
            ChatTurn(user="a", assistant="a", provenance=UNKNOWN, restored=True),
            ChatTurn(user="b", assistant="b", provenance=UNKNOWN, restored=True),
            ChatTurn(user="c", assistant="c", provenance=UNKNOWN, restored=True),
        ]

    assert _replayed_provenance(Session()) == [UNKNOWN, UNKNOWN]


def test_a_live_turn_in_a_resumed_thread_is_not_counted_as_restored() -> None:
    """The flag is about how the turn reached the session, not about the session."""
    from chimera.cli.main import _replayed_provenance

    class Session:
        max_history = 6
        turns = [
            ChatTurn(user="a", assistant="a", provenance=UNKNOWN, restored=True),
            ChatTurn(user="b", assistant="b", provenance=CLEAN, restored=False),
        ]

    assert _replayed_provenance(Session()) == [UNKNOWN]
