"""The real ``chat`` / ``assist`` / ``tui`` loops, driven through ``CliRunner``.

Before this file the ``chat`` loop had **zero** invocations in the suite — not one
``runner.invoke(app, ["chat", ...])`` anywhere — which is how eight defects lived in it, including
a reply that crashed the REPL after the turn was paid for and a refused command whose invented
output was the only thing on screen.

Only ``ChatSession`` is faked, so no turn reaches a provider; everything else (Typer parsing, the
loop, the command table, the session store, the usage log) is the shipped code.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.cli.main import app
from chimera.config import get_settings
from chimera.interface.session import ChatTurn, DeclinedTool, TurnReport

runner = CliRunner()

DECLINE = "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
FABRICATION = "The command printed exactly: marker-42"


def squashed(text: str) -> str:
    """Rich hard-wraps at the console width; join the pieces so a token survives the wrap."""
    return "".join(text.split())


def shows(output: str, phrase: str) -> bool:
    """Whether ``phrase`` is on screen, ignoring wherever Rich decided to break the line."""
    return squashed(phrase) in squashed(output)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    for var in ("CHIMERA_CHAT_MEMORY", "CHIMERA_CASCADE", "CHIMERA_COST_MODE"):
        monkeypatch.delenv(var, raising=False)
    # The "no OS sandbox" notice is claimed once per PROCESS and the whole suite is one process.
    monkeypatch.setattr("chimera.sandbox._warned", False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def install_session(monkeypatch: pytest.MonkeyPatch, **script: Any) -> list[Any]:
    """Swap ``ChatSession`` for a scripted fake. Returns the list of sessions the command built.

    ``raises`` may be one exception (every turn) or a list consumed one per turn, which is how a
    Ctrl-C on the first turn can be told apart from a REPL that never stopped.
    """
    queue = list(script["raises"]) if isinstance(script.get("raises"), list) else None
    always = None if queue is not None else script.get("raises")
    made: list[Any] = []

    class Fake:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            self.turns: list[ChatTurn] = []
            self.profile = ""
            self.kwargs = kwargs
            self.remember_from_chat = bool(kwargs.get("remember_from_chat"))
            self.sent: list[str] = []
            made.append(self)

        def send_verbose(self, message: str, **_kw: Any) -> TurnReport:
            self.sent.append(message)
            blow_up = queue.pop(0) if queue else always
            if blow_up is not None:
                raise blow_up
            reply = str(script.get("reply", "ok"))
            self.turns.append(ChatTurn(user=message, assistant=reply))
            return TurnReport(
                answer=reply,
                declined=list(script.get("declined", ())),
                usd=script.get("usd"),
                prompt_tokens=int(script.get("prompt_tokens", 0)),
                completion_tokens=int(script.get("completion_tokens", 0)),
                memory_saved=script.get("memory_saved"),
                model=str(script.get("model", "fake/model")),
            )

        def send(self, message: str) -> str:
            return self.send_verbose(message).answer

        def reset(self) -> None:
            self.turns.clear()

        def set_model(self, _slug: str | None) -> bool:
            return True

    monkeypatch.setattr("chimera.interface.ChatSession", Fake)
    return made


def usage_rows() -> list[dict[str, Any]]:
    path = get_settings().home / "usage.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# -- 1. a reply cannot crash the REPL ------------------------------------------------------------


@pytest.mark.parametrize("command", ["chat", "assist"])
def test_a_reply_containing_a_closing_tag_does_not_kill_the_repl(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    install_session(monkeypatch, reply="close with [/] ok")
    result = runner.invoke(app, [command, "--no-memory"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "[/]" in result.stdout


@pytest.mark.parametrize("command", ["chat", "assist"])
def test_an_error_keeps_the_repl_alive_and_hides_the_account_id(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    boom = RuntimeError('no endpoints [/] found, "user_id": "user_2abcDEF"')
    install_session(monkeypatch, raises=boom)
    result = runner.invoke(app, [command, "--no-memory"], input="hi\nagain\n/exit\n")
    assert result.exit_code == 0, result.output
    out = squashed(result.stdout)
    assert "user_2abcDEF" not in out
    assert "<redacted>" in out
    assert out.count("noendpoints") == 2  # the second turn still ran: the REPL survived


# -- 2. print first, then persist ----------------------------------------------------------------


def test_a_reply_survives_a_save_that_cannot_happen(monkeypatch: pytest.MonkeyPatch) -> None:
    install_session(monkeypatch, reply="paid for and kept")

    def unwritable(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr("chimera.api.sessions.SessionStore.save", unwritable)
    result = runner.invoke(app, ["chat", "--no-memory"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "paid for and kept" in result.stdout  # printed before anything could fail
    assert "not saved" in result.stdout


def test_an_escaping_session_id_is_refused_before_the_first_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = install_session(monkeypatch)
    result = runner.invoke(app, ["chat", "--no-memory", "-s", "../escape"], input="hi\n/exit\n")
    assert result.exit_code == 1
    assert shows(result.stdout, "invalid session id")
    assert made == []  # nothing was built, so no turn was paid for and then thrown away


# -- 3. the documented tui fallback --------------------------------------------------------------


def test_the_documented_tui_fallback_survives_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    install_session(monkeypatch, reply="fell back cleanly")
    monkeypatch.setitem(sys.modules, "chimera.tui.app", None)  # force the ImportError branch
    result = runner.invoke(app, ["tui", "--no-memory"], input="hi\n/exit\n")
    assert result.exit_code == 0, result.output
    assert shows(result.stdout, "falling back")
    assert "fell back cleanly" in result.stdout
    saved = list((get_settings().home / "sessions").glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text(encoding="utf-8"))["id"] == saved[0].stem


def test_the_tui_fallback_passes_values_not_option_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    from typer.models import OptionInfo

    from chimera.cli.main import chat as real_chat

    wanted = set(inspect.signature(real_chat).parameters)
    seen: dict[str, Any] = {}
    monkeypatch.setattr("chimera.cli.main.chat", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setitem(sys.modules, "chimera.tui.app", None)
    result = runner.invoke(app, ["tui"])
    assert result.exit_code == 0, result.output
    assert seen, "the fallback never called chat"
    # An OMITTED parameter is the bug: calling a Typer command as a plain function gives it the
    # `typer.OptionInfo` default OBJECT, whose bool() is True and whose str() is a repr.
    missing = wanted - set(seen)
    assert missing == set(), f"these would arrive as OptionInfo objects: {sorted(missing)}"
    leaked = {k for k, v in seen.items() if isinstance(v, OptionInfo)}
    assert leaked == set(), f"passed as OptionInfo objects: {sorted(leaked)}"
    assert seen["cascade"] is False and seen["new"] is False and seen["session_id"] is None


# -- 4. an unknown command never reaches the model -----------------------------------------------


def test_an_unknown_slash_command_is_answered_by_the_repl(monkeypatch: pytest.MonkeyPatch) -> None:
    made = install_session(monkeypatch)
    result = runner.invoke(app, ["chat", "--no-memory"], input="/foo\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "unknown command /foo" in result.stdout
    assert made[0].sent == []


def test_a_word_that_merely_starts_with_new_does_not_start_a_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = install_session(monkeypatch)
    result = runner.invoke(app, ["chat", "--no-memory"], input="/newsletter\n/exit\n")
    assert "new thread" not in result.stdout
    assert shows(result.stdout, "unknown command /newsletter")
    assert made[0].sent == []


def test_taskforce_does_not_run_the_task_command(monkeypatch: pytest.MonkeyPatch) -> None:
    fused: list[Any] = []

    class NeverFuse:
        def __init__(self, *_a: Any, **_k: Any) -> None: ...

        def complete(self, messages: Any, **_k: Any) -> Any:
            fused.append(messages)
            raise AssertionError("fusion ran for /taskforce")

    monkeypatch.setattr("chimera.fusion.FusionEngine", NeverFuse)
    made = install_session(monkeypatch)
    result = runner.invoke(app, ["assist", "--no-memory"], input="/taskforce\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "unknown command" in result.stdout
    assert fused == []
    assert made[0].sent == []


def test_a_path_shaped_message_still_reaches_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    made = install_session(monkeypatch)
    runner.invoke(app, ["chat", "--no-memory"], input="/etc/hosts please\n/exit\n")
    assert made[0].sent == ["/etc/hosts please"]


@pytest.mark.parametrize(
    ("command", "marker"),
    [("chat", "start a fresh thread"), ("assist", "full-power fusion route")],
)
def test_help_is_answered_by_the_repl_not_by_the_model(
    monkeypatch: pytest.MonkeyPatch, command: str, marker: str
) -> None:
    made = install_session(monkeypatch)
    result = runner.invoke(app, [command, "--no-memory"], input="/help\n/exit\n")
    assert shows(result.stdout, marker)
    assert made[0].sent == []


# -- 5. "remember that…" says what actually happened ---------------------------------------------


def test_the_terminal_says_when_it_did_not_remember(monkeypatch: pytest.MonkeyPatch) -> None:
    install_session(monkeypatch, reply="Got it, I'll remember that")
    result = runner.invoke(
        app, ["chat", "--no-memory"], input="remember that I prefer PT-BR\n/exit\n"
    )
    assert shows(result.stdout, "not remembered")
    assert shows(result.stdout, "chimera memory add")


def test_a_fact_that_was_written_is_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    install_session(monkeypatch, memory_saved="I prefer PT-BR")
    result = runner.invoke(
        app, ["chat", "--no-memory"], input="remember that I prefer PT-BR\n/exit\n"
    )
    assert "remembered:" in result.stdout
    assert not shows(result.stdout, "not remembered")


@pytest.mark.parametrize("command", ["chat", "assist"])
def test_the_chat_memory_setting_reaches_the_session(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setenv("CHIMERA_CHAT_MEMORY", "1")
    get_settings.cache_clear()
    made = install_session(monkeypatch)
    result = runner.invoke(app, [command, "--no-memory"], input="/exit\n")
    assert result.exit_code == 0, result.output
    assert made[0].kwargs.get("remember_from_chat") is True


def test_the_tui_gets_the_memory_setting_and_somewhere_to_log_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHIMERA_CHAT_MEMORY", "1")
    get_settings.cache_clear()
    made = install_session(monkeypatch)
    built: list[dict[str, Any]] = []

    class StubTUI:
        def __init__(self, _session: Any, **kwargs: Any) -> None:
            built.append(kwargs)

        def run(self) -> None: ...

    monkeypatch.setattr("chimera.tui.app.ChimeraTUI", StubTUI)
    result = runner.invoke(app, ["tui"])
    assert result.exit_code == 0, result.output
    assert made[0].kwargs.get("remember_from_chat") is True
    assert built[0]["usage_home"] == get_settings().home


# -- 6. Ctrl-C mid-turn --------------------------------------------------------------------------


def test_ctrl_c_during_a_turn_ends_the_repl_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    made = install_session(monkeypatch, raises=[KeyboardInterrupt(), None])
    result = runner.invoke(app, ["chat", "--no-memory"], input="hi\nsecond\n/exit\n")
    assert result.exit_code == 0, result.output  # not 130, and not Click's Abort
    assert shows(result.stdout, "interrupted")
    assert shows(result.stdout, "bye")
    assert made[0].sent == ["hi"]  # the loop really stopped: "second" was never sent


# -- 7. noise ------------------------------------------------------------------------------------


def test_the_missing_sandbox_is_one_line_and_said_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.sandbox.os_sandbox.os_sandbox_available", lambda: False)
    install_session(monkeypatch)
    first = runner.invoke(app, ["chat", "--no-memory"], input="/exit\n")
    assert "no OS sandbox" in first.stdout
    assert "WARNING" not in first.stdout  # not the four-line log block above the banner
    second = runner.invoke(app, ["chat", "--no-memory"], input="/exit\n")
    assert "no OS sandbox" not in second.stdout


def test_doctor_says_where_commands_run() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert shows(result.stdout, "OS sandbox")  # the banner points here, so this has to answer
    assert shows(result.stdout, "Host execution")


# -- 8. a refusal is visible ---------------------------------------------------------------------


@pytest.mark.parametrize("command", ["chat", "assist"])
def test_a_refused_command_is_named_under_the_invented_answer(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    install_session(
        monkeypatch, reply=FABRICATION, declined=[DeclinedTool("run_shell", DECLINE)]
    )
    result = runner.invoke(app, [command, "--no-memory"], input="run echo marker-42\n/exit\n")
    assert result.exit_code == 0, result.output
    assert shows(result.stdout, "marker-42")  # the model still said what it said
    assert shows(result.stdout, "run_shell did not succeed")
    assert shows(result.stdout, "host execution declined")


# -- 9. money ------------------------------------------------------------------------------------


def test_the_repl_prints_what_the_turn_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    install_session(monkeypatch, usd=0.00123, prompt_tokens=120, completion_tokens=44)
    result = runner.invoke(app, ["chat", "--no-memory"], input="hi\n/exit\n")
    assert shows(result.stdout, "in 120") and shows(result.stdout, "out 44")
    assert shows(result.stdout, "$0.0012")


def test_an_unknown_price_is_never_a_guessed_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    install_session(monkeypatch, usd=None, prompt_tokens=10, completion_tokens=2)
    result = runner.invoke(app, ["chat", "--no-memory"], input="hi\n/exit\n")
    assert shows(result.stdout, "cost: unavailable")
    assert "$" not in result.stdout


# -- 10. the terminal enters the project's own census --------------------------------------------


@pytest.mark.parametrize("command", ["chat", "assist"])
def test_a_terminal_turn_is_written_to_the_usage_log(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    install_session(
        monkeypatch,
        usd=0.002,
        prompt_tokens=30,
        completion_tokens=7,
        declined=[DeclinedTool("run_shell", DECLINE)],
    )
    result = runner.invoke(app, [command, "--no-memory"], input="hi\nagain\n/exit\n")
    assert result.exit_code == 0, result.output
    rows = usage_rows()
    assert len(rows) == 2
    assert rows[0]["model"] == "fake/model"
    assert rows[0]["prompt_tokens"] == 30
    assert rows[0]["completion_tokens"] == 7
    assert rows[0]["usd"] == 0.002
    assert rows[0]["declined"] == 1
    assert rows[0]["session_id"] and rows[0]["session_id"] == rows[1]["session_id"]


def test_a_turn_that_failed_is_not_billed_as_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    install_session(monkeypatch, raises=RuntimeError("provider down"))
    runner.invoke(app, ["chat", "--no-memory"], input="hi\n/exit\n")
    assert usage_rows() == []
