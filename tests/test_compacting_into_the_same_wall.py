"""`compact` documented the guard, and the only caller in the repository did not implement it.

Its docstring says it plainly: *"callers should treat a no-op as 'this did not help' rather than
retrying into the same wall"*. `Agent._step` reads the returned flag only to set `record.compacted`,
so when compaction has nothing left to give — the transcript is already down to `keep_recent`, and
the prompt is still over the threshold — the loop compacts (no-op), pays for a full model call with
the same oversized prompt, gets the same `prompt_tokens` back, and does it again until `max_steps`.

A guard that ASSERTS in prose what the code does not do is the shape this project has been bitten by
before, and this time the prose sits in the docstring of the function that needed the caller.

Stopping is the honest response. Not another provider call: the next one costs exactly what the last
one cost and cannot succeed, because nothing about the prompt changed. The loop already has the
machinery — the same final turn `max_steps` uses, answering with what it has, under its own reason.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.core.context_budget import compact
from chimera.providers.gateway import CompletionResult
from chimera.tools.registry import ToolRegistry


class _SempreGrande:
    """A backend whose prompt is always over budget — the wall this test is about.

    The count is absurd on purpose. `ContextBudget` takes its window from the MODEL name, not from
    the backend, so a plausible-looking 1000 sat far under the default window's threshold and the
    run finished normally — the test passed while measuring nothing.
    """

    def __init__(self, *, prompt_tokens: int = 10_000_000) -> None:
        self.chamadas = 0
        self.prompt_tokens = prompt_tokens

    def complete(self, messages: list[Any], **_kwargs: Any) -> CompletionResult:
        self.chamadas += 1
        return CompletionResult(
            content=f"resposta {self.chamadas}", model="m",
            prompt_tokens=self.prompt_tokens, completion_tokens=5,
        )


def _agente(backend: Any, *, max_steps: int = 8) -> Agent:
    return Agent(
        backend,
        ToolRegistry(),
        AgentConfig(max_steps=max_steps, context_budget=0.6, detect_tool_loops=False),
    )


# ------------------------------------------------------------------ the wall


def test_a_run_that_cannot_compact_stops_instead_of_paying(monkeypatch: Any) -> None:
    """The whole point, counted in provider calls — which is what the defect actually cost."""
    backend = _SempreGrande()

    resultado = _agente(backend, max_steps=8).run("faça algo")

    assert backend.chamadas <= 2, f"paid for {backend.chamadas} calls against an unchangeable prompt"
    assert resultado.stopped_reason == "context_stuck"


def test_it_says_which_wall_it_hit() -> None:
    """`max_steps` and "compaction has nothing left" both end a turn early and need opposite
    responses: one is a ceiling to raise, the other is a conversation to start over."""
    resultado = _agente(_SempreGrande()).run("faça algo")

    assert resultado.stopped_reason == "context_stuck"
    assert resultado.stopped_reason != "max_steps"


def test_it_answers_with_what_it_has() -> None:
    """Stopping must not mean returning nothing. The run has done real work by this point and the
    person asked a question — the same final turn `max_steps` already takes."""
    assert _agente(_SempreGrande()).run("faça algo").answer


# ------------------------------------------------------------------ the ordinary case


def test_a_run_under_budget_is_untouched() -> None:
    """The guard against a fix that stops every long conversation."""
    backend = _SempreGrande(prompt_tokens=10)

    resultado = _agente(backend).run("faça algo")

    assert resultado.stopped_reason == "final"


def test_compaction_that_helps_is_not_treated_as_stuck() -> None:
    """The distinction the flag carries: a no-op is not the same as a compaction that shrank the
    prompt. `compact` reports `changed`, and only `False` means there is nothing left to try."""
    mensagens: list[Any] = [{"role": "system", "content": "s"}] + [
        {"role": "user", "content": f"m{i}"} for i in range(20)
    ]

    _menores, mudou = compact(mensagens, keep_recent=6)

    assert mudou is True


def test_a_long_conversation_compacts_and_carries_on() -> None:
    """The same distinction, asserted in the LOOP rather than in `compact` — which is where it is
    decided, and where the version of this test that only called `compact` was not looking.

    A run with a real transcript behind it goes over budget, compaction works, and the turn has to
    continue. A brake that fires on a successful compaction would end every long conversation at its
    first crowded step, and the helper test above could not have shown it.
    """
    class _GrandeUmaVez:
        def __init__(self) -> None:
            self.chamadas = 0

        def complete(self, messages: list[Any], **_kwargs: Any) -> CompletionResult:
            self.chamadas += 1
            # Over budget on the first call, comfortable once the transcript has been compacted.
            grande = 10_000_000 if self.chamadas == 1 else 10
            return CompletionResult(
                content="pronto", model="m", prompt_tokens=grande, completion_tokens=5
            )

    backend = _GrandeUmaVez()
    historia: list[Any] = [{"role": "user", "content": f"m{i}"} for i in range(20)]

    resultado = _agente(backend).run("faça algo", history=historia)

    # `final`, not a second call. A turn whose model asks for no tool ends on its first step, so
    # counting calls here asserted something the loop never does — and that version failed against
    # the CORRECT code while still going red under the sabotage, which is a test passing for the
    # wrong reason in the one direction that looks like success.
    assert resultado.stopped_reason == "final"
    assert resultado.answer == "pronto"


def test_a_transcript_already_at_the_floor_reports_no_change() -> None:
    """The precondition of the whole fix, asserted on `compact` itself: it does say so."""
    mensagens: list[Any] = [{"role": "system", "content": "s"}, {"role": "user", "content": "m"}]

    _iguais, mudou = compact(mensagens, keep_recent=6)

    assert mudou is False
