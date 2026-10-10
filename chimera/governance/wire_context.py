"""Which run a gateway call belongs to, and what kind of call it is — declared by the caller.

The wire log (``CHIMERA_WIRE_LOG``, off by default) is an independent record of every provider
exchange, reconciled against the run traces by :func:`chimera.governance.reconcile.reconcile`. It
used to carry no idea of *who* made a call, so every call that is not a step — the closing call at
``max_steps``, the compaction summariser — read as a step the trace had lost (S30-61, Amendment 3).

The fix is not to guess afterwards. Guessing from the request's shape (tools sent, the summariser's
system prompt) is what the bench did, and a guess is exactly what a forged record would imitate.
The caller that makes the call knows what it is, so the caller says so: an :class:`Agent` opens a
:func:`wire_run` for the length of a run, and every place that calls the backend inside it sets
:func:`wire_kind` around the call. The gateway reads both at tap time and writes them into the
record. A ``ContextVar`` carries them so no backend signature changes and every wrapper in between
(spend caps, metering, the cascade) passes them through untouched.

A call made where no caller declared anything is written as :data:`UNDECLARED`, and outside any run
it carries no ``run_id``; the reconciler reports those by kind instead of comparing them.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

#: One agent step: the call whose result becomes a ``StepRecord``. The only kind compared one to one.
STEP = "step"
#: The compaction summariser's call (``summarise_compaction``), made between two steps.
SUMMARY = "summary"
#: ``Agent._close``: the tool-free final ask at ``max_steps``, after the loop breaker, on handover.
CLOSE = "close"
#: The one tool-free re-ask after a final reply that came back empty.
EMPTY_RETRY = "empty_retry"
#: The tool router's pick (``AgentConfig.tool_router``), made before a step.
ROUTER = "router"
#: A model call made by a tool while it runs (a governance judge, a tool with a model of its own).
TOOL = "tool"
#: A call nobody declared a kind for.
UNDECLARED = "undeclared"


@dataclass
class WireRun:
    """One run's correlation id and the calls the gateway tapped while it was open.

    ``calls`` is what the run copies into its own trace (``side_calls``), so that every non-step
    record in the wire log is claimed by the run that made it. ``step`` is the loop's current step
    index, stamped on each call so a claim can be checked against the trace (a summary must follow
    a step that compacted).
    """

    run_id: str
    traced: bool
    step: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def note(self, entry: dict[str, Any]) -> None:
        # Tools that run together do so on worker threads that share this object.
        with self._lock:
            self.calls.append({**entry, "at_step": self.step})


_RUN: ContextVar[WireRun | None] = ContextVar("chimera_wire_run", default=None)
_KIND: ContextVar[str | None] = ContextVar("chimera_wire_kind", default=None)


@contextlib.contextmanager
def wire_run(run_id: str, *, traced: bool) -> Iterator[WireRun]:
    """Open a run scope. A run nested inside a tool gets its own scope and does not inherit the
    outer call's kind; the outer scope is restored when it ends."""
    scope = WireRun(run_id=run_id, traced=traced)
    run_token = _RUN.set(scope)
    kind_token = _KIND.set(None)
    try:
        yield scope
    finally:
        _KIND.reset(kind_token)
        _RUN.reset(run_token)


@contextlib.contextmanager
def wire_kind(kind: str) -> Iterator[None]:
    """Declare the kind of the gateway calls made inside the block."""
    token = _KIND.set(kind)
    try:
        yield
    finally:
        _KIND.reset(token)


def current() -> tuple[WireRun | None, str]:
    """The open run (or None) and the declared kind (or :data:`UNDECLARED`)."""
    return _RUN.get(), _KIND.get() or UNDECLARED
