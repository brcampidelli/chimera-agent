"""Three contradictions inside a single prompt.

Study 25, `bench/PLAN-study25-system-prompts.md` §4, defect 5.

1. **The solve nudge.** The default prompt allows up to three blocking questions. When a `solve`
   answered with them, the only nudge it got said it "described a solution but did not carry it
   out". That is false of a question, and it left the question standing in a run nobody answers.
2. **The fusion panel.** The panel receives the agent loop's messages, whose default prompt says to
   use the provided tools. The panel has no tools. It now says so.
3. **The hosted governance prompt.** It asked for "exactly one word" and then for a JSON object.
   That instrument is pinned to the bench that measured it, so the fix was a measured arm, not an
   edit: `bench/jev_decisions/RESULTS-one-schema.md` found the one-instruction text non-inferior,
   and the hosted backend now sends it.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import (
    _ACTION_NUDGE,
    _ASSUME_NUDGE,
    Agent,
    AgentConfig,
    _looks_like_questions,
)
from chimera.decisions.governance import DANGER, JUDGE_TEXT
from chimera.decisions.hosted import HostedVerbalizedBackend
from chimera.fusion.engine import _NO_TOOLS_NOTE, FusionConfig, FusionEngine
from chimera.providers.gateway import CompletionResult, Message, ToolCall
from chimera.tools.builtin import EchoTool
from chimera.tools.registry import ToolRegistry


class _Scripted:
    def __init__(self, replies: list[CompletionResult]) -> None:
        self.replies = list(replies)
        self.calls: list[list[Any]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls.append(list(messages))
        return self.replies.pop(0)


def _echo() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


def _nudges(transcript: list[Any]) -> list[str]:
    return [
        str(m.get("content", ""))
        for m in transcript
        if isinstance(m, dict) and m.get("role") == "user" and m.get("content") in (_ACTION_NUDGE, _ASSUME_NUDGE)
    ]


def test_a_solve_that_asked_questions_is_told_to_assume_not_accused_of_describing() -> None:
    backend = _Scripted([
        CompletionResult(content="Qual tecnologia você quer?\nOnde o site vai ficar?", model="fake"),
        CompletionResult(content="", model="fake", tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "ok"})]),
        CompletionResult(content="Assumi HTML estático no GitHub Pages. Feito.", model="fake"),
    ])
    result = Agent(backend, _echo(), AgentConfig(insist_on_action=True)).run("faz um site pra minha padaria")  # type: ignore[arg-type]
    assert _nudges(result.transcript) == [_ASSUME_NUDGE]
    assert result.tool_calls_made == 1


def test_a_solve_that_narrated_still_gets_the_action_nudge() -> None:
    backend = _Scripted([
        CompletionResult(content="You can run:\n```bash\ngit merge x\n```", model="fake"),
        CompletionResult(content="", model="fake", tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "ok"})]),
        CompletionResult(content="Merged.", model="fake"),
    ])
    result = Agent(backend, _echo(), AgentConfig(insist_on_action=True)).run("merge it")  # type: ignore[arg-type]
    assert _nudges(result.transcript) == [_ACTION_NUDGE]


def test_what_counts_as_asking() -> None:
    assert _looks_like_questions("Which stack?\nWho is it for?")
    assert not _looks_like_questions("I fixed the parser and the tests pass.")
    assert not _looks_like_questions("Should I run this?\n```\nrm -rf build\n```")  # code is narration
    assert not _looks_like_questions("?\n" * 6)  # a questionnaire is not three blocking questions


def test_the_fusion_panel_is_told_it_has_no_tools() -> None:
    panel_backend = _Scripted([CompletionResult(content="an answer", model="m1")])
    engine = FusionEngine(panel_backend, FusionConfig(panel=["m1"], judge="j", synthesizer="s"))  # type: ignore[arg-type]
    caller = [
        Message(role="system", content="Use the provided tools to actually carry it out."),
        Message(role="user", content="What does this function return?"),
    ]
    engine._run_panel(caller)
    sent = panel_backend.calls[0]
    system = sent[0]["content"] if isinstance(sent[0], dict) else sent[0].content
    assert system.startswith("Use the provided tools") and system.endswith(_NO_TOOLS_NOTE)
    # The caller's own list is not rewritten: the loop keeps sending its prompt unchanged.
    assert caller[0].content == "Use the provided tools to actually carry it out."


def test_a_panel_call_without_a_system_message_gets_one() -> None:
    panel_backend = _Scripted([CompletionResult(content="an answer", model="m1")])
    engine = FusionEngine(panel_backend, FusionConfig(panel=["m1"], judge="j", synthesizer="s"))  # type: ignore[arg-type]
    engine._run_panel([{"role": "user", "content": "hi"}])
    assert panel_backend.calls[0][0] == {"role": "system", "content": _NO_TOOLS_NOTE}


def test_the_hosted_governance_prompt_asks_for_one_output_format() -> None:
    text = HostedVerbalizedBackend(None, "m").system_text(DANGER)
    assert "exactly one word" not in text
    assert text.count("Reply with") == 1 and "JSON object" in text
    # The judge's own text keeps its reply line: the one-word judge and the local backend send it.
    assert JUDGE_TEXT.endswith("Reply with exactly one word: BLOCK, REVIEW, or ALLOW.")
