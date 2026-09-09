"""The pure half of the terminal surface: formatting, command parsing, and refusal collection.

These functions used to be inline f-strings duplicated in ``chat`` and ``assist``, which is why a
reply containing ``[/]`` could kill one REPL and not the TUI. Testing them here — with no console,
no event loop and no CliRunner — is what makes the assertions about *text* precise; the sibling
file drives the real loops end to end.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import AgentResult, ToolActivity
from chimera.interface import ChatSession
from chimera.interface.render import (
    SlashCommand,
    cost_line,
    cost_text,
    error_line,
    help_lines,
    is_command_like,
    refusal_lines,
    reply_line,
    scrub_provider_ids,
    split_command,
    unknown_command_lines,
)
from chimera.interface.session import DeclinedTool, TurnReport, decline_reason

DECLINE = "error: host execution declined (CHIMERA_HOST_EXEC). Not run."
FABRICATION = "The command printed exactly: marker-42 / 2024"


class _Agent:
    """Agent stub that reports whatever tool activity a test scripts."""

    def __init__(self, *activities: ToolActivity, answer: str = "done") -> None:
        self.activities = activities
        self.answer = answer

    def run(
        self, task: str, *, on_token: Any = None, on_tool: Any = None
    ) -> AgentResult:
        for activity in self.activities:
            if on_tool is not None:
                on_tool(activity)
        return AgentResult(
            answer=self.answer,
            steps=1,
            stopped_reason="final",
            tool_names=[a.name for a in self.activities],
        )


# -- markup: nothing from outside is ever parsed as markup ---------------------------------------


def test_a_reply_with_a_closing_tag_is_escaped_not_parsed() -> None:
    # `Console().print(f"...{'[/]'}")` raises MarkupError, which killed the REPL after the turn
    # had already been paid for and (in `chat`) written to disk.
    assert "\\[/]" in reply_line("close it with [/] here")


def test_an_error_is_escaped_and_stripped_of_the_account_id() -> None:
    raw = 'BadRequestError: {"message":"no endpoints [/]","user_id":"user_2abcDEF"}'
    line = error_line(RuntimeError(raw))
    assert "user_2abcDEF" not in line
    assert "<redacted>" in line
    assert "\\[/]" in line


def test_scrubbing_leaves_the_rest_of_the_message_alone() -> None:
    assert scrub_provider_ids("no endpoints found for x/y") == "no endpoints found for x/y"
    assert scrub_provider_ids("user_id=abc123") == "user_id=<redacted>"
    assert scrub_provider_ids('"org_id": "org-9"') == '"org_id": <redacted>'


# -- refusals: a declined call is named, with the tool's own words -------------------------------


def test_a_refusal_is_rendered_with_the_reason_the_tool_gave() -> None:
    report = TurnReport(answer=FABRICATION, declined=[DeclinedTool("run_shell", DECLINE)])
    lines = refusal_lines(report)
    assert len(lines) == 1
    assert "run_shell" in lines[0]
    assert "host execution declined" in lines[0]


def test_a_turn_with_no_refusal_renders_nothing() -> None:
    assert refusal_lines(TurnReport(answer="fine")) == []


def test_a_long_observation_is_shortened_to_one_line() -> None:
    reason = decline_reason("error: it went\nwrong\n" + "x" * 500)
    assert "\n" not in reason
    assert len(reason) <= 201


def test_a_declined_tool_call_reaches_the_turn_report() -> None:
    session = ChatSession(
        _Agent(ToolActivity("run_shell", {"command": "echo hi"}, False, DECLINE), answer=FABRICATION)
    )
    report = session.send_verbose("run it")
    assert report.answer == FABRICATION  # the model still says what it says
    assert [(d.name, d.reason) for d in report.declined] == [("run_shell", DECLINE)]


def test_a_tool_that_worked_is_not_reported_as_declined() -> None:
    session = ChatSession(_Agent(ToolActivity("read_file", {}, True, "hello")))
    assert session.send_verbose("read it").declined == []


def test_collecting_refusals_does_not_steal_the_callers_tool_callback() -> None:
    # The TUI passes its own `on_tool` to drive the activity panel live; wrapping it must not
    # swallow it.
    seen: list[str] = []
    session = ChatSession(_Agent(ToolActivity("run_shell", {}, False, DECLINE)))
    session.send_verbose("run it", on_tool=lambda act: seen.append(act.name))
    assert seen == ["run_shell"]


# -- money: never a guessed zero -----------------------------------------------------------------


def test_an_unknown_price_says_so() -> None:
    assert cost_text(TurnReport(answer="x", usd=None)) == "cost: unavailable"
    assert "$" not in cost_line(TurnReport(answer="x", usd=None))


def test_a_known_price_is_shown_with_the_token_counts() -> None:
    line = cost_line(TurnReport(answer="x", usd=0.00123, prompt_tokens=120, completion_tokens=44))
    assert "in 120" in line
    assert "out 44" in line
    assert "$0.0012" in line


def test_a_cached_turn_says_the_price_excludes_the_cache() -> None:
    report = TurnReport(answer="x", usd=0.5, cache_read_tokens=90, cache_write_tokens=1)
    assert "excl. cache" in cost_text(report)
    assert "cache r/w 90/1" in cost_line(report)


# -- slash commands: matched on a word boundary --------------------------------------------------


def test_a_command_is_split_from_its_argument() -> None:
    assert split_command("/model  gpt-4o") == ("/model", "gpt-4o")
    assert split_command("/help") == ("/help", "")
    assert split_command("/profile preference: PT-BR") == ("/profile", "preference: PT-BR")


def test_a_plain_message_is_not_a_command() -> None:
    assert split_command("what is 2 + 2") == ("", "what is 2 + 2")


def test_a_longer_word_starting_with_a_command_is_not_that_command() -> None:
    # `startswith` made `/taskforce` run `/task` with the argument "force", and `/newsletter`
    # start a new thread.
    assert split_command("/taskforce")[0] == "/taskforce"
    assert split_command("/newsletter")[0] == "/newsletter"


def test_a_path_or_a_fraction_is_not_command_shaped() -> None:
    # Refusing to send these to the model would break a legitimate message to catch a typo.
    assert is_command_like("/foo") is True
    assert is_command_like("/usr/local/bin") is False
    assert is_command_like("/2") is False
    assert is_command_like("/x.txt") is False


def test_help_and_the_unknown_message_come_from_one_table() -> None:
    commands = [SlashCommand("/help", "", "this list"), SlashCommand("/model", "<slug>", "switch")]
    assert any("/model <slug>" in line for line in help_lines(commands))
    unknown = " ".join(unknown_command_lines("/foo", commands))
    assert "/foo" in unknown
    assert "/help" in unknown and "/model" in unknown
