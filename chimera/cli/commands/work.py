"""Work management: mcp, kanban and project.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.markup import escape
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.kanban import KanbanBoard



# --- mcp subcommands ----------------------------------------------------------

mcp_app = typer.Typer(
    help="Configure MCP servers (persisted to .chimera/mcp.json). Terminal-first source of truth.",
    no_args_is_help=True,
)
app.add_typer(mcp_app, name="mcp")


def _mcp_path() -> Path:
    return get_settings().home / "mcp.json"


# Module-level Option singletons: repeatable list options must not be built inline in the signature
# (ruff B008 — read the default from a module-level singleton instead).
_MCP_ARG_OPT = typer.Option(None, "--arg", "-a", help="A command argument (repeatable).")
_MCP_ENV_OPT = typer.Option(None, "--env", "-e", help="An env var as K=V (repeatable).")


@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="A unique name for the server (namespaces its tools)."),
    command: str | None = typer.Option(None, "--command", "-c", help="The launch command (e.g. npx, uvx, python)."),
    url: str | None = typer.Option(None, "--url", help="A streamable-HTTP MCP endpoint."),
    token_env: str | None = typer.Option(None, "--token-env", help="Environment variable containing a bearer token."),
    oauth_authorization_url: str | None = typer.Option(None, "--oauth-authorization-url", help="OAuth authorization endpoint."),
    oauth_token_url: str | None = typer.Option(None, "--oauth-token-url", help="OAuth token endpoint."),
    oauth_client_id: str | None = typer.Option(None, "--oauth-client-id", help="OAuth public client ID."),
    oauth_redirect_uri: str | None = typer.Option(None, "--oauth-redirect-uri", help="OAuth loopback redirect URI."),
    oauth_scope: str | None = typer.Option(None, "--oauth-scope", help="OAuth scope string."),
    arg: list[str] = _MCP_ARG_OPT,
    env: list[str] = _MCP_ENV_OPT,
) -> None:
    """Add (or replace-by-name) an MCP server. Persists to .chimera/mcp.json — no connect."""
    from chimera.integrations.mcp_config import McpServerConfig, add_server

    env_map: dict[str, str] = {}
    for pair in env or []:
        if "=" not in pair:
            console.print(f"[red]bad --env '{pair}' (expected KEY=VALUE)[/red]")
            raise typer.Exit(code=1)
        key, value = pair.split("=", 1)
        env_map[key.strip()] = value
    if bool(command) == bool(url):
        console.print("[red]choose exactly one of --command or --url[/red]")
        raise typer.Exit(code=1)
    cfg = McpServerConfig(
        name=name, command=command or "", args=list(arg or []), env=env_map, url=url,
        token_env=token_env, oauth_authorization_url=oauth_authorization_url,
        oauth_token_url=oauth_token_url, oauth_client_id=oauth_client_id,
        oauth_redirect_uri=oauth_redirect_uri, oauth_scope=oauth_scope,
    )
    add_server(_mcp_path(), cfg)
    description = url or command or ""
    console.print(f"[green]added[/green] MCP server [cyan]{name}[/cyan] ({description})")


@mcp_app.command("list")
def mcp_list() -> None:
    """List configured MCP servers (name, command + args, env key names). No connect."""
    from chimera.integrations.mcp_config import load_servers

    servers = load_servers(_mcp_path())
    if not servers:
        console.print("[dim]no MCP servers configured — add one with `chimera mcp add`[/dim]")
        return
    from chimera.integrations.mcp_pins import held_change

    table = Table(title="MCP servers", show_header=True, header_style="bold")
    for col in ("name", "transport", "env", "tools"):
        table.add_column(col)
    for s in servers:
        cmd = s.url or " ".join([s.command, *s.args])
        # Held is the one state worth a column: a server that silently stopped reaching any run
        # because its tools changed is otherwise indistinguishable from one that works.
        held = held_change(_mcp_path(), s.name) is not None
        estado = f"[yellow]held — `chimera mcp approve {s.name}`[/yellow]" if held else "-"
        table.add_row(s.name, cmd, ", ".join(sorted(s.env)) or "-", estado)
    console.print(table)


@mcp_app.command("remove")
def mcp_remove(name: str = typer.Argument(..., help="The server name to remove.")) -> None:
    """Remove a configured MCP server by name."""
    from chimera.integrations.mcp_config import remove_server

    if not remove_server(_mcp_path(), name):
        console.print(f"[yellow]no MCP server named {name}[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]removed[/green] {name}")


@mcp_app.command("test")
def mcp_test(
    name: str = typer.Argument(..., help="The configured server to live-test."),
    timeout: float = typer.Option(12.0, "--timeout", help="Connect timeout in seconds."),
) -> None:
    """Live-connect a configured server and print the tools it exposes (or a clear error).

    This is the ONLY MCP subcommand that connects (spawns the server + runs the async handshake). It
    is the sole honest proof a server is reachable. Needs the 'mcp' extra and the server's own runtime
    (e.g. Node for an npx server).
    """
    from chimera.integrations.mcp_config import load_servers, probe_tools

    cfg = next((s for s in load_servers(_mcp_path()) if s.name == name), None)
    if cfg is None:
        console.print(f"[yellow]no MCP server named {name}[/yellow]")
        raise typer.Exit(code=1)
    try:
        tools = probe_tools(cfg, connect_timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — a graceful message, never a stack trace
        console.print(f"[red]could not connect to {name}: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc
    if not tools:
        console.print(f"[yellow]{name} connected but exposed no tools[/yellow]")
        return
    from chimera.integrations.mcp_pins import tool_cues

    table = Table(title=f"{name}: {len(tools)} tool(s)", show_header=True, header_style="bold")
    table.add_column("tool")
    table.add_column("description")
    table.add_column("cues")
    for tool in tools:
        # The server's text, so escaped: a description is not ours to interpret as console markup.
        # Read over the parameter descriptions too, as the held diff is: at first sight a pin is
        # taken on trust, so this table is the only review a server hostile from day one gets.
        cues = tool_cues(tool["description"], tool.get("input_schema"))
        table.add_row(
            escape(tool["name"]),
            escape(tool["description"]),
            f"[yellow]{', '.join(cues)}[/yellow]" if cues else "-",
        )
    console.print(table)


@mcp_app.command("approve")
def mcp_approve(
    name: str = typer.Argument(..., help="The held server whose changed tools to approve."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve without asking (the diff still prints)."),
) -> None:
    """Show how a held server's tools changed since you approved them, and approve the change.

    A server whose tool descriptions or parameters changed since they were approved is not mounted
    by the app, `chimera serve` or its bots until approved here or on the app's MCP screen. File
    I/O only; the server is connected again on the next start.
    """
    from chimera.integrations.mcp_pins import StaleApproval, approve_change, held_change

    held = held_change(_mcp_path(), name)
    if held is None:
        console.print(f"[yellow]nothing held for {escape(name)}[/yellow]")
        raise typer.Exit(code=1)
    for change in held["changes"]:
        console.print(f"[bold]{escape(change['tool'])}[/bold] — {change['change']}")
        if change["duplicate"]:
            console.print("  [yellow]listed more than once; only the first is mounted[/yellow]")
        if change["change"] != "added" and change["description_changed"]:
            console.print(f"  [red]- {escape(change['old_description'])}[/red]")
        if change["change"] != "removed" and change["description_changed"]:
            console.print(f"  [green]+ {escape(change['new_description'])}[/green]")
        # The parameters themselves, not "parameters changed": a parameter description is text the
        # model reads, and approving it unseen is the rubber stamp this command exists to avoid.
        if change["schema_changed"]:
            if change["change"] != "added" and change["old_schema"]:
                console.print("  [red]- parameters:[/red]")
                console.print(f"[red]{escape(change['old_schema'])}[/red]")
            if change["change"] != "removed" and change["new_schema"]:
                console.print("  [green]+ parameters:[/green]")
                console.print(f"[green]{escape(change['new_schema'])}[/green]")
        if change["cues"]:
            console.print(
                f"  [yellow]steering cues in the new text: {', '.join(change['cues'])}[/yellow]"
            )
    if not yes and not typer.confirm("Approve these changes?", default=False):
        console.print("[dim]left held[/dim]")
        raise typer.Exit(code=1)
    try:
        # The digest of what was printed above: if a mount elsewhere replaced the held listing while
        # the question was on screen, this approves nothing rather than the unseen replacement.
        approve_change(_mcp_path(), name, held["digest"])
    except StaleApproval:
        console.print(
            f"[yellow]{escape(name)} changed again while you were reading; nothing approved. "
            "Run the command again to see the new diff.[/yellow]"
        )
        raise typer.Exit(code=1) from None
    console.print(f"[green]approved[/green] {escape(name)} — it connects on the next start")


@mcp_app.command("desktop")
def mcp_desktop() -> None:
    """Serve an MCP server on stdio that operates the RUNNING desktop app (for Claude Code/Desktop).

    Needs the app open with Settings > "Allow Claude to operate this app" on; the tools then call
    the app's local bridge. Register it with: claude mcp add chimera-desktop -- chimera mcp desktop
    """
    import sys

    from chimera.server.desktop_mcp import DesktopMCP

    # stdio IS the MCP wire, so the notice goes to stderr.
    print("chimera desktop bridge on stdio — operating the running Chimera app", file=sys.stderr)
    try:
        DesktopMCP().serve_stdio()
    except ModuleNotFoundError as exc:
        print(f"MCP SDK missing — install with: pip install 'chimera-agent[mcp]' ({exc})", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)


# --- kanban subcommands -------------------------------------------------------

kanban_app = typer.Typer(help="Task board with worker lanes (backlog/doing/review/done).", no_args_is_help=True)
app.add_typer(kanban_app, name="kanban")


def _board() -> KanbanBoard:
    from chimera.kanban import KanbanBoard

    return KanbanBoard(get_settings().home / "kanban.json")


@kanban_app.command("add")
def kanban_add(
    title: str = typer.Argument(..., help="Short card title."),
    action: str = typer.Option(None, "--action", "-a", help="Task text to run (defaults to title)."),
    lane: str = typer.Option(
        "solve",
        "--lane",
        "-l",
        help="Who works it: solve | crew | the id of one of your agents (`chimera agents`).",
    ),
    verify: str = typer.Option(None, "--verify", help="Verify command for the solve lane (exit 0)."),
) -> None:
    """Add a card to the backlog."""
    card = _board().add(title, action or title, lane=lane, verify=verify)
    console.print(f"added [cyan]{card.id}[/cyan] to backlog (lane {card.lane})")


@kanban_app.command("board")
def kanban_board() -> None:
    """Show the board, column by column."""
    from chimera.kanban import COLUMNS

    board = _board()
    for column in COLUMNS:
        cards = board.cards(column)
        console.print(f"[bold]{column}[/bold] ([cyan]{len(cards)}[/cyan])")
        for card in cards:
            mark = "" if card.success is None else (" [green]✓[/green]" if card.success else " [red]✗[/red]")
            console.print(f"  [cyan]{card.id}[/cyan] [{card.lane}] {card.title}{mark}")


@kanban_app.command("move")
def kanban_move(
    card_id: str = typer.Argument(..., help="Card id."),
    column: str = typer.Argument(..., help="backlog | doing | review | done."),
) -> None:
    """Move a card to another column."""
    from chimera.kanban import COLUMNS

    if column not in COLUMNS:
        console.print(f"[red]invalid column '{column}' (use {', '.join(COLUMNS)})[/red]")
        raise typer.Exit(code=1)
    board = _board()
    if board.get(card_id) is None:
        console.print(f"[red]no card {card_id}[/red]")
        raise typer.Exit(code=1)
    board.move(card_id, column)  # type: ignore[arg-type]
    console.print(f"moved [cyan]{card_id}[/cyan] -> {column}")


@kanban_app.command("rm")
def kanban_rm(card_id: str = typer.Argument(..., help="Card id.")) -> None:
    """Remove a card."""
    console.print(f"removed [cyan]{card_id}[/cyan]" if _board().remove(card_id) else "[dim]no such card[/dim]")


@kanban_app.command("run")
def kanban_run(
    limit: int = typer.Option(None, "--limit", "-n", help="Max backlog cards to dispatch."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace for the solve lane."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    workers: int = typer.Option(
        1, "--workers", "-j", help="Work this many cards at once, each in its own git worktree."
    ),
) -> None:
    """Dispatch backlog cards through their lanes (solve/crew). Requires a key."""
    from chimera.core.registry import load as load_agents
    from chimera.kanban import LaneRunner, dispatch
    from chimera.kanban.lanes import CrewLane, SolveLane, runners_for

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    board = _board()
    # The registered agents FIRST, so the two built-in lanes cannot be shadowed by an agent that
    # happens to be called `solve`. Naming an agent after a built-in should not silently take over
    # every card already filed under it.
    runners: dict[str, LaneRunner] = {
        **runners_for(load_agents(settings.home), workspace=Path(workspace), model=model),
        "solve": SolveLane(workspace=Path(workspace), model=model),
        "crew": CrewLane(model=model),
    }
    def conflitos(paths: list[str]) -> None:
        # Said before the per-card lines, because it changes how they should be read: those cards
        # succeeded, and not all of their edits survived the merge.
        console.print(f"[yellow]{len(paths)} file(s) changed by more than one card:[/yellow]")
        for path in paths[:10]:
            console.print(f"  [dim]{path}[/dim]")

    outcomes = dispatch(
        board,
        runners,
        limit=limit,
        workers=workers,
        workspace=Path(workspace),
        on_conflict=conflitos,
    )
    if not outcomes:
        console.print("[dim]nothing in backlog to run[/dim]")
        return
    for outcome in outcomes:
        tag = "[green]done[/green]" if outcome.success else "[yellow]review[/yellow]"
        console.print(f"  [cyan]{outcome.card_id}[/cyan] [{outcome.lane}] -> {tag}")


@kanban_app.command("learn")
def kanban_learn(
    min_occurrences: int = typer.Option(3, "--min", help="Min repeats to turn into a card."),
    lane: str = typer.Option("solve", "--lane", "-l", help="Lane for the created cards."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Add every card without prompting."),
) -> None:
    """Turn recurring tasks (from the experience buffer) into backlog cards.

    Uses the cron-learner's recurrence detector; each card is confirmed (or --yes), and
    a task already on the board is skipped — so re-running is safe.
    """
    from chimera.evolution import ExperienceBuffer
    from chimera.scheduler import CronLearner

    history = [e.task for e in ExperienceBuffer(get_settings().home / "experience.json").all()]
    proposals = CronLearner(min_occurrences=min_occurrences).analyze(history)
    if not proposals:
        console.print("[dim]no recurring tasks found in history[/dim]")
        return

    board = _board()
    existing = {card.action for card in board.cards()}
    created = 0
    for proposal in proposals:
        if proposal.action in existing:
            console.print(f"[dim]skip {proposal.name} (already on the board)[/dim]")
            continue
        if yes or typer.confirm(
            f"Add card '{proposal.name}' (seen {proposal.occurrences}x)?", default=False
        ):
            card = board.add(proposal.name, proposal.action, lane=lane)
            created += 1
            console.print(f"  [green]added[/green] {card.id} {proposal.name}")
    console.print(f"added {created} card(s) of {len(proposals)} recurring task(s).")


# --- project subcommands (M19 Track B) ----------------------------------------

project_app = typer.Typer(
    help="Run a project start-to-finish against a Spec (drift = acceptance authority).",
    no_args_is_help=True,
)
app.add_typer(project_app, name="project")


def _project_lane(workspace: str, model: str | None) -> Any:
    from chimera.kanban.lanes import SolveLane

    return SolveLane(workspace=Path(workspace), model=model)


def _print_project(state: Any) -> None:
    color = {"done": "green", "escalated": "red", "awaiting_approval": "yellow"}.get(
        state.status, "cyan"
    )
    console.print(
        f"[bold]{state.id}[/bold]  [{color}]{state.status}[/{color}]  "
        f"iter {state.iterations}"
    )
    if state.note:
        console.print(f"  [dim]{state.note}[/dim]")
    if state.pending_card_id:
        console.print(f"  [yellow]pending approval:[/yellow] card {state.pending_card_id}")


@project_app.command("start")
def project_start(
    spec: str = typer.Argument(..., help="Spec YAML (the acceptance authority)."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Project workspace root."),
    model: str = typer.Option(None, "--model", "-m", help="Override the solve model."),
    max_iterations: int = typer.Option(20, "--max-iterations", help="Hard rail on card runs."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the initial plan-approval pause (auto-approve)."
    ),
) -> None:
    """Create a project from a spec and run it until it aligns or a rail stops it."""
    from chimera.orchestration.project import ProjectConfig, ProjectOrchestrator

    settings = get_settings()
    proj = ProjectOrchestrator.start(
        spec, workspace, home=settings.home, solve_card=_project_lane(workspace, model),
        config=ProjectConfig(max_iterations=max_iterations, require_plan_approval=not yes),
    )
    console.print(f"[green]created[/green] project [bold]{proj.state.id}[/bold] from {spec}")
    state = proj.run()
    _print_project(state)
    if state.status == "awaiting_approval" and not state.plan_approved:
        console.print(
            f"[dim]review the plan, then:[/dim] chimera project approve {state.id}"
        )


@project_app.command("status")
def project_status(project_id: str = typer.Argument(..., help="Project id.")) -> None:
    """Show a project's status and its board."""
    from chimera.kanban import KanbanBoard
    from chimera.orchestration.project import ProjectOrchestrator, ProjectState

    pdir = ProjectOrchestrator.project_dir(get_settings().home, project_id)
    if not (pdir / "project.json").exists():
        console.print(f"[red]no project {project_id}[/red]")
        raise typer.Exit(code=1)
    state = ProjectState.load(pdir / "project.json")
    _print_project(state)
    board = KanbanBoard(Path(state.board_path))
    from chimera.kanban import COLUMNS

    for column in COLUMNS:
        cards = board.cards(column)
        if cards:
            console.print(f"[bold]{column}[/bold] ([cyan]{len(cards)}[/cyan])")
            for card in cards:
                console.print(f"  [cyan]{card.id}[/cyan] {card.title}")


def _resume_project(project_id: str, model: str | None) -> Any:
    from chimera.orchestration.project import ProjectOrchestrator, ProjectState

    pdir = ProjectOrchestrator.project_dir(get_settings().home, project_id)
    if not (pdir / "project.json").exists():
        console.print(f"[red]no project {project_id}[/red]")
        raise typer.Exit(code=1)
    state = ProjectState.load(pdir / "project.json")
    # No config override: `load` rebuilds the rails (max_iterations, require_plan_approval) from the
    # DURABLE state, so a `--max-iterations N` set at start survives a resume. (The plan gate is
    # already correct on its own — `require_plan_approval and not plan_approved`, both persisted.)
    return ProjectOrchestrator.load(
        get_settings().home, project_id, solve_card=_project_lane(state.workspace, model),
    )


@project_app.command("run")
def project_run(
    project_id: str = typer.Argument(..., help="Project id."),
    model: str = typer.Option(None, "--model", "-m", help="Override the solve model."),
) -> None:
    """Continue running a paused/escalated project (re-attempts a soft rail-stop)."""
    _print_project(_resume_project(project_id, model).run())


@project_app.command("step")
def project_step(
    project_id: str = typer.Argument(..., help="Project id."),
    model: str = typer.Option(None, "--model", "-m", help="Override the solve model."),
) -> None:
    """Run exactly one iteration (cron-able)."""
    _print_project(_resume_project(project_id, model).step())


@project_app.command("approve")
def project_approve(
    project_id: str = typer.Argument(..., help="Project id."),
    card: str = typer.Option(None, "--card", help="Approve a specific high-risk card instead of the plan."),
    model: str = typer.Option(None, "--model", "-m", help="Override the solve model."),
) -> None:
    """Approve the initial plan (default) or a paused high-risk card, then continue."""
    proj = _resume_project(project_id, model)
    if card:
        proj.approve_card(card)
    else:
        proj.approve_plan()
    _print_project(proj.run())


@project_app.command("deny")
def project_deny(
    project_id: str = typer.Argument(..., help="Project id."),
    card: str = typer.Option(..., "--card", help="The high-risk card to reject."),
) -> None:
    """Reject a paused high-risk card (parks it for review, escalates to a human)."""
    proj = _resume_project(project_id, None)
    _print_project(proj.deny_card(card))
