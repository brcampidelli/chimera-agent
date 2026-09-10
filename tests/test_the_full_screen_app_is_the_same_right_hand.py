"""``chimera tui`` gets the rest of what ``chimera chat`` has.

#405 governed this surface and listed, in its own PR body, four things it deliberately left behind.
None of them is a missing mechanism: each is something the terminal already builds, already tests,
and hands to every surface except this one — which is the shape that survives a review, because
nobody has to disable a guard that was never wired in.

Every check below drives the **real command** through ``CliRunner`` and then asks the object what it
was handed. That distinction is not stylistic. #400 shipped structural checks — "does the command
call the builder" — and a sabotage that called the builder and threw the result away went straight
through 109 tests. So `project` is read off the session the command constructed, `instructions` off
the agent's own config, and the write region is proven by the file that does not appear on disk.

The one place a structural check is still the right instrument is the import-time fallback, and it
already has one (``test_the_tui_fallback_passes_values_not_option_objects``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app as cli
from chimera.config import get_settings
from chimera.core.agent import AgentResult
from chimera.memory.models import project_key


@pytest.fixture
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr("chimera.sandbox._warned", False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _drive(monkeypatch: pytest.MonkeyPatch, *args: str) -> dict[str, Any]:
    """Run the real ``chimera tui`` up to the frame it would draw, and keep what it built.

    Only ``ChimeraTUI`` is faked. The settings, the memory manager, the registry, the agent and the
    ``ChatSession`` are all the shipped objects, which is what makes the assertions below statements
    about the command rather than about this file.
    """
    seen: dict[str, Any] = {}

    class FakeTUI:
        def __init__(self, session: Any, **kwargs: Any) -> None:
            seen["session"] = session
            seen.update(kwargs)

        def run(self) -> None:
            seen["ran"] = True

    monkeypatch.setattr("chimera.tui.app.ChimeraTUI", FakeTUI)
    result = CliRunner().invoke(cli, ["tui", *args])
    assert result.exit_code == 0, result.output
    assert seen.get("ran"), "the command never reached the app"
    seen["output"] = result.output
    return seen


class _Recorder:
    """Stands in for the agent so a turn can run without a provider. Keeps the prompt it was sent."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, task: str, *, on_token: Any = None, on_tool: Any = None) -> AgentResult:
        self.prompts.append(task)
        return AgentResult(answer="ok", steps=1, stopped_reason="final")


# --- 1. recall is scoped to the folder the app was opened on -------------------------------------


def _two_projects(a: Path, b: Path) -> None:
    """Seed the real memory store this home will read back, one fact per folder plus a global one."""
    from chimera.evolution.wiring import build_memory_manager

    memory = build_memory_manager(get_settings())
    memory.remember("the retry cap here is six", project=project_key(a))
    memory.remember("the retry cap here is nine", project=project_key(b))
    memory.remember("Alex prefers absolute imports", project=None)


def test_the_app_recalls_the_folder_it_was_opened_on(
    _isolated: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``chat`` narrowed recall to ``--workspace`` in #401 and this surface did not, so a TUI opened
    on one codebase was handed every other project's facts as context. The symptom is not an error;
    it is a prompt with a second project's answer in it, which is exactly the noise the field was
    added to stop."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _two_projects(a, b)

    session = _drive(monkeypatch, "--workspace", str(a))["session"]
    assert session.project == project_key(a), "the app was opened on a folder and recalled globally"

    session.agent = _Recorder()
    session.send("what is the retry cap?")

    assert "six" in session.agent.prompts[0]
    assert "nine" not in session.agent.prompts[0], "another project's fact reached the prompt"


def test_the_default_workspace_is_filed_under_the_name_the_writer_used(
    _isolated: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The instrument above cannot show the defect underneath #401, so this one exists.

    Both tests were run against ``project=str(workspace)`` -- the spelling ``chimera solve`` used to
    write -- and both PASSED, because they hand the command an already-absolute path, where the two
    spellings are the same string. A guard that cannot exhibit the failure it is named after is not
    evidence about the failure.

    ``--workspace .`` is the default and the case that breaks: ``str(workspace)`` is the literal
    ``"."`` while the writer files an absolute path, so a scoped read matches nothing a scoped write
    produced -- with no error, just memory that is never recalled again.
    """
    folder = tmp_path / "repo"
    folder.mkdir()
    monkeypatch.chdir(folder)

    session = _drive(monkeypatch, "--workspace", ".")["session"]

    assert session.project == project_key(folder)
    assert Path(str(session.project)).is_absolute(), "the app filed this folder under '.'"


def test_scoping_the_app_did_not_hide_the_facts_that_belong_everywhere(
    _isolated: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The half that could have gone wrong quietly. A fact with no project is every fact written
    before the field existed, so narrowing that dropped them would read as memory loss on upgrade —
    and would look, from the outside, exactly like the fix working."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _two_projects(a, b)

    session = _drive(monkeypatch, "--workspace", str(a))["session"]
    session.agent = _Recorder()
    session.send("any rule about imports?")

    assert "absolute imports" in session.agent.prompts[0]


# --- 3. the app answers as the agent the owner configured ----------------------------------------


def test_the_owners_identity_reaches_the_full_screen_app(
    _isolated: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`agent.json` is the owner's own voice. `chat`, `assist` and the desktop app all apply it and
    this surface did not, so one configuration produced two agents that answered in different
    languages depending on which window you opened -- with nothing failing and nothing saying so.

    Read off the config the command handed the agent. That `config.instructions` reaches the system
    prompt, last and appended rather than substituted, is pinned by `tests/test_instructions.py`.
    """
    from chimera.core.instructions import AgentIdentity, save

    save(get_settings().home, AgentIdentity(language="Português (Brasil)", instructions="Be terse."))

    agent = _drive(monkeypatch, "--no-memory", "--workspace", str(tmp_path))["session"].agent

    assert "Be terse." in agent.config.instructions
    assert "Português (Brasil)" in agent.config.instructions


# --- 4. the declared write region reaches this surface's writers ---------------------------------


def _write(hand: Any, path: str) -> str:
    return str(hand.registry.get("write_file").run(path=path, content="x"))


def test_a_write_outside_the_declared_region_is_refused_here_too(
    _isolated: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The injection-to-arbitrary-write case, on the surface that had no fence for it.

    Driven through the real writer rather than through the flag: a command can parse
    ``--write-region``, hand it to the builder, and be handed back a registry it then replaces --
    which is the sabotage 109 tests missed in #400. So the tool the agent would actually call is
    made to refuse.
    """
    (tmp_path / "src").mkdir()
    hand = _drive(
        monkeypatch, "--no-memory", "--workspace", str(tmp_path), "--write-region", "src/**"
    )["hand"]

    inside = _write(hand, "src/ok.py")
    outside = _write(hand, "config/secrets.py")

    assert (tmp_path / "src" / "ok.py").exists(), f"the region refused its own file: {inside!r}"
    # The file, not the sentence. `refuse_write` returns a plain ``error:`` string rather than a
    # `refusal()`-marked one, so a check on the wording alone would pass against a gate that
    # explained itself and wrote the file anyway.
    assert not (tmp_path / "config" / "secrets.py").exists(), "the write happened"
    assert "write-region" in outside, f"refused for some other reason: {outside!r}"


def test_declaring_no_region_still_writes_anywhere_in_the_workspace(
    _isolated: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control, and the half a one-sided test would miss. The region is opt-in: a writer that
    refused ``config/secrets.py`` with no flag would pass the test above while proving nothing about
    the flag, and would have broken every existing run."""
    hand = _drive(monkeypatch, "--no-memory", "--workspace", str(tmp_path))["hand"]

    wrote = _write(hand, "config/secrets.py")

    assert (tmp_path / "config" / "secrets.py").exists(), wrote


def test_the_fallback_carries_the_region_rather_than_dropping_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without Textual installed, ``tui`` runs ``chat`` instead -- and that branch passed a literal
    ``None`` here for as long as this surface had no flag. Leaving it would turn a fence the person
    typed into a fence silently removed, announced by nothing: the fallback says "falling back to
    chimera chat" and not a word about the narrowing it just dropped.

    ``test_the_tui_fallback_passes_values_not_option_objects`` proves every parameter is *passed*;
    this proves this one is passed the value it was given.
    """
    import sys

    seen: dict[str, Any] = {}
    monkeypatch.setattr("chimera.cli.main.chat", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setitem(sys.modules, "chimera.tui.app", None)  # force the ImportError branch

    result = CliRunner().invoke(cli, ["tui", "--write-region", "src/**"])

    assert result.exit_code == 0, result.output
    assert seen.get("write_region") == "src/**", "the fallback dropped the declared region"


# --- 2. the conversation outlives the window -----------------------------------------------------


def _store_and_manager(tmp_path: Path) -> tuple[Any, Any]:
    """A real store and manager over ``tmp_path``, with a real ``ChatSession`` behind a recorder."""
    from chimera.api.sessions import SessionManager, SessionStore
    from chimera.interface import ChatSession

    store = SessionStore(tmp_path / "sessions")
    return store, SessionManager(lambda: ChatSession(_Recorder(), gate=None), store)


def _saved(tmp_path: Path, session_id: str) -> dict[str, Any]:
    import json

    return dict(
        json.loads((tmp_path / "sessions" / f"{session_id}.json").read_text(encoding="utf-8"))
    )


async def _turn(app: Any, pilot: Any, text: str) -> None:
    """Type a message, submit it, and wait for the thread worker to finish the turn."""
    from textual.widgets import Input

    app.query_one("#prompt", Input).value = text
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_a_turn_is_on_disk_before_the_window_closes(tmp_path: Path) -> None:
    """The whole defect: this app built a session in memory and dropped it on exit, so a
    conversation died with the window while ``chat`` -- the same conversation, one surface over --
    had been saving since #401.

    Saved after the turn rather than at exit, because Ctrl-C and a closed terminal are how this app
    usually ends and neither runs a shutdown hook.
    """
    from chimera.tui.app import ChimeraTUI

    store, manager = _store_and_manager(tmp_path)
    active = manager.new()
    app = ChimeraTUI(manager.get(active), sessions=manager, session_id=active, stream=False)

    async with app.run_test() as pilot:
        await _turn(app, pilot, "what is the retry cap?")

    assert _saved(tmp_path, active)["turns"][0]["user"] == "what is the retry cap?"
    assert [m.id for m in store.list()] == [active], "not the thread `chimera sessions` would list"


async def test_the_next_run_picks_the_thread_up(tmp_path: Path) -> None:
    """Saving is only half of it. A store nothing resumes from is a log file."""
    from chimera.cli.main import _resume_or_new
    from chimera.tui.app import ChimeraTUI

    _, manager = _store_and_manager(tmp_path)
    active = manager.new()
    app = ChimeraTUI(manager.get(active), sessions=manager, session_id=active, stream=False)
    async with app.run_test() as pilot:
        await _turn(app, pilot, "remember the cap is six")

    # A second run of the command, over the same store: no `--session`, no `--new`.
    _, second = _store_and_manager(tmp_path)
    picked, resumed = _resume_or_new(second, None, False)

    assert (picked, resumed) == (active, True)
    assert second.get(picked).turns[0].user == "remember the cap is six"
    assert second.get(picked).turns[0].restored is True, "a replayed turn must say it is replayed"


async def test_starting_a_new_thread_does_not_destroy_the_open_one(tmp_path: Path) -> None:
    """The trap this feature sets for itself, and the reason ``chat`` changed what ``/reset`` means.

    ``action_reset`` used to call ``session.reset()``, which cost nothing while the transcript lived
    only in memory. With a file behind it, that clear followed by the next save rewrites the thread
    empty -- so the command labelled "clear context" becomes the one that destroys the conversation,
    and it destroys it silently.
    """
    from chimera.tui.app import ChimeraTUI

    _, manager = _store_and_manager(tmp_path)
    first = manager.new()
    app = ChimeraTUI(manager.get(first), sessions=manager, session_id=first, stream=False)

    async with app.run_test() as pilot:
        await _turn(app, pilot, "the first thing I said")

        await pilot.press("ctrl+r")
        await pilot.pause()
        second = app.session_id

        await _turn(app, pilot, "the second thing I said")

    assert second != first, "^R stayed on the same thread, so the next save overwrote it"
    assert _saved(tmp_path, first)["turns"][0]["user"] == "the first thing I said"
    assert _saved(tmp_path, second)["turns"][0]["user"] == "the second thing I said"
    assert len(_saved(tmp_path, first)["turns"]) == 1, "the old thread kept growing"


async def test_an_app_with_no_store_still_just_clears_its_context(tmp_path: Path) -> None:
    """The control. ``ChimeraTUI`` is constructed without a store by every dispatch test and by the
    bench arms, and for those ^R must go on meaning what it meant -- there is no file to protect."""
    from chimera.tui.app import ChimeraTUI

    class Cleared:
        reset_called = False

        def reset(self) -> None:
            self.reset_called = True

    session = Cleared()
    app = ChimeraTUI(session, stream=False)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()

    assert session.reset_called is True


def test_the_command_opens_the_thread_it_was_asked_for(
    _isolated: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Through Typer: ``-s`` names the thread, and the store is the one ``chat`` writes."""
    seen = _drive(monkeypatch, "--no-memory", "--workspace", str(tmp_path), "-s", "standup")

    assert seen["session_id"] == "standup"
    assert seen["sessions"] is not None
    # Reaching for the private store on purpose: the claim being pinned is *which directory*, and
    # a second transcript store beside `chat`'s is exactly the mistake this wiring exists to avoid.
    assert seen["sessions"]._store.root == get_settings().home / "sessions"


def test_an_escaping_session_id_is_refused_before_the_screen_opens(
    _isolated: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``chat`` learned this the expensive way: the store rejected the id on the SAVE, after a turn
    had been paid for. Here it would arrive with Textual holding the terminal."""
    built: list[Any] = []

    class FakeTUI:
        def __init__(self, *a: Any, **kw: Any) -> None:
            built.append(kw)

        def run(self) -> None: ...

    monkeypatch.setattr("chimera.tui.app.ChimeraTUI", FakeTUI)
    result = CliRunner().invoke(cli, ["tui", "--no-memory", "-s", "../escape"])

    assert result.exit_code == 1
    assert "invalid session id" in result.output
    assert built == [], "the screen opened on an id that can never be saved"


def test_the_fallback_carries_the_thread_rather_than_minting_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``chimera tui -s standup`` and ``chimera chat -s standup`` are one conversation now, so a
    fallback that hardcoded ``session_id=None`` would answer a different question than the one
    asked -- and would say only "falling back to chimera chat"."""
    import sys

    seen: dict[str, Any] = {}
    monkeypatch.setattr("chimera.cli.main.chat", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setitem(sys.modules, "chimera.tui.app", None)

    result = CliRunner().invoke(cli, ["tui", "-s", "standup", "--new"])

    assert result.exit_code == 0, result.output
    assert (seen.get("session_id"), seen.get("new")) == ("standup", True)


def test_the_dispatch_seam_agrees_with_the_key_binding_about_what_reset_means(
    tmp_path: Path,
) -> None:
    """``reply_to`` is a second place the meaning of ``/reset`` is written down, and it had already
    drifted: it cleared the conversation while ``action_reset`` started a new thread. Whichever of
    the two the next reader trusts, one of them would be wrong about the shipped app."""
    from chimera.tui.app import ChimeraTUI

    _, manager = _store_and_manager(tmp_path)
    first = manager.new()
    app = ChimeraTUI(manager.get(first), sessions=manager, session_id=first)
    app.session.send("something worth keeping")
    manager.persist(first)

    assert app.reply_to("/reset") is None

    assert app.session_id != first
    assert app.session.turns == []
    assert _saved(tmp_path, first)["turns"][0]["user"] == "something worth keeping"
