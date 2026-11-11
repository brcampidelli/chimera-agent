"""Markdown prompt commands and subagents, for the desktop: listed per project, and expanded on a turn.

Until this module the two kinds of Markdown definition had no route. ``.chimera/commands/*.md`` worked
in the terminal chat and the TUI, and ``.chimera/agents/*.md`` only behind ``chimera solve
--subagents`` — so the app could neither show what a project defines nor use a command, and a
person who wrote one in their repository could only find out it existed from the terminal.

Nothing here adds a capability. A command's only output is the text of a user message, built by
``chimera.core.commands.expand_command`` exactly as the terminal builds it, and a plugin's command is
recorded on the turn's taint ledger by the same call the terminal makes. The agents listing is read
only: the Code turn has no ``spawn_subagent`` tool (``assemble_registry`` registers none, and saying
otherwise would mean building a new path to spawn work on this surface), so the screen lists them
and says where they run today.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, params
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from chimera.config import Settings
from chimera.core.code_session import CodeSessionStore
from chimera.core.commands import (
    PromptCommand,
    discover_commands,
    expand_command,
    find_command,
    source_label,
)
from chimera.governance.sanitize import sanitize_untrusted
from chimera.orchestration.metering import MeteredBackend
from chimera.providers import LLMGateway, MissingCredentialsError
from chimera.review import CautiousVerifier, DiffError, KeepAll, collect
from chimera.review import review as run_review
from chimera.review.code_card import (
    MAX_REVIEW_BATCHES,
    MAX_REVIEW_BODY,
    MAX_REVIEW_CHARS,
    MAX_REVIEW_CHECKS,
    REVIEW_CALL_TIMEOUT_S,
    REVIEW_TIMEOUT_S,
    build_card,
    edits_review,
    fit_diff,
    fit_edits,
    parse_limit,
    sanitised,
)
from chimera.review.diff import ReviewDiff, file_patch
from chimera.review.effort import DEFAULT_EFFORT, confidence_cut, verifies
from chimera.review.pipeline import BUDGET_REASON
from chimera.review.reviewer_for import reviewer_for
from chimera.telemetry import get_logger


class PromptCommandOut(BaseModel):
    """One command the composer can offer."""

    name: str
    description: str
    #: ``project`` (the folder's ``.chimera/commands``), ``owner`` (``<home>/commands``) or
    #: ``plugin:<name>`` (an active plugin's, which a turn records as untrusted).
    source: str
    argument_hint: str
    #: A broken file stays listed with what is wrong, so it can be repaired; sending it is refused.
    error: str | None = None


class PromptCommandsOut(BaseModel):
    commands: list[PromptCommandOut]
    #: Names the app keeps for itself: a file with one of these names is never expanded here.
    reserved: list[str]


class MarkdownAgentOut(BaseModel):
    """One Markdown subagent, as the session would see it."""

    id: str
    description: str
    #: ``project``, ``owner`` or ``plugin:<name>``.
    source: str
    #: Where it was read from, as discovery labels it (``project:.chimera/agents/x.md``).
    file: str
    #: The pinned model — only an owner agent may pin one. None: inherits the default.
    model: str | None
    #: A project or plugin agent: any model its file names is ignored, by rule.
    model_ignored: bool
    #: The names the file declares, after a plugin's are translated to ours.
    declared_tools: list[str]
    #: What it would actually get here: the declared names this install has, minus the owner's floor.
    effective_tools: list[str]
    steps: int
    #: ``claude`` (a Claude Code folder), ``chimera`` (``.chimera/agents`` or the owner's ``agents``)
    #: or ``plugin``. With ``source`` it says where the file is: project/owner/plugin and which folder.
    origin: str = "chimera"
    #: opus/sonnet/haiku/fable resolved to weak, mid or top by the owner's mapping.
    model_role: str | None = None
    color: str | None = None
    #: ``user``, ``project`` or ``local``: the memory scope the file asked for.
    memory: str | None = None
    #: ``permissionMode: plan`` narrowed it to read-only tools.
    plan_only: bool = False
    #: The file lists no ``tools``, so it gets what the session offers (after its own denials).
    inherits_tools: bool = False


class AgentIssueOut(BaseModel):
    path: str
    message: str
    agent_id: str


class MarkdownAgentsOut(BaseModel):
    agents: list[MarkdownAgentOut]
    issues: list[AgentIssueOut]
    #: False: the Code turn has no subagent tool, so these run from ``chimera solve --subagents``.
    runs_in_code: bool


class ReviewEditIn(BaseModel):
    """One edit the conversation made, as its transcript card holds it."""

    path: str = Field(max_length=1024)
    patch: str = Field(max_length=200_000)


class CodeReviewRequest(BaseModel):
    """``/code-review``: the working tree against its merge base, reviewed now."""

    workspace: str | None = None
    #: The conversation's edits, in order. Reviewed only when the folder has no git diff to review.
    edits: list[ReviewEditIn] | None = Field(default=None, max_length=500)
    #: The conversation the card belongs to. Its folder is the one reviewed; a `workspace` that differs is refused.
    session_id: str | None = None
    #: A number from 1 to 200, or ``all``. Absent: the card's default.
    max_findings: str | None = None


class CodeReviewFindingOut(BaseModel):
    id: str
    priority: str
    severity: str
    file: str
    line: int
    title: str
    consequence: str
    evidence: str
    confidence: float | None
    #: Not checked by the verifier: shown with a mark, and not sent with "Apply fixes" by default.
    unverified: bool = False


class CodeReviewFileOut(BaseModel):
    file: str
    #: The file's raw diff against the merge base, for "Ver no diff".
    patch: str
    findings: list[CodeReviewFindingOut]


class CodeReviewOut(BaseModel):
    #: ``findings``, ``no_findings``, ``incomplete`` (some of the change was not reviewed, or a call
    #: failed), ``empty`` (nothing changed) or ``no_diff`` (not a git repository).
    status: str
    reason: str = ""
    #: A part of the change was left out (a cost ceiling) or unverified: the card says so.
    partial: bool = False
    base: str = ""
    model: str = ""
    usd: float | None = None
    shown: int = 0
    #: Findings beyond the person's limit: counted, so the card can say how many it left out.
    omitted: int = 0
    #: Findings the anchor or the verifier dropped, with their reasons in the terminal's report.
    dropped: int = 0
    tainted: bool = False
    files: list[CodeReviewFileOut] = []
    notes: list[str] = []
    untested_paths: list[str] = []


def reserved_command_names() -> frozenset[str]:
    """The slash names the app's conversations keep: the commands every conversational surface
    shares (`chimera.interface.render.session_commands`). A project file named ``undo.md`` must not
    turn ``/undo`` into prompt text here while it undoes in the terminal — one name, one meaning."""
    from chimera.interface.render import session_commands

    # `code-review` belongs to the Code screen's built-in review card: a file of that name must not
    # turn the command into prompt text here.
    return frozenset({*(command.name.lstrip("/") for command in session_commands()), "code-review"})


@dataclass(frozen=True)
class ExpandedTurn:
    """A turn message that started with a known command, and what it became."""

    text: str
    command: PromptCommand


def expand_turn_message(message: str, workspace: Path, home: Path) -> ExpandedTurn | None:
    """``/name args`` -> the command's text, by the terminal's rules; None for anything else.

    None for a message that does not start with ``/``, for an unknown name and for a reserved one:
    the message then goes out as typed, as it always did. A command whose file is broken raises
    ``ValueError`` — sending its name as literal text would hide the error behind an odd answer.
    """
    from chimera.interface.render import split_command

    head, argument = split_command(message)
    if not head:
        return None
    command = find_command(
        discover_commands(workspace, home), head, builtins=set(reserved_command_names())
    )
    if command is None:
        return None
    return ExpandedTurn(expand_command(command, argument), command)


def _agent_source(source: str) -> str:
    if source.startswith("project:"):
        return "project"
    if source.startswith("global:"):
        return "owner"
    if source.startswith("plugin:"):
        return source.split("/", 1)[0]
    return source


def available_tools(workspace: Path, settings: Settings) -> set[str]:
    """The native tool names a turn in ``workspace`` starts from, inside the owner's fence.

    The same registry ``GET /api/tools`` shows, cut by ``deployment_fence`` (the denylist, the reach
    floor and the allowlist). A request's own posture can only narrow it further, so this is the
    most an agent's list could reach here — never a promise of more than a session would give.
    """
    from chimera.api.posture import deployment_fence
    from chimera.tools.builtin import default_registry

    denied, allowed = deployment_fence(settings)
    names = set(default_registry(workspace).declared_names()) - denied
    return names & allowed if allowed is not None else names


def list_markdown_agents(workspace: Path, settings: Settings) -> dict[str, Any]:
    from chimera.core.markdown_agents import discover_agents
    from chimera.core.registry import load as load_saved_agents

    home = Path(settings.home)
    catalog = discover_agents(workspace, home, saved_agents=load_saved_agents(home), settings=settings)
    available = available_tools(workspace, settings)
    agents = []
    for agent in catalog.agents:
        bound = agent.for_session(available)
        agents.append(
            MarkdownAgentOut(
                id=agent.id,
                description=agent.description,
                source=_agent_source(agent.source),
                file=agent.source,
                model=agent.model or None,
                model_ignored=agent.from_project if agent.model_ignored is None else agent.model_ignored,
                model_role=agent.model_role or None,
                origin=agent.origin,
                color=agent.color or None,
                memory=agent.memory_scope or None,
                plan_only=agent.plan_only,
                inherits_tools=agent.inherit_tools,
                declared_tools=list(agent.definition.allowed_tools),
                effective_tools=list(bound.effective_tools),
                steps=agent.steps,
            )
        )
    issues = [
        AgentIssueOut(path=issue.path, message=issue.message, agent_id=issue.agent_id)
        for issue in catalog.issues
    ]
    return MarkdownAgentsOut(agents=agents, issues=issues, runs_in_code=False).model_dump()


#: The meter label and usage row prefix of a ``/code-review`` call.
CODE_REVIEW_LABEL = "code-review"
_log = get_logger("api.code_review")


def _same_folder(a: Path, b: Path) -> bool:
    """Whether two paths name one folder, case and symlinks aside as the OS sees them."""
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def _record_review_spend(home: Path, meter: Any, session_id: str | None) -> None:
    """One usage row for the calls the review made, or none when no call returned."""
    if not meter.calls:
        return
    from uuid import uuid4

    from chimera.api.usage import record_spend

    record_spend(
        home,
        session_id=f"{CODE_REVIEW_LABEL}:{session_id or 'no-session'}:{uuid4().hex[:8]}",
        model=meter.last_model,
        prompt_tokens=meter.prompt_tokens,
        completion_tokens=meter.completion_tokens,
        usd=meter.usd,
        route_kind=None,
    )


def _mark_outside_read(home: Path, session_id: str) -> None:
    """Record that this conversation read outside text, through the turn's own taint mechanism."""
    from chimera.core.task_cards import CARDS_FILE_NAME, open_store

    open_store(home / CARDS_FILE_NAME).settle_turn(session_id, [], tainted=True)


def _tainted(home: Path, session_id: str | None) -> bool:
    """Whether the conversation read outside text. A store that cannot be read says yes."""
    if not session_id:
        return False
    from chimera.core.task_cards import CARDS_FILE_NAME, open_store

    return open_store(home / CARDS_FILE_NAME).is_tainted_session(session_id)


def register_commands_api(
    app: FastAPI,
    guard: params.Depends,
    workspace: Path,
    settings: Settings,
    *,
    live_settings: Any = None,
    review_backend: Callable[[], Any] | None = None,
) -> None:
    """Mount the read-only listings and ``/code-review``. ``workspace`` is the folder a request that
    names none means. ``review_backend`` replaces the configured models (tests only)."""

    def live() -> Settings:
        return live_settings() if live_settings is not None else settings

    def folder(requested: str | None) -> Path:
        if not requested:
            return workspace
        path = Path(requested).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(status_code=400, detail="workspace not found")
        return path

    @app.get("/api/code/commands", dependencies=[guard], response_model=PromptCommandsOut)
    def code_commands(workspace: str | None = None) -> dict[str, Any]:
        """The Markdown commands a conversation in ``workspace`` can send, as the composer lists
        them: the project's win over the owner's on a shared name, then the active plugins'."""
        reserved = reserved_command_names()
        commands = [
            PromptCommandOut(
                name=command.name,
                description=command.description,
                source=source_label(command.origin),
                argument_hint=command.argument_hint,
                error=command.error,
            )
            for command in discover_commands(folder(workspace), Path(live().home))
            if command.name not in reserved
        ]
        return PromptCommandsOut(commands=commands, reserved=sorted(reserved)).model_dump()

    @app.get("/api/agents/markdown", dependencies=[guard], response_model=MarkdownAgentsOut)
    def markdown_agents(workspace: str | None = None) -> dict[str, Any]:
        """The Markdown and plugin subagents for ``workspace``, with the tools each would really
        get and every file that was shadowed or refused. Read only: nothing here edits a file."""
        return list_markdown_agents(folder(workspace), live())

    def review_folder(req: CodeReviewRequest) -> Path:
        """The folder a review reads: the conversation's own folder when it has one, otherwise the server's
        own folder, which is where a new turn on this screen runs. Any other folder named in the request is
        refused: a card reviews only the code the conversation can reach."""
        own = folder(None).resolve()
        stored = ""
        if req.session_id:
            stored = CodeSessionStore(Path(live().home) / "code_sessions").stored_workspace(req.session_id)
        owned = folder(stored).resolve() if stored else own
        if req.workspace and not _same_folder(folder(req.workspace), owned):
            raise HTTPException(status_code=409, detail="that folder is not this conversation's")
        return owned

    @app.post("/api/code/review", dependencies=[guard], response_model=CodeReviewOut)
    async def code_review(request: Request) -> dict[str, Any]:
        """``/code-review``. The body is size-checked before it is parsed, then the review runs off the event loop."""
        declared = request.headers.get("content-length", "")
        if (declared.isdigit() and int(declared) > MAX_REVIEW_BODY):
            raise HTTPException(status_code=413, detail="the review request is too large")
        raw = await request.body()
        if len(raw) > MAX_REVIEW_BODY:
            raise HTTPException(status_code=413, detail="the review request is too large")
        try:
            req = CodeReviewRequest.model_validate_json(raw or b"{}")
        except ValidationError:
            raise HTTPException(status_code=422, detail="the review request is not valid") from None
        return await run_in_threadpool(review_now, req)

    def review_now(req: CodeReviewRequest) -> dict[str, Any]:
        """``/code-review``: review a change with a reviewer from another model family. The reviewer is a
        plain completion with no tools: it reads the change and says what it finds. The change is the
        working tree against its merge base, or the conversation's own edits when there is none. The
        change is somebody's text, so the card marks the conversation as having read outside text.
        Cost has ceilings; past them the card says the review is partial. Nothing here writes a file."""
        try:
            limit = parse_limit(req.max_findings)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        settings = live()
        home = Path(settings.home)
        root = review_folder(req)
        try:
            diff: ReviewDiff | None = collect(root, context=3)
            git_reason = ""
        except DiffError as exc:
            diff, git_reason = None, str(exc)
        extra_notes: list[str] = []
        left_out: list[str] = []
        if diff is not None and diff.files:
            diff, left_out = fit_diff(sanitised(diff), MAX_REVIEW_CHARS)
            if left_out:
                extra_notes.insert(0, f"outside this review's size budget: {', '.join(left_out)}")
            base = diff.base

            def patch_of(path: str) -> str:
                return file_patch(root, base, path)
        elif req.edits:
            # No git, or no diff against the base: the conversation's own edits are the change.
            kept, left_out = fit_edits([(e.path, sanitize_untrusted(e.patch)) for e in req.edits], MAX_REVIEW_CHARS)
            diff, patches, unreadable = edits_review(kept)
            extra_notes = [f"not readable as a diff: {path}" for path in unreadable]
            if left_out:
                extra_notes.insert(0, f"outside this review's size budget: {', '.join(left_out)}")
            if not diff.files:
                return CodeReviewOut(
                    status="incomplete", reason="the conversation's edits are not readable as a diff",
                    notes=extra_notes, partial=bool(left_out),
                ).model_dump()

            def patch_of(path: str) -> str:
                return patches.get(path, "")
        elif diff is None:
            return CodeReviewOut(status="no_diff", reason=git_reason).model_dump()
        else:
            return CodeReviewOut(status="empty", base=diff.base_label).model_dump()
        if review_backend is None and not settings.can_answer():
            raise HTTPException(status_code=409, detail="no model is configured to answer")
        # The change was read: the conversation is tainted from here on, as a turn that read a file is.
        if req.session_id:
            _mark_outside_read(home, req.session_id)
        choice = reviewer_for(settings.default_model)
        # Each model call has its own limit: a call that hangs ends, and the review goes on without it.
        call_timeout = (
            REVIEW_CALL_TIMEOUT_S if settings.request_timeout <= 0
            else min(settings.request_timeout, REVIEW_CALL_TIMEOUT_S)
        )
        review_settings = settings.model_copy(update={"request_timeout": call_timeout})
        inner = review_backend() if review_backend is not None else LLMGateway(review_settings)
        meter = MeteredBackend(inner, label=CODE_REVIEW_LABEL)
        verifier = CautiousVerifier(meter, choice.model) if verifies(DEFAULT_EFFORT) else KeepAll()
        def work() -> Any:
            # The worker records the spend itself, so a call that ends after the wall-clock limit is still counted.
            try:
                return run_review(
                    diff, meter, choice, verifier,
                    confidence_cut=confidence_cut(DEFAULT_EFFORT), effort=DEFAULT_EFFORT,
                    max_batches=MAX_REVIEW_BATCHES, max_verifications=MAX_REVIEW_CHECKS,
                )
            finally:
                _record_review_spend(home, meter, req.session_id)

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(work)
            report = future.result(timeout=REVIEW_TIMEOUT_S)
        except FuturesTimeout:
            return CodeReviewOut(status="incomplete", reason="the review ran out of time").model_dump()
        except MissingCredentialsError:
            raise HTTPException(status_code=409, detail="no model is configured to answer") from None
        except Exception as exc:  # noqa: BLE001 - the detail goes to the log, never to the card
            _log.warning("code review failed: %s: %s", type(exc).__name__, exc)
            return CodeReviewOut(
                status="incomplete", reason="the review failed; the server log has the detail",
            ).model_dump()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for item in report.not_reviewed:
            if item.reason not in ("binary file", BUDGET_REASON):
                _log.warning("code review could not read %s: %s", item.file, item.reason)
        tainted = True
        card = build_card(
            report, limit=limit, patch_of=patch_of, tainted=tainted,
            extra_notes=extra_notes, partial=bool(left_out),
        )
        return CodeReviewOut(**card).model_dump()


__all__ = [
    "CodeReviewOut",
    "CodeReviewRequest",
    "ReviewEditIn",
    "ExpandedTurn",
    "MarkdownAgentsOut",
    "PromptCommandsOut",
    "available_tools",
    "expand_turn_message",
    "list_markdown_agents",
    "register_commands_api",
    "reserved_command_names",
]
