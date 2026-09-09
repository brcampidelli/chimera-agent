"""The dollar ceiling a terminal conversation runs under — one meter for the whole thread.

``chimera solve`` has taken ``--max-usd`` since the flag was added, and the desktop's code turn
takes ``max_usd`` on the request (`chimera/api/code_api.py:136-139`, which records that the
mechanism existed with *no route reaching it*). The terminal had neither: a ``chat`` turn that
looped on tools stopped at ``max_steps`` and nowhere else, and a conversation could run all night.

**Per SESSION, not per turn.** ``AgentConfig.max_usd`` builds a fresh :class:`SpendBudget` inside
every ``Agent.run`` — see the comment there: "Per RUN, not per Agent". That is right for a
scheduler dispatching unrelated jobs and wrong for a REPL, where the runs are one conversation: a
ceiling that resets every turn caps a single answer and caps nothing about the evening. It is also
the meaning ``solve --max-usd`` already has ("the whole run, all attempts together"), and two
flags with the same name and opposite scopes would be worse than no flag.

The mechanism is the one ``AutonomousAgent`` already documents for exactly this case — "a caller
that spans several ``run`` calls passes its own [budget], and every attempt then draws on the same
money". :class:`BudgetedTurns` is that caller, in the shape :class:`~chimera.interface.session.
ChatSession` can use: a ``SupportsRun`` that forwards to the real agent with the conversation's
budget attached. Nothing in ``ChatSession`` changes, which is the point — a ``spend=`` keyword on
its protocol would have to be accepted by every fake in the suite and by the messaging gateway,
neither of which has a ceiling to enforce.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.core.agent import AgentConfig, AgentResult, ToolActivity
    from chimera.orchestration.budget import SpendBudget


@dataclass
class BudgetedTurns:
    """Every turn of one conversation, drawn from one :class:`SpendBudget`."""

    agent: Any
    budget: SpendBudget

    @property
    def config(self) -> AgentConfig:
        """The wrapped agent's config.

        Not decoration: :meth:`chimera.interface.session.ChatSession.set_model` reaches for
        ``agent.config`` to switch models mid-conversation, and a wrapper without this would make
        ``/model`` answer "can't switch model" on any session that has a ceiling.
        """
        config: AgentConfig = self.agent.config
        return config

    def run(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> AgentResult:
        """One turn, charged to the conversation's budget rather than to a fresh one."""
        result: AgentResult = self.agent.run(
            task, on_token=on_token, on_tool=on_tool, spend=self.budget
        )
        return result


def session_budget(max_usd: float | None) -> SpendBudget | None:
    """The conversation's ceiling, or ``None`` when none was asked for.

    ``None`` and ``0`` both mean "no ceiling" here, and that is deliberate: ``SpendBudget`` raises
    on a non-positive cap, and raising out of a Typer callback because somebody typed
    ``--max-usd 0`` would kill the REPL before its first prompt. A zero ceiling is also the one
    value that fails in the dangerous direction — the same argument ``code_api`` makes for its
    ``gt=0`` — so it is refused by the option's own validation, not here.
    """
    if not max_usd:
        return None
    from chimera.orchestration.budget import SpendBudget

    return SpendBudget(max_usd)
