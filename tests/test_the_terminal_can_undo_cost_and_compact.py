"""`/undo`, `/cost` and `/compact` in the terminal's conversations, and Ctrl-C in the full-screen app.

Study 30, S30-42. The Code screen could take back a turn's edits; `chimera chat`, `chimera assist`
and `chimera tui` could not, though their turns write files through the same tools. `/cost` existed
on no REPL, and `/compact` on none. Each is UX over a mechanism that already existed:
`WorkspaceGuard.restore_change` for the undo, the rows of `usage.jsonl` for the cost, and
`context_budget.compact` for the compaction.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from chimera.api.usage import UsageRecord, append_usage, session_spend
from chimera.core.agent import Agent, AgentConfig, AgentResult, ToolActivity
from chimera.interface import render
from chimera.interface.session import ChatSession, TurnReport
from chimera.providers.gateway import CompletionResult
from chimera.tools.registry import ToolRegistry

# --- /undo -----------------------------------------------------------------------------------------


class _Writer:
    """An agent whose turn writes the files its message names: ``"write a.txt=new"``."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.prompts: list[str] = []

    def run(self, task: str, **_: Any) -> AgentResult:
        self.prompts.append(task)
        # The last line only: the flattened prompt replays earlier turns above it.
        for word in task.splitlines()[-1].split():
            if "=" in word:
                name, text = word.split("=", 1)
                (self.workspace / name).write_text(text, encoding="utf-8")
        return AgentResult(answer="done", steps=1, stopped_reason="final")


def _session(workspace: Path) -> tuple[ChatSession, _Writer]:
    agent = _Writer(workspace)
    return ChatSession(agent, gate=None, workspace=workspace), agent


def test_undo_puts_back_what_the_last_turn_changed(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    session, _agent = _session(tmp_path)
    session.send("write a.txt=new b.txt=fresh")
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new"

    report = session.undo_last()

    assert report is not None and report.restored == 2
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "b.txt").exists()  # created by the turn, outside git: removed
    assert session.undo_last() is None  # once: the change is spent


def test_undo_keeps_a_file_edited_after_the_turn_and_says_so(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    (tmp_path / "c.txt").write_text("old", encoding="utf-8")
    session, _agent = _session(tmp_path)
    session.send("write a.txt=new c.txt=new")
    (tmp_path / "c.txt").write_text("the person's edit", encoding="utf-8")

    report = session.undo_last()

    assert report is not None and report.restored == 1 and report.kept == ["c.txt"]
    assert (tmp_path / "c.txt").read_text(encoding="utf-8") == "the person's edit"
    lines = render.undo_lines(report)
    assert "restored 1 file(s)" in lines[0] and "c.txt" in lines[1]


def test_undo_is_about_the_last_turn_only(tmp_path: Path) -> None:
    """A turn that changed nothing leaves nothing to undo — not the turn before it."""
    session, _agent = _session(tmp_path)
    session.send("write a.txt=new")
    session.send("just talk")
    assert session.undo_last() is None
    assert (tmp_path / "a.txt").exists()
    assert "nothing to undo" in render.undo_lines(None)[0]


def test_the_next_turn_hears_once_that_the_edits_were_taken_back(tmp_path: Path) -> None:
    """The turn stays in the record; without the note the model would build on files that are gone."""
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    session, agent = _session(tmp_path)
    session.send("write a.txt=new")
    session.undo_last()
    session.send("and now?")
    session.send("again")
    assert "undid the previous turn's changes to these files" in agent.prompts[1]
    assert "a.txt" in agent.prompts[1]
    assert "undid" not in agent.prompts[2]
    assert [turn.user for turn in session.turns] == ["write a.txt=new", "and now?", "again"]
    assert all("undid" not in turn.user for turn in session.turns)


def test_a_turn_that_raised_after_writing_can_still_be_undone(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")

    class _Crashes(_Writer):
        def run(self, task: str, **kwargs: Any) -> AgentResult:
            super().run(task, **kwargs)
            raise RuntimeError("provider went away")

    session = ChatSession(_Crashes(tmp_path), gate=None, workspace=tmp_path)
    with pytest.raises(RuntimeError):
        session.send("write a.txt=new")
    assert (report := session.undo_last()) is not None and report.restored == 1
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "old"


def test_without_a_workspace_nothing_is_measured(tmp_path: Path) -> None:
    """The default every other surface gets — the gateway, the HTTP route, the benches."""
    agent = _Writer(tmp_path)
    session = ChatSession(agent, gate=None)
    session.send("write a.txt=new")
    assert session.undo_last() is None
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new"


def test_a_solve_measured_like_a_turn_is_undone_too(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    session, _agent = _session(tmp_path)
    measuring = session.measure()
    (tmp_path / "a.txt").write_text("what the loop left", encoding="utf-8")
    session.end_measure(measuring)
    assert (report := session.undo_last()) is not None and report.restored == 1
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "old"


# --- /cost -----------------------------------------------------------------------------------------


def _row(session_id: str, usd: float | None) -> UsageRecord:
    return UsageRecord(ts="2026-10-06T10:00:00+00:00", session_id=session_id, usd=usd)


def test_cost_sums_this_conversations_receipts_only(tmp_path: Path) -> None:
    for row in (_row("mine", 0.01), _row("mine", 0.02), _row("other", 5.0)):
        append_usage(tmp_path / "usage.jsonl", row)
    assert session_spend(tmp_path, "mine") == (0.03, 2, 0)
    assert render.session_cost_line(0.03, 2, 0) == "[dim]this conversation: $0.0300 over 2 receipt(s)[/dim]"


def test_an_unpriced_turn_makes_the_total_a_floor_and_the_line_says_so(tmp_path: Path) -> None:
    append_usage(tmp_path / "usage.jsonl", _row("mine", 0.01))
    append_usage(tmp_path / "usage.jsonl", _row("mine", None))
    usd, rows, unpriced = session_spend(tmp_path, "mine")
    assert (usd, rows, unpriced) == (0.01, 2, 1)
    assert "at least $0.0100 (1 turn(s) unpriced)" in render.session_cost_line(usd, rows, unpriced)
    assert "no receipts" in render.session_cost_line(*session_spend(tmp_path, "nobody"))


# --- /compact --------------------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.sent.append([dict(m) if isinstance(m, dict) else m.as_dict() for m in messages])
        return CompletionResult(  # type: ignore[call-arg]
            content=f"answer {len(self.sent)}", model="m", prompt_tokens=10, completion_tokens=2
        )


def _real_agent(backend: _Recorder) -> Agent:
    config = AgentConfig(
        prefix_nonce="", turn_context=True, inject_skill_context=False, project_root=None
    )
    return Agent(backend, ToolRegistry(), config)  # type: ignore[arg-type]


def test_compact_folds_the_earlier_turns_for_the_model_and_keeps_the_record() -> None:
    backend = _Recorder()
    session = ChatSession(_real_agent(backend), gate=None, real_history=True)
    for message in ("alpha question", "beta question", "gamma question"):
        session.send(message)

    assert session.compact() == 2
    assert session.compact() == 0  # nothing new to fold
    session.send("delta question")

    request = json.dumps(backend.sent[-1])
    assert "[earlier conversation, compacted]" in request
    assert "alpha question" not in request and "beta question" not in request
    assert "gamma question" in request  # the last turn stays verbatim
    assert [turn.user for turn in session.turns] == [
        "alpha question", "beta question", "gamma question", "delta question",
    ]


def test_compact_in_the_flattened_form_drops_the_folded_turns_from_the_replay() -> None:
    class _Prompts:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def run(self, task: str) -> AgentResult:
            self.prompts.append(task)
            return AgentResult(answer="ok", steps=1, stopped_reason="final")

    agent = _Prompts()
    session = ChatSession(agent, gate=None)
    session.send("alpha question")
    session.send("beta question")
    assert session.compact() == 1
    session.send("gamma question")
    assert "alpha question" not in agent.prompts[-1]
    assert "beta question" in agent.prompts[-1]
    assert "[earlier conversation, compacted]" in agent.prompts[-1]
    assert "compacted 1 earlier turn(s)" in render.compact_line(1)
    assert "nothing to compact" in render.compact_line(0)


# --- Ctrl-C in the full-screen app ----------------------------------------------------------------


class _SlowSession:
    """A session whose turn runs until asked to stop, as the agent loop does at a step boundary."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.saw_stop = threading.Event()
        self.release = threading.Event()  # the step in flight when Ctrl-C came, finishing
        self.stopped_by_flag = False

    def send_verbose(
        self,
        message: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> TurnReport:
        self.started.set()
        assert should_stop is not None
        for _ in range(500):
            if should_stop():
                self.stopped_by_flag = True
                self.saw_stop.set()
                self.release.wait(5)
                return TurnReport(answer="(cancelled)", steps=1, stopped_reason="cancelled")
            threading.Event().wait(0.01)
        return TurnReport(answer="ran to the end", steps=1, stopped_reason="final")

    def set_model(self, slug: str | None) -> bool:
        return True

    def reset(self) -> None:
        pass


async def test_ctrl_c_stops_a_running_turn_and_quits_only_when_idle() -> None:
    from textual.widgets import Input

    from chimera.tui.app import ChimeraTUI

    session = _SlowSession()
    app = ChimeraTUI(session, model_label="stub")  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.query_one("#prompt", Input).value = "take your time"
        await pilot.press("enter")
        assert session.started.wait(5)
        await pilot.press("ctrl+c")
        assert session.saw_stop.wait(5)
        await pilot.press("ctrl+c")  # a second press while it winds down does not quit either
        await pilot.pause()
        assert app.is_running is True
        session.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert session.stopped_by_flag is True
        assert app.is_running is True
        assert app.query_one("#prompt", Input).disabled is False

        await pilot.press("ctrl+c")  # idle now: this one quits
        await pilot.pause()
    assert app.is_running is False


async def test_the_tui_answers_undo_cost_and_compact_without_calling_the_model(
    tmp_path: Path,
) -> None:
    from textual.widgets import Input, RichLog

    from chimera.tui.app import ChimeraTUI

    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    agent = _Writer(tmp_path)
    session = ChatSession(agent, gate=None, workspace=tmp_path)
    session.send("write a.txt=new")
    app = ChimeraTUI(session, model_label="stub", usage_home=tmp_path)
    append_usage(tmp_path / "usage.jsonl", _row(app._usage_id(), 0.5))
    async with app.run_test() as pilot:
        for command in ("/undo", "/cost", "/compact"):
            app.query_one("#prompt", Input).value = command
            await pilot.press("enter")
            await pilot.pause()
        log = "\n".join(str(line.text) for line in app.query_one("#log", RichLog).lines)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "old"
    assert "restored 1 file(s)" in log
    assert "$0.5000 over 1 receipt(s)" in log
    assert "nothing to compact" in log
    assert len(agent.prompts) == 1  # none of the three reached the model
