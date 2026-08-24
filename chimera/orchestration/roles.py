"""Roles and role-bound agents for multi-agent teams.

A :class:`Role` is a specialization (a system prompt + optional model). A
:class:`RoleAgent` binds a role to a model backend and answers a task in character.
Role specialization is the core of Tier-3 teams (CrewAI-style).

A role can be a single-shot *persona* (text in, text out — the default) or, when given a
tool registry, a *tool-using worker* that runs a real agent loop (read/edit files, run
commands, etc.) and returns its final answer. Crews call ``act`` either way, so a crew can
mix talkers and doers transparently.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chimera.providers.gateway import Message, SupportsComplete
from chimera.tools.registry import ToolRegistry


@dataclass
class Role:
    """A specialization an agent can take on."""

    name: str
    system_prompt: str
    model: str | None = None
    allowed_tools: list[str] | None = None  # if set, the worker sees ONLY these tools (fail-closed)


def _restrict_tools(registry: ToolRegistry, allowed: list[str]) -> ToolRegistry:
    """A registry with only the ``allowed`` tools — fail-closed: an unknown name is simply absent."""
    subset = ToolRegistry()
    allow = set(allowed)
    for tool in registry.tools():
        if tool.name in allow:
            subset.register(tool)
    return subset


class RoleAgent:
    """A model backend bound to a role — a single-shot persona, or a tool-using worker.

    Pass ``tools`` to make the role execute a real agent loop (it can search, read and edit
    files, run commands …) instead of just answering in one shot. ``max_steps`` bounds that
    loop. Without ``tools`` the behaviour is unchanged: one model call, text in / text out.
    """

    def __init__(
        self,
        role: Role,
        backend: SupportsComplete,
        *,
        tools: ToolRegistry | None = None,
        max_steps: int = 6,
        identity: str = "",
        project_root: Path | None = None,
    ) -> None:
        self.role = role
        self.backend = backend
        self.tools = tools
        self.max_steps = max_steps
        # A crew worker's answer lands in front of the user, so the owner's instructions apply to
        # it exactly as they do to the coding agent. This built `AgentConfig` without them, and
        # since the same rendered block carries the "always answer in {language}" line, an owner
        # who set the app to Portuguese got English out of the crew.
        self.identity = identity
        # And the project's own conventions, for the same reason: `project_root` is what makes an
        # agent read AGENTS.md, and a crew worker editing a repo was the one worker in the stack
        # not reading it.
        self.project_root = project_root
        #: Why the last `act()` stopped, from `AgentResult.stopped_reason`: "final" when the model
        #: chose to answer, or "budget" / "max_steps" / "tool_loop" / "cancelled" when the loop was
        #: cut off. `act()` returns a bare string and eight call sites depend on that, so the reason
        #: rides here rather than widening the signature.
        #:
        #: It exists because discarding it was a defect with teeth. `Agent.run` catches
        #: `BudgetExceeded` and returns the message AS THE ANSWER — "not an error: the run did what
        #: it was told to do with the money it was given" — so a worker that was cut off produced a
        #: 44-character string that read like a finding, went through verification, and came back
        #: `verified (accepted)` with a green card. Reproduced at a 400-token cap.
        #:
        #: Safe as instance state because a RoleAgent is built per worker per run; nothing shares one.
        self.last_stop = "final"

    @property
    def name(self) -> str:
        return self.role.name

    def act(self, task: str, *, context: str = "", temperature: float = 0.3) -> str:
        user = f"{context}\n\n{task}" if context else task
        if self.tools is not None:
            from chimera.core.agent import Agent, AgentConfig

            # Enforce the role's declared tool allowlist (if any) by filtering the registry BEFORE the
            # agent loop — so a role can't reach a tool outside its remit even though it shares the
            # crew's registry. Fail-closed and enforced, not merely suggested in the prompt.
            tools = self.tools if self.role.allowed_tools is None else _restrict_tools(
                self.tools, self.role.allowed_tools
            )
            agent = Agent(
                self.backend,
                tools,
                AgentConfig(
                    model=self.role.model,
                    max_steps=self.max_steps,
                    temperature=temperature,
                    system_prompt=self.role.system_prompt,
                    instructions=self.identity,
                    project_root=self.project_root,
                ),
            )
            result = agent.run(user)
            self.last_stop = result.stopped_reason
            return result.answer
        # No loop here, so nothing can cut it short: a plain completion either answers or raises,
        # and `BudgetExceeded` propagates to the caller instead of becoming the answer.
        self.last_stop = "final"
        system = self.role.system_prompt
        if self.identity:
            # In front of the role, matching the agent loop's own order: the role is the more
            # specific instruction and reads closer to the task.
            system = f"{self.identity}\n\n{system}"
        return self.backend.complete(
            [
                Message(role="system", content=system),
                Message(role="user", content=user),
            ],
            model=self.role.model,
            temperature=temperature,
        ).content
