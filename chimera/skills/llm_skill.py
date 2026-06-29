"""Base class for skills backed by a language model.

An ``LLMSkill`` holds a model backend (any :class:`SupportsComplete`). If none is
provided it lazily constructs the default gateway, so built-in skills work out of
the box once a provider key is set, while tests inject a fake backend.
"""

from __future__ import annotations

from chimera.providers.gateway import SupportsComplete
from chimera.skills.base import Skill


class LLMSkill(Skill):
    """A skill that calls a model to do its work."""

    def __init__(
        self,
        backend: SupportsComplete | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__()
        self._backend = backend
        self.model = model

    @property
    def backend(self) -> SupportsComplete:
        backend = self._backend
        if backend is None:
            from chimera.providers import LLMGateway

            backend = LLMGateway()
            self._backend = backend
        return backend

    def ask(self, system: str, user: str, *, temperature: float = 0.1) -> str:
        """Single-turn helper returning the model's text."""
        result = self.backend.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=self.model,
            temperature=temperature,
        )
        return result.content
