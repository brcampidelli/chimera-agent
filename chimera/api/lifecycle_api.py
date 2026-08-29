"""The SDLC lifecycle crew over HTTP: plan → build → test → review.

`LifecycleCrew` has existed, tested, for a long time, and only ``chimera lifecycle`` in a terminal
could reach it. That is the recurring shape of defect in this codebase — a capability that works
and that nothing calls — and it is worse here than usual, because the thing the lifecycle adds over
an ordinary run is precisely that its stages are *visible*: a test gate you can see fail, and a
reviewer's opinion after it passes. Delivered as one blob at the end of several minutes, it is
`solve` with extra waiting.

So the route streams a frame per stage, and the crew learned to report them.

**Governance.** ``lifecycle_crew`` defaults to the bare workspace registry, which is right for a
terminal — somebody running the command in their own shell already has every capability the agent
is being handed — and wrong for anything that answers a request. This route builds the registry
through :func:`~chimera.api.code_api.assemble_registry`, the same path the run and conversation
endpoints use, so the trust kernel, the taint ledger and the owner's allowlist all apply. The AST
gate in ``tests/test_governed_surfaces.py`` exists because an HTTP surface that builds its own
registry skips all three, and this is an HTTP surface.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, params
from sse_starlette.sse import EventSourceResponse

from chimera.api.code_api import CodeSeams, assemble_registry, resolve_steps
from chimera.api.schemas import CancelOut
from chimera.api.sse import SSE_RESPONSE
from chimera.config import Settings
from chimera.telemetry import get_logger

_log = get_logger("api.lifecycle")

#: run_id -> stop Event. Cooperative cancel: the crew polls between stages, never mid model call.
#: In-process only, which is what a single-user local app needs.
_lifecycle_cancels: dict[str, threading.Event] = {}


class LifecycleRunIn(CodeSeams):
    """A run through plan → build → test → review.

    Inherits the coding seams rather than redeclaring them, for the same reason ``RunRequest``
    does: the build stage writes files and can run shell, so ``write_region``, the allowlist and
    the posture all govern something real here, and a field named ``max_steps`` must not mean two
    different things on two routes.
    """

    task: str
    verify: str | None = None
    """The shell command that judges the build (exit 0 == success), run in the workspace.

    None is allowed and is not neutral: without it the test stage reports "verified" on the
    strength of the agent's own account of its work. The route says which of the two happened
    before any work starts, in its own frame, rather than leaving it to be inferred from a form
    field somebody left blank."""

    workspace: str | None = None
    model: str | None = None
    max_attempts: int = 2
    """The verify-or-revert budget for the build stage."""


def register_lifecycle_api(
    app: FastAPI,
    guard: params.Depends,
    workspace: Path,
    settings: Settings,
    *,
    live_settings: Callable[[], Settings] | None = None,
    backend_factory: Callable[[], Any] | None = None,
) -> None:
    """Mount ``/api/lifecycle``.

    ``backend_factory`` is injectable for the same reason the run endpoint's agent factory is:
    this path is worth testing without a provider key, and a module-level import of the gateway
    would make that impossible.
    """
    read_settings = live_settings or (lambda: settings)

    @app.post("/api/lifecycle", dependencies=[guard], responses=SSE_RESPONSE)
    async def lifecycle_stream(req: LifecycleRunIn) -> EventSourceResponse:
        """Run one task through the four stages, reporting each as it lands.

        SAFETY POSTURE: identical to ``POST /api/runs`` — the build writes files inside the
        workspace and, if given, runs the caller's verify command there. Behind the bearer guard
        and the loopback bind, through the governed registry, and never outside the workspace.
        """
        if not req.task.strip():
            raise HTTPException(status_code=400, detail="no task")
        # Resolved before the stream opens. Once the SSE response is handed back the status is
        # already 200, and a missing folder could only arrive as an error frame — a failure dressed
        # up as a run that started.
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else workspace
        if not ws.is_dir():
            raise HTTPException(status_code=400, detail=f"no such folder: {ws}")

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        run_id = uuid.uuid4().hex
        stop = threading.Event()
        _lifecycle_cancels[run_id] = stop

        def emit(event: str, payload: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        emit("run", {"run_id": run_id, "task": req.task, "workspace": str(ws)})

        # What is about to judge this run, said BEFORE it starts. The same honesty `/api/runs`
        # already prints: "no verify command — this build is judged by a model reading its own
        # answer" has always been true whenever the box was empty, and an interface that does not
        # say so lets an approving paragraph pass for a passing test.
        from chimera.api.app import resolve_verify

        verify_cmd, verify_src = resolve_verify(req.verify, ws)
        emit("verify", {"command": verify_cmd or "", "source": verify_src})

        def work() -> None:
            try:
                from chimera.orchestration.lifecycle import StageResult, lifecycle_crew
                from chimera.providers import LLMGateway

                live = read_settings()
                gateway = backend_factory() if backend_factory is not None else LLMGateway()
                steps = resolve_steps(req.max_steps)
                registry, _ledger = assemble_registry(
                    req, ws, live, gateway, steps=steps, surface="api:lifecycle"
                )

                def on_stage(stage: StageResult) -> None:
                    emit(
                        "stage",
                        {
                            "name": stage.name,
                            # Bounded: a build's answer can be long, and a stage frame is a
                            # progress report, not the artefact. The `done` frame carries the
                            # answer the caller keeps.
                            "output": (stage.output or "")[:4000],
                            "passed": stage.passed,
                        },
                    )

                crew = lifecycle_crew(
                    gateway,
                    workspace=ws,
                    verify=verify_cmd,
                    model=req.model,
                    max_steps=steps,
                    max_build_attempts=req.max_attempts,
                    registry=registry,
                    on_stage=on_stage,
                    should_stop=stop.is_set,
                )
                result = crew.run(req.task)
                emit(
                    "done",
                    {
                        "success": result.success,
                        "answer": (result.answer or "")[:4000],
                        # A stop is not a failure. Reporting one as the other tells somebody their
                        # code did not pass when nothing ever tested it.
                        "cancelled": result.cancelled,
                    },
                )
            except Exception as exc:  # noqa: BLE001 — surfaced to the client as an error frame
                _log.warning("lifecycle run failed: %s", exc)
                emit("error", {"message": "the run failed"})
            finally:
                _lifecycle_cancels.pop(run_id, None)
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=work, daemon=True).start()

        async def events() -> AsyncIterator[dict[str, str]]:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield {"event": event, "data": json.dumps(payload)}

        return EventSourceResponse(events())

    @app.post("/api/lifecycle/{run_id}/cancel", dependencies=[guard], response_model=CancelOut)
    def cancel_lifecycle(run_id: str) -> dict[str, Any]:
        """Stop a run between stages. An unknown id is ``{ok: false}`` with a 200, not a 404 —
        a run that already ended is exactly the state a stale Stop click lands on."""
        event = _lifecycle_cancels.get(run_id)
        if event is None:
            return {"ok": False}
        event.set()
        return {"ok": True}
