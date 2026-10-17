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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, params
from pydantic import BaseModel

from chimera.config import Settings
from chimera.core.commands import (
    PromptCommand,
    discover_commands,
    expand_command,
    find_command,
    source_label,
)


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


class AgentIssueOut(BaseModel):
    path: str
    message: str
    agent_id: str


class MarkdownAgentsOut(BaseModel):
    agents: list[MarkdownAgentOut]
    issues: list[AgentIssueOut]
    #: False: the Code turn has no subagent tool, so these run from ``chimera solve --subagents``.
    runs_in_code: bool


def reserved_command_names() -> frozenset[str]:
    """The slash names the app's conversations keep: the commands every conversational surface
    shares (`chimera.interface.render.session_commands`). A project file named ``undo.md`` must not
    turn ``/undo`` into prompt text here while it undoes in the terminal — one name, one meaning."""
    from chimera.interface.render import session_commands

    return frozenset(command.name.lstrip("/") for command in session_commands())


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
    names = set(default_registry(workspace).names()) - denied
    return names & allowed if allowed is not None else names


def list_markdown_agents(workspace: Path, settings: Settings) -> dict[str, Any]:
    from chimera.core.markdown_agents import discover_agents
    from chimera.core.registry import load as load_saved_agents

    home = Path(settings.home)
    catalog = discover_agents(workspace, home, saved_agents=load_saved_agents(home))
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
                model=None if agent.from_project else (agent.model or None),
                model_ignored=agent.from_project,
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


def register_commands_api(
    app: FastAPI,
    guard: params.Depends,
    workspace: Path,
    settings: Settings,
    *,
    live_settings: Any = None,
) -> None:
    """Mount the two read-only listings. ``workspace`` is the folder a request that names none means."""

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


__all__ = [
    "ExpandedTurn",
    "MarkdownAgentsOut",
    "PromptCommandsOut",
    "available_tools",
    "expand_turn_message",
    "list_markdown_agents",
    "register_commands_api",
    "reserved_command_names",
]
