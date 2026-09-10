"""The app's chat tells its taint ledger the user's message, so ``CHIMERA_TAINT_AUTHORITY`` acts.

``guard_chat_registry`` builds a ``TaintLedger`` with the deployment's authority mode and nothing
ever told it an instruction. ``requester_of`` answers ``unknown`` for such a ledger, the narrowing
treats ``unknown`` exactly as it treats ``agent``, and the mode therefore had nothing to be a mode
about: measured on the same instrument as the terminal's, ``CHIMERA_TAINT_AUTHORITY=authority``
moved **0 rows** on this surface and **9** on ``chat``
(``bench/right_hand_governance/RESULTS.md``). Its own docstring said why — *"the mode travels; the
instruction cannot"* — which was true of the function and false of the surface.

**Why this file drives the real command.** #400 shipped four checks that all asked whether a command
*called* its builder, and a sabotage that called the builder and then overwrote the result with a
bare registry passed **0 of 109** of them. So the question here is never "was a hook wired" but
"what did the object the app actually builds do when a turn arrived".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.interface.session import ChatSession


class _Agent:
    """The smallest thing `ChatSession` will run a turn through."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, prompt: str, **kwargs: Any) -> Any:
        from chimera.core.agent import AgentResult

        self.prompts.append(prompt)
        return AgentResult(answer="ok", steps=1, stopped_reason="final", tool_names=[])


# --------------------------------------------------------------------------- the hook itself


def test_a_session_with_no_hook_behaves_exactly_as_before() -> None:
    """The default has to be byte-identical: this class also serves the messaging gateway and
    `/v1/chat/completions`, and neither asked for a hook."""
    session = ChatSession(_Agent())
    assert session.on_turn_start is None
    assert session.send("hello") == "ok"


@pytest.mark.parametrize("method", ["send", "send_verbose"])
def test_both_entry_points_announce_the_turn(method: str) -> None:
    """`send` and `send_verbose` are separate implementations, and this class has been bitten by
    that before: `remember_from_chat` once meant two different things depending on which one you
    called. A hook on only one of them would be that same bug with a different field name."""
    seen: list[str] = []
    session = ChatSession(_Agent(), on_turn_start=seen.append)
    getattr(session, method)("summarise https://example.test/page")
    assert seen == ["summarise https://example.test/page"]


def test_the_turn_is_announced_before_the_agent_runs() -> None:
    """Order is the whole point: a ledger told after the tools have run has been told nothing."""
    order: list[str] = []

    class _Recording(_Agent):
        def run(self, prompt: str, **kwargs: Any) -> Any:
            order.append("agent")
            return super().run(prompt, **kwargs)

    ChatSession(_Recording(), on_turn_start=lambda _m: order.append("hook")).send("x")
    assert order == ["hook", "agent"]


# --------------------------------------------------------------------------- the wiring


def _app_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChatSession:
    """The session `chimera desktop-app` builds, from the command itself.

    The factory is a closure inside the command body, so it is reached the way the app reaches it:
    by running the command far enough to build one and intercepting `build_api_app`, which is the
    first thing that receives it.
    """
    from typer.testing import CliRunner

    import chimera.cli.main as cli

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_GUARD_CHAT", "1")  # off by default; this file is about it being on
    from chimera.config import get_settings

    get_settings.cache_clear()

    captured: dict[str, Any] = {}

    def fake_build_api_app(factory: Any, **kwargs: Any) -> Any:
        captured["session"] = factory()
        raise SystemExit(0)  # nothing past this point is this test's business

    # Patched where it is DEFINED: the command imports it inside its own body
    # (`from chimera.api import build_api_app`), so an attribute on the cli module is not what the
    # call resolves.
    import chimera.api as api_pkg

    monkeypatch.setattr(api_pkg, "build_api_app", fake_build_api_app)
    # `app`, which is what `@app.command(name="app")` registers — the name a person types.
    CliRunner().invoke(cli.app, ["app", "--workspace", str(tmp_path), "--no-open"])
    get_settings.cache_clear()
    session = captured.get("session")
    assert isinstance(session, ChatSession), "the command never built a chat session"
    return session


def test_the_app_chat_hands_its_session_a_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring, asked of the object the command built rather than of the source that built it."""
    session = _app_session(tmp_path, monkeypatch)
    assert session.on_turn_start is not None, (
        "the app's chat session carries no turn hook, so its ledger is never told the "
        "instruction and CHIMERA_TAINT_AUTHORITY is inert again"
    )


def test_the_hook_reaches_the_ledger_the_registry_is_using(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half a hook-exists check cannot see: that it points at THIS session's ledger.

    Asked through the ledger's own answer rather than through a captured object — `requester_of`
    goes from `unknown` (nobody told it) to `user` (the person named that page), which is exactly
    the transition `authority` mode reads.
    """
    session = _app_session(tmp_path, monkeypatch)
    ledger = _ledger_behind(session)

    assert ledger.requester_of("https://example.test/page") == "unknown"
    assert session.on_turn_start is not None
    session.on_turn_start("please summarise https://example.test/page")
    assert ledger.requester_of("https://example.test/page") == "user"
    assert ledger.requester_of("https://somewhere.else/page") == "agent"


def _ledger_behind(session: ChatSession) -> Any:
    """The taint ledger the session's own tools are wrapped in.

    Reached through the registry rather than through a field the test asked to be given, so a wire
    that points at some *other* ledger fails here instead of passing.
    """
    registry = session.agent.tools  # type: ignore[attr-defined]  # `Agent.tools` IS the registry
    for tool in registry.tools():
        ledger = getattr(tool, "ledger", None)
        if ledger is not None:
            return ledger
    raise AssertionError("no ledgered tool in the app chat's registry — the guard did not apply")
