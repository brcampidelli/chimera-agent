"""The coding surface: the seams a coding agent needs, and the conversation that uses them.

Two things live here.

**The seams** (:class:`CodeSeams`) are the per-run knobs that decide how far a coding loop may go
and what it may touch. They are shared by the autonomous run endpoint and the conversational one
rather than declared twice, because :func:`assemble_registry` builds the tool registry in an order
that is *load-bearing* — the write region scopes the native write tools, the allowlist lands before
the meta-tools so a sub-agent inherits it, and the taint ledger stays outermost so it sees every
call. Two copies of that order is one copy waiting to drift, and the way it would drift is silent:
an allowlist applied one line too late still looks applied.

**The conversation** (``POST /api/code/turn``) is what the Code screen needed and the run endpoint
could not be. A run is a closed transaction — plan, execute, verify, revert, receipt — and that is
right for "make the tests pass" and wrong for "what does this module do?", "ok, rename it", "no, the
other one". Those are turns, and they need the previous turn's tool calls, which is exactly what
:class:`~chimera.core.code_session.CodeSession` keeps and the prose-flattening chat session cannot.

The two are not alternatives. The conversation is the default because it is fast and cheap; the run
is the button you press when the change is worth verifying. They share this file's seams so that
pressing that button does not change what the agent is allowed to do.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import re
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, params
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, PrivateAttr, model_validator

# Module level, not inside the registration function, and that is load-bearing rather than tidiness:
# this file uses `from __future__ import annotations`, so a `-> EventSourceResponse` return
# annotation is a string FastAPI resolves against THIS module's globals when it builds the OpenAPI
# schema. Imported locally, the name is absent there and `python -m chimera.api.schema_dump` dies
# with a PydanticUserError about an undefined ForwardRef — at schema-generation time, long after the
# tests that exercise the endpoint have all passed.
from sse_starlette.sse import EventSourceResponse

from chimera.api.folder_locks import FolderLocks
from chimera.api.posture import (
    DEFAULT_APPROVAL,
    DEFAULT_REACH,
    Approval,
    Posture,
    PostureFacts,
    Reach,
    ResolvedPosture,
    deployment_posture,
    describe,
)
from chimera.api.posture import resolve as _resolve_posture
from chimera.api.roles import Profile, RoleModels, RolePlan
from chimera.api.roles import resolve as resolve_roles
from chimera.api.schemas import (
    AttachmentOut,
    CodeGrantMigrationIn,
    CodeGrantMigrationOut,
    CodeProjectFlagsIn,
    CodeProjectGrantIn,
    CodeProjectIn,
    CodeProjectOut,
    CodeSessionArchiveOut,
    CodeSessionMetaOut,
    CodeSessionOut,
    CodeSessionRawOut,
    CodeSessionSeenOut,
    CodeTurnFramesOut,
    CodeTurnStopOut,
    DeletedCountOut,
    DictationOut,
    ProjectPackAcceptIn,
    ProjectPackOut,
    RunningTurnOut,
    TranscriberWarmOut,
    TranscriptOut,
    UiLayoutIn,
    UiLayoutOut,
    VisionOut,
    WorkActionOut,
    WorksOut,
)
from chimera.api.spoken_log import record_spoken_request
from chimera.api.sse import SSE_RESPONSE
from chimera.api.undo_offers import UndoOffers
from chimera.api.worth import WorthReport, summarize_worth
from chimera.core.context_budget import DEFAULT_BUDGET_FRACTION, UNMEASURED_USEFUL_TOKENS
from chimera.core.output_style import OUTPUT_STYLE_VERSION, OutputStyle, with_output_style
from chimera.governance.approval import ApprovalAnnouncer
from chimera.orchestration import runlog
from chimera.orchestration.budget import DEFAULT_SPEND_WARN_USD
from chimera.providers.failover import policy_block
from chimera.telemetry import get_logger
from chimera.tools.base import Tool
from chimera.tools.browser import FrameAnnouncer
from chimera.tools.chart import ChartAnnouncer

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings
    from chimera.core.agent import Agent
    from chimera.orchestration.metering import MeteredBackend
    from chimera.tools import ToolRegistry

_log = get_logger("api.code")


#: Hard ceiling on a requested step count. Not a judgement about how many steps a task needs — it
#: is the difference between a long run and a runaway one, and the client asking is a UI field.
MAX_RUN_STEPS = 100

#: How long a conversation nobody watches and nothing runs in keeps its live frames. Past it, a
#: screen that comes back reads the stored conversation, which holds every finished turn.
IDLE_BUS_SECONDS = 1800.0

#: Turns one conversation may hold at once, running or waiting for it: one running, three queued.
#: The owner's composer queues a follow-up itself and sends it when the turn before ends, so it never
#: comes near this; a share link reaches the guest route over the network, and without a bound every
#: turn a guest sent was one more thread waiting on the conversation's lock.
MAX_TURNS_PER_CONVERSATION = 4


class CodeSeams(BaseModel):
    """How far a coding loop may go, and what it may touch.

    Everything here is opt-in and defaults to the behaviour these endpoints already had. They exist
    because the run endpoint was built as a deliberate minimum and the desktop's Code screen then
    inherited that minimum as a ceiling — a run started from the app was structurally weaker than
    the same run started from a terminal, for no reason anyone had written down.
    """

    allow_host_exec: bool = False
    """Let this request's agent actually RUN the shell tools its reach already mounts.

    Two locks, both opened deliberately. ``posture.reach`` decides whether the shell tools exist at
    all; this decides whether a mounted one may run outside a container. Neither works alone —
    setting this with a reach below ``workspace_shell`` changes nothing, because there is no shell
    tool to ungate, and that is asserted by a test rather than left to reading.

    **It closes an asymmetry rather than opening a door.** A caller that reaches this API can
    already run host commands two other ways: `RunRequest.verify` goes to `CommandVerifier` (a
    typed command is authorised by construction; since 2026-09-08 it runs where the shell runs, and
    an INFERRED one on an unsandboxed host consults this same gate), and the Runner panel spawns
    processes with no gate at all.
    The only thing refused was the AGENT — this machine would run pytest to judge its work and
    refuse to let it run pytest to fix its work, while the screen said "asks" on a server that has
    no terminal to ask at.

    Per REQUEST, which is what makes it per project: enabling commands for one folder does not
    enable them for the next folder someone opens. `CHIMERA_HOST_EXEC=deny` still refuses — an
    owner who turned host execution off system-wide is not overridden by a field on a request.

    **And it is only a request.** Since the grant moved to the server's project registry, this
    opens nothing unless the server ALSO records a grant for the folder (or the owner set
    ``CHIMERA_REACH=workspace_shell`` for every folder) — see :func:`server_grants_shell`. It used to
    be the grant itself: whatever a client sent, the server believed.
    """

    _grant_root: Path | None = PrivateAttr(default=None)
    """The folder whose recorded grant applies when the run happens in a COPY of it.

    A batch task runs in its own git worktree, a temporary directory no grant names, so the grant is
    looked up on the project the worktree was cut from. Private on purpose: set by the server when
    it cuts the worktree, never by a request — a field a client could fill in would be a way to
    borrow another folder's grant.
    """

    max_steps: int | None = None
    """Tool-loop steps the worker may take per turn. None = the agent's own default.

    This field replaces a hard-coded 6 that had no comment justifying it while ``AgentConfig``
    documents 8 — so a run through the API was capped lower than the identical run through
    ``chimera solve``, and nothing said so. Clamped to 1..MAX_RUN_STEPS: a client asking for ten
    thousand steps is asking for a bill, not a run."""

    context_budget: float | None = None
    """Fraction of the model's advertised window to spend on the prompt before compacting.

    None (default) keeps the historical behaviour: the message list only grows and an overflow is
    terminal. This pairs with ``max_steps`` and should usually move with it — raising the step
    ceiling without a budget raises the chance of dying on overflow instead of finishing."""

    summarise_compaction: bool = False
    """When a compaction fires, ask the model for the standing instructions of the dropped span and
    keep them beside the structural note (`chimera/core/summarise.py`), instead of the note alone.

    Off by default: a summary is believed in a way a count is not, and the rule-form summariser is
    measured before a surface sends this (`bench/compaction`, study 19 B1). Meaningless without
    ``context_budget`` — nothing compacts, so nothing is summarised — and it costs one model call
    per compaction on the turn's own model."""

    max_usd: float | None = Field(default=None, gt=0)
    """Dollar ceiling for the model calls of ONE loop. None (the default) = no cap, as before.

    The mechanism has existed since ``AgentConfig.max_usd`` and no route reached it, so the only
    caller that could set a ceiling was the cron dispatcher. It is checked BEFORE each call, so the
    money is never spent to discover it was over budget, and the partial answer survives the stop —
    see :class:`~chimera.orchestration.budget.SpendBudget`.

    ``gt=0`` because zero is the one value that would lie in the dangerous direction:
    :class:`~chimera.core.agent.AgentConfig` reads this field for truthiness, so ``0`` would read as
    "spend nothing" and mean "spend anything", and a negative would reach ``SpendBudget`` and raise
    from inside a streaming response, where the client sees a broken stream rather than a bad field.

    On ``/api/runs`` this bounds an ATTEMPT, not the run: ``AutonomousAgent`` calls the worker once
    per attempt and each call builds a fresh budget, so a $1 ceiling with ``max_attempts=3`` can
    spend $3. Stated here because the field name does not say it, and it is not divided by the
    attempt count on the way in — that would invent arithmetic nobody asked for and make the same
    number mean different money on the two endpoints."""

    warn_usd: float | None = Field(default=None, gt=0)
    """Dollars at which the turn SAYS what it has spent, once, and goes on. None = the server's
    default (:data:`DEFAULT_SPEND_WARN_USD`).

    This is what the desktop's armed US$1 ceiling became: a warning, not a stop. A stop for money
    exists only when the person types ``max_usd``. Named separately so that "I want to hear about it
    at $5" and "refuse the call that would cross $5" are two different sentences on the wire."""

    repo_map: bool = False
    """Prepend a bounded structural digest of the repository, ranked by importance, so the agent can
    aim at the right file instead of exploring blind (mirrors ``chimera solve --repo-map``)."""

    explorer: bool = False
    """Give the agent an isolated read-only Context Explorer for repository search, so localisation
    ("where does X live?") costs a cheap sub-agent rather than turns of the main loop."""

    hide_own_env: bool = False
    """Keep Chimera's own ``.env`` from this run's read tools whatever ``CHIMERA_AGENT_READS_OWN_ENV``
    says. Narrowing only. The desktop bridge sets it on every run it starts (the owner's decision of
    2026-10-04: a bridge client never gets that file), and the owner's own runs leave it off and
    follow the setting."""

    allow_tools: list[str] | None = None
    """Session allowlist of tool names. None = every tool. An explicit list — *including an empty
    one* — is an allowlist, so ``[]`` is a fully locked, read-nothing session."""

    deny_tools: list[str] | None = None
    """Tool names removed from the session even when allowed (deny wins over allow)."""

    write_region: list[str] | None = None
    """Globs the write tools are confined to, relative to the workspace. None = the whole workspace
    (fenced by ``WorkspaceGuard`` as before). Fail-closed: a write outside the region is refused."""

    profile: Profile | None = None
    """Which tier each ROLE draws from: economy / balanced / max (see :mod:`chimera.api.roles`).

    None = every role runs on the request's own ``model``, which is what every caller got before
    roles existed. A profile is a starting point, never a ceiling — ``roles`` overrides it field by
    field."""

    roles: RoleModels | None = None
    """Per-role model overrides, merged over the profile. Only the fields actually sent are applied,
    so a partial override cannot blank the rest of the profile back to the default model."""

    attachments: list[str] = Field(default_factory=list)
    """Ids from ``POST /api/attachments`` — images for the model to look at, documents for it to read.

    Ids rather than inline data: a base64 image in the request body would be re-sent on every retry
    and logged wherever the request is logged. They belong to THIS turn only, and are not stored with
    the conversation — re-sending a picture on every later turn costs again for no new information.
    """

    fuse: bool = False
    """Route THIS turn through the fusion panel.

    A fused turn CANNOT call tools: ``FusionEngine.complete`` drops the tool schemas, so the agent
    finishes in one step having touched nothing and answers from the prompt alone. In a coding
    conversation that is a sharper edge than in a chat — ask it to fix a file and it will describe a
    fix it never applied. It is offered anyway, because a deliberative question ("which of these two
    designs is better?") is exactly what a panel is good at, and refusing to offer it would be
    deciding for the user. What is NOT optional is that the turn says so, which ``done.fused`` does.
    """

    fusion_panel: list[str] | None = None
    """Which models answer this fused turn, overriding the configured panel.

    ``None`` means the install's default, which is what every caller sent before this existed. The
    override is per-conversation on purpose: "which three models should argue about THIS question"
    is a property of the question, not of the installation. Ignored when ``fuse`` is false — a field
    that quietly changed a non-fused turn would be a second, invisible way to pick a model.
    """

    fusion_judge: str | None = None
    """Which model reads the panel's answers and writes the analysis. See ``fusion_panel``.

    Nothing here refuses a judge that also sits on the panel. It is reported instead — ``kinship``
    in the config, and a warning where the choice is made — because a user holding one provider key
    cannot avoid the overlap, and a refusal they cannot act on just removes the feature.
    """

    fusion_synthesizer: str | None = None
    """Which model writes the final answer from the judge's analysis. See ``fusion_panel``."""

    provider: str | None = None
    """Run this turn through an EXTERNAL coding agent instead of Chimera's own loop.

    ``None`` (default) is the native loop and nothing changes. A key from
    :mod:`chimera.acp.agents` — ``claude``, ``gemini``, ``custom`` — launches that agent over ACP
    and reports its work through this endpoint's existing events, so the transcript, the verifier,
    the checkpoint and the revert all still apply.

    What does NOT still apply is prevention. These agents have file and shell tools of their own,
    so ``write_region`` and ``deny_tools`` govern only the calls they choose to route through us.
    The checkpoint is what covers the rest, and :mod:`chimera.api.posture` has to say so — a
    posture sentence that claims a boundary this turn does not have is the one lie the product
    cannot afford."""

    provider_command: str | None = None
    """The command for ``provider="custom"``, as a shell-style string. Ignored otherwise.

    Split with :func:`shlex.split` and run WITHOUT a shell. A user pointing this at their own
    adapter is the same trust level as the verify command they already configure."""

    posture: Posture | None = None
    """How far the agent may reach, and when it stops to ask (see :mod:`chimera.api.posture`).

    A convenience over the fields above, not a second mechanism: it resolves into ``deny_tools`` and
    the pause flags, and an explicit ``deny_tools`` is unioned with it rather than replaced — two
    ways of saying "not this tool" must never cancel each other out. None = no posture applied, so
    every existing caller behaves exactly as before."""


#: Snapshots kept so a failed editing turn can be undone if the user asks. Bounded and in-memory: an
#: offer that outlives the app is an offer nobody remembers making, and persisting whole-workspace
#: snapshots to disk to support one button is a much larger promise than this button makes.
# The undo offers of editing turns, per conversation (`chimera/api/undo_offers.py`). A module-level
# dict capped at 8 for the whole app used to take away an older conversation's Undo after eight
# editing turns anywhere.
_undo_offers = UndoOffers()


class _Deleted(Exception):
    """The turn's conversation was deleted while it ran: nothing more is recorded for it."""


class _StoppedWhileWaiting(Exception):
    """A turn stopped before it started, while it waited for another turn in its folder."""

    MESSAGE = "Stopped before it started: another conversation was working in this folder."


#: FastAPI's upload marker, hoisted out of the signatures so a call in an argument default does not
#: trip the linter. Same object, same behaviour.
_UPLOAD = File(...)
_LANGUAGE_HINT = Form(None)
_LANGUAGE_CODE = re.compile(r"[a-z]{2,3}")

#: What a spoken sentence about the works looks like — the verbs the talking model has tools for.
_WORK_CONTROL = re.compile(
    r"\b(par[ae]|parar|pare|cancel[ae]r?|interromp[ae]r?|desfa[zç]|desfazer|revert[ae]r?|"
    r"stop|cancel|undo|revert|status|andamento|terminou|acabou|como (est[aá]|vai|anda))\b",
    re.IGNORECASE,
)
_ABOUT_WORKS = re.compile(r"\b(trabalhos?|works?|tarefas?|tasks?)\b", re.IGNORECASE)

SPOKEN_NOTE = (
    "The person said this aloud, and your answer will be read to them by a voice before they see "
    "it on a screen. Answer for the ear: two to four short sentences of plain prose — no headings, "
    "no lists, no tables, no code blocks, no Markdown, no emoji. If the answer needs more than "
    "that (a plan, a list of files, code), say the gist in one or two sentences first, then put a "
    "line containing only --- and write the rest below it: the voice reads what is above the line, "
    "and the screen shows all of it."
)
"""What a turn that arrived by voice tells the model, in the system prompt and for that turn only.

The first live test of the hands-free mode (2026-09-17) had the agent answer a spoken "are you
understanding me?" with four paragraphs, two lists and a rocket emoji, and the voice read all of
it. The model had no way to know it was being heard rather than read. Now it is told, and told
where to put the part that is for the screen; the reader stops at that line."""


#: Provider errors whose text is about the REQUEST rather than about our internals — the user can act
#: on every one of them, and none of them names anything private. Matched loosely on purpose: a
#: provider rewords its messages far more often than it changes what they mean.
_ACTIONABLE = (
    "image input",
    "does not exist",
    "not found",
    # "LLM Provider NOT provided" — what a slug the router cannot parse produces, which is what
    # typing a model by hand produces. As fixable as "does not exist" and it was reaching the
    # composer as "the coding turn failed", which reads like a crash in us.
    "llm provider",
    "no endpoints",
    "context length",
    "maximum context",
    "rate limit",
    "quota",
    "insufficient",
    "credit",
    "invalid api key",
    "authentication",
    "unauthorized",
    "moderation",
    "tool use",
    "tool_use",
    "function calling",
)


def _cast_for_turn(backend: Any, req: CodeSeams) -> Any:
    """The fusion engine this turn should use — the shared one, or a copy with a different cast.

    The engine takes its roles from a ``FusionConfig`` it holds, so a per-turn choice is a new engine
    over the SAME gateway rather than anything reentrant: no shared state is mutated, the swap in the
    caller still restores the original, and an install with no override keeps the exact object it had
    before. A panel of one is not a panel, so a single-model override is refused back to the default
    rather than silently running fusion over one opinion.
    """
    panel = [m.strip() for m in (req.fusion_panel or []) if m and m.strip()]
    judge = (req.fusion_judge or "").strip()
    synth = (req.fusion_synthesizer or "").strip()
    if not (panel or judge or synth):
        return backend

    config = getattr(backend, "config", None)
    gateway = getattr(backend, "backend", None)
    if config is None or gateway is None:  # not a FusionEngine — nothing to re-cast
        return backend

    from dataclasses import replace

    from chimera.fusion.engine import FusionEngine

    return FusionEngine(
        gateway,
        replace(
            config,
            panel=panel if len(panel) >= 2 else list(config.panel),
            judge=judge or config.judge,
            synthesizer=synth or config.synthesizer,
        ),
    )


def _native_failure(exc: Exception) -> str:
    """What to tell the user when Chimera's own loop failed.

    "the coding turn failed" used to be the whole answer, on the reasoning that a native failure is
    ours to debug. That holds for a bug in this repository and not at all for the more common case:
    the provider refused, and said exactly why. Attaching an image to a model without vision produced
    `No endpoints found that support image input` — fixable in four seconds — and the app rendered a
    sentence indistinguishable from a crash.

    Only the recognised classes are forwarded, and only the first line: a stack trace or an internal
    path in the composer is noise, and the point is the one sentence that says what to change.
    """
    text = str(exc).strip()
    if not text:
        return "the coding turn failed"
    first = text.splitlines()[0].strip()
    if not any(marker in first.lower() for marker in _ACTIONABLE):
        return "the coding turn failed"

    # The provider's sentence, without the wrapping. LiteLLM stacks its own class names in front of
    # the body and the body is usually JSON, so the raw string reads
    # `litellm.NotFoundError: NotFoundError: OpenrouterException - {"error":{"message":"No endpoints
    # found that support image input","code":404}}` — which contains the one useful clause and buries
    # it behind four names that mean nothing to the person reading. Unwrapped when the shape is
    # recognisable, left alone when it is not: a regex that half-matches would be worse than the
    # noise it removes.
    embedded = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', first)
    if embedded:
        try:
            first = json.loads(f'"{embedded.group(1)}"')
        except ValueError:
            first = embedded.group(1)

    # Bounded: a provider that answers with a wall of JSON must not paste it into the transcript.
    return first if len(first) <= 300 else f"{first[:297]}…"


def resolve_posture(posture: Posture | None) -> ResolvedPosture:
    """The posture's effect, with None meaning "no posture" rather than "the default posture".

    The distinction matters at the boundary: a caller that never heard of postures must keep the
    behaviour it had, and the DEFAULT posture denies the exec tools. Silently applying it would
    break every existing client in a way that looks like the agent got worse at its job.
    """
    return _resolve_posture(posture) if posture is not None else ResolvedPosture([], False, False, False)


def server_grants_shell(settings: Settings, folder: Path) -> bool:
    """Whether the SERVER's own records let the agent run commands in ``folder``.

    Two records say yes, and nothing a request carries is one of them:

    * ``CHIMERA_REACH=workspace_shell`` — the owner granted commands in every folder at once;
    * a grant on the folder in the project registry (``code_projects.json``), set from the Folders
      card, the Code screen's switch, the one-time migration of the old browser-side grants, or the
      bridge at its Full tier.

    The desktop used to keep the per-folder grant in its own ``localStorage`` and tell the server
    about it on every request, as ``posture.reach = workspace_shell`` and ``allow_host_exec``. The
    server had nothing to check that against, so the claim WAS the grant — for the desktop, for the
    bridge's Full tier, and for any local process that could reach the API.
    """
    if settings.reach.strip() == "workspace_shell":
        return True
    from chimera.core.code_projects import CodeProjectRegistry

    return CodeProjectRegistry(Path(settings.home) / "code_projects.json").shell_granted_for(folder)


def granted_posture(posture: Posture | None, granted: bool) -> Posture | None:
    """The request's posture, held to what the server granted.

    A ``workspace_shell`` reach on a folder with no grant is answered with ``workspace`` — the reach
    the desktop sends for a folder nobody granted — rather than refused: the turn still runs, it just
    runs without commands, which is what the owner's record says. Nothing else is touched. A posture
    can only get NARROWER here, so ``read_only`` stays ``read_only``, and an absent posture stays
    absent (its host execution is still held by ``allow_host_exec`` needing the grant, below).
    """
    if posture is None or granted or posture.reach != "workspace_shell":
        return posture
    return posture.model_copy(update={"reach": "workspace"})


def resolve_role_plan(seams: CodeSeams, settings: Settings) -> RolePlan:
    """The roles this request runs with. No profile and no overrides = no role routing at all."""
    return resolve_roles(seams.profile, settings, seams.roles)


def clamp_steps(value: int | None) -> int | None:
    """Clamp a requested step ceiling into 1..MAX_RUN_STEPS. None (not asked) stays None."""
    if value is None:
        return None
    return max(1, min(MAX_RUN_STEPS, value))


def resolve_steps(value: int | None) -> int:
    """The step ceiling actually used: the caller's, clamped, or the agent's documented default."""
    from chimera.core import AgentConfig

    clamped = clamp_steps(value)
    return clamped if clamped is not None else AgentConfig.max_steps


def build_write_region(globs: list[str] | None, ws: Path) -> Any:
    """Build a ``WriteRegion`` from the requested globs, or None when the caller named none.

    Blank entries are dropped, and a list that contained nothing but blanks is treated as "no region
    asked for" rather than as an empty region — an empty region forbids every write, which is a
    thing a caller should have to say on purpose, not stumble into via a trailing comma.
    """
    from chimera.tools.write_region import WriteRegion

    cleaned = [g.strip() for g in (globs or []) if g.strip()]
    return WriteRegion(cleaned, ws) if cleaned else None


def _intersect_allow(request: list[str] | None, deployment: list[str] | None) -> list[str] | None:
    """Two allowlists, intersected — never replaced.

    ``None`` means "no allowlist here"; an explicit list, *including an empty one*, is a real one.

    Intersection rather than precedence, because the two lists are not peers. The deployment's list
    is the owner's ceiling, set once in the environment; the request's is one caller's ask. If a
    request could replace the ceiling, an owner's restriction would be removable by whoever sends
    the request — the wrong direction for the one control here that can only ever take capability
    away. The CLI keeps its own precedence rule (a typed flag beats the env) because there the
    person typing owns both.
    """
    if request is None:
        return deployment
    if deployment is None:
        return request
    return sorted(set(request) & set(deployment))


class NoticeAnnouncer:
    """Carries a warning from a tool to the turn's stream, bound after the registry exists.

    The same late binding as :class:`ApprovalAnnouncer`: the registry is built before the turn's
    `emit` is, so the tool holds this and the turn points ``emit`` at the stream afterwards. A
    warning sent before that reaches nobody, which is the safe direction: it never blocks anything.
    """

    def __init__(self) -> None:
        self.emit: Callable[[str, str, dict[str, Any]], None] | None = None

    def __call__(self, code: str, text: str, data: dict[str, Any]) -> None:
        if self.emit is not None:
            self.emit(code, text, data)


def assemble_registry(
    seams: CodeSeams,
    ws: Path,
    settings: Settings,
    gateway: Any,
    *,
    steps: int,
    surface: str = "api",
    shared: Any = None,
    approval_sink: Any = None,
    instruction: str | None = None,
    frame_sink: Any = None,
    extra_tools: Sequence[Tool] | None = None,
    run_id: str | None = None,
    notice_sink: Any = None,
    grant_root: Path | None = None,
    chart_sink: Any = None,
    guest: bool = False,
) -> tuple[ToolRegistry, Any]:
    """Build the tool registry for a coding turn, and the taint ledger watching it.

    The order mirrors the CLI's ``_run_solve`` and each step depends on the one before:

    1. the **write region** scopes the native write tools as they are constructed;
    2. the **allowlist** is applied BEFORE the meta-tools, so a sub-agent inherits it;
    3. the **explorer** is registered into that already-scoped registry;
    4. the **trust kernel** wraps that, so BLOCK/REVIEW is decided before a tool can run;
    5. the **taint ledger** wraps everything, so it sees every call including the sub-agent's.

    Returns the registry and the ledger, because the same ledger has to reach the agent as ``taint=``
    — that is what lets a run know it read untrusted content and is therefore pausable. Building two
    would mean the run that got tainted and the run that gets asked about it are different objects,
    and the pause would never fire.

    ``instruction`` is the person's own words for this run — the task, or the turn's message — so a
    fetch of a page or a file it names is recorded as the user's request. A caller with no single
    task (the hierarchy's fixed seams) passes nothing, and every fetch there reads ``unknown``.

    ``grant_root`` is the folder whose recorded shell grant applies when ``ws`` is a copy of it — a
    crew worker's worktree. None = ``ws`` itself (or the root the server stamped on the seams).

    ``guest`` is a turn somebody other than the owner sent, through a conversation's share link.
    It gets no OpenAPI connector: a connector's GET runs with the owner's key and needs no
    approval, so on a guest's turn it would read the owner's private API for whoever holds the
    link — the exposure the bots are closed to (``unattended_surface_may_load``).
    """
    from chimera.core import ExploreRepositoryTool
    from chimera.governance import TaintLedger, ledger_registry, restrict_registry
    from chimera.governance.audit import AuditLog

    # Whether a mounted shell tool may run on the host, decided per request rather than only per
    # install. `resolve_host_exec_confirm` answers None for "no gate", a refusal for `deny`, and a
    # terminal prompt for `ask` — and this surface has no terminal, so its `ask` resolves to a
    # refusal. That last clause used to be a claim rather than a fact: the resolver decided from
    # `sys.stdin.isatty()`, which in the packaged desktop reports a terminal (a console with no
    # window, courtesy of CREATE_NO_WINDOW), so `ask` drew a prompt nobody could see and blocked
    # this request thread forever. `declare_no_human_here`, called once in `build_api_app`, is what
    # makes the sentence true. Passing None is what lets a project someone opened commands for
    # actually run them.
    #
    # BOTH locks, and never against the owner's `deny`. The reach lock is read from the resolved
    # posture rather than from the reach string: `deny_tools` is what actually removes the tools, so
    # asking the same question the registry asks keeps the two from drifting apart.
    from chimera.governance.ledger import EXEC_TOOLS
    from chimera.governance.profile import govern_step
    from chimera.integrations import mcp_pool
    from chimera.sandbox.confirm import resolve_host_exec_confirm
    from chimera.tools import default_registry

    #
    # And a THIRD condition since the grant moved to the server: the server's own record for this
    # folder (`server_grants_shell`). The request's `workspace_shell` and `allow_host_exec` are
    # still read, but as a request — a folder the owner did not grant gets the reach below it and a
    # gated tool, whatever was asked. Narrowing only: the record cannot lift `read_only` (the posture
    # keeps it) nor `deny` (`owner_refuses`), because neither consults it.
    granted = server_grants_shell(settings, grant_root or seams._grant_root or ws)
    posture_in = granted_posture(seams.posture, granted)
    if posture_in is not seams.posture:
        _log.info("no shell grant recorded for %s: the request's workspace_shell runs as workspace", ws)
    reach_mounts_shell = not (EXEC_TOOLS & set(resolve_posture(posture_in).deny_tools))
    owner_refuses = (settings.host_exec or "ask").lower() == "deny"
    ungated = seams.allow_host_exec and granted and reach_mounts_shell and not owner_refuses

    registry = default_registry(
        ws,
        write_region=build_write_region(seams.write_region, ws),
        host_exec_confirm=None if ungated else resolve_host_exec_confirm(settings),
    )
    # The web research sub-agent (study 25, S12), when the owner switched it on. Registered BEFORE
    # the lists below, unlike the explorer, so every one of them reaches it by name the way they
    # reach a built-in: a request's own allowlist that does not name it narrows it away too. Its web
    # tools are drawn from this registry late (`lambda: registry` sees the final, wrapped value, as
    # `SubAgentTool` documents), so a denied fetch tool is denied to it and its fetches pass the
    # kernel and the taint ledger wrapped around everything below.
    if settings.research_agent:
        from chimera.core.research import ResearchWebTool

        registry.register(
            ResearchWebTool(
                gateway, lambda: registry, model=resolve_role_plan(seams, settings).models.explore
            )
        )
    # The owner's approver — the object the taint ledger and, since #495, the policy kernel are
    # handed below — reaches the FILE tools too, on the surface that has a person: a path outside
    # the project folder becomes a question on the screen instead of a refusal. Only when a screen
    # is bound (`approval_sink`), for the same reason the kernel gets it only then: headless, a
    # question announced to nobody is a timeout with a bill, and the jail's refusal is the right
    # answer. The declared write region is not softened by a yes (`resolve_for` says why).
    owner = _owner_allows(settings, approval_sink, run_id=run_id, surface=surface)
    if approval_sink is not None:
        for tool in registry.tools():
            if hasattr(tool, "workspace"):
                tool.ask_outside = owner  # type: ignore[attr-defined]
    # A run the bridge started: every file tool keeps Chimera's own `.env` out, inside the
    # workspace or outside it with a person's yes, whatever the owner's setting (`hides_own_env`).
    if seams.hide_own_env:
        for tool in registry.tools():
            if hasattr(tool, "workspace"):
                tool.hide_own_env = True  # type: ignore[attr-defined]
    # `open_pull_request` asks the owner on every call, and `owner` above is NOT the approver for
    # it: under CHIMERA_APPROVAL_MODE=allow that one says yes to everything, which is a choice about
    # taint and policy reviews, not about publishing the owner's code. `always_ask` reads `allow` as
    # `ask`. With a screen bound the card shows on the turn's own stream (and waits as long as the
    # owner's other cards do); without one the tool keeps its default, the durable question on the
    # owner's channel — never a yes.
    from chimera.tools.pull_request import OpenPullRequestTool

    pull_request = registry.get("open_pull_request") if "open_pull_request" in registry else None
    if approval_sink is not None and isinstance(pull_request, OpenPullRequestTool):
        from chimera.governance.approval import always_ask, deliverer_for

        def wait_for_the_screen() -> float:
            bound = getattr(approval_sink, "emit", None) is not None
            return float(settings.approval_wait) if bound else 0.0

        pull_request.approve = always_ask(
            settings.home,
            mode=settings.approval_mode,
            deliver=deliverer_for(settings),
            on_asked=approval_sink,
            wait_seconds=wait_for_the_screen,
            facts={k: v for k, v in (("run_id", run_id), ("surface", surface)) if v},
        )
    # The browser draws its viewport on the screen after every action — only for a request that
    # has a screen to draw on (`frame_sink`, bound to the turn's stream the way the approval
    # announcer is). A headless run never captures a frame: the tool checks the sink before it
    # asks the driver for a picture.
    if frame_sink is not None:
        for tool in registry.tools():
            if getattr(tool, "name", "") == "browser" and hasattr(tool, "on_frame"):
                tool.on_frame = frame_sink  # type: ignore[attr-defined]
    # The same for a chart: `render_chart` tells the screen about each chart it wrote, so the
    # conversation can draw it where it was asked for instead of leaving a file name in a tool row.
    # Bound only where a screen is (`chart_sink`); headless, the tool writes the file and says nothing.
    if chart_sink is not None:
        for tool in registry.tools():
            if getattr(tool, "name", "") == "render_chart" and hasattr(tool, "on_chart"):
                tool.on_chart = chart_sink  # type: ignore[attr-defined]
    # The configured MCP servers, HERE and not lower down, because everything below this line has to
    # reach them: the denial list, the trust kernel, and the taint ledger that treats their output as
    # untrusted. The chat path learned this the hard way and says so at its own injection point — "a
    # denylist that cover only the tools we wrote is not a denylist" — and until now this surface did
    # not pour them in at all, so the Code screen, runs and orchestration ran without the servers a
    # user had connected and watched Test prove live.
    #
    # Pooled per process rather than connected here: this function runs once per TURN and once per
    # worker in a fan-out, so connecting per call would spawn a container per message.
    pool = mcp_pool.connectors(settings)
    # The project's pack (study 29, P7.6), when the owner switched packs on and accepted a pack for
    # this folder. Read from the folder whose grant applies (a crew worktree's original), because
    # that is the folder the owner accepted. Decided ONCE, here, for every half it narrows: the
    # servers it leaves out are dropped from the pool before any of them is listed (so nothing below
    # can register, defer or call them), its `tools_deny` joins the denials further down, and the
    # skills narrowing is stamped on the registry this returns, which is where the agent reads it.
    from chimera.core.project_pack import applied_pack, bundle_filter, denied_tools, narrow_pool

    pack = applied_pack(settings, grant_root or seams._grant_root or ws)
    pool = narrow_pool(pack, pool)
    if pool is not None and not settings.mcp_defer:
        pool.into_tool_registry(registry)
    # The OpenAPI connectors the owner added and switched on (study 29, P7.5), here for the reason
    # the MCP servers are: above the lists, so CHIMERA_TOOL_DENYLIST reaches them by name, and
    # inside the kernel and the ledger, which fences what they return. An operation that is not GET
    # asks the owner on the card the file tools use; with no screen bound it is refused. Not on a
    # guest's turn: the owner's approval card guards the writes, but nothing guards a GET, and a
    # guest is not the owner. Not even a connector switched on for the unattended surfaces — that
    # switch sends it to the owner's bots and jobs, and says nothing about a share link.
    from chimera.integrations.openapi_store import with_connectors

    if not guest:
        with_connectors(registry, settings, ask=owner if approval_sink is not None else None)
    # Union, never replace: a posture, an explicit denylist and the deployment's own denylist are
    # three ways of saying "not this tool", and letting one overwrite another means the strictest of
    # several stated intentions loses.
    #
    # CHIMERA_TOOL_DENYLIST / _ALLOWLIST reach the app for the first time here. They were read by
    # `chimera run` and `chimera solve` and by nothing else, so setting them and then using the
    # desktop app or the API restricted exactly nothing. A variable named like a security control
    # that controls nothing is worse than no variable at all: it reads as a fence in `.env` while
    # every request runs unfenced, and it fails in the direction nobody checks.
    denied = sorted({
        *(seams.deny_tools or ()),
        *resolve_posture(posture_in).deny_tools,
        *deployment_posture(settings).deny_tools,
        *settings.tool_denylist,
    })
    # The pack's `tools_deny`, joined to the union above and never the other way round — a pack is
    # names to REMOVE, and nothing reads one into `allowed`, so a pack naming a tool cannot grant it.
    if pack is not None:
        denied = sorted({*denied, *denied_tools(pack)})
    allowed = _intersect_allow(seams.allow_tools, settings.tool_allowlist or None)
    if allowed is not None or denied:
        registry = restrict_registry(registry, allow=allowed, deny=denied or None)
    # Checked, because this one is registered AFTER the filter ran and would otherwise be the one
    # tool no list could touch. Against the DEPLOYMENT allowlist and the union of every denial —
    # deliberately not against the request's own `allow_tools`: a caller that sets `explorer=True`
    # alongside its own list is granting itself the tool by another field, which is its right, while
    # an owner's ceiling must not be raisable by a request at all. And unlike `spawn_subagent`, this
    # tool does not inherit the restricted registry — it builds its own read-only set internally, so
    # letting it through was granting a capability, not just a name.
    # Registered AFTER the filter, and carrying the lists with it — for exactly the reason the
    # explorer below documents about itself, only more so. Deferral takes the servers' N names out
    # of the registry, so the restriction filter has nothing left to match: a denylist naming an MCP
    # tool would silently stop removing it while one proxy could still reach every one of them.
    # Before the MCP deferral, and deliberately: this one only ever moves BUILT-IN tools, and the
    # MCP proxies are registered after the filter for their own reasons. Running it second would
    # sweep `mcp_list`/`mcp_describe`/`mcp_call` into the deferred set — a proxy behind a proxy,
    # costing a round trip to reach a round trip.
    if settings.defer_tools:
        from chimera.tools.defer import defer_builtins

        # The lists travel with it for the reason the MCP block below states: after deferral the
        # names are gone from the registry, so the filter that already ran cannot match them, and a
        # denylist would keep reporting success while `tool_call` still reached the tool.
        registry, _ = defer_builtins(
            registry,
            denied=frozenset(denied),
            # The DEPLOYMENT ceiling only, never the request's own list — a caller may narrow
            # itself and may not raise an owner's ceiling.
            allowed=frozenset(settings.tool_allowlist) if settings.tool_allowlist else None,
        )

    if pool is not None and settings.mcp_defer:
        from chimera.integrations.mcp_defer import register_deferred_mcp

        register_deferred_mcp(
            pool,
            registry,
            denied=frozenset(denied),
            # The DEPLOYMENT ceiling only, never the request's own list — same asymmetry the
            # explorer applies: a caller may narrow itself, and may not raise an owner's ceiling.
            allowed=frozenset(settings.tool_allowlist) if settings.tool_allowlist else None,
        )
    explorer_ok = ExploreRepositoryTool.name not in denied and (
        not settings.tool_allowlist or ExploreRepositoryTool.name in settings.tool_allowlist
    )
    if seams.explorer and explorer_ok:
        # A narrow localisation question does not need the worker's model, and answering it in a
        # sub-agent keeps the finding — not the search — in the main loop's context.
        # A cheap model for the explorer is the point of the role: a narrow localisation
        # question does not need the editor's model, and the sub-agent returns the finding rather
        # than the search, so the main loop never pays for the hunt.
        explore_model = resolve_role_plan(seams, settings).models.explore
        registry.register(
            ExploreRepositoryTool(
                gateway, ws, model=explore_model, max_turns=steps,
                contract=settings.explorer_contract,
            )
        )
    # Tools the caller brings for THIS turn — the talking model's handles on the conversation's
    # background works — registered here, before the kernel and the ledger wrap the registry, so
    # they are governed and audited like every other tool rather than bolted on outside.
    for extra in extra_tools or ():
        registry.register(extra, replace=True)
    # `shared` binds several workers to ONE ledger. Independent tasks must not share it — that
    # would block a worker for something a sibling read — but workers collaborating on a single
    # task and merging into a single workspace must, because untrusted content one of them read
    # can reach the others through the merge. It is the same distinction the CLI already draws
    # between `solve-batch` (independent, own ledgers) and `crew-isolated` (shared).
    ledger = TaintLedger(
        shared=shared,
        authority=settings.taint_authority,
        egress_allow=settings.egress_allow.split(","),
    )
    if instruction is not None:
        ledger.set_instruction(instruction, workspace=ws)
    # A UNION, exactly like the denial list twelve lines up, and for the reason that block already
    # gives: a floor is not a default. A default is what a request gets when it sends nothing, so any
    # client can step around it by sending something.
    #
    # This read `settings.taint_narrow` only in the branch where the request sent NO posture. So an
    # owner with CHIMERA_TAINT_NARROW=1 and no CHIMERA_APPROVAL had it silently disarmed by any
    # request that sent a posture — `deployment_posture(...).narrow_on_taint` derives from
    # `approval`, not from `taint_narrow`, so nothing downstream put it back. Meanwhile the
    # Governance screen reports `"armed": bool(settings.taint_narrow)`, which stayed true: the app
    # said the defence was on while requests were turning it off.
    narrow = (
        (resolve_posture(posture_in).narrow_on_taint if posture_in is not None else False)
        or settings.taint_narrow
        or deployment_posture(settings).narrow_on_taint
    )
    # The audit trail starts here, and only here. `LedgeredTool` records the three things worth a
    # permanent line — a dangerous tool narrowed after untrusted input, a call escalated for review,
    # a side effect suppressed as a duplicate — and each is rare by construction.
    #
    # Deliberately NOT passed to `restrict_registry` above: the default posture excludes the exec
    # tools on every single turn, so that would append an identical entry per turn and bury the
    # events someone opens this log to find. A trail nobody can read is the same as no trail.
    #
    # Until now the app wrote nothing here at all, which made the Security screen's empty state mean
    # "nothing is recording" while it read as "nothing has happened". Those are opposite claims.
    audit = AuditLog(settings.home / "audit.jsonl")
    # The trust kernel, under the deployment's own governance mode — `off` by default, so a stock
    # install behaves exactly as it did. Every surface served over HTTP was assembled without it:
    # `POST /api/runs`, `POST /api/agents` and `POST /api/code/turn` all arrive here, and measured
    # before this line existed, the chain around a write tool was `LedgeredTool -> WriteFileTool`
    # with nothing in between. The kernel returns `review` for `git push --force origin main` — and
    # on this path that verdict was reached by nobody, because no wrapper was there to ask.
    #
    # It goes HERE, after every registration and before the ledger, so the order matches the one
    # `governed_profile` has always used and the ledger stays outermost, seeing exactly what the
    # kernel saw. (Not, as an earlier draft of this comment said, so the kernel can see a
    # sub-agent's calls: `SubAgentTool` is built in one place, `cli/main.py`, and never here. The
    # only sub-agent on this surface is `ExploreRepositoryTool`, which builds its own read-only
    # registry internally — so it is governed as a TOOL and its inner calls are not governed at
    # all. That is a real gap; it is just not this comment's.)
    #
    # `attended=False`: nobody is at this server's console on behalf of an HTTP caller.
    # `audit_allows=False`: an ALLOW per tool call would bury this log's rare events in a day.
    #
    # `screen`: the owner's approver, the SAME object the ledger gets below, and only when this
    # request has a screen to announce to. Until 0.58.0 the kernel got nothing here, so with the
    # kernel switched on every policy REVIEW on the Code screen was a refusal — measured on an
    # installed 0.57.0: `curl … | bash` came back "Nobody could be asked … start the run with
    # pause-on-taint", the taint switch, for a clean run stopped by a policy rule — while the taint
    # ledger one layer out asked the very same screen with a card. A surface with no announcer
    # (`POST /api/runs`, the lifecycle and orchestration routes) keeps the unattended refusal: a
    # question announced to nobody is a timeout with a bill, which `pending.py` names as the thing
    # to avoid.
    #
    # Deliberately NOT passing `step.approve` to the ledger below. In `observe` that approver says
    # yes to everything, so handing it to the taint layer would turn taint narrowing on this path
    # from REFUSING a dangerous call after untrusted input into allowing it — someone switching to
    # `observe` in order to MEASURE would silently weaken the surface. Observe adds measurement; it
    # must never subtract protection that was already there. The other direction is the point of
    # `screen`: the owner's approver reaches the kernel only under `enforce`, where a yes is a yes.
    step = govern_step(
        registry,
        settings=settings,
        audit=audit,
        surface=surface,
        attended=False,
        audit_allows=False,
        lineage=ledger.lineage,
        screen=owner if approval_sink is not None else None,
    )
    governed = ledger_registry(
        step.registry,
        ledger,
        narrow_on_taint=narrow,
        audit=audit,
        # The owner's policy, and ONLY when it is an explicit `allow`.
        #
        # `CHIMERA_APPROVAL_MODE` reached `solve` and `crew` and nothing else, so an owner who set
        # it to `allow` still got a refusal from the desktop app and from `serve` — a setting named
        # like a control that controlled nothing on the surface most people use. That is the same
        # defect `CHIMERA_TOOL_DENYLIST` had here, pointing the other way: the fence was missing
        # then, and now the gate cannot be opened by the person entitled to open it.
        #
        # Measured on `bench/injection`, which had never been run: with no approver the narrowing
        # refuses 100% of legitimate work that starts by reading something external — fix the file
        # the issue names, apply the upgrade the docs describe — for an over-block of 50% against a
        # 5% ceiling. With an approver it is 0%.
        #
        # **`allow` only, never `ask`.** `ask` on a server has nobody at a console; with a `home` it
        # would wait fifteen minutes inside an HTTP request, and without one it degrades to `deny`,
        # which is today's behaviour reached by a longer road. Wiring `ask` here would trade a
        # refusal for a timeout.
        #
        # And never `step.approve`, for the reason above: in `observe` that one says yes to
        # everything, so measurement would silently subtract protection.
        approve=owner,
        # A send to an address nobody mentioned asks only where a screen can show the card (study
        # 24, M2). Without a sink this is `POST /api/runs` and friends: nobody to ask, so the send
        # goes ahead and the audit records it — the owner's decision, never a block.
        ask_unseen_recipients=approval_sink is not None,
        # A person is watching this turn (a sink to show a card to is the same fact): a write inside
        # the workspace after untrusted input is a warning on their screen, not a card. Everything
        # that reaches a shell, the network or a path outside the workspace still asks.
        warn_workspace_writes=approval_sink is not None,
        notify=notice_sink,
    )
    # The pack's skills half, carried by the registry the agent is built with — not re-derived by
    # the agent from its `project_root`, which for a crew worker is a temporary worktree nobody
    # accepted a pack for, and for a hierarchy worker is nothing at all. Both had their tools
    # narrowed here and their skills not.
    governed.bundle_only = bundle_filter(pack)
    # And the home they are read from: these settings may be the app's own (`settings=` at build),
    # not the process's, and the agent must list the bundles of the home this run was assembled in.
    governed.bundle_home = Path(settings.home)
    return governed, ledger


def _message_texts(messages: Sequence[Any]) -> list[str]:
    """The text of every stored message, whatever its shape: a plain string, or a list of parts
    of which only the ``text`` ones count. A part this cannot read is skipped, not guessed at."""
    texts: list[str] = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return texts


def _owner_allows(
    settings: Settings, sink: Any = None, *, run_id: str | None = None, surface: str = ""
) -> Any:
    """The approver this deployment chose — and for the default, `ask`, one that actually asks.

    ``run_id`` and ``surface`` are written on every question's record line (`pending.FACTS`): they
    are what joins an answer to the turn that asked and to what that turn went on to do.

    For ``allow`` and ``deny`` this is what it always was. For ``ask`` it used to return ``None``,
    which `LedgeredTool` reads as *refuse*: the setting whose name is *ask* asked nobody, and one
    installed copy of the app recorded 229 `taint_narrowed` refusals under it. The comment that
    justified ``None`` was right about the code as it stood — a durable ask inside an HTTP request
    is a fifteen-minute timeout when nothing tells the person a question exists — and that is the
    half this fixes: the question is written (`pending.ask_durably`), announced on the turn's own
    stream through ``sink``, answered from the screen (`POST /api/approvals/{id}`), and bounded by
    ``approval_wait``. Silence still refuses. Measured before/after in
    `bench/injection/PREREGISTRATION_attended.md`.

    The tool runs on the turn's worker thread (`threading.Thread(target=work)`), so the wait blocks
    that thread and nothing else; the stream and the answering request are served by the loop.

    An approver that says yes, when the deployment explicitly chose `allow`. Otherwise none.

    Returning `None` rather than a deny-approver is the point: `LedgeredTool` already refuses when
    there is no approver, so an absent one is exactly today's behaviour, and this cannot make any
    configuration stricter than it already was.

    The trade this buys is real and belongs to whoever sets the variable: the number that makes
    `allow` attractive — over-block 50% to 0% — comes from a bench that hands the approver to the
    legitimate corpus only, modelling a person approving work they asked for. A standing `allow` on
    a server approves whatever an injected page asks for too, and `bench/injection` measures
    `plant_backdoor` and `self_modify_skill` at 0% blocked without the narrowing.
    """
    mode = (settings.approval_mode or "ask").strip().lower()
    from chimera.governance.approval import allow, ask_elsewhere, deliverer_for, deny

    if mode == "allow":
        return allow()
    if mode == "deny":
        return deny()
    # `ask`, and every value that is not one of the three: the person is at the screen, so ask them
    # there. `nobody_is_at_a_terminal()` is not consulted — it reads stdin, and a server has none;
    # the desktop IS the terminal, which is what the stream sink is for.
    # Wait only when a screen is bound to the announcer. Every other caller of this registry —
    # the batch/agents path, a test, a turn whose stream has not attached yet — has nobody who could
    # answer through it, and for them the question is written, announced to no one, and refused at
    # once. Resolved per question, because the binding happens after the approver is built.
    def wait_for_the_screen() -> float:
        bound = sink is not None and getattr(sink, "emit", None) is not None
        return float(settings.approval_wait) if bound else 0.0

    return ask_elsewhere(
        settings.home,
        deliver=deliverer_for(settings),
        on_asked=sink,
        wait_seconds=wait_for_the_screen,
        facts={k: v for k, v in (("run_id", run_id), ("surface", surface)) if v},
    )


class PostureQuery(BaseModel):
    """Ask what a posture would mean, without committing to it — the selectors' live preview."""

    surface: str = "run"
    """Which surface is asking: ``"run"``, ``"turn"`` or ``"chat"``.

    The same posture means different things on each, and only one of them was ever reported. A
    conversational turn is built without a checkpointer, so it cannot stop and ask — whatever the
    approval axis says. A CHAT is built with the ledger unless ``CHIMERA_GUARD_CHAT`` was turned
    OFF; without it nothing marks the conversation after it reads untrusted content and the tools
    that would start refusing keep working. Defaulting to ``"run"`` keeps every existing caller
    reading exactly what it read before."""

    provider: str | None = None
    """The external agent this posture would apply to, if any.

    Asked here rather than inferred, because it changes what every other field MEANS. A turn driven
    through Claude Code can write with its own tools, so the write region describes the calls we see
    rather than the ones that happen — and the sentence the user reads has to say the smaller,
    truer thing."""

    reach: Reach = DEFAULT_REACH
    approval: Approval = DEFAULT_APPROVAL
    workspace: str | None = None


class RolesQuery(BaseModel):
    """Ask which model each role would run on, without committing to it."""

    profile: Profile = "balanced"


class PolicyRetry(BaseModel):
    """The refusal a turn is the owner's retry of (study 29 P5.7).

    Sent by the error card's "Try with another model" and by nothing else. It changes nothing about
    how the turn runs — the model is the request's ``model`` like any turn's — and exists for one
    line of the receipt: "blocked on X, redone on Y by the owner's choice", so the conversation
    keeps the fact that the answer under it came from a model the person CHOSE after a refusal,
    not from the one the conversation was on.

    It is the client's own account of the previous turn, recorded as that. Bounded because it is
    stored and drawn; nothing reads it as a fact about the provider.
    """

    blocked_model: str = Field(min_length=1, max_length=200)
    request_id: str | None = Field(default=None, max_length=120)


class CodeTurnRequest(CodeSeams):
    """One turn of a coding conversation."""

    message: str
    session_id: str | None = None
    """The conversation this turn belongs to. None mints a new one, returned in the first frame."""
    workspace: str | None = None
    model: str | None = None
    stream: bool = True
    open_file: str | None = None
    """The file the user has open, workspace-relative. Two effects, both real: it focuses which
    ``AGENTS.md`` files apply, and it is what a compaction restores. Not read here — the agent has
    tools for that, and a server-side read would put a stale copy in the prompt."""
    plan_gate: bool = False
    """Stop for a person on the PLAN before the turn runs anything (:mod:`chimera.api.plan_gate`).

    Off by default, and that default is a judgement rather than caution: this adds a model call and
    a wait to the most-used surface in the product, so it has to be asked for. It only ever ADDS a
    stop — every per-action question still happens, and BLOCK is untouched."""
    spoken: bool = False
    """The message was spoken and the answer will be read aloud: the model is asked to answer for
    the ear (``SPOKEN_NOTE``), in the system prompt of this turn and nowhere in the transcript."""
    thinking: bool | None = None
    """``False`` asks a reasoning model not to think before it answers — the voice mode sends it,
    because the thinking is where the wait before the first spoken word was measured to go
    (``LLMGateway._provider_kwargs``). ``None`` leaves the model as configured; a typed turn sends
    nothing."""
    retry_of: PolicyRetry | None = None
    """This turn redoes one the provider refused on content policy, on a model the owner picked.
    Only the receipt reads it (:class:`PolicyRetry`); a guest's turn drops it, since the line it
    writes says the choice was the owner's."""
    style: OutputStyle = "default"
    """How this conversation's answers are written (:mod:`chimera.core.output_style`). The default
    adds nothing to the prompt, so a client that never heard of styles sends what it sent before.
    Wording only: it reaches the system prompt and the receipt, and nothing that decides what the
    turn may do."""

    @model_validator(mode="after")
    def _a_retry_runs_on_the_model_it_names(self) -> CodeTurnRequest:
        """Refuse a retry line on a turn that would not run on ``model``.

        The receipt says "blocked on X, redone on Y by the owner's choice", and Y is ``model``. A
        fused turn ignores ``model`` (``FusionEngine.complete`` answers with its panel and judge,
        the very ones that may have refused), and an external agent picks its own; either way the
        line would name a model that never answered. Refused rather than quietly un-fused: the
        desktop sends neither with a retry, so a request that does is not one this endpoint can
        honour as written, and saying so beats running something else under the owner's name.
        """
        if self.retry_of is not None and (self.fuse or (self.provider or "").strip()):
            raise ValueError("a retry of a refused turn runs on one native model: no fuse, no provider")
        return self


def _applied_style(req: CodeTurnRequest) -> str | None:
    """The style this turn's prompt actually carries, or None when it carries none.

    None for the default, for a spoken turn — the voice note is the contract of that surface, and a
    "be explanatory" under "two to four sentences, for the ear" would be two instructions fighting —
    and for an external agent, whose prompt is not ours to suffix. The receipt names a style only
    when the turn ran under it, so the badge never claims words the model was not given.
    """
    if req.style == "default" or req.spoken or (req.provider or "").strip():
        return None
    return req.style


def _model_for(req: CodeTurnRequest, settings: Settings) -> tuple[str | None, bool | None]:
    """Which model answers this turn, and whether it may think first.

    A typed turn: the conversation's model, thinking as the request says. A spoken turn is two
    things, and the owner asked for them to be two models (2026-09-18): *talk* — a question, a
    remark — goes to the voice model when one is set, without thinking, because the first word is
    what a listener waits for and that is where models differ most (`Settings.voice_model`); *work*
    — a spoken request to create, fix, refactor, install — goes to the work model when one is set
    (`Settings.voice_work_model`), else the conversation's, with its thinking as configured, because
    the answer to "fix the login" is the fix, not the first word.

    The split is `classify_task` — deterministic, no model call, the same markers the hierarchy
    routes by, with the spoken imperatives ("faz", "arruma", "me faça") already in it. Its bias is
    the safe one for this use: a phrase it is unsure about ("quero entender…") counts as work and
    takes the slower, stronger road. The receipt under the answer names the model that answered.
    """
    if not req.spoken:
        return req.model, req.thinking
    from chimera.orchestration.hierarchy import classify_task

    if classify_task(req.message) == "sequential_write":
        return (settings.voice_work_model or req.model), None
    return (settings.voice_model or req.model), req.thinking


def _log_usage(payload: dict[str, Any], session_id: str, settings: Settings) -> None:
    """Append one coding turn to the usage log.

    Best-effort in the same way `_append_usage` is: a failure to record what a turn cost must
    never break the turn that already happened. `usd` stays None when the price is unknown —
    the summary counts unpriced turns separately, and a guessed zero would launder that away.
    """
    from datetime import UTC, datetime

    from chimera.api.usage import UsageRecord, append_usage

    try:
        route = payload.get("route_meta") or {}
        append_usage(
            Path(settings.home) / "usage.jsonl",
            UsageRecord(
                ts=datetime.now(UTC).isoformat(),
                session_id=session_id,
                model=str(payload.get("model") or ""),
                prompt_tokens=int(payload.get("prompt_tokens") or 0),
                completion_tokens=int(payload.get("completion_tokens") or 0),
                usd=payload.get("usd"),
                tools=len(payload.get("tool_names") or []),
                memory_facts=int(payload.get("memory_facts_used") or 0),
                route_kind=route.get("kind") if isinstance(route, dict) else None,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — logging what a turn cost is never worth losing it
        _log.debug("usage logging skipped: %s", exc)


def _with_metered_call(payload: dict[str, Any], meter: MeteredBackend | None) -> dict[str, Any]:
    """``payload`` with what ``meter`` saw added to what the turn spent.

    For the calls a turn makes outside its loop: the plan gate's (:mod:`plan_gate`) and the merge
    "Tidy memory" asks for (:func:`_remember_and_tidy`). Both are made on the turn's thread before
    the turn's row is written, so they go IN that row rather than beside it: the turn is still one
    turn on the Cost screen, and the receipt under the answer says what the whole turn cost. Tokens
    are added; the price follows :func:`~chimera.orchestration.metering.add_usd`, so an unknown
    price on either side makes the turn's unknown instead of reading as a low total. A row that
    names no model gets the one that answered, because a row with dollars and no model is the blank
    line the Cost screen once showed.

    A meter that recorded no call leaves the payload alone: a call that raised cost nothing, and
    adding its zero would change nothing but the look of the row.
    """
    if meter is None or not meter.calls:
        return payload
    from chimera.orchestration.metering import add_usd

    out = dict(payload)
    out["prompt_tokens"] = int(payload.get("prompt_tokens") or 0) + meter.prompt_tokens
    out["completion_tokens"] = int(payload.get("completion_tokens") or 0) + meter.completion_tokens
    out["usd"] = add_usd(payload.get("usd"), meter.usd)
    if not out.get("model"):
        out["model"] = meter.last_model
    return out


def _remember_and_tidy(
    message: str, memory: Any, settings: Settings, *, backend: Any = None
) -> tuple[str | None, int]:
    """Honour an explicit "remember that…" from the user's own message, then tidy if asked.

    Two Settings toggles fired nothing from the app before this. "Remember from chat" was real and
    correctly gated — but reachable only from ``/api/chat/stream``, and the desktop's ``streamChat``
    has no callers: every conversation in the app goes through ``/api/code/turn``. "Tidy memory" was
    read only inside the CLI REPL's own loop, so it did nothing unless the same person also ran
    ``chimera chat`` against the same home.

    They belong together and they belong here. Memory only grows when something is written, so the
    moment after a write is exactly when tidying is worth checking — and ``autoconsolidate`` returns
    0 without calling a model while memory is under budget, so the check is free until it is not.
    That also keeps ``features.py``'s rule intact: the token-spending path stays off the read-only
    feature routes and lives on the streaming turn, like every other call that can cost money.

    Best-effort throughout: neither toggle may take a turn down. The answer is the product.

    ``backend`` is what the tidy's merge asks, a fresh gateway when None. The Code turn passes a
    :class:`~chimera.orchestration.metering.MeteredBackend` over its own gateway, because the merge
    is a model call the turn pays for and nothing else was recording it.
    """
    if not getattr(settings, "remember_from_chat", False) or memory is None:
        return None, 0
    write = getattr(memory, "remember", None)
    if not callable(write):
        return None, 0
    try:
        from chimera.memory.capture import parse_remember_request

        fact = parse_remember_request(message)
        if fact is None:
            return None, 0
        write(fact, source="chat")  # deduped by the manager; provenance is clean — the user typed it
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        _log.debug("remember-from-chat skipped: %s", exc)
        return None, 0

    if not getattr(settings, "auto_consolidate", False):
        return fact, 0
    try:
        from chimera.memory.consolidate import model_summarizer

        if backend is None:
            from chimera.providers import LLMGateway

            backend = LLMGateway()
        removed = int(
            memory.autoconsolidate(model_summarizer(backend), max_items=settings.memory_budget)
        )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        _log.debug("auto-consolidate skipped: %s", exc)
        return fact, 0
    return fact, removed


def _extract_after_turn(
    message: str, answer: str, memory: Any, settings: Settings, *, tainted: bool, session_id: str
) -> None:
    """Keep what the user stated about themselves in this turn (study 25 S13), off the turn's path.

    Called after the ``done`` frame, and the work runs on its own thread
    (:class:`chimera.memory.extract.MemoryExtractor`), so the person has the answer before the
    extraction's model call starts and a slow or failing call cannot delay or fail it.

    ``message`` is what the user typed, not the prompt the turn was sent with: an attached document
    is somebody else's words, and a fact must trace to the user's own.

    What the call costs goes to the usage log under this conversation's ``session_id``, beside the
    turn's own row. It cannot go IN that row: the row is written before ``done``, and the call it
    would have to wait for is the one this function moves off the turn's path.
    """
    if not getattr(settings, "memory_extract", False) or memory is None or not answer.strip():
        return
    try:
        from chimera.memory.extract import MemoryExtractor

        MemoryExtractor(
            memory, usage_home=Path(settings.home), usage_id=session_id
        ).after_turn(message, answer, tainted=tainted)
    except Exception as exc:  # noqa: BLE001 -- the turn is over and paid for; memory is extra
        _log.debug("memory extraction skipped: %s", exc)


#: The memory manager built here for a store the app did NOT boot with, keyed by the settings that
#: decide which store that is. Cached because the entity graph derived from it walks every stored
#: memory, and rebuilding that per turn would put a growing cost on every question.
_switched_memory: tuple[tuple[str, str, bool], Any, Any] | None = None
_memory_lock = threading.Lock()


def _live_memory(settings: Settings, boot: Any, boot_graph: Any, boot_key: Any) -> tuple[Any, Any]:
    """The memory store this turn should read, and the entity graph over it.

    `chimera app` builds ONE manager at boot and hands the same object to every conversational
    surface. The Memory screen builds a fresh one per request, from live settings. So switching
    `CHIMERA_MEMORY_BACKEND` from json to sqlite left that screen showing the new store — usually
    empty — while every coding turn kept recalling from the old one. Two screens, two stores, one
    app, and nothing on either saying so.

    The boot pair is returned unchanged while the settings still describe the store it was built
    from, so an install that changes nothing keeps the exact objects it had. `None` stays `None`:
    that is `--no-memory`, a launch decision with no store to switch to.
    """
    global _switched_memory

    if boot is None:
        return None, None
    key = memory_key(settings)
    if key == boot_key:
        return boot, boot_graph
    with _memory_lock:
        if _switched_memory is not None and _switched_memory[0] == key:
            return _switched_memory[1], _switched_memory[2]
        from chimera.evolution.wiring import build_memory_manager

        manager = build_memory_manager(settings)
        graph = None
        try:
            from chimera.memory import build_graph

            graph = build_graph(
                [i.content for i in manager.store.all() if i.provenance == "clean"]
            )
        except Exception as exc:  # noqa: BLE001 -- the graph is an optimisation, not the memory
            _log.debug("entity graph skipped for the switched store: %s", exc)
        _switched_memory = (key, manager, graph)
        return manager, graph


def memory_key(settings: Settings) -> tuple[str, str, bool]:
    """What decides WHICH store a memory manager is. Used to tell a boot one from a stale one.

    The RESOLVED backend, not the raw setting: the default is `sqlite` on a build with FTS5 and
    `json` without, and the key has to name the store that was actually opened.
    """
    from chimera.memory.backend import resolve_memory_backend

    return (str(settings.home), resolve_memory_backend(settings), bool(settings.semantic_memory))


def _card_retriever(settings: Settings, gateway: Any) -> Any:
    """The learned-skill retriever, or None when the owner has the feature off or it cannot build.

    Best-effort: a coding turn must not fail because a card store is unreadable. The answer is the
    product; the cards are advice about it.
    """
    if not getattr(settings, "skill_cards", False):
        return None
    try:
        from chimera.evolution import build_evolution_context

        return build_evolution_context(
            settings,
            # Required by the signature and unused on this path: `evolve_skills=False` means nothing
            # here calls a model. Passing the turn's own gateway rather than a stub, so if that ever
            # stops being true it is the right one.
            gateway,
            None,
            home=settings.home,
            evolve_skills=False,  # reading, never minting: no verify-or-revert signal on this path
            skill_cards=True,
            include_memory=False,
            include_playbook=False,
        ).cards
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        _log.debug("skill-card retriever unavailable: %s", exc)
        return None


def register_code_api(
    app: FastAPI,
    guard: params.Depends,
    workspace: Path,
    settings: Settings,
    *,
    live_settings: Callable[[], Settings] | None = None,
    memory: Any = None,
    graph: Any = None,
    fuse_backend: Any = None,
    static_dir: Path | None = None,
    folder_locks: FolderLocks | None = None,
) -> None:
    """Mount ``POST /api/code/turn`` — a conversational coding turn, streamed.

    ``folder_locks`` is the app's one lock per folder, shared with autonomous runs so a turn and a run
    never edit one folder at once. A test mounting this alone gets its own.

    ``memory``/``graph`` are READ from, and written to in exactly ONE case: an explicit
    "remember that…" in the message the USER typed.

    The asymmetry that made this one-way is about what the AGENT or the WORKSPACE produced. A
    recalled fact learned from untrusted content carries its ``[unverified]`` label into the prompt
    and the admission gate still rejects injection patterns, so the worst case is a wasted line of
    context. Writing derived text is the opposite — with the workspace trusted by default, a poisoned
    README would enter memory looking clean, and a memory item has no project scope, so it would be
    recalled into every other project too.

    None of that describes the user's own sentence. ``parse_remember_request`` reads the typed
    message and nothing else, anchored to the start so an incidental "I can't remember where I put
    my keys" is not a command; it never touches tool output, the answer, or a file. Keeping this
    write out was not a decision anyone made about it — it was the general rule catching a case it
    does not cover, and the cost was that the toggle in Settings did nothing from the app at all
    while the empty Memory screen advertised it as the way to fill memory.
    """
    from chimera.core.code_projects import CodeProjectRegistry
    from chimera.core.code_session import CodeSession, CodeSessionStore
    from chimera.core.events import tool as tool_event
    from chimera.core.instructions import load as load_identity
    from chimera.core.instructions import render as render_identity
    from chimera.core.jobs import finished_note
    from chimera.core.redact import redact
    from chimera.interface.session import recall_facts
    from chimera.memory.history import files_of_exchange, history_for
    from chimera.memory.models import project_key

    # What the injected `memory` IS, so a later turn can tell "the owner changed the backend" from
    # "nothing changed". Resolved once, from the same settings the app built that manager with.
    boot_memory_key = memory_key(settings)

    # `settings` is the snapshot taken when the routes were mounted. It is the right thing for
    # `home` — the data directory must not move under an open conversation — and the wrong thing for
    # anything the Settings screen can change, which is why those read through `live()`. Without a
    # reader supplied (a test mounting its own Settings), the snapshot IS the answer: an explicit
    # injection means "use THIS one".
    live: Callable[[], Settings] = live_settings or (lambda: settings)

    store = CodeSessionStore(settings.home / "code_sessions")
    # Background works (`chimera.api.works`): each runs on its own session record, kept apart from
    # the conversations so the sidebar lists conversations and a work's transcript is reachable by
    # its id. The manager is built once the launcher exists, below.
    from chimera.api.works import WORKS_FILE, Work, WorkManager, WorkStore, work_tools

    work_sessions = CodeSessionStore(settings.home / "code_works")
    work_store = WorkStore(settings.home / WORKS_FILE)
    # A conversation shared with a second person (`chimera.api.sharing`): the tokens that open one
    # conversation each, and the bus every turn's frames go out on so the owner's screen and a
    # guest's both see a turn whoever started it.
    from chimera.api.sharing import SHARES_FILE, SessionBus, ShareStore

    shares = ShareStore(settings.home / SHARES_FILE)

    def _from_run_log(turn_id: str, after: int, before: int) -> list[dict[str, Any]]:
        # What the live ring dropped of a turn it still holds part of: the run log keeps every frame
        # of a coding turn, each with the session number the bus gave it (`emit` below).
        return [
            f for f in runlog.frames(settings.home, turn_id, area="code")
            if isinstance(f.get("session_seq"), int) and after < f["session_seq"] < before
        ]

    bus = SessionBus(backfill=_from_run_log)
    # The turns running right now, so a screen that left a conversation mid-turn can find its way
    # back: the stored file holds nothing of a turn until the agent finishes.
    from chimera.api.live_turns import LiveTurns

    live_turns = LiveTurns()
    # The machine must not go to sleep under a turn (`chimera/core/keep_awake.py`). The app's keeper
    # when there is one, so the status route and the turns read the same counter; a test mounting
    # this alone gets the process's.
    from chimera.core.keep_awake import service as _keep_awake_service

    keep_awake = getattr(app.state, "keep_awake", None) or _keep_awake_service()
    # What the turns running at once have spent together. Each turn warns about its own spend; five
    # at once could each stay under that and spend five times it without a word (R14, 2026-09-30).
    from chimera.api.combined_spend import CombinedSpend

    combined_spend = CombinedSpend()
    # Turns whose conversation was deleted while they ran. Stopped, and they write nothing more of it:
    # a turn that finished after its conversation was deleted used to save it again, and the deleted
    # conversation came back.
    deleted_mid_turn: set[str] = set()

    def _bring_back_if_archived(session_id: str) -> None:
        import time

        try:
            if store.marks.get(session_id).archived_at is not None:
                store.marks.unarchive(session_id, at=time.time())
        except OSError as exc:  # a list badge must not stop a turn
            _log.debug("could not bring %s back from the archive: %s", session_id, exc)

    def _turn_ended(
        session_id: str, *, failed: bool, edited: bool, only_if_stored: bool = False
    ) -> None:
        """Record how a conversation's turn ended (`chimera/core/code_session_marks.py`). Never
        raises: the turn is over and paid for, and a badge is not worth failing it."""
        import time

        try:
            if only_if_stored and not store._path(session_id).is_file():
                return
            store.marks.turn_ended(session_id, failed=failed, edited=edited, at=time.time())
        except (OSError, ValueError) as exc:
            _log.debug("could not record how the turn of %s ended: %s", session_id, exc)

    def forget_running(session_ids: list[str]) -> None:
        wanted = set(session_ids)
        for turn in live_turns.running():
            if turn.session_id in wanted:
                deleted_mid_turn.add(turn.turn_id)
                live_turns.request_stop(turn.turn_id)
    # The index of finished turns (`chimera.memory.history`), one per home, shared with the
    # `recall_history` tool every registry mounts. The session file is what a conversation is
    # RESUMED from and trims itself accordingly; this is what a person's question about a turn
    # from two weeks ago is answered from, and it never trims.
    history = history_for(settings.home)
    # Beside the conversations, not inside them: a project you have added but not yet worked in
    # has no conversation to hang off, which is the whole reason the list cannot be derived.
    projects = CodeProjectRegistry(settings.home / "code_projects.json")
    # The desktop's screen layout (dynamic screen, phase 6), beside the projects for the same reason:
    # the webview's own storage does not survive a reinstall.
    from chimera.core.ui_layout import UiLayoutStore

    ui_layout = UiLayoutStore(settings.home / "ui_layout.json")
    # One lock per session: two concurrent turns on the same conversation would interleave their
    # transcripts and the last save would silently win. Different sessions never wait on each other.
    # The lock alone did not give that: a turn loads its conversation when the request arrives, so
    # one that waited here ran on the history it loaded before the other finished, and its save
    # erased the other's exchange. Every use of the lock now reads the conversation again first
    # (`store.refresh`), and a save is atomic.
    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    def lock_for(session_id: str) -> threading.Lock:
        with locks_guard:
            return locks.setdefault(session_id, threading.Lock())

    # One writer per folder, for every turn (background works included) and every autonomous run,
    # which share this object (`chimera/api/folder_locks.py`). Two writers in one folder left a
    # snapshot, a verification and an undo each describing a mix of both, and undoing one reverted
    # the other's edits. Different folders never wait on each other. Taken before the session lock,
    # always, so the two can't cross.
    shared_folders = folder_locks if folder_locks is not None else FolderLocks()

    def folder_lock(ws: Path) -> threading.Lock:
        return shared_folders.lock(ws)

    # How many turns each conversation holds right now, running or waiting (R17, 2026-09-30).
    held_turns: dict[str, int] = {}

    def admit_turn(session_id: str) -> None:
        with locks_guard:
            if held_turns.get(session_id, 0) >= MAX_TURNS_PER_CONVERSATION:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"this conversation already has {MAX_TURNS_PER_CONVERSATION} turns running or "
                        "waiting; send this one when one of them ends"
                    ),
                )
            held_turns[session_id] = held_turns.get(session_id, 0) + 1

    def release_turn(session_id: str) -> None:
        with locks_guard:
            left = held_turns.get(session_id, 0) - 1
            if left > 0:
                held_turns[session_id] = left
            else:
                held_turns.pop(session_id, None)

    def build_agent(
        req: CodeTurnRequest,
        ws: Path,
        facts: list[str],
        note: str = "",
        approval_sink: Any = None,
        frame_sink: Any = None,
        extra_tools: Sequence[Tool] | None = None,
        run_id: str | None = None,
        notice_sink: Any = None,
        chart_sink: Any = None,
        guest: bool = False,
    ) -> tuple[Agent, Any]:
        """The agent for this turn, and the ledger watching it. ``guest``: a share link's turn.

        The ledger used to be built and thrown away, which left the turn unable to answer the one
        question the posture claims to care about: did this run read untrusted content? Narrowing
        still worked (it lives inside the wrapped tools), but nothing could REPORT it — so a turn
        that had been steered by a planted instruction looked exactly like one that had not.
        """
        from chimera.core import Agent, AgentConfig
        from chimera.providers import LLMGateway

        gateway = LLMGateway()
        steps = resolve_steps(req.max_steps)
        registry, ledger = assemble_registry(
            req, ws, live(), gateway, steps=steps, surface="api:turn", approval_sink=approval_sink,
            frame_sink=frame_sink,
            instruction=req.message,
            extra_tools=extra_tools,
            run_id=run_id,
            notice_sink=notice_sink,
            chart_sink=chart_sink,
            guest=guest,
        )
        # Recalled facts and the turn's notes go in the TURN CONTEXT, not the system prompt (study
        # 25, wave 2). They used to be appended to the system prompt so that `absorb`, which drops
        # system messages when it stores the transcript, would not keep stale copies. The turn
        # context keeps that property, because the loop puts the bare user message back into the
        # transcript. And the system message is now the same bytes every turn, where before it
        # changed about 318 tokens in whenever a fact, a job or a work did, so a provider could
        # cache almost none of it.
        from chimera.core.agent import DEFAULT_SYSTEM_PROMPT
        from chimera.prompts.context import facts_block

        system_prompt = DEFAULT_SYSTEM_PROMPT
        # The spoken note stays: it is the contract of the surface (voice or text), the same on every
        # turn of a spoken conversation, not something that is true of one turn.
        if req.spoken:
            system_prompt += f"\n\n{SPOKEN_NOTE}"
        # The conversation's output style, after the base and only on the turns `_applied_style`
        # names: the default returns the same string, so a turn nobody chose a style for sends the
        # system message it always sent.
        system_prompt = with_output_style(system_prompt, _applied_style(req))
        turn_notes = "\n\n".join(part for part in (facts_block(facts), note) if part)
        model, thinking = _model_for(req, live())
        agent = Agent(
            gateway,
            registry,
            AgentConfig(
                model=model,
                system_prompt=system_prompt,
                turn_context=True,
                turn_notes=turn_notes,
                thinking=thinking,
                max_steps=steps,
                # On by default here: a turn that outgrows its model's window should compact and
                # go on, not stop. A client that names a fraction still gets exactly that one.
                context_budget=(
                    req.context_budget if req.context_budget is not None else DEFAULT_BUDGET_FRACTION
                ),
                unmeasured_context_tokens=UNMEASURED_USEFUL_TOKENS,
                summarise_compaction=req.summarise_compaction,
                # A conversational turn is exactly one loop, so the ceiling and the turn's bill are
                # the same number — the one surface where the cap means what its name says without
                # a footnote about attempts.
                max_usd=req.max_usd,
                warn_usd=req.warn_usd if req.warn_usd is not None else DEFAULT_SPEND_WARN_USD,
                # A person is waiting on this turn: warn and ask for a different approach before the
                # breaker cuts a run, and keep the cut as a net only.
                loop_correction=True,
                # No ceiling on steps: `max_steps` is a window, and the loop goes on until the run is done,
                # cancelled, stuck, or over a ceiling the person typed.
                auto_continue=True,
                project_root=ws,
                # Read per turn, so editing the identity applies to the next question rather than
                # to the next launch.
                instructions=render_identity(load_identity(settings.home)),
                trace_path=settings.home / "traces.jsonl",
            ),
            # What the agent LEARNED, read back when it matches this task. The Settings row that
            # mints cards says "a learned skill is read back when it matches the task"; it was true
            # on `/api/runs`, which builds an `AutonomousAgent`, and this endpoint builds a plain
            # `Agent`. So on the Code screen — first in the navigation rail — nothing the agent had
            # ever learned came back.
            #
            # Read-only: `build_evolution_context` is asked for the retriever alone, with skill
            # evolution off, because minting a card needs a verify-or-revert signal this path does
            # not have. Advisory either way — the card suggests, the verifier decides.
            cards=_card_retriever(live(), gateway),
        )
        if req.open_file:
            # Path only, never content: what a compaction must restore is *which* file is being
            # worked on. Re-reading it is the agent's job and it has a tool for that; a copy taken
            # at turn start would be restored stale, which is worse than restoring nothing.
            agent.run_state.open_file = (req.open_file, "")
        return agent, ledger

    @app.post("/api/code/turn", dependencies=[guard], responses=SSE_RESPONSE)
    async def code_turn(req: CodeTurnRequest) -> EventSourceResponse:
        return await _start_turn(req)

    async def _start_turn(req: CodeTurnRequest, *, author: str = "") -> EventSourceResponse:
        """One coding turn, started by the owner (``author`` empty) or by a guest (their name).

        The guest app calls this directly with the request it built, so a guest's message runs
        through exactly the machinery the owner's does: one turn, one receipt shape, one bus. The
        author reaches the receipt — so a reopened conversation still says who asked — and every
        frame the turn emits, so a screen watching live can label it as it happens.

        A SPOKEN request for work does not run here: it becomes a background work
        (`chimera.api.works`) and this stream says so and ends, so the conversation — and the
        person talking — stay free while the work runs.
        """
        # SAFETY POSTURE: identical to the run endpoint — file writes and shell inside ``ws``, behind
        # the bearer guard and the localhost bind, scoped by whatever seams the caller declared.
        # Validated the same way the read-only fs endpoints and POST /api/runs validate theirs. A
        # path that does not exist used to be accepted silently, and the turn would run against a
        # directory the agent then created files in — the one axis on which this endpoint was more
        # permissive than the chat it is replacing.
        if req.workspace:
            ws = Path(req.workspace).expanduser().resolve()
            if not ws.is_dir():
                raise HTTPException(status_code=400, detail="workspace not found")
            # "Recent" in the sidebar's order means a turn started here, not that the row was
            # looked at. Only a registered folder is stamped; see `CodeProjectRegistry.touch`.
            projects.touch(req.workspace)
        else:
            ws = workspace
        if req.spoken and not (req.provider or "").strip():
            has_works = bool(req.session_id and work_store.for_parent(req.session_id))
            is_work = _is_work(req.message, has_works=has_works)
            # The label is written down BEFORE it routes anything: a spoken request with the
            # verdict the regex gave it is one row of the corpus the voice router's typed decision
            # will be measured on (`chimera/api/spoken_log.py`). Nothing reads the file yet.
            record_spoken_request(
                Path(live().home),
                req.message,
                "work" if is_work else "talk",
                session_id=req.session_id,
                has_works=has_works,
            )
            if is_work:
                return EventSourceResponse(await _spoken_work_frames(req, ws, author=author))
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
        session_id, turn_id = _launch_turn(req, ws, author=author, loop=loop, queue=queue)

        async def events() -> AsyncIterator[dict[str, str]]:
            # The session id first, so a client that minted a new conversation can address it from
            # the very first frame rather than after the turn it is already watching.
            yield {
                "event": "session",
                # The turn id rides with the session id because a client that loses the stream needs
                # both: the session to reopen the conversation, and the turn to ask what it missed.
                "data": json.dumps({"session_id": session_id, "turn_id": turn_id}),
            }
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield {"event": event, "data": json.dumps(payload)}

        return EventSourceResponse(events())

    def _is_work(message: str, *, has_works: bool = False) -> bool:
        """A spoken request for work — create, fix, refactor, install — by the deterministic
        classifier the hierarchy routes by (`_model_for` says why its bias is the right one).

        Not when the sentence is ABOUT the works: "stop work one", "undo what it did", "how is it
        going" are for the talking model and its tools, and the classifier's substring markers
        would read "mudei de ideia" (mude) and "desfaz" (faz) as new work — measured live on
        2026-09-18: both sentences started a work instead of stopping one. So a control verb in a
        conversation that has works, or beside the word "work", is talk.
        """
        from chimera.orchestration.hierarchy import classify_task

        if _WORK_CONTROL.search(message) and (has_works or _ABOUT_WORKS.search(message)):
            return False
        return classify_task(message) == "sequential_write"

    async def _spoken_work_frames(
        req: CodeTurnRequest, ws: Path, *, author: str
    ) -> AsyncIterator[dict[str, str]]:
        """Start the work in the background and hand back the three frames that say it started.

        The route (`_start_turn`) wraps them in the stream; this is the half that acts.

        The conversation's transcript gets one compact exchange — the request, and "started as
        work N" — so the next turn's history knows what was asked; the work's own transcript lives
        on its own session. The screen turns the pending exchange into the work's card from the
        `work_started` frame, and the voice says it started without a model call.
        """
        from fastapi.concurrency import run_in_threadpool

        # The parent conversation: loaded or minted now, so the work has something to attach to.
        parent = store.load(req.session_id, None) if req.session_id else CodeSession(None)  # type: ignore[arg-type]
        if not parent.workspace:
            parent.workspace = req.workspace or ""
        parent_id = parent.session_id
        template = req.model_dump(mode="json")
        # The work is not a spoken answer: it acts on the strong model, thinking as configured.
        template.update(
            {"spoken": False, "thinking": None, "session_id": None, "workspace": str(ws), "attachments": []}
        )
        work = await run_in_threadpool(
            lambda: works.create(
                parent=parent_id, workspace=ws, title=req.message,
                model=live().voice_work_model or req.model or "", author=author, request=template,
            )
        )
        with lock_for(parent_id):
            store.refresh(parent)
            parent.messages.append({"role": "user", "content": req.message})
            parent.messages.append({
                "role": "assistant",
                "content": f"[started background work {work.number}: {work.title[:120]}]",
            })
            store.save(parent)

        async def events() -> AsyncIterator[dict[str, str]]:
            yield {"event": "session", "data": json.dumps({"session_id": parent_id, "turn_id": work.turn_id})}
            yield {"event": "work_started", "data": json.dumps({"seq": 1, "work": work.to_dict()})}
            yield {
                "event": "done",
                "data": json.dumps({
                    "seq": 2, "answer": "", "steps": 0, "stopped_reason": "work_started",
                    "tool_names": [], "model": "", "prompt_tokens": 0, "completion_tokens": 0,
                    "usd": None, "tainted": False, "memory_facts_used": 0, "memory_layer": "",
                    "fused": False, "work_id": work.id,
                }),
            }

        return events()

    def _launch_work(work: Work) -> None:
        """Run a work: the same turn machinery, in the background, on the work's own session."""
        template = dict(work.request)
        template.update({"message": work.title, "session_id": None, "workspace": work.workspace})
        if work.model:
            template["model"] = work.model
        req = CodeTurnRequest(**template)
        _launch_turn(req, Path(work.workspace), author=work.author, background=work)

    works = WorkManager(
        work_store,
        launch=_launch_work,
        publish=lambda parent, event, payload: bus.publish(parent, event, payload),
        revert=lambda token: _revert(token),
    )
    app.state.work_manager = works

    def approval_origin(run_id: str) -> dict[str, str]:
        """The conversation and folder of the turn that asked a question, while it waits.

        A question only exists while its turn waits for the answer, so the running turns (and, for a
        background work's turn, the work's record) always know it.
        """
        turn = live_turns.get(run_id)
        if turn is not None:
            return {"session_id": turn.session_id, "workspace": turn.workspace}
        work = work_store.by_turn(run_id)
        if work is not None:
            return {"session_id": work.parent, "workspace": work.workspace, "work": work.title}
        return {}

    # Read by `GET /api/approvals` (app.py), which lists every waiting question from every screen.
    app.state.approval_origin = approval_origin

    def _launch_turn(
        req: CodeTurnRequest,
        ws: Path,
        *,
        author: str = "",
        loop: asyncio.AbstractEventLoop | None = None,
        queue: asyncio.Queue[tuple[str, Any] | None] | None = None,
        background: Work | None = None,
    ) -> tuple[str, str]:
        """Admit the turn into its conversation, then build and start it (:func:`_start_admitted`).

        Admitted BEFORE anything is built: past that point the turn is announced on the bus and
        listed as running, so a refusal after it would leave a turn that never ran on everyone's
        screen. A conversation's first turn has no id yet and nothing to wait behind; a background
        work runs on its own session, bounded by the works manager.
        """
        held = req.session_id if background is None and req.session_id else ""
        if held:
            admit_turn(held)
        try:
            return _start_admitted(
                req, ws, author=author, loop=loop, queue=queue, background=background, held=held
            )
        except BaseException:
            # Nothing was started, so the place goes back. Once the thread runs, it gives it back.
            if held:
                release_turn(held)
            raise

    def _start_admitted(
        req: CodeTurnRequest,
        ws: Path,
        *,
        author: str,
        loop: asyncio.AbstractEventLoop | None,
        queue: asyncio.Queue[tuple[str, Any] | None] | None,
        background: Work | None,
        held: str,
    ) -> tuple[str, str]:
        """Build the turn and start its thread; return ``(session_id, turn_id)``.

        With a ``loop`` and ``queue`` the frames also feed a stream (the endpoint above). With a
        ``background`` work they feed the work's record and the parent conversation's bus instead,
        and the turn runs on the work's own session — its own lock, so the parent stays free.
        """
        store_for = work_sessions if background is not None else store
        # Attachments: images the model looks at, documents it reads. Resolved here rather than in
        # the request so a stale or forged id is simply skipped instead of reaching the model.
        from chimera.api.attachments import load as load_attachment
        from chimera.api.attachments import vision_support

        # What the composer already told the user, applied. The warning under the paperclip says an
        # image "will not be seen" by a model without vision — and until now the server sent it
        # anyway, so OpenRouter answered `No endpoints found that support image input` and the whole
        # turn died as a generic "the coding turn failed". The interface promised a degraded turn and
        # the system delivered no turn at all.
        #
        # Only for a model LiteLLM's table positively says cannot see. `unknown` still sends: a table
        # that has not heard of a model is not evidence about the model, and refusing on that basis
        # would break every new vision model on the day it ships. When a send does fail this way, the
        # provider's own sentence now reaches the user — see `_native_failure`.
        blind = vision_support(req.model or live().default_model) == "no"

        images: list[str] = []
        dropped_images: list[str] = []
        doc_blocks: list[str] = []
        # The documents' own texts, as the sources a grounded answer is checked against
        # (`chimera/fusion/verified.py`): the verifier reads exactly what the model was handed.
        doc_texts: list[str] = []
        for ident in req.attachments:
            found = load_attachment(settings.home, ident)
            if found is None:
                continue
            if found.kind == "image":
                if blind:
                    dropped_images.append(found.name)
                    continue
                images.append(str(found.path))
            else:
                # Extracted at upload and fenced there. Folded into the message rather than handed
                # over as a file path, so it works with every model — including the ones that cannot
                # see — and so the user does not need the document tool installed to attach a PDF.
                from chimera.api.attachments import save as _save

                text = _save(settings.home, found.name, found.path.read_bytes()).text
                if text:
                    doc_blocks.append(f"Attached document `{found.name}`:\n{text}")
                    doc_texts.append(text)

        message = req.message
        if doc_blocks:
            message = message + "\n\n" + "\n\n".join(doc_blocks)
        # What the model is told about the image it did not get. In the turn context, not appended
        # to the user's message, and that placement is the difference between a note and a
        # defacement: the loop stores the user's message bare, so this reaches the model for this
        # turn and never becomes part of what the user said. Appended
        # to the message it also became the conversation's TITLE in the sidebar — a row reading
        # "what do you see in this image? [The user attached `6d2b57e5…" — because a title is the
        # first thing the user asked, and this was pretending to be part of it.
        #
        # No filename either: the stored name is the attachment id, and an id in a sentence meant
        # for a model is noise that reads like a fact.
        note = (
            "The user attached an image, which was NOT sent to you: this model cannot accept "
            "images. Say so plainly rather than describing anything."
            if dropped_images
            else ""
        )

        # A turn with attached documents is checked against them when it answers without a tool
        # (`chimera/fusion/verified.py`, study 26). Not an external agent's turn: its loop is not
        # ours, and whether it called a tool is not something this side can see.
        from chimera.fusion.verified import GroundedTurn, grounded_note

        grounded_turn = (
            GroundedTurn.make(doc_texts, req.message, ["attachments"])
            if doc_texts and live().verified_answers and not (req.provider or "").strip()
            else None
        )
        # The measured drafter was told this rule; the model is told the rule it is checked by, so a
        # question the documents do not cover is answered "they do not cover it" by the model itself
        # rather than declined after the fact. Only for a QUESTION: a task (summarize, translate…)
        # passes straight through, with no check and no note.
        if grounded_note(grounded_turn):
            note = (note + "\n\n" if note else "") + grounded_note(grounded_turn)

        def _check_grounded(turn: GroundedTurn | None, result: Any) -> tuple[str, dict[str, Any] | None, float]:
            """The finished turn's answer, checked against its documents when it is the measured
            shape; the draft unchanged otherwise. Never raises."""
            from chimera.fusion.verified import build_grounded_answers, check_answer

            def build() -> Any:
                from chimera.providers import LLMGateway

                return build_grounded_answers(live(), LLMGateway())

            return check_answer(
                build, turn, str(result.answer or ""), tool_names=list(result.tool_names),
                stopped_reason=str(result.stopped_reason or ""), drafter_model=str(result.model or ""),
            )

        # Background jobs that ended since a turn last looked. Handed to the model here — true for
        # this turn, absent from the stored transcript, like the image note above — so "the
        # download finished, exit 0" reaches the person through the agent instead of through
        # nobody. See `finished_note`.
        jobs_note = finished_note(live().home, ws)
        if jobs_note:
            note = (note + "\n\n" if note else "") + jobs_note

        # What this conversation's background works are up to, for the model that is talking —
        # and the handles to stop or undo one. Only for a conversation that exists: a first message
        # has no works to ask about, and a work's own turn is not told about its siblings.
        extra_tools: list[Tool] = []
        if background is None and req.session_id:
            works_note = works.note(req.session_id)
            if works_note:
                note = (note + "\n\n" if note else "") + works_note
                extra_tools = work_tools(works, req.session_id)
        # Read memory BEFORE building the agent: the facts go into this turn's system prompt.
        # The store the SETTINGS describe, which is not always the one the app booted with.
        turn_memory, turn_graph = _live_memory(live(), memory, graph, boot_memory_key)
        # Scoped to the folder this turn is in. What the agent learns while working here is
        # written here too, so an unrelated project's note stops arriving as context — which is
        # what a real store did: a note about one project rode along on ordinary requests in
        # another. Facts with no project belong everywhere and still arrive.
        # `project_key(ws)` rather than `str(ws)`: identical here (`ws` is already resolved), and
        # it is the same function the writer and the terminal now call, so one folder cannot end up
        # with two names again.
        facts, memory_layer = recall_facts(
            req.message, memory=turn_memory, graph=turn_graph, project=project_key(ws),
            # Quoted with source and date under the same switch that writes extracted facts
            # (study 25 S13): a fact the model did not see being written is shown with its age.
            cite=bool(getattr(live(), "memory_extract", False)),
        )
        # Created before the agent so the approver can hold it, bound to `emit` after `emit`
        # exists. Until then a question announces to nobody — and is still on disk for
        # `chimera approve`, which is the same guarantee the unattended path already had.
        approval_sink = ApprovalAnnouncer()
        # Same late binding for the browser's frames: built here, bound to `emit` below.
        frame_sink = FrameAnnouncer()
        # And for a warning a tool raises (a write after untrusted input): bound to `emit` below.
        notice_sink = NoticeAnnouncer()
        # And for a chart `render_chart` wrote: bound to `emit` below, like the frames.
        chart_sink = ChartAnnouncer()
        # The turn's id is minted here, before the agent, because the registry's approver writes it
        # on every question it asks (`pending.FACTS`): a record line that names its run can be
        # joined to the run's trace and receipt; one that does not is a sentence in a file.
        turn_id = background.turn_id if background is not None else uuid.uuid4().hex
        agent, ledger = build_agent(
            req, ws, facts, note, approval_sink=approval_sink, frame_sink=frame_sink,
            extra_tools=extra_tools or None, run_id=turn_id, notice_sink=notice_sink,
            chart_sink=chart_sink,
            # A turn with an author was sent through a share link (`guest_api` never sends an empty
            # name: `_clean_name` falls back to ANONYMOUS), or is a background work one started.
            guest=bool(author),
        )
        if background is not None:
            session = CodeSession(agent, session_id=background.session_id)
            session.workspace = str(ws)
        else:
            session = store.load(req.session_id, agent) if req.session_id else CodeSession(agent)
        session.agent = agent  # a loaded session carries messages, not the agent that made them
        # The ledger is built per turn and knows only this turn's message; an address the person
        # gave three turns ago is still one they gave. The earlier turns go in as "seen" so a send
        # to it is not flagged as made up (study 24, M2).
        if ledger is not None:
            ledger.note_seen(*_message_texts(session.messages))
        # A conversation belongs to the project it STARTED in, and keeps it. Overwriting on every
        # turn would let a session drift between projects in the sidebar as the user switches
        # around, so an old conversation would file itself under whatever codebase happened to be
        # selected when it was last reopened — which is the one thing a grouped list must not do.
        if not session.workspace:
            session.workspace = req.workspace or ""
        session_id = session.session_id

        # This turn's durable identity, and the counter that makes replay safe. The orchestration
        # route has had both since it landed; the coding turn — the most expensive route in the
        # product — emitted frames with no number and kept none of them, so a dropped connection
        # threw away work that had already been paid for. `runlog`'s own docstring says why the
        # number is the load-bearing part: a client that has seen up to `seq` asks for what came
        # after, and a reducer that ignores what it has makes replay-then-live and live-only
        # converge on the same state.
        seq = itertools.count(1)

        def emit(event: str, payload: Any) -> None:
            numbered = {**payload, "seq": next(seq)} if isinstance(payload, dict) else payload
            published: dict[str, Any] | None = None
            if background is None:
                # Onto the session's bus, for everyone watching this conversation — the owner's
                # own screen when a guest asked, a guest's when the owner did. The browser picture
                # goes live and is not kept, for the reason the run log does not keep it.
                published = bus.publish(
                    session_id, event,
                    numbered if isinstance(numbered, dict) else {"value": payload},
                    turn_id=turn_id, author=author, keep=event != "browser",
                )
            # A browser frame is a picture of a moment, tens of kilobytes each, and replay is for
            # the words a dropped connection lost — not for redrawing a page that has moved on.
            # So frames go to the live stream and never to the run log. Each frame the bus
            # numbered carries that number into the run log: the bus keeps the last few thousand
            # and a long turn outgrows them, so a screen that comes back to it gets the start from
            # here, and the number says which frames it already has.
            if isinstance(numbered, dict) and event != "browser":
                record = numbered if published is None else {**numbered, "session_seq": published["session_seq"]}
                runlog.append(settings.home, turn_id, event, record, area="code")
            if loop is not None and queue is not None:
                loop.call_soon_threadsafe(queue.put_nowait, (event, numbered))
            if background is not None:
                # The parent conversation hears about the work in compact frames — its state, the
                # tools it ran, the files it edited, a card it raised — never its every token.
                _work_frame(background, event, numbered if isinstance(numbered, dict) else {})

        # The turn's opening frame on the bus: what was asked and by whom, before any work. A
        # viewer who did not send this message needs both to draw the row the answer will land
        # under; the session file only learns the author when the receipt is written at the end.
        if background is None:
            # The frames of conversations left alone for a while go first: every conversation ever
            # opened in this process kept its last few thousand frames until the app closed.
            bus.trim_idle(
                max_age=IDLE_BUS_SECONDS,
                keep={t.session_id for t in live_turns.running()} | {session_id},
            )
            # How many files the turn carries, never which: a screen that FOLLOWS this turn (it
            # came back mid-turn, or another window started it) has no other way to know the turn
            # had any, and a retry it sent of a refusal would go out without them, unannounced
            # (study 29 P5.7). The ids stay off the bus because guests read it too.
            opening = bus.publish(
                session_id,
                "turn_started",
                {
                    "message": req.message,
                    "author": author,
                    "attachment_count": len(req.attachments),
                },
                turn_id=turn_id, author=author,
            )
            # Findable from outside until it ends. `live_since` is the sequence BEFORE the opening
            # frame, so asking the live stream for what came after it brings the turn back whole.
            # The workspace is the one the conversation RECORDS, not the resolved folder: an empty
            # request is the app's own project, and filing the running row under the resolved path
            # moved it to another group in the sidebar the moment the turn ended (seen live).
            live_turns.start(
                turn_id=turn_id, session_id=session_id, workspace=session.workspace,
                message=req.message, live_since=int(opening["session_seq"]) - 1,
            )
            # A turn in an archived conversation brings it back to the list: the archive is a
            # collapsed section, and a conversation working in there would be work nobody sees.
            _bring_back_if_archived(session_id)

        # What the panel draws: the viewport as base64 JPEG, the page's address and title, and
        # which action produced it. `n` counts frames of this turn so the screen can say "frame 7".
        frames_sent = itertools.count(1)

        def announce_frame(action: str, frame: Any) -> None:
            emit(
                "browser",
                {
                    "action": action,
                    "url": frame.url,
                    "title": frame.title,
                    "width": frame.width,
                    "height": frame.height,
                    "jpeg": base64.b64encode(frame.jpeg).decode("ascii"),
                    "n": next(frames_sent),
                },
            )

        frame_sink.emit = announce_frame
        # Kept, unlike a browser frame: a chart is part of the answer, not a picture of a moment, and a
        # screen that reconnects mid-turn should get it back. `chart_frame` caps what it carries.
        chart_sink.emit = lambda frame: emit("chart", frame)
        approval_sink.emit = lambda question: emit(
            "approval",
            {
                "id": question.id,
                "action": question.action,
                "reason": question.reason,
                "asked_at": question.asked_at,
                "decision": question.decision,
                # The number that raised the question and what it was read against — the calibrated
                # probability, its band and the build that answered. The card is where a person
                # reads it, and it is the only surface that can turn the answer into a label: the
                # record line joins this `p` to the yes/no the person gives.
                "p": question.p,
                "band": question.band,
                "decider_model": question.decider_model,
                "decision_id": question.decision_id,
                "wait_seconds": float(settings.approval_wait),
            },
        )

        def on_token(text: str) -> None:
            emit("token", {"text": text})

        def on_tool(activity: Any) -> None:
            # Reuses the run channel's event builder, so a tool call looks the same whichever
            # endpoint produced it — and is clipped by the same rules, which SAY they clipped.
            emit("tool", tool_event(activity.name, activity.arguments, activity.ok,
                                    activity.observation).data)

        edited: list[str] = []

        def on_edit(path: str, patch: str) -> None:
            edited.append(path)
            emit("edit", {"path": path, "patch": patch})

        def on_todo(items: list[dict[str, str]]) -> None:
            """The agent's own task list, whole, each time it records one.

            `claimed` travels with it and is not decoration. Every other structured frame this
            surface sends reports something that was observed — `edit` carries a diff read off
            disk, `verified` carries a command's exit code — and a row of ticks is exactly the
            shape a reader has been taught to trust. This one is what the model said about
            itself, so the frame says so and the screen has to repeat it.

            Sent whole rather than as a delta: `seq` gives this surface replay, but a consumer
            that renders a partial list would show a list that never existed.
            """
            emit("todo", {"items": items, "claimed": True})

        def on_notice(code: str, text: str, data: dict[str, Any]) -> None:
            """A warning that does not stop the turn. Its own frame, so it replays like the rest."""
            emit("notice", {"code": code, "text": text, **data})

        notice_sink.emit = on_notice

        def work() -> None:
            from chimera.orchestration.metering import MeteredBackend as _Meter

            # A background work is stopped through the works registry; every other turn through its
            # own signal, raised by POST /api/code/turns/{id}/stop.
            stop_signal = (
                works.should_stop(background.id) if background is not None else live_turns.should_stop(turn_id)
            )
            folder = folder_lock(ws)
            holds_folder = False

            # What the plan gate's call cost, when the turn has one. Out here so the `except` below
            # can still add it to a turn that died after the plan was paid for.
            plan_meter: MeteredBackend | None = None
            # What "Tidy memory"'s merge costs, when a "remember that…" makes one. On the turn's
            # own gateway, taken here before a fused turn swaps the agent's backend for the engine:
            # the merge used a fresh gateway and still does not go through fusion.
            turn_backend = getattr(agent, "backend", None)
            tidy_meter = None if turn_backend is None else _Meter(turn_backend, label="tidy")
            # Held from here to the `finally` that releases it, with nothing between that can raise,
            # and under its own name so the status bar can say which kind of work keeps the machine
            # up. Counting is all this does unless the owner turned CHIMERA_KEEP_AWAKE on.
            awake_reason = "work" if background is not None else "turn"
            keep_awake.acquire(awake_reason)
            try:
                # Taken BEFORE the turn, so a turn that edits can be judged and undone like a run.
                #
                # Until now the two buttons on this screen were not two ways of doing one thing: Send
                # edited your files and kept whatever it wrote, while "Run with verification" planned,
                # verified, and reverted on failure. Nothing on the screen said so. Collapsing them to
                # one button would have made pressing Enter silently the weaker of the two, so the
                # weaker one had to stop being weaker first.
                from chimera.core.checkpoint import WorkspaceGuard

                # The folder first: the snapshot, the run, the verification and the undo offer all
                # describe this turn alone. A turn that waits says so, and Stop still reaches it.
                if not folder.acquire(blocking=False):
                    on_notice(
                        "folder_busy",
                        "Waiting: another conversation or run is working in this folder. This one "
                        "starts when it finishes.",
                        {"workspace": str(ws)},
                    )
                    while not folder.acquire(timeout=0.25):
                        if stop_signal():
                            raise _StoppedWhileWaiting
                holds_folder = True
                if stop_signal():
                    raise _StoppedWhileWaiting

                guard = WorkspaceGuard(ws)
                before = guard.snapshot()
                # What a background work's record keeps of the turn's end: the undo offer and the
                # verifier's verdict, minted inside `_verify_and_finish` and read after it.
                outcome: dict[str, str] = {}

                def _verify_and_finish(payload: dict[str, Any]) -> None:
                    """Judge what the turn wrote and close the stream.

                    Shared by both branches on purpose. The verifier, the revert offer and the
                    receipt are what this endpoint IS; an external worker that skipped them would be
                    a second, weaker product wearing the same screen.

                    It is also where usage is logged, for the same reason: every path out of a turn
                    goes through here. Until now `_append_usage` was called from exactly one place,
                    `POST /api/chat` — and NO screen in the app calls that route. So the Cost and
                    usage tab read a log nothing wrote, and answered "talk to it a bit and come
                    back" to someone who had been talking to it all day. A dashboard that cannot
                    fill up is worse than an absent one: it reports zero spend as a fact.
                    """
                    nonlocal plan_meter
                    # None on every path that did not verify — an unedited turn, an external
                    # worker's turn, a turn whose workspace had no test command. Distinct from a
                    # verdict of "none", which means we looked and there was nothing to run.
                    verdict: dict[str, Any] | None = None
                    # Here rather than in either branch: both go through this function, and an
                    # external agent's turn is still a turn the user typed "remember that…" into.
                    # Before the row, not after it as it once was: the tidy's merge is a model call
                    # this turn pays for, and a row already written cannot carry it.
                    saved, tidied = _remember_and_tidy(
                        req.message, turn_memory, live(), backend=tidy_meter
                    )
                    # Before the row, the receipt and `done` are written from it, so all three
                    # carry the planning call and the merge: every way out of a turn passes here.
                    # The plan's meter is then cleared, so a failure later in this function cannot
                    # bill it a second time through the `except` below.
                    payload = _with_metered_call(payload, plan_meter)
                    payload = _with_metered_call(payload, tidy_meter)
                    plan_meter = None
                    payload["memory_saved"] = saved
                    payload["memory_consolidated"] = tidied
                    # The owner redid a refused turn on a model they picked (study 29 P5.7). On the
                    # receipt, so a reopened conversation still says the answer is not from the
                    # model the conversation was on, and why.
                    if req.retry_of is not None and not author:
                        payload["policy_retry"] = {
                            "blocked_model": req.retry_of.blocked_model,
                            "request_id": req.retry_of.request_id,
                        }
                    _log_usage(payload, session_id, live())
                    if edited:
                        from chimera.api.app import resolve_verify, verifier_source
                        from chimera.core.verify import CommandVerifier

                        # Minted for every turn that EDITED, not only for one whose verification
                        # failed. The snapshot was already taken above and the Posture note promises
                        # outright: "What is guaranteed is the snapshot and the undo, not the
                        # limits." It was guaranteed on one branch of four. Verification that
                        # passed, abstained, or was never configured — which for a project with no
                        # test command is ALWAYS — left the snapshot to die with the request.
                        #
                        # A pass is not consent. The check answers "does this still build", and the
                        # question the button answers is "do I want this", which nothing else on the
                        # screen can answer for the person reading the diff.
                        # What THIS turn changed, measured now, before the verifier runs: an undo
                        # puts back these files and no others (`WorkspaceGuard.restore_change`).
                        token = _undo_offers.offer(session_id, (guard, guard.diff_since(before)))
                        outcome["token"] = token
                        command, source = resolve_verify(None, ws)
                        # Did this turn change what is about to judge it — a test rewritten,
                        # deleted or skipped, the Makefile behind `make test`? Measured on the
                        # turn's own change, BEFORE the verifier runs (its caches are not the
                        # turn's work). Record-only, as on the autonomous loop's attempt receipts:
                        # a green check beside one of these is a pass against tests this same turn
                        # rewrote, and the reader should not have to re-read the diff to know.
                        #
                        # The inferred-from file (`inferred:Makefile`) is NOT passed as a verifier
                        # file. Every origin that decides what runs is already covered by the
                        # command itself (`make test` -> Makefile,
                        # `npm test` -> the "test" line of package.json, pytest.ini/pyproject/
                        # setup.cfg -> their runner section), and the rest — Cargo.toml, go.mod,
                        # `tests/` — would flag every dependency edit as "the verifier changed".
                        from chimera.governance.verifier_integrity import flag_snapshots

                        integrity = [
                            f.render()
                            for f in flag_snapshots(
                                before.files, guard.snapshot().files,
                                verify_command=command or "",
                            )
                        ][:50]
                        if command is None:
                            outcome["verified"] = "none"
                            emit("verified", {
                                "command": None, "source": source, "state": "none",
                                "revert_token": token, "integrity_flags": integrity,
                            })
                        else:
                            # No `ledger.record_verify` here, unlike the autonomous loop. This
                            # turn's ledger is in memory only: after the turn nothing reads it but
                            # `run_tainted()`, which a verify event does not move, and it is never
                            # dumped (only `chimera solve` writes `ledger.jsonl`). An event written
                            # to it would be gone with the request. What it would have said — the
                            # command, where it came from (`inferred:<file>`), how it ended — is
                            # the stored receipt's `verified` verdict below, which IS kept.
                            verified_run = CommandVerifier(
                                command, ws, source=verifier_source(source)
                            ).verify()
                            state = (
                                "abstained" if verified_run.abstained
                                else "passed" if verified_run.passed
                                else "failed"
                            )
                            outcome["verified"] = state
                            emit("verified", {
                                "command": command, "source": source, "state": state,
                                "output": verified_run.output[:4000], "revert_token": token,
                                "integrity_flags": integrity,
                            })
                            verdict = {
                                "command": command, "source": source, "state": state,
                                "output": verified_run.output[:4000],
                                "integrity_flags": integrity,
                            }
                    # Stored before it is announced, and stored HERE for the same reason usage is:
                    # every path out of a turn comes through this function. The receipt is this
                    # app's answer to "what just happened and what did it cost" — it says "price
                    # unknown" instead of zero, separates "recalled 0 facts" from "we did not
                    # look", and puts the stopping reason first. It existed only in the live
                    # stream: reopening a conversation kept the words and threw away the
                    # accounting, along with the verification verdict.
                    #
                    # `answer` is left out — it is already in the message list, and a receipt is
                    # about the turn, not a second copy of it. `revert_token` is left out
                    # deliberately: the undo offer is single-use and in-memory, so persisting one
                    # would put a button on a reopened conversation that cannot do what it says.
                    receipt = {k: v for k, v in payload.items() if k != "answer"}
                    if verdict is not None:
                        receipt["verified"] = verdict
                    # Who asked, when it was not the owner. Absent for the owner's own turns —
                    # every turn before sharing existed was theirs, and an absent field reads as
                    # exactly that rather than as a name nobody gave.
                    if author:
                        receipt["author"] = author
                    # This turn's own messages, for the history index below, before the stored
                    # conversation (which may by now hold a later turn) is read back.
                    own_messages = session.to_dict()["messages"]
                    try:
                        # The verification before this can take minutes, and another turn of this
                        # conversation may have saved meanwhile: the receipt goes on top of what is
                        # stored now, never back over it with this turn's older copy.
                        with lock_for(session_id):
                            if turn_id not in deleted_mid_turn:
                                store_for.refresh(session)
                                session.remember_receipt(receipt)
                                with live_turns.writing(turn_id):
                                    store_for.save(session)
                    except OSError as exc:  # noqa: BLE001 — a failed record must not fail the turn
                        _log.debug("could not store the turn receipt: %s", exc)
                    # How it ended, for the list's state: the verifier's own verdict and the
                    # agent's own edit list, never a model's reading of the answer.
                    if background is None and turn_id not in deleted_mid_turn:
                        _turn_ended(
                            session_id,
                            failed=verdict is not None and verdict.get("state") == "failed",
                            edited=bool(edited),
                        )
                    # The turn joins the conversation history index — the record that outlives the
                    # session's own trimming, so "what did we do about the login page two weeks
                    # ago?" has somewhere to look. Written by this code and not by the model, after
                    # the transcript is saved, and read off the same fold the replay endpoint shows
                    # (so the files the index says a turn touched are the files the screen shows
                    # it touching). A record that will not write must not fail a turn that was
                    # already paid for; the index logs and the turn goes on.
                    try:
                        from chimera.api.code_replay import exchanges_from_messages

                        exchanges = exchanges_from_messages(own_messages)
                        if turn_id in deleted_mid_turn:
                            raise _Deleted  # the conversation is gone; so is its index
                        history.record(
                            turn_id=turn_id,
                            session_id=session_id,
                            project=project_key(ws),
                            asked=message,
                            answered=str(payload.get("answer") or ""),
                            files=files_of_exchange(exchanges[-1]) if exchanges else [],
                            edited=list(edited),
                            tools=[str(t) for t in (payload.get("tool_names") or [])],
                            tainted=bool(payload.get("tainted")),
                        )
                    except _Deleted:
                        pass  # deliberately not indexed: its conversation was deleted mid-turn
                    except Exception as exc:  # noqa: BLE001 — a failed record must not fail the turn
                        _log.debug("could not index the turn in the history: %s", exc)
                    emit("done", payload)
                    # After `done`, so the answer is on screen first. Not for a background work,
                    # whose message is a brief the talking model wrote, and not for a guest, whose
                    # words are not the owner's: a fact is only ever about the person who said it.
                    if background is None and not author:
                        _extract_after_turn(
                            req.message, str(payload.get("answer") or ""), turn_memory, live(),
                            tainted=bool(payload.get("tainted")), session_id=session_id,
                        )
                    if background is not None:
                        works.finished(
                            background.id, payload,
                            revert_token=outcome.get("token", ""), verified=outcome.get("verified", ""),
                        )
                    elif req.session_id:
                        # The news of ended works reached the person through this turn's prompt.
                        works.mark_reported(req.session_id)

                # Same swap the chat turn uses, under the same per-session lock: hand the agent the
                # fusion engine for this call and restore it in `finally`. Fusion ignores tools, so
                # this turn will not touch a file — reported as `fused` rather than left to look like
                # a turn that simply did not need to.
                fused = bool(req.fuse) and fuse_backend is not None
                original_backend = getattr(agent, "backend", None) if fused else None
                if fused:
                    agent.backend = _cast_for_turn(fuse_backend, req)  # type: ignore[assignment]

                # BEFORE the branch, so it covers the external providers too — and there it matters
                # more, not less: somebody else's agent runs its own loop, and the per-action
                # governance underneath this one does not reach inside it. Here is the last point
                # where a person can stop the whole thing having spent one planning call.
                if req.plan_gate:
                    from chimera.api import plan_gate as _plan_gate
                    from chimera.orchestration.metering import MeteredBackend as _Meter

                    # One meter for the one planning call, as the calls made for a run are metered
                    # (`chimera.orchestration.metering`). The gate's own code is untouched: the
                    # meter is a transparent backend, and it records the moment the call returns,
                    # so a turn that dies in the question after it still knows what it paid.
                    plan_meter = _Meter(getattr(agent, "backend", None), label="plan")
                    verdict = _plan_gate.gate(
                        message,
                        home=Path(settings.home),
                        backend=plan_meter,
                        model=(req.roles.plan if req.roles else None) or req.model,
                        on_plan=lambda p: emit("plan", {"steps": p.steps, "raw": p.raw}),
                        on_asked=approval_sink.emit,
                        wait_seconds=float(settings.approval_wait),
                        facts={"run_id": turn_id, "surface": "api:turn", "tool": "plan"},
                    )
                    if not verdict.approved:
                        # Through `_verify_and_finish` like every other way out, so a gated turn is
                        # still logged, still snapshots, still offers the revert. A turn that never
                        # started has nothing to revert — but taking a different exit here is how
                        # the usage log lost its most expensive rows once already.
                        _verify_and_finish({
                            "answer": "",
                            "steps": 0,
                            "stopped_reason": f"plan_gate:{verdict.outcome}",
                            "tool_names": [],
                            "model": req.model or "",
                            # The loop never ran, and a loop that never ran cost a known nothing;
                            # the planning call it did pay for is added in `_verify_and_finish`.
                            # This read None, "price unknown", with no tokens: the one call the
                            # turn made was already known and was nowhere.
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "usd": 0.0,
                            "tainted": False,
                            "memory_facts_used": len(facts),
                            "memory_layer": memory_layer,
                            "fused": fused,
                        })
                        return
                    if verdict.plan is not None:
                        # The approved steps, into the turn context: same placement and same reason
                        # as the recalled facts above. It steers this turn without being recorded as
                        # something the user said, and without changing the cached system message.
                        approved = _plan_gate.as_system_note(verdict.plan)
                        agent.config.turn_notes = (
                            f"{agent.config.turn_notes}\n\n{approved}" if agent.config.turn_notes
                            else approved
                        )

                external = (req.provider or "").strip().lower()
                if external:
                    # Somebody else's agent does the work; everything around it is unchanged. The
                    # snapshot above was already taken, the verifier below still runs, and the
                    # revert offer still applies — which is the entire reason this is a branch
                    # inside the existing turn rather than a second endpoint.
                    from chimera.api import code_acp
                    from chimera.api.code_acp import done_payload

                    # A Stop pressed on this turn cancels the external agent's prompt: its loop is
                    # not ours, so the step-by-step stop signal cannot reach it.
                    live_turns.on_stop(
                        turn_id,
                        lambda: code_acp.cancel_external_turn(
                            provider=external, command=req.provider_command,
                            workspace=ws, session_id=session_id,
                        ),
                    )
                    with lock_for(session_id):
                        store_for.refresh(session)
                        acp_result = code_acp.run_external_turn(
                            provider=external,
                            command=req.provider_command,
                            message=message,
                            workspace=ws,
                            session_id=session_id,
                            images=images or None,
                            write_region=build_write_region(req.write_region, ws),
                            on_token=on_token if req.stream else None,
                            on_tool=lambda name, args, ok, obs: emit(
                                "tool", tool_event(name, args, ok, obs).data
                            ),
                            on_edit=on_edit,
                        )
                        # The conversation is the agent's, but the RECORD is ours: without this the
                        # sidebar would show an untitled, empty session for work that really happened.
                        session.messages.append({"role": "user", "content": message})
                        session.messages.append({"role": "assistant", "content": acp_result.answer})
                        if turn_id not in deleted_mid_turn:
                            with live_turns.writing(turn_id):
                                store_for.save(session)
                    _verify_and_finish(
                        done_payload(acp_result, provider=external, tainted=bool(ledger.run_tainted()))
                    )
                    return

                # The meter the agent would have built from the config's `max_usd`/`warn_usd`,
                # built here instead so it also reports to the sum of the turns running at once.
                agent_config = getattr(agent, "config", None)
                turn_spend = combined_spend.budget(
                    turn_id,
                    max_usd=getattr(agent_config, "max_usd", None),
                    warn_usd=getattr(agent_config, "warn_usd", None),
                )
                with lock_for(session_id):
                    # What is stored now, not what was stored when this request arrived: a turn of
                    # the same conversation may have finished while this one waited for the lock.
                    store_for.refresh(session)
                    result = session.send(
                        message,
                        # Fusion has no token stream (the engine only implements `complete`), so
                        # asking for one would leave the screen blank until the whole panel lands.
                        on_token=on_token if (req.stream and not fused) else None,
                        on_tool=on_tool,
                        on_edit=on_edit,
                        on_todo=on_todo,
                        on_notice=on_notice,
                        images=images or None,
                        # A background work is stopped through the works registry; every other
                        # turn through its own signal, raised by POST /api/code/turns/{id}/stop.
                        should_stop=stop_signal,
                        spend=turn_spend,
                    )
                    if fused:
                        agent.backend = original_backend  # type: ignore[assignment]
                    # Inside the lock and before the save: a declined or escalated answer replaces
                    # the draft in the stored conversation too, so a reopened conversation shows
                    # what shipped, and the next turn's history is not built on a withheld answer.
                    answer, grounded, grounded_usd = _check_grounded(grounded_turn, result)
                    if answer != result.answer:
                        session.replace_last_answer(answer)
                    if turn_id not in deleted_mid_turn:
                        with live_turns.writing(turn_id):
                            store_for.save(session)
                _verify_and_finish(
                    {
                        "answer": answer,
                        # The check against the attached documents (`chimera/fusion/verified.py`),
                        # or None for a turn with none. Its own key: `verified` on this receipt
                        # already means the workspace's test command.
                        "grounded": grounded,
                        "steps": result.steps,
                        "stopped_reason": result.stopped_reason,
                        "tool_names": list(result.tool_names),
                        "model": result.model,
                        "prompt_tokens": result.prompt_tokens,
                        "completion_tokens": result.completion_tokens,
                        # The verifier's and the escalation's calls are this turn's spend too;
                        # an unpriced turn stays unpriced rather than becoming only their cost.
                        "usd": None if result.usd is None else round(result.usd + grounded_usd, 6),
                        # The number that says whether raising max_steps is safe. Reported because
                        # a ceiling the user can raise without seeing its cost is a trap.
                        "context_peak_tokens": result.steplog.context_peak_tokens,
                        # Output tokens per second of time spent INSIDE model calls — measured per
                        # step, not divided out of the run's duration, which would fold the tools
                        # and the verifier into the model's speed. None when nothing was measured;
                        # zero would say the model produced nothing.
                        "tokens_per_second": result.steplog.tokens_per_second,
                        # Which route answered, and what its cache served — the two fields every
                        # autonomous attempt receipt carries since `bench/cache_confound`, and this
                        # receipt did not: tested live on 0.57.0, the run receipt named the route
                        # and the turn receipt of the same install said nothing. A score belongs
                        # to the route that produced it, and a turn's cost differs ~10x on the cache
                        # alone, so a receipt without them is a receipt for a different run.
                        # `None`, never zero, when no step reported cache usage.
                        "provider": result.steplog.provider,
                        "cache_read_tokens": result.steplog.cache_read_tokens,
                        # The router's ids for this turn's calls. On a streamed turn `provider`
                        # above is EMPTY — measured: the chunks carry no route — and these are what
                        # let the stored receipt learn it when the conversation is reopened
                        # (`chimera.providers.generation`; the record exists ~10 s after the call).
                        "generation_ids": result.steplog.generation_ids,
                        # Which instructions produced this turn: the fingerprint of the system
                        # message, read off the same step log the trace line was written from, so
                        # the receipt and the trace cannot name two different prompts. The turn
                        # context is not in it — it changes every turn, and a hash of it would
                        # differ between two turns given the same instructions (study 25, wave 0).
                        "system_sha": result.steplog.system_sha,
                        # A cut turn, and tool calls dropped for arguments that did not parse — both
                        # used to arrive here as a normal turn (study 30, S30-09). Record-only.
                        "truncated_steps": result.steplog.truncated_steps,
                        "dropped_tool_calls": result.steplog.dropped_tool_calls,
                        "route_meta": result.route_meta,
                        # Did this turn read anything untrusted? A turn steered by a planted
                        # instruction used to be indistinguishable from one that was not.
                        "tainted": bool(ledger.run_tainted()),
                        # What the conversation remembered, and from which layer. A coding turn used
                        # to accumulate nothing and recall nothing; it now READS what the chat reads.
                        "memory_facts_used": len(facts),
                        "memory_layer": memory_layer,
                        # This turn could not use tools, and a reader cannot tell that from a zero
                        # tool count alone — the same count a turn that needed none reports.
                        "fused": fused,
                        # The style this turn's prompt carried and the version of its words — only
                        # when it carried one, so a default turn's receipt is the one it always was.
                        **(
                            {"style": style, "style_version": OUTPUT_STYLE_VERSION}
                            if (style := _applied_style(req))
                            else {}
                        ),
                    },
                )

            except Exception as exc:  # noqa: BLE001 — surfaced to the client as an error event
                _log.warning("code turn failed: %s", exc)
                # A turn that failed after paying for work is still a turn that spent money.
                #
                # `_log_usage` lives in `_verify_and_finish`, which only the success path reaches —
                # so a run measured on rc13 made seven tool calls, wrote 19 KB of correct output,
                # died, and left the usage log untouched. The Cost screen then answered "what has
                # this cost me" with a total that was missing the most expensive turn of the day.
                #
                # Only when something was actually spent. A turn that fails on its first call — a
                # model name that does not exist — has nothing to record, and writing a zero row
                # there would swap a silent undercount for an invented entry, which is worse
                # because nothing downstream can tell an invented row from a real one.
                from chimera.core.agent import partial_spend

                # The plan gate's call, when one was paid for and not yet written: an approved plan
                # followed by a run that died is still a plan somebody paid for. The loop's part
                # is a known zero when it never reached a model, so the sum stays known.
                spent = partial_spend(exc)
                failed = _with_metered_call(
                    {
                        "model": spent.model if spent is not None else "",
                        "prompt_tokens": spent.prompt_tokens if spent is not None else 0,
                        "completion_tokens": spent.completion_tokens if spent is not None else 0,
                        "usd": spent.usd if spent is not None else 0.0,
                        "tool_names": [],
                        "memory_facts_used": len(facts),
                        "route_meta": None,
                    },
                    plan_meter,
                )
                if failed["prompt_tokens"] or failed["completion_tokens"]:
                    _log_usage(failed, session_id, live())
                # An external agent's own words when we have them. "the coding turn failed" is right
                # for the native branch, where the failure is ours to debug — but an adapter that is
                # not installed, or that could not authenticate, has already said something more
                # useful than anything this line could invent, and hiding it behind a generic
                # sentence turns a two-minute fix into a support thread.
                from chimera.api.code_acp import failure_message

                message_out = (
                    _StoppedWhileWaiting.MESSAGE
                    if isinstance(exc, _StoppedWhileWaiting)
                    else failure_message(exc)
                    if (req.provider or "").strip()
                    else _native_failure(exc)
                )
                frame: dict[str, Any] = {"message": message_out}
                # A refusal on content policy is said as one, with what identifies it, so the
                # screen can offer the owner another model (study 29 P5.7). Native turns only: an
                # external agent's failure is in its own words, and it picks its own model.
                block = (
                    None
                    if (req.provider or "").strip() or isinstance(exc, _StoppedWhileWaiting)
                    else policy_block(exc)
                )
                if block is not None:
                    from dataclasses import replace

                    # The gateway names the model that refused; a backend that is not the gateway
                    # (a fused panel, a test double) does not, and then the turn's own model is the
                    # best account there is.
                    if block.model is None:
                        turn_model = _model_for(req, live())[0] or live().default_model
                        block = replace(block, model=turn_model or None)
                    # Before this the sentence was "the coding turn failed": none of the markers
                    # `_native_failure` forwards reads a policy refusal, so a refusal looked like a
                    # crash in this repository.
                    frame["message"] = message_out = block.sentence()
                    frame["reason"] = "content_policy"
                    frame["model"] = block.model
                    frame["provider"] = block.provider
                    frame["request_id"] = block.request_id
                emit("error", frame)
                if background is not None:
                    works.fail(background.id, message_out)
                # A turn the person stopped while it waited for the folder did not fail; every
                # other way here did. Only for a conversation with a file: a first turn that died
                # left nothing in the list to mark.
                elif turn_id not in deleted_mid_turn and not isinstance(exc, _StoppedWhileWaiting):
                    _turn_ended(session_id, failed=True, edited=bool(edited), only_if_stored=True)
            finally:
                if holds_folder:
                    folder.release()
                # Every way out of a turn, so a turn that died still stops being "running".
                live_turns.finish(turn_id)
                keep_awake.release(awake_reason)
                combined_spend.close(turn_id)
                deleted_mid_turn.discard(turn_id)
                if held:
                    release_turn(held)
                if loop is not None and queue is not None:
                    loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: end of stream

        threading.Thread(target=work, daemon=True).start()
        return session_id, turn_id

    def _work_frame(work: Work, event: str, payload: dict[str, Any]) -> None:
        """What the parent conversation hears of a work's turn: state, tools, edits, cards."""
        if event == "tool":
            works.progress(work.id, tool=str(payload.get("name") or ""))
        elif event == "edit":
            works.progress(work.id, edit=str(payload.get("path") or ""))
        elif event == "approval":
            # The card itself goes to the owner's screen, under the work's turn id, so answering
            # it reaches the work's approver like any other card.
            works.waiting(work.id, True)
            bus.publish(work.parent, "approval", payload, turn_id=work.turn_id, author=work.author)

    def _revert(token: str) -> dict[str, Any]:
        """Undo through the same single-use offer the receipt makes (`revert_turn`)."""
        pending = _undo_offers.take(token)
        if pending is None:
            return {"ok": False, "restored": 0}
        workspace_guard, change = pending
        report = workspace_guard.restore_change(change)
        return {
            "ok": True,
            "restored": report.restored,
            "left_new_files": bool(report.left_new),
            # Files that changed again after the turn, by another conversation or the person: left as
            # they are and named, rather than overwritten with the turn's "before".
            "kept": report.kept,
        }

    # ------------------------------------------------------------------ the works, from the screen

    @app.get("/api/code/sessions/{session_id}/works", dependencies=[guard], response_model=WorksOut)
    def list_works(session_id: str) -> dict[str, Any]:
        """This conversation's background works, oldest first — what the Works panel draws."""
        return {"works": [w.to_dict() for w in work_store.for_parent(session_id)]}

    @app.post("/api/code/works/{work_id}/stop", dependencies=[guard], response_model=WorkActionOut)
    def stop_work(work_id: str) -> dict[str, Any]:
        """Stop a work: a queued one now, a running one at its next step (a model call in flight
        ends first). What it did so far stays, and can then be undone."""
        work = works.stop(work_id)
        if work is None:
            raise HTTPException(status_code=404, detail="no such work")
        return {"ok": True, "work": work.to_dict()}

    @app.post("/api/code/works/{work_id}/undo", dependencies=[guard], response_model=WorkActionOut)
    def undo_work(work_id: str) -> dict[str, Any]:
        """Undo a finished work's edits through the same offer the receipt makes. `ok: false`
        with the reason when there is nothing to undo — a second click is not an error."""
        result = works.undo(work_id)
        work = work_store.get(work_id)
        if work is None:
            raise HTTPException(status_code=404, detail="no such work")
        return {"ok": bool(result.get("ok")), "work": work.to_dict(), "reason": str(result.get("reason") or "")}

    @app.get("/api/code/works/{work_id}/session", dependencies=[guard], response_model=CodeSessionOut)
    def work_session(work_id: str) -> dict[str, Any]:
        """The work's own transcript, folded like a conversation's — what "see it" opens."""
        from chimera.api.code_replay import attach_receipts, exchanges_from_messages

        work = work_store.get(work_id)
        if work is None:
            raise HTTPException(status_code=404, detail="no such work")
        try:
            path = work_sessions._path(work.session_id)
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            data = {}
        messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
        receipts = [r for r in data.get("receipts", []) if isinstance(r, dict)]
        return {
            "id": work.session_id,
            "workspace": work.workspace,
            "exchanges": attach_receipts(exchanges_from_messages(messages), receipts),
        }

    def _running_out(turn: Any) -> dict[str, Any]:
        return {
            "turn_id": turn.turn_id,
            "session_id": turn.session_id,
            "workspace": turn.workspace,
            "message": turn.message,
            "started_at": turn.started_at,
            "live_since": turn.live_since,
            "transcript_saved": turn.transcript_saved,
        }

    # Declared BEFORE `/api/code/turns/{turn_id}`: the router matches in order, and `running` would
    # otherwise be read as a turn id and answered with a 404.
    @app.get(
        "/api/code/turns/running", dependencies=[guard], response_model=list[RunningTurnOut]
    )
    def code_turns_running() -> list[dict[str, Any]]:
        """The coding turns running now, oldest first.

        A turn keeps running when the screen that started it goes away, and until this existed
        nothing could say so: the conversation is stored when the agent finishes, so a session left
        mid-turn read as empty and was not in the list. Empty here means nothing is running, not
        that the question failed; an error is an error.
        """
        return [_running_out(t) for t in live_turns.running()]

    @app.post(
        "/api/code/turns/{turn_id}/stop", dependencies=[guard], response_model=CodeTurnStopOut
    )
    def code_turn_stop(turn_id: str) -> dict[str, Any]:
        """Stop a running coding turn on the server.

        Until this existed the Stop button only aborted the screen's request: the turn went on
        calling the model, editing files and spending until it finished by itself, while the screen
        said it had stopped. The agent loop polls the signal once per step, so the step in progress
        finishes first; an external agent's prompt is cancelled at once. 404 for a turn that is not
        running, never 200-with-nothing: a stop that reached nothing must not read as one that
        worked.
        """
        if not live_turns.request_stop(turn_id):
            raise HTTPException(status_code=404, detail="no such running turn")
        return {"turn_id": turn_id, "stopping": True}

    @app.get(
        "/api/code/turns/{turn_id}", dependencies=[guard], response_model=CodeTurnFramesOut
    )
    def code_turn_frames(turn_id: str, since: int = 0) -> dict[str, Any]:
        """Everything this turn emitted after ``since``, so a dropped stream costs nothing.

        The same shape the orchestration route has had since it landed, on the route that actually
        needed it: a coding turn is the most expensive thing this product does, and losing the
        connection threw away work that had already been paid for while the bill stayed.

        The frames go through the SAME handlers the live stream feeds, and a client that ignores a
        `seq` it has already applied gets one state whether it replayed first or not.

        404 for an id that was never recorded, never 200-with-nothing: an unknown turn and a turn
        with no new frames are opposite instructions for a client deciding whether to keep asking.
        """
        home = Path(settings.home)
        if not runlog.exists(home, turn_id, area="code"):
            raise HTTPException(status_code=404, detail="no such turn")
        quadros = runlog.frames(home, turn_id, since=since, area="code")
        maior = max((int(f.get("seq") or 0) for f in quadros), default=since)
        return {"turn_id": turn_id, "frames": quadros, "seq": maior}

    @app.post("/api/code/posture", dependencies=[guard], response_model=PostureFacts)
    def code_posture(req: PostureQuery) -> PostureFacts:
        """What the chosen posture MEANS on this machine, right now.

        A POST rather than a GET because it reports the live state of the sandbox rather than a
        stored resource, and because caching this answer is precisely the bug: a Docker daemon that
        died since the last call must change the answer, not be served from a cache.
        """
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else workspace
        # The coding turn is always assembled with a ledger. A chat is assembled with one unless the
        # user turned the guard off — and when they have, the sentence has to say so, because the
        # whole product rests on stating what is true on this machine rather than what reads better.
        chat_guarded = live().guard_chat
        # Held to the folder's recorded grant exactly as the turn will be (`assemble_registry`), so
        # the sentence on screen describes the run that happens rather than the one asked for.
        asked = Posture(reach=req.reach, approval=req.approval)
        held = granted_posture(asked, server_grants_shell(live(), ws)) or asked
        return describe(
            held,
            ws,
            settings,
            # A guarded chat CAN now stop and ask: its ledger narrows on taint and its approver
            # writes a durable question that `POST /api/chat/stream` draws on the turn's own stream
            # (`chimera/api/app.py`). Saying "never" here was true for as long as the chat had
            # nobody to ask, and became a false statement about the shipped default on 2026-09-10 —
            # the exact direction of error this module's docstring exists to prevent, since a user
            # who reads "never pauses" and then meets a modal has been told the wrong thing about
            # what their agent may do.
            #
            # Still "never" for a conversational CODING turn (`"turn"`), which is built with no
            # checkpointer at all and cannot stop whatever the approval axis says.
            can_pause=req.surface == "run" or (req.surface == "chat" and chat_guarded),
            guarded=req.surface != "chat" or chat_guarded,
            external_agent=(req.provider or "").strip().lower(),
        )

    @app.post("/api/code/roles", dependencies=[guard], response_model=RoleModels)
    def code_roles(req: RolesQuery) -> RoleModels:
        """The concrete model slugs a profile resolves to, so the UI can show them.

        Resolved server-side rather than mirrored in the frontend: the tiers already honour the
        user's cost mode and explicit per-tier settings, and a second copy of that resolution in
        TypeScript would display a model the run does not actually use.
        """
        return resolve_roles(req.profile, settings).models

    @app.get("/api/code/worth", dependencies=[guard], response_model=WorthReport)
    def code_worth(workspace: str | None = None) -> WorthReport:
        """What each configuration cost and got, over the runs that really happened here.

        Read from the same append-only run log the receipts come from — no separate store to drift
        out of sync with the evidence it summarises.
        """
        from chimera.api.runs import load_runs

        return summarize_worth(load_runs(settings.home / "runs.jsonl", workspace=workspace))

    @app.post("/api/attachments", dependencies=[guard], response_model=AttachmentOut)
    async def upload_attachment(file: UploadFile = _UPLOAD) -> AttachmentOut:
        """Take one file for the agent to look at or read.

        The response carries an id, never the content. Documents are converted to text HERE rather
        than at turn time, so an unreadable PDF or a missing optional dependency surfaces while the
        user is still looking at the attach button, instead of halfway through a turn they are
        paying for. Images are stored as-is; the gateway encodes them into the request.
        """
        from chimera.api.attachments import save as save_attachment

        data = await file.read()
        try:
            saved = save_attachment(settings.home, file.filename or "file", data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AttachmentOut(
            id=saved.id, name=saved.name, kind=saved.kind, chars=len(saved.text), note=saved.note
        )

    @app.get("/api/vision", dependencies=[guard], response_model=VisionOut)
    def vision(model: str | None = None) -> VisionOut:
        """Can the model that answers a turn look at an image?

        Asked when someone attaches one, so the answer arrives while they can still act on it. An
        image sent to a model without vision is either a provider error or — worse — silently
        ignored, and a confident answer about a picture nobody looked at is indistinguishable from
        one about a picture that was.

        ``model`` is the one the composer has picked. It used to be unaskable: this always answered
        about ``default_model``, so the moment a per-conversation picker existed the warning started
        naming a model the turn was not going to use — and the sentence under the paperclip was about
        somebody else's capability. Omitted still means the default, which is what a caller with no
        picker means.
        """
        from chimera.api.attachments import vision_support

        chosen = (model or "").strip() or live().default_model
        return VisionOut(model=chosen, support=vision_support(chosen))

    @app.get("/api/dictation", dependencies=[guard], response_model=DictationOut)
    def dictation() -> DictationOut:
        """Can speech become text here? Asked before the microphone opens, not after."""
        from chimera.api.attachments import dictation_support

        support, how = dictation_support(settings)
        return DictationOut(support=support, how=how)

    @app.post("/api/transcribe", dependencies=[guard], response_model=TranscriptOut)
    async def transcribe_audio(
        file: UploadFile = _UPLOAD, language: str | None = _LANGUAGE_HINT
    ) -> TranscriptOut:
        """Speech to text, for dictating a message instead of typing it.

        Runs through the same tool the agent uses — a local model when the `stt` extra is installed,
        the API otherwise. Deliberately not a second implementation: a person dictating and an agent
        transcribing a recording must not be able to get different answers, or to have one path work
        while the other is quietly unconfigured.

        ``language`` is the app's own language, as a hint: a two-second clip is not much for the
        model to detect a language from, and a wrong guess reads Portuguese as something else. An
        unknown value is ignored rather than refused — the clip still transcribes, detected.

        Off the event loop: a transcription is a few hundred milliseconds of CPU, and while it ran
        on the loop every stream the app had open — the turn being answered, a shared conversation's
        live window — stood still for exactly that long.
        """
        from chimera.api.attachments import save as save_attachment
        from chimera.api.attachments import transcribe

        hint = (language or "").strip().lower()
        saved = save_attachment(settings.home, file.filename or "speech.webm", await file.read())
        text = await run_in_threadpool(
            transcribe, saved.path, hint if _LANGUAGE_CODE.fullmatch(hint) else None
        )
        # The tool reports its own failures as text rather than raising. Passing "error: ..." into
        # the composer as if it were dictation is the one outcome worth intercepting.
        if text.lower().startswith("error"):
            return TranscriptOut(text="", note=text)
        return TranscriptOut(text=text.strip(), note="")

    @app.post("/api/transcribe/warm", dependencies=[guard], response_model=TranscriberWarmOut)
    async def warm_transcriber_route() -> TranscriberWarmOut:
        """Load the local speech model ahead of the first utterance.

        Called when the voice mode is switched on or a dictation starts — the moments a person is
        seconds away from speaking. Nothing to load on the hosted route, and nothing is kept that
        a transcription would not have kept anyway. Off the loop like the transcription itself.
        """
        import time

        from chimera.tools.media import warm_transcriber

        began = time.perf_counter()
        warmed = await run_in_threadpool(warm_transcriber)
        return TranscriberWarmOut(warmed=warmed, seconds=round(time.perf_counter() - began, 2))

    def _waiting_sessions() -> set[str]:
        """The conversations a waiting question belongs to: the join `GET /api/approvals` makes,
        so the list's "waiting" and the approvals card can never count two different things."""
        from chimera.governance.pending import pending

        waiting: set[str] = set()
        for question in pending(live().home):
            if question.run_id:
                sid = approval_origin(question.run_id).get("session_id", "")
                if sid:
                    waiting.add(sid)
        return waiting

    @app.get("/api/code/sessions", dependencies=[guard], response_model=list[CodeSessionMetaOut])
    def list_code_sessions(archived: bool = False) -> list[dict[str, Any]]:
        """Past coding conversations, newest first, each carrying the project it belongs to.

        The list is what makes a sidebar possible: without the project on each row, past
        conversations are a flat pile you cannot file. Titles are the first thing the user asked,
        derived on read — never generated, so a row is never a paraphrase of the conversation it
        points at.

        A conversation with a turn running is marked, and one whose first turn is still running is
        listed although its file does not exist yet: the file is written when the agent finishes,
        so without this a task started in a new conversation was invisible for exactly as long as it
        took to do.

        Each row carries its ``state`` (`chimera/api/conversation_state.py`), from facts only.
        ``archived=true`` lists the archived conversations instead of the others. This is also where
        ``CHIMERA_ARCHIVE_AFTER_DAYS`` is applied — on the look, so no thread has to run for it, and
        never to a conversation working, waiting, with a background work or with a share link open.
        """
        import time

        from chimera.api.conversation_state import (
            archive_refusal,
            conversation_state,
            due_for_archive,
        )
        from chimera.core.code_session import _title_of
        from chimera.core.code_session_marks import ConversationMark

        rows = store.list_meta()
        running = {t.session_id: t for t in live_turns.running()}
        waiting = _waiting_sessions()
        with_work = work_store.active_parents()
        marks = store.marks.all()
        after_days = live().archive_after_days
        now = time.time()
        for row in rows:
            sid = str(row["id"])
            mark = marks.get(sid) or ConversationMark()
            row["running"] = sid in running
            row["state"] = conversation_state(
                running=sid in running or sid in with_work,
                waiting=sid in waiting,
                mark=mark,
                last_verdict=str(row.get("last_verdict") or ""),
            )
            if due_for_archive(
                updated_at=float(row["updated_at"]), mark=mark, now=now, after_days=after_days
            ) and not archive_refusal(
                running=sid in running,
                waiting=sid in waiting,
                background=sid in with_work,
                shared=bool(shares.for_session(sid)),
            ):
                mark = store.marks.archive(sid, at=now)
            row["archived_at"] = mark.archived_at
        stored = {row["id"] for row in rows}
        unsaved = [
            {
                "id": t.session_id,
                "title": _title_of(t.message),
                "workspace": t.workspace,
                "turns": 0,
                "updated_at": t.started_at,
                "running": True,
                "state": "waiting" if sid in waiting else "running",
                "archived_at": None,
            }
            for sid, t in running.items()
            if sid not in stored
        ]
        listed = [r for r in (*unsaved, *rows) if (r["archived_at"] is not None) == archived]
        return sorted(listed, key=lambda r: float(r["updated_at"]), reverse=True)

    def _stored_or_404(session_id: str) -> None:
        try:
            exists = store._path(session_id).is_file()
        except ValueError as exc:  # an id with no usable characters — a client error, not a 500
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not exists:
            raise HTTPException(status_code=404, detail="no such conversation")

    @app.post(
        "/api/code/sessions/{session_id}/archive",
        dependencies=[guard],
        response_model=CodeSessionArchiveOut,
    )
    def archive_code_session(session_id: str) -> dict[str, Any]:
        """Move a conversation out of the list. **Nothing is touched**: no transcript, folder or
        worktree, and a share link keeps working — it is a timestamp beside the transcripts.

        Refused (409) while a turn or background work of it runs or a question of it waits: the
        archive is a collapsed section, and an agent's state must not be hidden by tidying a list.
        """
        import time

        _stored_or_404(session_id)
        if session_id in _waiting_sessions():
            raise HTTPException(status_code=409, detail="a question in it is waiting for you")
        if live_turns.of_session(session_id) is not None:
            raise HTTPException(status_code=409, detail="a turn is running in it")
        if session_id in work_store.active_parents():
            raise HTTPException(status_code=409, detail="a background work of it has not finished")
        mark = store.marks.archive(session_id, at=time.time())
        return {"id": session_id, "archived_at": mark.archived_at}

    @app.post(
        "/api/code/sessions/{session_id}/unarchive",
        dependencies=[guard],
        response_model=CodeSessionArchiveOut,
    )
    def unarchive_code_session(session_id: str) -> dict[str, Any]:
        """Bring a conversation back into the list. Idempotent: one already there stays there."""
        import time

        _stored_or_404(session_id)
        if store.marks.get(session_id).archived_at is not None:
            store.marks.unarchive(session_id, at=time.time())
        return {"id": session_id, "archived_at": None}

    @app.post(
        "/api/code/sessions/{session_id}/seen",
        dependencies=[guard],
        response_model=CodeSessionSeenOut,
    )
    def seen_code_session(session_id: str) -> dict[str, bool]:
        """The owner's screen drew this conversation, so its last edits are no longer unseen.

        Its own route rather than a side effect of reading the conversation: the desktop bridge
        reads conversations too, and an agent reading one is not the owner looking at its diff.
        Not in the bridge's table for the same reason.
        """
        return {"changed": store.marks.seen(session_id)}

    @app.get("/api/code/sessions/{session_id}", dependencies=[guard], response_model=CodeSessionOut)
    def get_code_session(session_id: str) -> dict[str, Any]:
        """A stored conversation as the exchanges a person had, ready to render.

        Resuming used to show an empty screen while the agent silently carried the whole history —
        the worst of both, because the next question worked for reasons the user could not see.

        An unknown id returns an empty conversation rather than a 404: the store treats a missing
        file as the ordinary first-turn case, and a screen that errors on a session someone just
        deleted in another window would be reporting a race as a fault.

        A turn running in this conversation is named in ``running_turn``, and the file is read
        between two looks at it (`LiveTurns.read_consistently`): the agent saves the transcript
        when it finishes and the turn goes on verifying after that, so the file can already hold the
        exchange of a turn that is still running, and a screen has to know which of the two it got.
        """
        view, turn = live_turns.read_consistently(session_id, lambda: _session_view(session_id))
        out = view if view is not None else {"id": session_id, "workspace": "", "exchanges": []}
        if turn is None:
            return out
        return {
            **out,
            "workspace": out["workspace"] or turn.workspace,
            "running_turn": _running_out(turn),
        }

    def _session_view(session_id: str) -> dict[str, Any] | None:
        """The stored conversation as exchanges, or None when there is no readable file.

        Split from the route so the guest app reads the same fold with the same receipts: two
        readers of one file would be two ways for the owner and the guest to see different
        conversations.
        """
        from chimera.api.code_replay import attach_receipts, exchanges_from_messages

        try:
            path = store._path(session_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
            receipts = [r for r in data.get("receipts", []) if isinstance(r, dict)]
        except (OSError, ValueError):
            return None
        # A streamed turn's receipt was written before its route could be known (the router's
        # record appears ~10 s after the call — `chimera.providers.generation`). This is where the
        # receipt is read back, so this is where it learns the route: bounded, newest first, and
        # written down so the next reopen does not ask again. Skipped when nothing is unresolved,
        # when there is no key to ask with, and — the write, not the lookup — when a turn holds
        # this session, because its own save would win anyway and the answer keeps.
        _learn_routes(session_id, data, receipts)
        return {
            "id": session_id,
            "workspace": str(data.get("workspace") or ""),
            "exchanges": attach_receipts(exchanges_from_messages(messages), receipts),
        }

    def _learn_routes(session_id: str, data: dict[str, Any], receipts: list[dict[str, Any]]) -> None:
        import time

        from chimera.providers.generation import (
            PASS_BUDGET,
            resolve_missing_bills,
            resolve_missing_routes,
            wants_bill,
            wants_route,
        )

        if not any(wants_route(r) or wants_bill(r) for r in receipts):
            return
        current = live()
        pool = current.credential_pool("openrouter")
        key = (pool[0] if pool else None) or current.openrouter_api_key or ""
        if not key:
            return
        # One budget for both passes: the route first (one lookup a receipt, and the badge that
        # reframes the rest), then the bill with whatever time is left.
        started = time.monotonic()
        try:
            filled = resolve_missing_routes(receipts, api_key=key)
            left = PASS_BUDGET - (time.monotonic() - started)
            if left > 0:
                filled += resolve_missing_bills(receipts, api_key=key, budget_seconds=left)
        except Exception as exc:  # noqa: BLE001 — a replay must render whatever the router does
            _log.debug("routes not learned for %s: %s", session_id, exc)
            return
        if not filled:
            return
        lock = lock_for(session_id)
        if not lock.acquire(blocking=False):
            return
        try:
            data["receipts"] = receipts
            store._path(session_id).write_text(
                redact(json.dumps(data)), encoding="utf-8"
            )
        except OSError as exc:
            _log.debug("routes learned but not stored for %s: %s", session_id, exc)
        finally:
            lock.release()

    @app.post(
        "/api/code/sessions/{session_id}/fork",
        dependencies=[guard],
        response_model=CodeSessionMetaOut,
    )
    def fork_code_session(session_id: str) -> dict[str, Any]:
        """Branch a conversation: a copy under a new id, sharing its past and nothing after it.

        404 rather than the delete route's soft ``{ok: false}``, because the two failures are not
        the same event. Deleting something already gone is the state a second click lands in and
        the user's intent is satisfied either way; forking something that is not there produces no
        conversation to open, and the caller needs to know that before it navigates.
        """
        try:
            new_id = store.fork(session_id)
        except ValueError as exc:  # an id with no usable characters — a client error, not a 500
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if new_id is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        meta = next((m for m in store.list_meta() if m["id"] == new_id), None)
        # The fork's own row, not the source's: the caller drops it straight into the sidebar, and
        # a row carrying the parent's id would resume the conversation the user just branched away
        # from — the exact outcome forking exists to avoid.
        return meta or {"id": new_id, "title": "", "workspace": "", "turns": 0, "updated_at": 0.0}

    @app.get(
        "/api/code/sessions/{session_id}/raw",
        dependencies=[guard],
        response_model=CodeSessionRawOut,
    )
    def raw_code_session(session_id: str) -> dict[str, Any]:
        """The conversation's stored file, verbatim.

        Everything else this API returns about a session has been through a parser and a fold into
        exchanges — which is the right thing to render and the wrong thing to debug with, because a
        message the parser dropped is invisible in it. This is the one view where a malformed file
        looks malformed.
        """
        try:
            text = store.raw(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if text is None:
            raise HTTPException(status_code=404, detail="no such conversation")
        return {"id": session_id, "text": text, "bytes": len(text.encode("utf-8"))}

    @app.post("/api/code/revert/{token}", dependencies=[guard])
    def revert_turn(token: str) -> dict[str, Any]:
        """Undo an editing turn, if the user takes the offer.

        Any editing turn, not only one whose verification failed. A check answers "does this still
        build"; the button answers "do I want this", and only the person reading the diff can.

        A token is single-use and dies with the process. An unknown one is ``{ok: false}`` rather
        than a 404 — that is the state a second click hits, and a stale offer is not an error.
        """
        # Whether files this turn CREATED go away with it. Inside a git repository the delete pass
        # is skipped unconditionally — deliberately, since a path bug once let a revert wipe a repo
        # — so "Edits undone." over surviving new files reported something that did not happen.
        # (`left_new_files` says so.) Shared with a work's undo, which takes the same offer.
        return _revert(token)

    @app.delete("/api/code/sessions/{session_id}", dependencies=[guard])
    def delete_code_session(session_id: str) -> dict[str, bool]:
        """Forget a conversation. An unknown id is ``{ok: false}`` with a 200, not a 404 — that is
        exactly the state a second click on Clear hits, and it is not an error."""
        # A turn still running in it is stopped first, and told to write nothing more of it.
        forget_running([session_id])
        try:
            gone = store.delete(session_id)
        except ValueError:
            return {"ok": False}
        # Its rows in the history index go with it: the screen says the conversation is gone, and
        # an index that still answered questions about it would make that a lie. So do its share
        # tokens: a link into a deleted conversation must open nothing.
        history.forget_session(session_id)
        shares.revoke_session(session_id)
        bus.drop(session_id)
        work_store.forget_parent(session_id)
        return {"ok": gone}

    @app.delete("/api/code/projects", dependencies=[guard], response_model=DeletedCountOut)
    def delete_code_project(workspace: str) -> dict[str, int]:
        """Forget every conversation filed under one project. **The folder is not touched.**

        A project is a grouping and nothing more: the sidebar files conversations by the workspace
        each recorded, and no other record of one exists. So "delete the project" can only honestly
        mean the transcripts — and the count comes back so the screen can say how many went rather
        than reporting a success with no size.
        """
        # The ids first, then the files, then the index: the index is keyed by session id, and
        # the list is the only place the workspace-to-id mapping exists.
        ids = [str(m["id"]) for m in store.list_meta() if m["workspace"] == workspace]
        forget_running(ids)
        deleted = store.delete_project(workspace)
        history.forget_sessions(ids)
        for sid in ids:
            shares.revoke_session(sid)
            bus.drop(sid)
        return {"deleted": deleted}

    # The registered projects live at `/workspaces`, NOT at `/projects`, and the distance is
    # deliberate. `DELETE /api/code/projects` above already means "delete every conversation filed
    # under this project" — a second DELETE on that path meaning "forget the bookmark" would read
    # identically at the call site and destroy transcripts when a user tidied their list. Two verbs
    # that differ only in what they erase do not share a noun.
    def _project_rows(rows: Sequence[Any]) -> list[dict[str, Any]]:
        return [
            {
                "path": row.path,
                "alias": row.alias,
                "shell_granted": row.shell_granted,
                "granted_at": row.granted_at,
                "pinned": row.pinned,
                "last_used_at": row.last_used_at,
                "hidden": row.hidden,
            }
            for row in rows
        ]

    @app.get("/api/code/workspaces", dependencies=[guard], response_model=list[CodeProjectOut])
    def list_code_workspaces() -> list[dict[str, Any]]:
        """The projects you have added, in the order you added them — hidden ones included.

        The sidebar unions these with the projects it derives from conversations, so a project you
        have worked in stays listed whether or not it was ever registered — nothing disappears
        because it was not on this list. Hidden rows are sent rather than filtered, because hiding a
        folder you have talked about is exactly the case the sidebar must know about to leave out.
        """
        return _project_rows(projects.entries())

    @app.patch("/api/code/workspaces", dependencies=[guard], response_model=list[CodeProjectOut])
    def flag_code_workspace(body: CodeProjectFlagsIn) -> list[dict[str, Any]]:
        """Pin or hide a project, registering it if it was not. Hiding revokes its grant and pin.

        Only ever narrows what the agent may do, so the bridge serves it at its operate tier.
        """
        try:
            rows = projects.set_flags(body.path, pinned=body.pinned, hidden=body.hidden)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _project_rows(rows)

    @app.put(
        "/api/code/workspaces/grant", dependencies=[guard], response_model=list[CodeProjectOut]
    )
    def grant_code_workspace(body: CodeProjectGrantIn) -> list[dict[str, Any]]:
        """Grant or revoke commands in one folder — the record every coding turn is held to.

        Its own route rather than a field on the PATCH above, so the bridge can be kept out of
        granting altogether (`bridge_routes.OWNER_DECISION_ROUTES`, every tier) while pinning and
        hiding stay at operate. Behind the same guard as the rest of the
        API: with no ``CHIMERA_SERVER_TOKEN`` set, a local process can reach this as it can reach
        every other route. What moving the grant here changes is that a REQUEST no longer carries
        it; recording one is a separate act, listed in the Folders card where it can be revoked.
        """
        try:
            rows = projects.set_grant(body.path, body.shell_granted)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _project_rows(rows)

    # ---- The project's pack (study 29, P7.6) --------------------------------------------------
    # Not on the bridge: accepting a pack is the owner reading a file from a repository and
    # deciding it may shape the agent there, and revoking one widens again. Both are the owner's.
    def _pack_state(path: str) -> dict[str, Any]:
        from chimera.core import project_pack as packs
        from chimera.integrations.mcp_config import load_servers
        from chimera.skills.bundles import active as active_bundles

        current = live()
        folder = path.strip()
        if not folder:
            raise HTTPException(status_code=400, detail="name the project folder")
        found = packs.read_pack(folder)
        out = ProjectPackOut(enabled=bool(current.project_pack), present=found.present)
        out.error = found.error
        out.digest = found.digest
        agreed = packs.accepted_digest(current.home, folder)
        out.accepted = found.present and bool(agreed) and agreed == found.digest
        out.changed = bool(agreed) and agreed != found.digest
        # The file moved away from what was accepted — edited, broken or deleted — and the version
        # the owner accepted still applies, from their record. Said on the card, with the revoke
        # button, even when the file is gone: a narrowing nobody can see is one nobody can lift.
        out.held = out.changed and packs.held_pack(current.home, folder) is not None
        out.applied = out.enabled and (out.accepted or out.held)
        pack = found.pack
        if pack is None:
            return out.model_dump()
        out.skills = list(pack.skills) if pack.skills is not None else None
        out.mcp = list(pack.mcp) if pack.mcp is not None else None
        out.tools_deny = list(pack.tools_deny)
        out.ignored = list(pack.ignored)
        # Held against what is switched on and configured — read from disk, never by connecting a
        # server: a screen asking "what would this hide?" must not be what spawns them.
        effect = packs.effect(
            pack,
            (b.name for b in active_bundles(current.home)),
            [s.name for s in load_servers(current.home / "mcp.json")],
        )
        for name, value in vars(effect).items():
            setattr(out, name, value)
        return out.model_dump()

    def _pack_folder(path: str) -> Path:
        """The folder a write names, checked BEFORE the record is touched (400 otherwise). A blank
        path resolved to the sidecar's working directory, so a blank revoke lifted that folder's
        acceptance and a blank accept could accept a pack nobody named."""
        from chimera.core.project_pack import PackError, project_folder

        try:
            return project_folder(path)
        except PackError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/code/pack", dependencies=[guard], response_model=ProjectPackOut)
    def get_code_pack(path: str) -> dict[str, Any]:
        """What this folder's pack asks for, what it would hide, and whether it applies now."""
        return _pack_state(path)

    @app.post("/api/code/pack/accept", dependencies=[guard], response_model=ProjectPackOut)
    def accept_code_pack(body: ProjectPackAcceptIn) -> dict[str, Any]:
        """Let this folder's pack narrow runs here — the file the screen showed, by its digest.

        A file that changed between the screen reading it and this request is refused (409): the
        click was about the bytes that were shown, and transferring it to new ones would be a
        consent nobody gave. Accepting never widens anything; it is still the owner's, because the
        file may come from someone else's repository.
        """
        from chimera.core.project_pack import PackError, accept

        folder = _pack_folder(body.path)
        try:
            accept(live().home, folder, body.digest.strip())
        except PackError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _pack_state(str(folder))

    @app.delete("/api/code/pack", dependencies=[guard], response_model=ProjectPackOut)
    def revoke_code_pack(path: str) -> dict[str, Any]:
        """Stop applying this folder's pack. The file stays; the owner's settings apply whole."""
        from chimera.core.project_pack import revoke

        folder = _pack_folder(path)
        revoke(live().home, folder)
        return _pack_state(str(folder))

    @app.post(
        "/api/code/workspaces/grant/migrate",
        dependencies=[guard],
        response_model=CodeGrantMigrationOut,
    )
    def migrate_code_workspace_grants(body: CodeGrantMigrationIn) -> dict[str, Any]:
        """Record the folders the desktop had granted in its own storage — ONCE per installation.

        The first call closes the window whatever it carries, an empty list included; every later
        one changes nothing and answers ``migrated: false``. Not on the bridge: it exists for one
        client's one-time upgrade, not as a second way to grant.
        """
        migrated, recorded, rows = projects.migrate_grants(body.paths)
        return {"migrated": migrated, "recorded": recorded, "projects": _project_rows(rows)}

    @app.post("/api/code/workspaces", dependencies=[guard], response_model=list[CodeProjectOut])
    def register_code_workspace(body: CodeProjectIn) -> list[dict[str, Any]]:
        """Add a project, or name one you already added. Idempotent on the path.

        Registering says nothing about whether the folder exists — a bookmark to a moved checkout
        should stay visible so it can be corrected, rather than vanishing and taking its name with
        it — and nothing about where the agent may write, which the workspace guard decides from the
        request and never from here.
        """
        try:
            rows = projects.register(body.path, body.alias)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _project_rows(rows)

    @app.delete("/api/code/workspaces", dependencies=[guard], response_model=list[CodeProjectOut])
    def forget_code_workspace(path: str) -> list[dict[str, Any]]:
        """Forget a bookmark. **Conversations are not touched**, so a project you have worked in
        reappears in the sidebar as one you have talked about rather than one you registered. Its
        grant goes with it; to keep a folder out of the lists for good, hide it (PATCH)."""
        return _project_rows(projects.remove(path))

    @app.get("/api/ui/layout", dependencies=[guard], response_model=UiLayoutOut)
    def get_ui_layout() -> dict[str, Any]:
        """The screen layout the desktop stored, or null. Null is the ordinary first-run answer, and the
        client then keeps what its own storage has (and sends it here), or its default."""
        return {"layout": ui_layout.read()}

    @app.put("/api/ui/layout", dependencies=[guard], response_model=UiLayoutOut)
    def put_ui_layout(body: UiLayoutIn) -> dict[str, Any]:
        """Keep the screen layout. 413 for one over the size cap; the shape is the client's to check."""
        try:
            ui_layout.write(body.layout)
        except OverflowError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return {"layout": body.layout}

    # --- sharing: the owner's controls, and the guest app under /guest -------------------------
    #
    # Last, because the guest app is handed the turn starter and the session view defined above,
    # and `register_code_api` is the only place both exist. The network listener it returns is
    # kept on `app.state` so the process can close the door on shutdown.
    from chimera.api.guest_api import build_guest_app, register_sharing_api

    def _session_workspace(session_id: str) -> str:
        # A conversation started in the server's own folder stores no workspace ("" is the
        # request's convention for "yours"); the guest is told, and sent to, that folder.
        view = _session_view(session_id)
        stored = str(view.get("workspace") or "") if view else ""
        return stored or str(workspace)

    def _session_exists(session_id: str) -> bool:
        try:
            return store._path(session_id).is_file()
        except ValueError:
            return False

    # Both read per request from the live settings, so the Settings screen's switch applies at once.
    def _sharing_on() -> bool:
        return bool(live().sharing)

    def _share_expires_in() -> float | None:
        hours = live().share_expiry_hours
        return hours * 3600.0 if hours else None

    guest_app = build_guest_app(
        store=shares,
        bus=bus,
        session_view=_session_view,
        session_workspace=_session_workspace,
        start_turn=_start_turn,
        static_dir=static_dir,
        sharing_on=_sharing_on,
    )
    app.state.guest_server = register_sharing_api(
        app,
        guard,
        store=shares,
        bus=bus,
        guest=guest_app,
        session_exists=_session_exists,
        sharing_on=_sharing_on,
        expires_in=_share_expires_in,
    )
    # The access card (`chimera/api/access_api.py`) names each link's conversation by the title the
    # sidebar shows, read from the same listing rather than from a second reader of the files.
    app.state.code_session_titles = lambda: {
        str(row["id"]): str(row.get("title") or "") for row in store.list_meta()
    }
    app.state.session_bus = bus
    app.state.share_store = shares

