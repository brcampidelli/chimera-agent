"""``chimera tui`` gets the rest of what ``chimera chat`` has.

#405 governed this surface and listed, in its own PR body, four things it deliberately left behind.
None of them is a missing mechanism: each is something the terminal already builds, already tests,
and hands to every surface except this one — which is the shape that survives a review, because
nobody has to disable a guard that was never wired in.

Every check below drives the **real command** through ``CliRunner`` and then asks the object what it
was handed. That distinction is not stylistic. #400 shipped four structural checks — "does the
command call the builder" — and a sabotage that called the builder and threw the result away passed
0 of 109 of them. So `project` is read off the session the command constructed, `instructions` off
the agent's own config, and the write region is proven by making a real writer refuse.

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
