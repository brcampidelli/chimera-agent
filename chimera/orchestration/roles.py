"""Roles and role-bound agents for multi-agent teams.

A :class:`Role` is a specialization (a system prompt + optional model). A
:class:`RoleAgent` binds a role to a model backend and answers a task in character.
Role specialization is the core of Tier-3 teams (CrewAI-style).
"""

from __future__ import annotations

from dataclasses import dataclass

from chimera.providers.gateway import Message, SupportsComplete


@dataclass
class Role:
    """A specialization an agent can take on."""

    name: str
    system_prompt: str
    model: str | None = None


class RoleAgent:
    """A model backend bound to a role."""

    def __init__(self, role: Role, backend: SupportsComplete) -> None:
        self.role = role
        self.backend = backend

    @property
    def name(self) -> str:
        return self.role.name

    def act(self, task: str, *, context: str = "", temperature: float = 0.3) -> str:
        user = f"{context}\n\n{task}" if context else task
        return self.backend.complete(
            [
                Message(role="system", content=self.role.system_prompt),
                Message(role="user", content=user),
            ],
            model=self.role.model,
            temperature=temperature,
        ).content
