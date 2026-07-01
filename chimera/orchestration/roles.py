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

from chimera.providers.gateway import Message, SupportsComplete
from chimera.tools.registry import ToolRegistry


@dataclass
class Role:
    """A specialization an agent can take on."""

    name: str
    system_prompt: str
    model: str | None = None


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
    ) -> None:
        self.role = role
        self.backend = backend
        self.tools = tools
        self.max_steps = max_steps

    @property
    def name(self) -> str:
        return self.role.name

    def act(self, task: str, *, context: str = "", temperature: float = 0.3) -> str:
        user = f"{context}\n\n{task}" if context else task
        if self.tools is not None:
            from chimera.core.agent import Agent, AgentConfig

            agent = Agent(
                self.backend,
                self.tools,
                AgentConfig(
                    model=self.role.model,
                    max_steps=self.max_steps,
                    temperature=temperature,
                    system_prompt=self.role.system_prompt,
                ),
            )
            return agent.run(user).answer
        return self.backend.complete(
            [
                Message(role="system", content=self.role.system_prompt),
                Message(role="user", content=user),
            ],
            model=self.role.model,
            temperature=temperature,
        ).content
