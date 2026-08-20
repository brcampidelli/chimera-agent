"""Structured orchestration events — live progress from a fan-out, without the transcript.

``chimera.core.events`` does this for one agent's loop; this does it for a *team* of them. The
distinction that shapes the module is ``task_id``: a single agent's progress is a sequence, so an
index in ``data`` is enough, but a fan-out is N sequences interleaved by a thread pool, and the
consumer needs to route every frame to the right worker before it can render anything. That makes
the subtask id a first-class field rather than a ``data`` key — a payload cannot accidentally
overwrite the thing the UI keys on.

There is deliberately no ``worker_token`` kind. Workers run through ``BudgetedBackend.complete``,
which is not streaming and whose return value is what the receipts are computed from; a streaming
variant would need its own accounting, and that accounting is the basis of every economy number
this package reports. Chopping up already-complete text to look like typing is the other option,
and it is the one this codebase has refused before (see the note in ``chimera/api/app.py`` about
fusion: no tokens fire, and that is honest). A consumer that wants liveness gets a heartbeat from
its own transport, which is where a heartbeat belongs.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

#: The vocabulary. ``fell_back`` is not an error: the orchestrator refusing to fan out is the
#: single-agent path working as designed, and a consumer that renders it as a failure is lying
#: about the most common outcome.
OrchEventKind = Literal[
    "classified",
    "decomposed",
    "worker_started",
    "worker_verified",
    "worker_rejected",
    "synthesizing",
    "fell_back",
    "done",
    "error",
]

OrchEventSink = Callable[["OrchEvent"], None]


@dataclass(frozen=True)
class OrchEvent:
    """One progress frame from an orchestrated run.

    ``task_id`` is the subtask this frame belongs to, empty for frames about the run as a whole.
    It is a field and not a ``data`` entry because it is the consumer's routing key: an event
    whose route can be clobbered by a payload key is an event that lands in the wrong card.
    """

    kind: OrchEventKind
    text: str = ""
    task_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def thread_safe_sink(sink: OrchEventSink) -> OrchEventSink:
    """Serialise calls into ``sink``.

    The orchestrator does NOT do this for you, and that is a decision rather than an omission: the
    sink that matters in production is an SSE bridge whose whole body is
    ``loop.call_soon_threadsafe(...)``, which is already safe, and wrapping every emission in a lock
    would serialise N workers around a callback whose cost the orchestrator cannot see. Callers
    whose sink is not safe — anything writing to a console or appending to a plain list — wrap it
    here and pay for the lock they actually need.

    Note what this does not give you: an order. Two workers finishing at once will still be
    serialised in an arbitrary order, because there is no true order to report. A consumer that
    needs a stable sequence assigns it at the single point where one exists — its own writer.
    """
    lock = threading.Lock()

    def guarded(event: OrchEvent) -> None:
        with lock:
            sink(event)

    return guarded
