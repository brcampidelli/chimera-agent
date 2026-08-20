"""HTTP surface for the hierarchical orchestrator — the plan, the run, and what it cost.

The orchestration package has been complete and tested for a long time and had no way in from
outside a terminal: no route, no screen. This mounts the smallest set of endpoints that lets a UI
do the whole loop honestly — *see the plan before spending, run it, watch each worker, stop it,
and read what it saved*.

Two shapes, deliberately different:

- ``POST /api/orchestration/preview`` is a plain JSON call. It answers "what would happen", and
  the answer arrives at once because there is nothing to watch.
- ``POST /api/orchestration/hierarchy`` is SSE, following ``POST /api/agents`` frame for frame: a
  worker thread does the work and pushes frames onto an ``asyncio.Queue`` through
  ``loop.call_soon_threadsafe``. That bridge is the reason the orchestrator's event sink does not
  need a lock of its own.

Not in ``features.py`` because that module's own docstring excludes token-spending paths, and not
in ``app.py`` because that file is already 105KB.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, params
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from chimera.telemetry import get_logger
from chimera.orchestration.events import OrchEvent

if TYPE_CHECKING:
    from chimera.config import Settings

_log = get_logger("api.orchestration")

#: Cooperative stop flags, keyed by run id. Same shape and same lifecycle as ``_agents_cancels``
#: in ``app.py``: registered before the work starts, popped in the worker's ``finally``.
_orch_cancels: dict[str, threading.Event] = {}

#: The hierarchy's own cap. ``EffortPolicy`` already limits workers per task; this stops a request
#: from asking for a pool far larger than the subtasks a decomposition will ever produce.
_MAX_WORKERS = 8


# ---------------------------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------------------------


class HierarchyPreviewIn(BaseModel):
    task: str
    max_workers: int = 4
    budget: int | None = None


class HierarchyRunIn(BaseModel):
    """What to run, and the ceilings it runs under.

    This deliberately does NOT inherit ``CodeSeams``. The hierarchy's workers are tool-free —
    ``_run_one`` builds a ``RoleAgent`` with no registry at all — so posture, write-region and the
    tool allow/deny lists would have nothing to govern here. Advertising a governance surface that
    governs nothing is worse than not offering it: a caller sets ``write_region`` on a fan-out,
    reads no error, and concludes the field works. ``CrewRunIn`` will inherit it, because those
    workers really do write files.
    """

    task: str
    max_workers: int = 4
    budget: int | None = Field(default=None, description="Token budget per delegation.")
    verifier_model: str | None = None
    fuse: bool = True
    max_usd: float | None = Field(
        default=None,
        description=(
            "Ceiling for the whole run. The token budget is per delegation and says nothing about "
            "money; a fan-out spends a top-model decompose, N mid-model workers and a synthesis."
        ),
    )


class OrchCancelOut(BaseModel):
    ok: bool
    cancelled: bool


# ---------------------------------------------------------------------------------------------
# response bodies — concrete, because dict[str, object] does not survive the schema
# ---------------------------------------------------------------------------------------------


class HierarchyPreviewOut(BaseModel):
    """The plan, with every key present rather than conditionally absent.

    ``dry_run`` returns ``dict[str, object]`` whose keys depend on the branch taken. That is fine
    for a console and useless for a typed client: without a concrete model here the generated
    TypeScript would be ``Record<string, unknown>``, and the drift gate that exists to keep the UI
    and the API in step would be guarding nothing.
    """

    shape: str
    profitable_estimate: bool
    estimate_margin: float
    would_fall_back: bool
    fell_back_reason: str = Field(
        default="",
        description="Machine-readable: shape | unprofitable. Empty when the run would fan out.",
    )
    subtasks: list[str] = Field(default_factory=list)
    workers: int = 0
    budget_per_worker: int = 0
    sources: int = 0
    decompose_spent: bool = Field(
        default=False,
        description=(
            "Whether producing this plan cost a model call. True on the fan-out path, where the "
            "top model really did decompose the task. The preview spends no WORKER tokens, which "
            "is not the same as spending nothing, and a UI that says 'free' is lying on one of "
            "the two branches."
        ),
    )


class DelegationSummaryOut(BaseModel):
    """``summarize_delegations`` with its conditional keys made explicit as nulls.

    Null is not zero here and the distinction is the point: ``usd_saving=None`` means the receipts
    carry no price, while ``0.0`` would claim the hierarchy saved nothing. A display that renders
    the first as ``$0.00`` invents a measurement.
    """

    n: int = 0
    by_tier: dict[str, int] = Field(default_factory=dict)
    measured_tokens: int = 0
    measured_usd: float | None = None
    priced_n: int = 0
    estimated_n: int = 0
    counterfactual_n: int | None = None
    counterfactual_tokens: int | None = None
    paired_measured_tokens: int | None = None
    token_saving: int | None = None
    counterfactual_usd: float | None = None
    paired_measured_usd: float | None = None
    usd_saving: float | None = None


class DelegationsOut(BaseModel):
    summary: DelegationSummaryOut


# --- the SSE frame shapes, published so they reach OpenAPI and the generated types -------------


class ClassifiedOut(BaseModel):
    shape: str = ""
    sources: int = 0


class SubtaskOut(BaseModel):
    task_id: str = ""
    objective: str = ""
    output_format: str = ""
    boundaries: str = ""


class DecomposedOut(BaseModel):
    specs: list[SubtaskOut] = Field(default_factory=list)
    overhead_tokens: int = 0


class WorkerStartedOut(BaseModel):
    task_id: str = ""
    objective: str = ""
    tier: str = ""
    model: str = ""
    max_tokens: int = 0


class WorkerVerifiedOut(BaseModel):
    task_id: str = ""
    stage: str = ""
    reasked: bool = False
    tokens: int = 0
    summary_chars: int = 0
    evidence_refs: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class WorkerRejectedOut(BaseModel):
    task_id: str = ""
    reason: str = Field(default="", description="no_output | verifier | deadline")
    stage: str = ""
    detail: str = ""
    tokens: int = 0


class FellBackOut(BaseModel):
    shape: str = ""
    reason: str = Field(
        default="", description="shape | unprofitable | decompose_failed | workers_failed"
    )


class HierarchyDoneOut(BaseModel):
    shape: str = ""
    fell_back: bool = False
    cancelled: bool = False
    envelopes: int = 0
    receipts: int = 0
    total_tokens: int | None = None
    counterfactual_tokens: int | None = None
    answer: str = ""


class OrchestrationFramesOut(BaseModel):
    """Every SSE payload this module can emit, in one shape a schema dump can see.

    An SSE endpoint cannot declare a ``response_model``, so without this the frame payloads never
    reach OpenAPI and the desktop app would hand-write the types — which is exactly the drift the
    generated client exists to prevent. Same trick, same reason, as ``GET /api/agents/schema``.
    """

    classified: ClassifiedOut = Field(default_factory=ClassifiedOut)
    decomposed: DecomposedOut = Field(default_factory=DecomposedOut)
    worker_started: WorkerStartedOut = Field(default_factory=WorkerStartedOut)
    worker_verified: WorkerVerifiedOut = Field(default_factory=WorkerVerifiedOut)
    worker_rejected: WorkerRejectedOut = Field(default_factory=WorkerRejectedOut)
    fell_back: FellBackOut = Field(default_factory=FellBackOut)
    done: HierarchyDoneOut = Field(default_factory=HierarchyDoneOut)


# ---------------------------------------------------------------------------------------------


def _preview_dict(plan: dict[str, Any], *, sources: int, spent: bool) -> dict[str, Any]:
    """Fill in the keys ``dry_run`` leaves out, so the response has one shape on every branch."""
    fell_back = bool(plan.get("would_fall_back"))
    reason = ""
    if fell_back:
        reason = "shape" if plan.get("shape") != "parallel_read" else "unprofitable"
    return {
        "shape": str(plan.get("shape", "")),
        "profitable_estimate": bool(plan.get("profitable_estimate", False)),
        "estimate_margin": float(plan.get("estimate_margin", 0.0) or 0.0),
        "would_fall_back": fell_back,
        "fell_back_reason": reason,
        "subtasks": list(plan.get("subtasks", []) or []),
        "workers": int(plan.get("workers", 0) or 0),
        "budget_per_worker": int(plan.get("budget_per_worker", 0) or 0),
        "sources": sources,
        "decompose_spent": spent,
    }


def register_orchestration_api(
    app: FastAPI,
    guard: params.Depends,
    workspace: Path,
    settings: Settings,
    *,
    live_settings: Callable[[], Settings] | None = None,
    backend_factory: Callable[[], Any] | None = None,
) -> None:
    """Mount the orchestration routes.

    ``backend_factory`` is injectable for the same reason ``solve_agent_factory`` is on the run
    endpoint: these paths are worth testing without a provider key, and a module-level import of
    the gateway would make that impossible.
    """
    read_settings = live_settings or (lambda: settings)

    def _build(req: HierarchyRunIn | HierarchyPreviewIn, **extra: Any) -> Any:
        from chimera.evolution import build_evolution_context
        from chimera.fusion import FusionEngine
        from chimera.orchestration.artifacts import ArtifactStore
        from chimera.orchestration.budget import EffortPolicy
        from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
        from chimera.providers import LLMGateway

        live = read_settings()
        ladder = live.tier_ladder()
        gateway = backend_factory() if backend_factory is not None else LLMGateway()
        fuse = getattr(req, "fuse", True)
        return HierarchicalOrchestrator(
            gateway,
            weak_model=ladder.weak,
            mid_model=ladder.mid,
            top_model=ladder.top,
            store=ArtifactStore(Path(live.home) / "artifacts"),
            verifier_model=getattr(req, "verifier_model", None),
            # Only when asked for. Fusion at synthesis is already conditional on the envelopes
            # actually conflicting; this is the outer switch, and a caller who turned it off
            # should not pay for a panel.
            fusion=FusionEngine(gateway) if fuse else None,
            receipts_path=Path(live.home) / "delegations.jsonl",
            config=HierarchyConfig(
                max_workers=max(1, min(_MAX_WORKERS, req.max_workers)),
                fuse_final=fuse,
                effort=EffortPolicy(complex_budget=req.budget or live.delegation_budget),
            ),
            evolution=build_evolution_context(
                live, gateway, None, home=live.home,
                evolve_skills=False, include_memory=True,
            ),
            **extra,
        )

    @app.post(
        "/api/orchestration/preview",
        dependencies=[guard],
        response_model=HierarchyPreviewOut,
    )
    async def preview_endpoint(req: HierarchyPreviewIn) -> dict[str, Any]:
        """What the orchestrator would do with this task, before any worker runs.

        Honest about its own cost: on the fan-out branch ``dry_run`` really does call the top
        model to decompose, so ``decompose_spent`` comes back true. The claim this endpoint
        supports is "no WORKER tokens", never "free".
        """
        if not req.task.strip():
            raise HTTPException(status_code=400, detail="no task")
        from chimera.orchestration.hierarchy import classify_task, count_sources

        orchestrator = _build(req)
        # Off the event loop: on the fan-out branch this makes a model call, and a coroutine that
        # blocks the loop stalls every other request the desktop app has in flight.
        plan = await run_in_threadpool(orchestrator.dry_run, req.task)
        return _preview_dict(
            plan,
            sources=count_sources(req.task),
            spent=classify_task(req.task) == "parallel_read",
        )

    @app.get(
        "/api/orchestration/schema", dependencies=[guard], response_model=OrchestrationFramesOut
    )
    def orchestration_schema_endpoint() -> dict[str, Any]:
        # An empty, side-effect-free shape sample so the frame payloads reach OpenAPI. The real
        # frames arrive over the SSE endpoint below; nothing here is fabricated data.
        return OrchestrationFramesOut().model_dump()

    @app.post("/api/orchestration/hierarchy", dependencies=[guard])
    async def hierarchy_stream(req: HierarchyRunIn) -> EventSourceResponse:
        """Run a hierarchy, streamed frame by frame.

        The worker thread owns the orchestrator; this coroutine owns the queue. Frames cross the
        boundary through ``call_soon_threadsafe``, which is what makes the orchestrator's own sink
        safe to call from N worker threads without a lock.
        """
        if not req.task.strip():
            raise HTTPException(status_code=400, detail="no task")
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        run_id = uuid.uuid4().hex
        stop = threading.Event()
        _orch_cancels[run_id] = stop

        seq = 0
        seq_lock = threading.Lock()

        def emit(event: str, payload: dict[str, Any]) -> None:
            # The sequence number is stamped HERE, under a lock, because this is the only point in
            # the system where a total order legitimately exists. The orchestrator runs N workers
            # in parallel and has no order to offer; a consumer that needs one — to replay after a
            # reload without duplicating what it already has — gets it from the single writer.
            nonlocal seq
            with seq_lock:
                seq += 1
                numbered = {**payload, "seq": seq}
            loop.call_soon_threadsafe(queue.put_nowait, (event, numbered))

        # Sent before any work, so a Stop control can target this run from the first moment.
        emit("run", {"run_id": run_id, "task": req.task})

        def on_event(event: OrchEvent) -> None:
            emit(event.kind, {"task_id": event.task_id, "text": event.text, **event.data})

        def work() -> None:
            try:
                orchestrator = _build(req, on_event=on_event, should_stop=stop.is_set)
                orchestrator.run(req.task)
            except Exception as exc:  # noqa: BLE001 -- surfaced to the client as an error frame
                _log.warning("hierarchy run failed: %s", exc)
                emit("error", {"message": "the run failed"})
            finally:
                _orch_cancels.pop(run_id, None)
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=work, daemon=True).start()

        async def events() -> Any:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield {"event": event, "data": json.dumps(payload)}

        return EventSourceResponse(events())

    @app.post(
        "/api/orchestration/runs/{run_id}/cancel",
        dependencies=[guard],
        response_model=OrchCancelOut,
    )
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Ask a run to stop at its next boundary.

        Cooperative, and worth being precise about in the UI: the flag is read between units of
        work, so a model call already in flight finishes and is charged. What it does buy is every
        call that had not started — the queued workers, the verifier's re-ask, the synthesis.

        An unknown or finished id is ``{ok: false}`` with a 200, never a 404: a run that already
        ended is exactly the state a stale Stop click lands in.
        """
        stop = _orch_cancels.get(run_id)
        if stop is None:
            return {"ok": False, "cancelled": False}
        fresh = not stop.is_set()
        stop.set()
        return {"ok": True, "cancelled": fresh}

    @app.get(
        "/api/orchestration/delegations", dependencies=[guard], response_model=DelegationsOut
    )
    def delegations_endpoint() -> dict[str, Any]:
        """What delegating has measured against its counterfactual, over the whole ledger.

        Scoped to the home ledger with no path parameter. The CLI takes ``--path`` because it
        already runs as the user; an arbitrary path behind an HTTP guard is a file read.
        """
        from chimera.orchestration.receipts import load_delegations, summarize_delegations

        path = Path(read_settings().home) / "delegations.jsonl"
        summary = summarize_delegations(load_delegations(path)) if path.exists() else {"n": 0}
        return {"summary": DelegationSummaryOut(**summary).model_dump()}
