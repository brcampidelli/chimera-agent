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

from chimera.api.code_api import CodeSeams
from chimera.orchestration import runlog
from chimera.orchestration.budget import SpendExceeded
from chimera.orchestration.events import OrchEvent
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.config import Settings

_log = get_logger("api.orchestration")

#: Cooperative stop flags, keyed by run id. Same shape and same lifecycle as ``_agents_cancels``
#: in ``app.py``: registered before the work starts, popped in the worker's ``finally``.
_orch_cancels: dict[str, threading.Event] = {}

#: The hierarchy's own cap. ``EffortPolicy`` already limits workers per task; this stops a request
#: from asking for a pool far larger than the subtasks a decomposition will ever produce.
_MAX_WORKERS = 8

#: What a hierarchy worker may do: open things, and nothing else.
#:
#: Not configurable by the request, deliberately. Two of the three families left out would each
#: break something this design depends on. WRITE tools would put N parallel workers in one folder
#: with no worktree between them — the failure `IsolatedCrew` exists to prevent, and the one the
#: prior art this screen was modelled on never solved. NETWORK tools would pull untrusted content
#: into a fan-out whose taint ledger is per-worker, so what one worker read could reach the
#: synthesis through another's summary.
#:
#: Read-heavy is not a limitation of the hierarchy, it is the thing the hierarchy measured a win
#: on: the (D-1)/D saving comes from each worker seeing ONE source instead of every worker seeing
#: all of them. Tools make that sharper — a worker now fetches its own source rather than being
#: handed every document up front.
_WORKER_TOOLS = ("read_file", "read_document", "list_dir", "glob", "grep", "map")

#: Plans the preview produced, keyed by the id it handed back. A run that names one executes THAT
#: decomposition instead of asking the model for a second one.
#:
#: This exists because the two calls were independent and the model runs at a non-zero temperature:
#: a preview could show one subtask and the run then split the same task into three. Approving a
#: plan has to mean something. It also saves the second decompose call, so the honest fix is the
#: cheaper one.
#:
#: In memory and bounded. A plan is a handful of strings, it is worth nothing after its run, and a
#: restart losing it is harmless — an unknown id falls back to decomposing, which is exactly what
#: happened before this existed.
_plans: dict[str, Any] = {}
_MAX_PLANS = 32


# ---------------------------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------------------------


class HierarchyPreviewIn(BaseModel):
    task: str
    workspace: str | None = None
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
    workspace: str | None = Field(
        default=None,
        description=(
            "Which folder the workers read. Absent, they read the app's own workspace — which, "
            "on a screen that inherits its project from the Code tab, would be a different "
            "folder than the one on screen, and nothing would say so."
        ),
    )
    plan_id: str = Field(
        default="",
        description=(
            "A plan id from /preview. Given one, the run executes that decomposition rather than "
            "producing a new one — so the plan a person approved is the plan that runs. An "
            "unknown or expired id decomposes afresh rather than failing."
        ),
    )
    max_workers: int = 4
    budget: int | None = Field(default=None, description="Token budget per delegation.")
    verifier_model: str | None = None
    fuse: bool = True
    max_usd: float | None = Field(
        default=None,
        # Matching `CodeSeams.max_usd`. Zero and negatives are not "no ceiling" — `SpendBudget`
        # rejects them, and accepting one here would have refused the run at construction instead
        # of at validation, which is a 500 where a 422 belongs.
        gt=0,
        description=(
            "Ceiling for the whole run. The token budget is per delegation and says nothing about "
            "money; a fan-out spends a top-model decompose, N mid-model workers and a synthesis."
        ),
    )


class CrewWorkerIn(BaseModel):
    """One member of the crew: a name and what it is told to do.

    The name is what the run is reported by — it becomes the `task_id` on every frame — so it has
    to be distinct and readable. The instruction is the whole difference between workers: they all
    receive the SAME task, and the role is what makes three attempts at it three different attempts
    rather than one attempt run three times.
    """

    name: str
    instruction: str


class CrewRunIn(CodeSeams):
    """A crew: N roles, one task, one worktree each, and a check that decides who lands.

    Inherits `CodeSeams` — the promise `HierarchyRunIn` made when it declined to. These workers
    write files, so posture, write region and the tool lists have something real to govern here.

    On `verify`: without it, every worker that does not crash is merged, and since they all attack
    the same task they tend to touch the same files — where one-file-one-owner then means NOBODY
    lands. A crew without a check is a crew that usually produces nothing, so the screen treats
    this field as the point rather than as an option.
    """

    task: str
    workers: list[CrewWorkerIn]
    workspace: str | None = None
    verify: str | None = Field(
        default=None,
        description=(
            "Shell command run in each worker's own worktree; exit 0 merges it. Without one, "
            "every worker that did not crash merges — and workers that touched the same file all "
            "lose to the conflict rule."
        ),
    )
    max_workers: int = 4
    synthesize: bool = Field(
        default=False,
        description="Fold the merged workers' answers into one report. Costs a top-model call.",
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
    plan_id: str = Field(
        default="",
        description=(
            "Hand this back on the run to execute THIS decomposition. Empty on the fallback "
            "branch, where there is no decomposition to keep."
        ),
    )
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


class CrewWorkerStartedOut(BaseModel):
    task_id: str = ""
    workspace: str = Field(
        default="", description="The worktree this worker writes in — its own checkout."
    )
    instruction: str = ""


class CrewWorkerVerifiedOut(BaseModel):
    task_id: str = ""
    verified_by: str = Field(
        default="", description="The check that passed. Empty when the run had no check at all."
    )
    answer_chars: int = 0


class CrewWorkerRejectedOut(BaseModel):
    task_id: str = ""
    reason: str = Field(default="", description="verify | cancelled")
    detail: str = Field(default="", description="What the failing check printed.")


class ConflictOut(BaseModel):
    path: str = Field(
        default="",
        description="A file two workers both changed. NEITHER version was merged.",
    )


class CrewWorkerProducedOut(BaseModel):
    task_id: str = ""
    files: list[str] = Field(
        default_factory=list,
        description="The files this worker wrote that actually reached the workspace.",
    )
    lost: list[str] = Field(
        default_factory=list,
        description=(
            "The files it wrote that did NOT — discarded by the check, or contested by another "
            "worker. Reported because a discarded attempt leaves nothing else behind: the "
            "worktree is removed when the run ends."
        ),
    )
    answer: str = Field(default="", description="The worker's own report, truncated.")
    landed: bool = Field(
        default=False,
        description="Whether these files were merged. False means the work existed and was thrown away.",
    )


class CrewDoneOut(BaseModel):
    merged: int = 0
    conflicts: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    answer: str = ""
    is_repo: bool = Field(
        default=False,
        description=(
            "False means the workers shared one folder because this is not a git repository — "
            "no isolation, and conflicts undetectable. The screen has to be able to say so."
        ),
    )


class ApproachOut(BaseModel):
    id: str = ""
    instruction: str = Field(
        default="",
        description=(
            "The system prompt this approach sends, verbatim. Untranslated on purpose: a "
            "translated prompt is a different prompt, and the screen shows what is actually sent."
        ),
    )


class OrchRunSummaryOut(BaseModel):
    """One past run, as much as can be known without reading its whole transcript."""

    run_id: str
    task: str
    kind: str  # "hierarchy" | "crew"
    started: float  # epoch seconds, from the transcript's own mtime
    frames: int
    done: bool
    """Read from the LAST frames, not from the file merely ending. A run killed with the process
    leaves a transcript that stops, and calling that finished would turn a crash into a completed
    run in the one list built to find them again."""

    orphaned: bool = False
    """Not finished, and nothing in this process is running it either.

    Computed, never stored: a run is orphaned when its transcript says unfinished and its id is
    absent from the live cancel registry, which every in-flight run is entered into for the whole
    of its life. That combination means the thread is gone — the process was restarted, or it died.

    Without it, `done: false` covered two states a reader has to tell apart: still working, and
    abandoned. One measured run sat at five frames for twenty-two minutes with every worker
    process gone, and nothing on the wire distinguished it from one that was still thinking."""


class OrchRunsOut(BaseModel):
    runs: list[OrchRunSummaryOut]  # newest first


#: What the SSE client lifts out of a frame's payload and onto the frame itself. Everything else
#: rides under ``data``. Kept as one constant because the split has to match ``api.ts`` exactly.
_FRAME_TOP_LEVEL = ("seq", "task_id", "text")


def _as_stream_frame(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One persisted line in the shape the live stream delivers, or None if it is not a frame.

    The transcript is written as ``{"event": event, **payload}`` — flat, and keyed ``event``. The
    stream delivers ``{seq, kind, task_id, text, data}``, because the client lifts three fields out
    and nests the rest. The desktop's reducer switches on ``kind``, so handing back the raw line
    gave it ``kind is undefined`` and it matched nothing: a replayed run drew its stepper, which
    renders unconditionally, and **no worker cards and no answer**.

    That made the whole persistence feature a shell. Transcripts were written from rc11 onward, and
    reading one back produced an empty run — which nobody saw, because until the run list was wired
    the only way to reach a replay was to reload the page mid-run.

    Normalised here rather than on disk. The file is also a debugging artefact and every transcript
    already written would be stranded by a format change; converting on the way out costs nothing
    and makes the contract the one the client was always promised — *this endpoint returns what the
    stream returns*.

    A line with no ``event`` is dropped rather than given an empty ``kind``. The reducer would
    accept such a frame and ignore it, which turns a damaged transcript into a silently short one.
    """
    event = raw.get("event")
    if not event:
        return None
    data = {k: v for k, v in raw.items() if k != "event" and k not in _FRAME_TOP_LEVEL}
    return {
        "seq": int(raw.get("seq") or 0),
        "kind": str(event),
        # Coerced rather than passed through: an older transcript may not carry these at all, and a
        # replay that raised on one frame would lose the run it was meant to preserve.
        "task_id": str(raw.get("task_id") or ""),
        "text": str(raw.get("text") or ""),
        "data": data,
    }


class OrchFramesOut(BaseModel):
    """A run's transcript from ``since`` onward, in the order its single writer stamped."""

    run_id: str
    frames: list[dict[str, Any]]
    #: The highest `seq` in `frames`, or the `since` that was asked for when there are none. A
    #: client stores this and asks again from it, which is what makes a second replay cheap.
    seq: int


class ApproachesOut(BaseModel):
    approaches: list[ApproachOut] = Field(default_factory=list)
    default: list[str] = Field(
        default_factory=list,
        description="The ids a fresh crew starts with — the widest pair in the catalogue.",
    )


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
    crew_worker_started: CrewWorkerStartedOut = Field(default_factory=CrewWorkerStartedOut)
    crew_worker_verified: CrewWorkerVerifiedOut = Field(default_factory=CrewWorkerVerifiedOut)
    crew_worker_rejected: CrewWorkerRejectedOut = Field(default_factory=CrewWorkerRejectedOut)
    crew_worker_produced: CrewWorkerProducedOut = Field(default_factory=CrewWorkerProducedOut)
    conflict: ConflictOut = Field(default_factory=ConflictOut)
    crew_done: CrewDoneOut = Field(default_factory=CrewDoneOut)


# ---------------------------------------------------------------------------------------------


def _resolve_workspace(requested: str | None, fallback: Path) -> Path:
    """The folder a run works in, or a 400 saying which one was not there.

    `Path.resolve()` does not check anything: given a path this OS cannot parse it produces a
    plausible-looking absolute path by joining it to the process directory. A Windows path handed
    to a Linux backend became
    a path like `/opt/chimera/` with the Windows path glued onto the end, so the crew ran
    against a directory that did not exist, and every
    worker was reported as "your check failed" — sending someone to look for a bug in code that
    was never read. Fail here, naming the path, instead of three layers down wearing a disguise.
    """
    if not requested:
        return fallback
    candidate = Path(requested).expanduser()
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail=f"workspace not found: {requested}")
    return candidate.resolve()


def _remember(plan: Any) -> str:
    """Keep a decomposition so the run can execute the plan that was shown, and bound the store."""
    if not plan.specs:
        return ""
    plan_id = uuid.uuid4().hex
    _plans[plan_id] = plan
    # Oldest out first. dicts keep insertion order, so this is the plan least likely to still be
    # sitting in front of somebody.
    while len(_plans) > _MAX_PLANS:
        _plans.pop(next(iter(_plans)))
    return plan_id


def _preview_dict(plan: Any, *, sources: int, plan_id: str) -> dict[str, Any]:
    """One response shape on every branch, whichever way the plan went."""
    reason = ""
    if plan.would_fall_back:
        reason = "shape"
    elif not plan.specs:
        reason = "unprofitable"
    return {
        "shape": plan.shape,
        "profitable_estimate": plan.profitable,
        "estimate_margin": plan.margin,
        "would_fall_back": plan.would_fall_back,
        "fell_back_reason": reason,
        "subtasks": [spec.objective for spec in plan.specs],
        "workers": plan.workers,
        "budget_per_worker": plan.budget_per_worker,
        "sources": sources,
        "plan_id": plan_id,
        "decompose_spent": plan.decompose_spent,
    }


def _owner_instructions(home: object) -> str:
    """The owner's rendered instructions, or "" if they set none or the file cannot be read.

    Every other surface that answers a person loads these — Code, Runs, chat, the bots. This one
    did not, and because the same rendered block carries the "always answer in {language}" line,
    an owner running the app in Portuguese got English out of the Orchestration tab.

    Best-effort by design: a malformed identity file must not take an orchestration run down.
    """
    from pathlib import Path as _Path

    try:
        from chimera.core.instructions import load as load_identity
        from chimera.core.instructions import render as render_identity

        return render_identity(load_identity(_Path(str(home))))
    except Exception:  # noqa: BLE001 -- see the docstring
        return ""


def _record_run_spend(home: object, run_id: str, outcome: object) -> None:
    """Put an orchestration run on the Cost screen, cancelled or not.

    The receipts already carry per-delegation tokens and dollars; nothing ever moved them into the
    usage log, so the one screen that answers "what has this cost me" was blind to the most
    expensive thing the app can start.
    """
    from pathlib import Path as _Path

    from chimera.api.usage import record_spend

    receipts = list(getattr(outcome, "receipts", None) or [])
    if not receipts:
        return
    priced = [r.usd for r in receipts if getattr(r, "usd", None) is not None]
    record_spend(
        _Path(str(home)),
        session_id=f"orchestration:{run_id}",
        model=str(getattr(receipts[0], "model", "") or ""),
        prompt_tokens=sum(int(getattr(r, "prompt_tokens", 0) or 0) for r in receipts),
        completion_tokens=sum(int(getattr(r, "completion_tokens", 0) or 0) for r in receipts),
        # None when ANY delegation could not be priced: a partial sum presented as the total is the
        # failure this whole accounting path exists to avoid.
        usd=round(sum(priced), 6) if len(priced) == len(receipts) else None,
        route_kind="hierarchy",
    )


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

    def _worker_tools(gateway: Any, ws: Path) -> Any:
        """A read-only registry for one worker, built through the governed path.

        Via ``assemble_registry`` rather than ``default_registry``: the AST gate in
        `test_governed_surfaces` exists because an HTTP surface that builds its own registry
        skips the trust kernel and the taint ledger, and this is an HTTP surface.

        The seams are fixed here rather than taken from the request. `HierarchyRunIn` still does
        not inherit `CodeSeams`, and the reason has inverted rather than disappeared: it used to be
        that there was nothing to govern, and now it is that the one safe configuration is the only
        one on offer. A caller who could set `allow_tools` could hand `write_file` to eight
        concurrent workers pointed at the same folder.
        """
        from chimera.api.code_api import CodeSeams, assemble_registry

        seams = CodeSeams(allow_tools=list(_WORKER_TOOLS))
        registry, _ledger = assemble_registry(
            seams, ws, read_settings(), gateway, steps=6, surface="api:hierarchy"
        )
        return registry

    def _build(req: HierarchyRunIn | HierarchyPreviewIn, **extra: Any) -> Any:
        from chimera.evolution import build_evolution_context
        from chimera.fusion import FusionEngine
        from chimera.orchestration.artifacts import ArtifactStore
        from chimera.orchestration.budget import (
            EffortPolicy,
            SpendBudget,
            SpendCappedBackend,
        )
        from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
        from chimera.providers import LLMGateway

        live = read_settings()
        ladder = live.tier_ladder()
        gateway = backend_factory() if backend_factory is not None else LLMGateway()
        # The request's folder, or the app's. Resolved once, here, so the preview and the run
        # cannot end up rooted differently.
        ws = _resolve_workspace(req.workspace, workspace)
        fuse = getattr(req, "fuse", True)
        # Declared on the request, documented as "ceiling for the whole run", and read by nothing
        # until now: the field reached the schema and the TypeScript client and stopped there. The
        # plan carried it as risk #1 — this route spends a top-model decompose, N mid-model workers
        # and a synthesis, and `budget` caps TOKENS PER DELEGATION, which says nothing about money.
        #
        # Around the gateway rather than through `AgentConfig.max_usd`, because that builds a
        # SpendBudget per `Agent.run`: N workers would get N separate ceilings and the decompose
        # and synthesis would get none. One wrapper, every call.
        capped: Any = gateway
        max_usd = getattr(req, "max_usd", None)
        if max_usd:
            capped = SpendCappedBackend(gateway, SpendBudget(max_usd))
        return HierarchicalOrchestrator(
            capped,
            weak_model=ladder.weak,
            mid_model=ladder.mid,
            top_model=ladder.top,
            store=ArtifactStore(Path(live.home) / "artifacts"),
            verifier_model=getattr(req, "verifier_model", None),
            # Only when asked for. Fusion at synthesis is already conditional on the envelopes
            # actually conflicting; this is the outer switch, and a caller who turned it off
            # should not pay for a panel.
            fusion=FusionEngine(capped) if fuse else None,
            receipts_path=Path(live.home) / "delegations.jsonl",
            config=HierarchyConfig(
                max_workers=max(1, min(_MAX_WORKERS, req.max_workers)),
                fuse_final=fuse,
                effort=EffortPolicy(complex_budget=req.budget or live.delegation_budget),
            ),
            identity=_owner_instructions(live.home),
            evolution=build_evolution_context(
                live, gateway, None, home=live.home,
                evolve_skills=False, include_memory=True,
            ),
            # A factory, so every worker gets its own registry and its own ledger rather than
            # sharing one across a thread pool.
            worker_tools=lambda: _worker_tools(gateway, ws),
            **extra,
        )

    @app.post(
        "/api/orchestration/preview",
        dependencies=[guard],
        response_model=HierarchyPreviewOut,
    )
    async def preview_endpoint(req: HierarchyPreviewIn) -> dict[str, Any]:
        """What the orchestrator would do with this task, before any worker runs.

        Honest about its own cost: on the fan-out branch this really does call the top model to
        decompose, so ``decompose_spent`` comes back true. The claim is "no WORKER tokens".

        The decomposition is KEPT, under ``plan_id``. Handing that id to the run makes it execute
        this split instead of asking for another one — the plan shown is the plan that runs, and
        the second decompose call is not paid for twice.
        """
        if not req.task.strip():
            raise HTTPException(status_code=400, detail="no task")
        from chimera.orchestration.hierarchy import count_sources

        orchestrator = _build(req)
        # Off the event loop: on the fan-out branch this makes a model call, and a coroutine that
        # blocks the loop stalls every other request the desktop app has in flight.
        plan = await run_in_threadpool(orchestrator.plan, req.task)
        return _preview_dict(
            plan, sources=count_sources(req.task), plan_id=_remember(plan)
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
        # Checked HERE, before the stream opens, and not where `_build` needs it: once the SSE
        # response has been handed back the status code is already 200, and a missing folder can
        # only arrive as an error frame — a failure dressed as a run that started.
        _resolve_workspace(req.workspace, workspace)
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
            # Numbered, persisted AND enqueued under one lock. The number alone is not enough: the
            # client's reducer drops a frame whose `seq` is not greater than the last it applied, so
            # two workers that stamp 4 and 5 and then hand them over in the other order lose card 4
            # entirely — silently, on the screen, with the run still reporting itself healthy.
            #
            # That window always existed and was one statement wide. Persisting the frame put a file
            # append inside it and made it happen: CI went red on 3.12 with `[1, 2, 3, 5, 4, ...]`.
            #
            # The cost is that emits serialise around a buffered local append, which is microseconds
            # and is already what the numbering does. `call_soon_threadsafe` appends to the loop's
            # callback queue in call order and the loop runs it FIFO, so calling it here is what
            # makes the stream's order the order the numbers claim.
            with seq_lock:
                seq += 1
                numbered = {**payload, "seq": seq}
                # A fan-out costs a top-model decompose, N workers and a synthesis, and until this
                # every frame of it existed only in the stream: close the app and the answer was
                # gone while the bill stayed.
                runlog.append(read_settings().home, run_id, event, numbered)
                loop.call_soon_threadsafe(queue.put_nowait, (event, numbered))

        # Sent before any work, so a Stop control can target this run from the first moment.
        emit("run", {"run_id": run_id, "task": req.task})

        def on_event(event: OrchEvent) -> None:
            emit(event.kind, {"task_id": event.task_id, "text": event.text, **event.data})

        def work() -> None:
            try:
                orchestrator = _build(req, on_event=on_event, should_stop=stop.is_set)
                approved = _plans.pop(req.plan_id, None) if req.plan_id else None
                if approved is not None and approved.specs:
                    # The decomposition a person looked at and said yes to. Popped rather than
                    # read: a plan is consumed by its run, and leaving it behind would let a
                    # second run silently reuse a split that was approved for the first.
                    outcome = orchestrator.run_prepared(
                        req.task, approved.specs, shape=approved.shape
                    )
                else:
                    # No id, or an id this process no longer has — decompose afresh. That is
                    # exactly the old behaviour, so a restart costs a model call, never an error.
                    outcome = orchestrator.run(req.task)
                # On the Cost screen, not only in the SSE stream that vanishes when the tab closes.
                # This path spends a top-model decompose plus N workers plus a synthesis and wrote
                # nothing to the usage log, so a screen reporting "the spend" left it out entirely —
                # including for a run the user cancelled, which is charged all the same.
                _record_run_spend(read_settings().home, run_id, outcome)
            except SpendExceeded as exc:
                # The one failure the caller ASKED for. `SpendBudget.blocked()` already says which
                # ceiling and how much of it was spent, and that sentence is the whole point of
                # setting one — "the run failed" would report a working cap as a fault.
                #
                # Nothing extra is charged getting here: the wrapper refuses BEFORE the call, so the
                # single-agent fallback the empty fan-out routes into never reaches a provider.
                _log.info("hierarchy run stopped on its spend ceiling: %s", exc)
                emit("error", {"message": str(exc)})
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


    @app.post("/api/orchestration/crew", dependencies=[guard])
    async def crew_stream(req: CrewRunIn) -> EventSourceResponse:
        """Run N roles against ONE task, each in its own worktree, and merge what passes.

        The other half of this screen. The hierarchy splits a task between workers who only read;
        a crew hands the SAME task to several workers who write, in separate checkouts, and lets a
        command decide which of them lands. That is the shape of work `classify_task` sends down
        the single-agent path today — anything with write intent — which in a coding tool is most
        of what anyone types.
        """
        if not req.task.strip():
            raise HTTPException(status_code=400, detail="no task")
        if not req.workers:
            raise HTTPException(status_code=400, detail="no workers")
        if len(req.workers) > _MAX_WORKERS:
            raise HTTPException(status_code=400, detail=f"too many workers (max {_MAX_WORKERS})")
        names = [w.name.strip() for w in req.workers]
        if len(set(names)) != len(names) or not all(names):
            # The name IS the routing key on every frame. Two workers called the same thing would
            # collapse into one card and report each other's results.
            raise HTTPException(status_code=400, detail="worker names must be distinct and non-empty")

        ws = _resolve_workspace(req.workspace, workspace)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        run_id = uuid.uuid4().hex
        stop = threading.Event()
        _orch_cancels[run_id] = stop

        seq = 0
        seq_lock = threading.Lock()

        def emit(event: str, payload: dict[str, Any]) -> None:
            nonlocal seq
            # One lock over all three, for the reason spelled out on the hierarchy's emit above: a
            # frame that arrives out of order is not reordered by the client, it is DROPPED.
            with seq_lock:
                seq += 1
                numbered = {**payload, "seq": seq}
                runlog.append(read_settings().home, run_id, event, numbered)
                loop.call_soon_threadsafe(queue.put_nowait, (event, numbered))

        emit("run", {"run_id": run_id, "task": req.task, "workspace": str(ws)})

        def on_event(event: OrchEvent) -> None:
            # Prefixed, because a crew worker and a hierarchy worker are not the same object and a
            # consumer must not have to guess which one a frame describes.
            name: str = event.kind
            if name.startswith("worker_") or name == "done":
                name = f"crew_{name}"
            emit(name, {"task_id": event.task_id, "text": event.text, **event.data})

        def work() -> None:
            try:
                from chimera.api.code_api import assemble_registry
                from chimera.governance.ledger import SharedTaint
                from chimera.orchestration.crew import IsolatedCrew, IsolatedWorker
                from chimera.orchestration.roles import Role, RoleAgent
                from chimera.providers import LLMGateway

                live = read_settings()
                gateway = backend_factory() if backend_factory is not None else LLMGateway()
                # ONE shared taint ledger for the whole crew, unlike `solve-batch`, which gives each
                # task its own. These workers collaborate on a single task and merge into a single
                # workspace, so untrusted content one of them read can reach the others through the
                # merge — the same distinction the CLI already draws between the two commands.
                shared = SharedTaint()

                def tools_for(worker_ws: Path) -> Any:
                    registry, _ledger = assemble_registry(
                        req, worker_ws, live, gateway,
                        steps=req.max_steps or 6, surface="api:crew", shared=shared,
                    )
                    return registry

                # `synthesize` was accepted, documented and dropped: the field reached the schema
                # and the TypeScript client, and nothing here ever read it. Everything downstream
                # was already in place — the crew emits the summary on its `done` frame, the reducer
                # stores it, `CrewRun` renders it — so the only missing link was the supervisor that
                # makes `IsolatedCrew` call `_synthesize` at all. Top tier, because folding N merged
                # reports into one is the reasoning step of the run, and the field's own description
                # already tells the caller it costs a top-model call.
                supervisor = (
                    RoleAgent(
                        Role(
                            "supervisor",
                            "You coordinate a team and write a single, unified final report from "
                            "the merged worker outputs. Be concise, and say plainly which workers "
                            "were rejected or left files in conflict.",
                            model=live.tier_ladder().top,
                        ),
                        gateway,
                    )
                    if req.synthesize
                    else None
                )
                crew = IsolatedCrew(
                    gateway,
                    [
                        IsolatedWorker(
                            role=Role(w.name, w.instruction, model=live.tier_ladder().mid),
                            tools=tools_for,
                            max_steps=req.max_steps or 6,
                        )
                        for w in req.workers
                    ],
                    supervisor=supervisor,
                    max_workers=max(1, min(_MAX_WORKERS, req.max_workers)),
                    on_event=on_event,
                    should_stop=stop.is_set,
                    identity=_owner_instructions(live.home),
                )
                crew.run(req.task, ws, verify=req.verify, timeout=live.batch_timeout)
            except Exception as exc:  # noqa: BLE001 -- surfaced to the client as an error frame
                _log.warning("crew run failed: %s", exc)
                emit("error", {"message": "the crew failed"})
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
        """Ask a run to stop, and stop waiting for it either way.

        Cooperative first: the flag is read between units of work, so a worker that is between
        steps returns a real outcome and the run can report WHY it stopped. A model call already in
        flight still finishes and is still charged — nothing can interrupt one.

        What changed is what happens when the flag cannot be heard. It used to be nothing: a worker
        stuck inside a model call never read it, and the batch went on waiting under a four-hour
        default. Measured on rc13 — three workers all produced correct work, one reported, the run
        sat at `done: false` for twenty-two minutes, and this endpoint answered
        `{"ok": true, "cancelled": true}` to a run it could not touch. The wait now gives an
        unresponsive unit a couple of seconds to leave cleanly and then abandons it, so the run
        concludes and the screen stops spinning. An abandoned worker produced no outcome, so its
        worktree is discarded rather than merged: stopping still cannot land half an edit.

        An unknown or finished id is ``{ok: false}`` with a 200, never a 404: a run that already
        ended is exactly the state a stale Stop click lands in.
        """
        stop = _orch_cancels.get(run_id)
        if stop is None:
            return {"ok": False, "cancelled": False}
        fresh = not stop.is_set()
        stop.set()
        return {"ok": True, "cancelled": fresh}

    @app.get("/api/orchestration/runs", dependencies=[guard], response_model=OrchRunsOut)
    def orch_runs_endpoint() -> dict[str, Any]:
        """The runs whose transcripts are still on disk, newest first.

        A fan-out costs a top-model decompose, N workers and a synthesis. Until these were persisted,
        closing the app threw the answer away and kept the bill — the cost was recorded and the
        product was not.
        """
        home = Path(read_settings().home)
        runlog.prune(home)
        return {
            "runs": [
                OrchRunSummaryOut(
                    **vars(s),
                    # Live registry, not the transcript: only this process knows which runs it is
                    # actually working on, and that is exactly the fact the file cannot carry.
                    orphaned=not s.done and s.run_id not in _orch_cancels,
                ).model_dump()
                for s in runlog.recent(home)
            ]
        }

    @app.get(
        "/api/orchestration/runs/{run_id}", dependencies=[guard], response_model=OrchFramesOut
    )
    def orch_run_frames_endpoint(run_id: str, since: int = 0) -> dict[str, Any]:
        """Everything after ``since``, so a reload replays only what it is missing.

        The frames go through the SAME reducer the live stream feeds, and that reducer ignores a
        `seq` it has already applied — which is what makes replay-then-live and live-only converge
        on one state instead of two.
        """
        raw = runlog.frames(Path(read_settings().home), run_id, since=since)
        frames = [f for f in (_as_stream_frame(line) for line in raw) if f is not None]
        highest = max((int(f["seq"]) for f in frames), default=since)
        return {"run_id": run_id, "frames": frames, "seq": highest}

    @app.get(
        "/api/orchestration/approaches", dependencies=[guard], response_model=ApproachesOut
    )
    def approaches_endpoint() -> dict[str, Any]:
        """The ready-made ways of attacking a task that a crew can be built from.

        Served rather than hard-coded in the app because the instruction is a model prompt: it
        belongs with the rest of the repo's prompts, where the CLI can reach it too, and where
        changing it does not mean shipping a new desktop build.
        """
        from chimera.orchestration.approaches import APPROACHES, default_pair

        return {
            "approaches": [ApproachOut(id=a.id, instruction=a.instruction).model_dump()
                           for a in APPROACHES],
            "default": [a.id for a in default_pair()],
        }

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
