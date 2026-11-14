"""One switch that keeps fusion out of a run that carries tools.

Lives under ``providers`` rather than ``fusion`` because the agent loop must set it and the chat
endpoint must not pay to import the fusion engine to do so.

Fusion does not tool-call, so a turn that carries ``tools`` has always gone to one model. The other
calls of such a run carry no ``tools`` argument (the closing call, the empty-reply retry, the
compaction summary), and that absence used to be read as "this turn may fuse": "never fuse a turn with
tools" held for the steps and not for the two or three calls around them, and the receipt of a retry
"escalated to fusion" was written from exactly those calls.

A ContextVar, not a parameter, because the callers are several layers apart (agent step, summariser,
a cascade) and a parameter threaded through all of them is how one of them gets missed.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_FUSION_FORBIDDEN: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "chimera_fusion_forbidden", default=False
)


@contextmanager
def fusion_forbidden() -> Iterator[None]:
    """Inside this block no router or cascade sends a turn to fusion; they answer on one model."""
    token = _FUSION_FORBIDDEN.set(True)
    try:
        yield
    finally:
        _FUSION_FORBIDDEN.reset(token)


def is_fusion_forbidden() -> bool:
    return _FUSION_FORBIDDEN.get()


def carries_tools(registry: object) -> bool:
    """Whether an agent's registry can offer the model any tool. An opaque registry counts as carrying some."""
    try:
        return len(registry) > 0  # type: ignore[arg-type]
    except TypeError:
        return True


def agent_carries_tools(agent: object) -> bool:
    """Whether ``agent`` has tools to offer. An object with no ``tools`` at all (a test double) has none."""
    registry = getattr(agent, "tools", None)
    return registry is not None and carries_tools(registry)
