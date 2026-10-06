"""The chat bot and the terminal get what 0.64 said a person waiting gets.

0.64.0 turned the limits into warnings: a step window instead of a wall, a correction before the
loop breaker, compaction instead of a dead run, a spend warning, and a channel to say all of it. A
read of the code after the release found that this reached `chimera chat` and the desktop and not:
- the Discord bot (`serve --discord`, the production bot on the VPS), which kept the six-step wall,
  died on an overflow, and could not show a warning at all, because the gateway called `send`;
- the terminal on its default model, whose context budget was sized for an empty slug;
- `--max-usd`, `tui` and `agent`, which dropped the warnings;
- every surface but the Code screen, which never heard that a background job ended.

Each test below names the surface it is about and fails on the code before the fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.core.agent import Agent, AgentConfig, AgentResult, attended
from chimera.core.context_budget import ContextBudget
from chimera.interface import ChatSession
from chimera.server.gateway import InboundMessage, MessageGateway, with_warnings
from chimera.tools.registry import ToolRegistry

# --------------------------------------------------------------------------- attended()


def test_attended_turns_on_the_five_settings_and_nothing_else() -> None:
    base = AgentConfig(model="m", max_steps=6, instructions="be brief", turn_context=True)
    on = attended(base)

    assert (on.warn_usd, on.loop_correction, on.auto_continue) == (1.0, True, True)
    assert on.context_budget and on.unmeasured_context_tokens
    # What the surface chose is kept.
    assert (on.model, on.max_steps, on.instructions, on.turn_context) == ("m", 6, "be brief", True)
    # And the library default every bench was measured with does not move.
    assert base.auto_continue is False and base.context_budget is None


# --------------------------------------------------------------------------- the context budget


class _NoBackend:
    def complete(self, *a: Any, **k: Any) -> Any:  # pragma: no cover - never called
        raise AssertionError("not called")


def _budget_agent(model: str | None) -> Agent:
    return Agent(_NoBackend(), ToolRegistry(), attended(AgentConfig(model=model)))


def test_a_run_on_the_default_model_is_budgeted_for_that_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before: `config.model` None sized the budget for "" — the 64k cap on a 255k model."""
    import chimera.core.agent as agent_mod

    measured = "openrouter/openai/gpt-6-luna"
    monkeypatch.setattr(agent_mod, "_default_model", lambda: measured)
    agent = _budget_agent(None)

    expected = ContextBudget.for_model(
        measured,
        fraction=agent.config.context_budget,
        unmeasured_cap=agent.config.unmeasured_context_tokens,
    )
    empty = ContextBudget.for_model(
        "", fraction=agent.config.context_budget, unmeasured_cap=agent.config.unmeasured_context_tokens
    )
    assert expected.budget != empty.budget, "the fixture model must be one the catalogue measured"
    assert agent._budget is not None and agent._budget.budget == expected.budget


def test_switching_the_model_mid_conversation_switches_the_budget() -> None:
    """`/model` in `chimera chat` swaps `config.model`; the budget used to stay the old model's."""
    agent = _budget_agent("openrouter/openai/gpt-6-luna")
    first = agent._budget
    agent.config.model = "some/model-nobody-measured"
    second = agent._budget

    assert first is not None and second is not None
    assert second.budget != first.budget
    assert second.budget == ContextBudget.for_model(
        "some/model-nobody-measured",
        fraction=agent.config.context_budget,
        unmeasured_cap=agent.config.unmeasured_context_tokens,
    ).budget


def test_a_library_run_with_no_model_keeps_the_unknown_model_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without `attended`, "no model" is whatever the caller's own backend picks: not guessed high."""
    import chimera.core.agent as agent_mod

    monkeypatch.setattr(agent_mod, "_default_model", lambda: "openrouter/openai/gpt-6-luna")
    agent = Agent(_NoBackend(), ToolRegistry(), AgentConfig(model=None, context_budget=0.6))

    assert agent._budget is not None
    assert agent._budget.budget == ContextBudget.for_model("", fraction=0.6, unmeasured_cap=None).budget


def test_no_budget_without_the_opt_in() -> None:
    assert Agent(_NoBackend(), ToolRegistry(), AgentConfig(model=None))._budget is None


# --------------------------------------------------------------------------- the gateway


@dataclass
class _Report:
    answer: str
    stopped_reason: str = "final"
    memory_saved: str | None = None


@dataclass
class _Session:
    """Speaks while it runs: a warning, then (optionally) a cut-short reason on the report."""

    notices: list[tuple[str, str]] = field(default_factory=list)
    stopped_reason: str = "final"
    plain_calls: int = 0
    max_turns: int | None = None

    def send(self, message: str) -> str:
        self.plain_calls += 1
        return "plain answer"

    def send_verbose(self, message: str, *, on_notice: Any = None) -> _Report:
        for code, text in self.notices:
            on_notice(code, text, {})
        return _Report("the answer", self.stopped_reason)


def _say(gateway: MessageGateway) -> str:
    return gateway.on_message(InboundMessage(text="hi", chat_id="c", platform="discord"))


def test_a_chat_platform_reply_carries_the_warnings_under_the_answer() -> None:
    session = _Session(notices=[("compacted", "x"), ("spend_warn", "this turn has spent $1.02")])
    reply = _say(MessageGateway(lambda: session, warnings_in_reply=True))  # type: ignore[arg-type, return-value]

    assert reply.startswith("the answer")
    assert "⚠ the conversation was compacted to keep going" in reply
    assert "⚠ this turn has spent $1.02" in reply


def test_a_reply_cut_short_says_so() -> None:
    session = _Session(stopped_reason="max_steps")
    reply = _say(MessageGateway(lambda: session, warnings_in_reply=True))  # type: ignore[arg-type, return-value]

    assert "⚠ the tool loop hit --max-steps" in reply


def test_the_same_warning_twice_is_said_once() -> None:
    session = _Session(notices=[("tool_loop_warn", "a"), ("tool_loop_warn", "b")])
    reply = _say(MessageGateway(lambda: session, warnings_in_reply=True))  # type: ignore[arg-type, return-value]

    assert reply.count("⚠") == 1


def test_the_http_reply_stays_the_answer_alone() -> None:
    """Off by default: `/chat`'s `reply` field is read by a program as the answer."""
    session = _Session(notices=[("compacted", "x")], stopped_reason="max_steps")
    reply = _say(MessageGateway(lambda: session))  # type: ignore[arg-type, return-value]

    assert reply == "plain answer" and session.plain_calls == 1


def test_a_session_that_cannot_take_the_callback_is_sent_plainly() -> None:
    class _Old(_Session):
        def send_verbose(self, message: str) -> _Report:  # type: ignore[override]
            raise AssertionError("must not be called without on_notice")

    session = _Old()
    assert _say(MessageGateway(lambda: session, warnings_in_reply=True)) == "plain answer"  # type: ignore[arg-type, return-value]


def test_with_warnings_leaves_a_quiet_answer_alone() -> None:
    assert with_warnings("hello", []) == "hello"
    assert with_warnings("", ["x"]) == "⚠ x"


# --------------------------------------------------------------------------- the platform bot, built by the real command


class _FakeAdapter:
    platform = "discord"

    def send(self, chat_id: str, text: str) -> str:  # pragma: no cover - never reached
        return "sent"

    def start(self, on_message: Any) -> None:  # pragma: no cover - the gateway stops first
        raise AssertionError("never started")

    def stop(self) -> None:
        return None


def _platform_bot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ChatSession, dict[str, Any]]:
    """The session `serve --discord` builds, and the keywords its gateway was built with."""
    import chimera.cli.main as cli
    import chimera.server as server_pkg
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_OPENROUTER_API_KEY", "test-key")
    get_settings.cache_clear()
    captured: dict[str, Any] = {}

    def fake_gateway(factory: Any, *args: Any, **kwargs: Any) -> Any:
        captured["session"] = factory()
        captured["kwargs"] = kwargs
        raise SystemExit(0)

    monkeypatch.setattr(server_pkg, "MessageGateway", fake_gateway)
    monkeypatch.setattr(cli, "_messaging_adapter", lambda _s, _p: _FakeAdapter())
    CliRunner().invoke(cli.app, ["serve", "--discord", "--workspace", str(tmp_path), "--no-memory"])
    get_settings.cache_clear()
    session = captured.get("session")
    assert isinstance(session, ChatSession), "the command never built a chat session"
    return session, captured["kwargs"]


def test_the_discord_bot_runs_as_a_person_is_waiting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session, kwargs = _platform_bot(tmp_path, monkeypatch)
    config: AgentConfig = session.agent.config  # type: ignore[attr-defined]

    assert config.auto_continue and config.loop_correction and config.context_budget
    assert config.warn_usd == 1.0
    assert kwargs.get("warnings_in_reply") is True
    assert session.turn_note is not None, "the bot never hears that a job it started has ended"


# --------------------------------------------------------------------------- the terminal's own gaps


def test_a_conversation_with_a_ceiling_still_hears_its_warnings() -> None:
    """`--max-usd` wraps the agent in `BudgetedTurns`, whose `run` did not declare `on_notice`."""
    from chimera.cli.spend import BudgetedTurns, session_budget
    from chimera.core.code_session import _accepts

    got: dict[str, Any] = {}

    class _Inner:
        config = AgentConfig()

        def run(self, task: str, **kw: Any) -> str:
            got.update(kw)
            return "ok"

    budget = session_budget(5.0)
    assert budget is not None
    wrapped = BudgetedTurns(_Inner(), budget)
    hear = lambda c, t, d: None  # noqa: E731

    assert _accepts(wrapped.run, "on_notice"), "ChatSession would skip the callback"
    wrapped.run("go", on_notice=hear)
    assert got["on_notice"] is hear and got["spend"] is budget


def test_the_full_screen_app_posts_each_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.tui.app import ChimeraTUI, NoticeSent

    class _Speaks:
        max_turns = None

        def send_verbose(self, text: str, *, on_token: Any = None, on_tool: Any = None, on_notice: Any = None) -> Any:
            on_notice("compacted", "x", {})
            return _Report("done")

    app = ChimeraTUI(_Speaks(), stream=False)  # type: ignore[arg-type]
    posted: list[Any] = []
    monkeypatch.setattr(app, "post_message", lambda message: posted.append(message) or True)
    app._respond("go")

    notices = [m for m in posted if isinstance(m, NoticeSent)]
    assert [(m.code, m.text) for m in notices] == [("compacted", "x")]


# --------------------------------------------------------------------------- finished jobs reach a chat


class _Recorder:
    """An agent that records the prompt it was given."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, task: str) -> AgentResult:
        self.prompts.append(task)
        return AgentResult(answer="ok", steps=1, stopped_reason="final", transcript=[], tool_names=[], model="m")


def test_the_turn_note_reaches_the_model_on_send() -> None:
    agent = _Recorder()
    session = ChatSession(agent, turn_note=lambda: "job j1 finished (exit 0): sleep 900")
    session.send("what happened?")

    assert "job j1 finished" in agent.prompts[-1]


def test_a_failing_turn_note_never_fails_the_turn() -> None:
    def broken() -> str:
        raise RuntimeError("the job store is gone")

    agent = _Recorder()
    assert ChatSession(agent, turn_note=broken).send("hi") == "ok"


def test_finished_note_names_each_job_and_how_to_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core.jobs as jobs_mod

    @dataclass
    class _Job:
        id: str
        state: str
        exit_code: int | None
        command: str

    asked: list[Path] = []

    class _Registry:
        def finished_unreported(self, within: Path | None = None) -> list[_Job]:
            asked.append(Path(str(within)))
            return [_Job("j1", "finished", 0, "sleep 900"), _Job("j2", "timed_out", None, "make")]

    monkeypatch.setattr(jobs_mod, "jobs_for", lambda home: _Registry())
    note = jobs_mod.finished_note(tmp_path, tmp_path / "ws")

    assert "- job j1 finished (exit 0): sleep 900" in note
    assert "- job j2 timed_out: make" in note
    assert "job_status" in note
    assert asked == [tmp_path / "ws"], "the news is scoped to the folder the chat works in"


def test_finished_note_is_empty_when_nothing_ended(tmp_path: Path) -> None:
    from chimera.core.jobs import finished_note

    assert finished_note(tmp_path, tmp_path) == ""
