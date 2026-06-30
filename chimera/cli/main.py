"""Chimera command-line interface (CLI-first).

Commands:
  version / doctor / models     status & configuration
  run PROMPT                     single-shot Tier-1 completion
  fuse PROMPT                    LLM-Fusion (panel -> judge -> synthesizer)
  agent TASK                     ReAct agent loop with native tools
  solve TASK                     Tier-2 autonomous (plan + verify-or-revert)
  tools / skills                 list native tools / built-in skills
  memory ...                     curated long-term memory (add/search/list)
  cron ...                       scheduled jobs (add/list/remove/enable/disable/learn)
  migrate SOURCE DIR             import config/skills/memory from another agent
  bench                          continuous-evolution benchmark
"""

from __future__ import annotations

import contextlib
import platform
import sys
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from chimera import __version__
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.ecosystem import TrajectoryCollector
    from chimera.memory import MemoryManager
    from chimera.pet import Pet, PetStore
    from chimera.scheduler import CronStore


def _force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 so model output never crashes a legacy console.

    Windows terminals default to cp1252, which raises UnicodeEncodeError on
    common model output (em dashes, non-breaking hyphens, emoji). Reconfiguring
    to UTF-8 with a safe error handler keeps the CLI robust everywhere.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):  # detached/odd streams
                reconfigure(encoding="utf-8", errors="backslashreplace")


_force_utf8_streams()

app = typer.Typer(
    name="chimera",
    help="Self-evolving AI agent with an LLM-Fusion reasoning core.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# Module-level so the list-typed default isn't a call-in-default (ruff B008).
_IMAGE_OPTION = typer.Option(
    None, "--image", help="Attach an image (path or URL); repeatable. Needs a vision model."
)


@app.command()
def version() -> None:
    """Show the Chimera version."""
    console.print(f"chimera [bold cyan]{__version__}[/bold cyan]")


@app.command()
def doctor() -> None:
    """Check the environment and configuration."""
    settings = get_settings()
    providers = settings.configured_providers()

    table = Table(title="Chimera doctor", show_header=False, title_style="bold")
    table.add_row("Chimera version", __version__)
    table.add_row("Python", platform.python_version())
    table.add_row("Platform", platform.platform())
    table.add_row("Home (state dir)", str(settings.home))
    table.add_row("Default model", settings.default_model)
    table.add_row(
        "Configured providers",
        ", ".join(providers) if providers else "[yellow]none[/yellow]",
    )
    console.print(table)

    if providers:
        console.print("[green]Ready[/green] — at least one provider key is configured.")
    else:
        console.print(
            Panel.fit(
                "No provider key found. Copy [bold].env.example[/bold] to [bold].env[/bold] "
                "and set at least one key\n(OpenRouter recommended). Then re-run "
                "[bold]chimera doctor[/bold].",
                title="[yellow]Action needed[/yellow]",
            )
        )
        raise typer.Exit(code=1)


@app.command()
def models() -> None:
    """Show the default model and the fusion panel/judge/synthesizer."""
    settings = get_settings()
    table = Table(title="Models", show_header=True, header_style="bold")
    table.add_column("Role")
    table.add_column("Model(s)")
    table.add_row("default (Tier 1)", settings.default_model)
    table.add_row("fusion panel", "\n".join(settings.fusion_panel))
    table.add_row("fusion judge", settings.fusion_judge)
    table.add_row("fusion synthesizer", settings.fusion_synthesizer)
    console.print(table)


@app.command()
def features() -> None:
    """Show optional capabilities and what each needs (a key or a dependency)."""
    from chimera.features import feature_status

    table = Table(title="Optional features", show_header=True, header_style="bold")
    table.add_column("feature")
    table.add_column("status")
    table.add_column("how to enable / use")
    for status in feature_status():
        state = "[green]ready[/green]" if status.ready else f"[yellow]{status.blocker}[/yellow]"
        table.add_row(status.feature.name, state, status.feature.how)
    console.print(table)


@app.command()
def guard(action: str = typer.Argument(..., help="The action/command to evaluate.")) -> None:
    """Show the governance verdict (allow/warn/review/block) for an action."""
    from chimera.governance import TrustKernel

    verdict = TrustKernel().evaluate(action)
    colors = {"allow": "green", "warn": "yellow", "review": "yellow", "block": "red"}
    color = colors[verdict.decision.value]
    detail = f" (rule: {verdict.rule})" if verdict.rule else ""
    console.print(f"[{color}]{verdict.decision.value.upper()}[/{color}] {verdict.reason}{detail}")


@app.command()
def run(
    prompt: str = typer.Argument(..., help="The prompt to send."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    system: str = typer.Option(None, "--system", "-s", help="Optional system prompt."),
    image: list[str] | None = _IMAGE_OPTION,
) -> None:
    """Run a single-shot Tier-1 completion (no fusion). Requires a provider key."""
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.providers.gateway import Message, MessageLike

    try:
        gateway = LLMGateway()
        if image:
            messages: list[MessageLike] = []
            if system:
                messages.append(Message(role="system", content=system))
            messages.append(Message(role="user", content=prompt, images=image))
            answer = gateway.complete(messages, model=model).content
        else:
            answer = gateway.quick(prompt, model=model, system=system)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(answer)


@app.command()
def deliver(
    request: str = typer.Argument(..., help="What to produce (a report, plan, spec, README...)."),
    out: str = typer.Option(None, "--out", "-o", help="Write the deliverable to this file."),
    fmt: str = typer.Option("md", "--format", "-f", help="md | txt | html"),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    fuse: bool = typer.Option(False, "--fuse", help="Use the fusion engine for higher quality."),
) -> None:
    """Deliverable Mode: produce a polished, self-contained artifact. Requires a key."""
    from chimera.deliver import produce_deliverable
    from chimera.providers import LLMGateway, MissingCredentialsError, SupportsComplete

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse:
        from chimera.fusion import FusionEngine

        backend = FusionEngine(gateway)
    try:
        document = produce_deliverable(backend, request, fmt=fmt, model=model)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if out:
        Path(out).write_text(document, encoding="utf-8")
        console.print(f"[green]wrote[/green] {out} [dim]({len(document)} chars)[/dim]")
    else:
        console.print(document)


@app.command()
def agent(
    task: str = typer.Argument(..., help="The task for the agent to accomplish."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(8, "--max-steps", help="Max tool-calling steps."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    guard: bool = typer.Option(False, "--guard", help="Gate tool calls through the governance kernel."),
) -> None:
    """Run the ReAct agent loop with native tools. Requires a provider key."""
    from chimera.core import Agent, AgentConfig
    from chimera.providers import LLMGateway, MissingCredentialsError, SupportsComplete
    from chimera.tools import default_registry

    try:
        gateway = LLMGateway()
        backend: SupportsComplete = gateway
        if fuse:
            from chimera.fusion import FusionEngine, RoutedBackend

            backend = RoutedBackend(gateway, FusionEngine(gateway))
        registry = default_registry(Path(workspace))
        if guard:
            from chimera.governance import AuditLog, TrustKernel, govern_registry

            kernel = TrustKernel(audit=AuditLog(get_settings().home / "audit.jsonl"))
            registry = govern_registry(registry, kernel)
        runner = Agent(backend, registry, AgentConfig(model=model, max_steps=max_steps))
        result = runner.run(task)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(result.answer)
    console.print(
        f"[dim]({result.stopped_reason}, {result.steps} steps, "
        f"{result.tool_calls_made} tool calls)[/dim]"
    )


@app.command()
def chat(
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
) -> None:
    """Interactive multi-turn chat — your terminal right-hand. Requires a key."""
    from chimera.core import Agent, AgentConfig
    from chimera.interface import ChatSession
    from chimera.providers import LLMGateway, MissingCredentialsError, SupportsComplete
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        backend = RoutedBackend(gateway, FusionEngine(gateway))
    agent = Agent(backend, default_registry(Path(workspace)), AgentConfig(model=model, max_steps=max_steps))
    session = ChatSession(agent, memory=None if no_memory else _memory_manager())

    console.print(
        "[bold]Chimera chat[/bold] — your terminal right-hand. "
        "[cyan]/model <slug>[/cyan] to switch, [cyan]/reset[/cyan] to clear, [cyan]/exit[/cyan] to quit."
    )
    while True:
        try:
            message = console.input("[bold green]you ›[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            break
        if not message:
            continue
        if message in ("/exit", "/quit", "/q"):
            console.print("[dim]bye[/dim]")
            break
        if message == "/reset":
            session.reset()
            console.print("[dim]context cleared[/dim]")
            continue
        if message.startswith("/model"):
            slug = message[len("/model") :].strip() or None
            ok = session.set_model(slug)
            console.print(
                f"[dim]model → {slug or 'default'}[/dim]" if ok else "[red]can't switch model[/red]"
            )
            continue
        try:
            with console.status("[dim]thinking…[/dim]"):
                reply = session.send(message)
        except MissingCredentialsError as exc:
            console.print(f"[red]{exc}[/red]")
            break
        except Exception as exc:  # noqa: BLE001 — keep the REPL alive on transient errors
            console.print(f"[red]error: {exc}[/red]")
            continue
        console.print(f"[bold magenta]chimera ›[/bold magenta] {reply}")


@app.command()
def tui(
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
) -> None:
    """Launch the full-screen TUI — your right-hand. Requires a key."""
    from chimera.core import Agent, AgentConfig
    from chimera.interface import ChatSession
    from chimera.providers import LLMGateway, SupportsComplete
    from chimera.tools import default_registry
    from chimera.tui.app import ChimeraTUI

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        backend = RoutedBackend(gateway, FusionEngine(gateway))
    agent = Agent(backend, default_registry(Path(workspace)), AgentConfig(model=model, max_steps=max_steps))
    session = ChatSession(agent, memory=None if no_memory else _memory_manager())
    ChimeraTUI(session, model_label=model or settings.default_model).run()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8765, "--port", help="Bind port."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
) -> None:
    """Run the messaging gateway HTTP server (POST /chat, GET /health). Requires a key."""
    from chimera.core import Agent, AgentConfig
    from chimera.interface import ChatSession
    from chimera.providers import LLMGateway, SupportsComplete
    from chimera.server import MessageGateway, make_server
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    llm = LLMGateway()
    backend: SupportsComplete = llm
    if fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        backend = RoutedBackend(llm, FusionEngine(llm))

    workspace_path = Path(workspace)
    shared_memory = None if no_memory else _memory_manager()

    def factory() -> ChatSession:
        runner = Agent(backend, default_registry(workspace_path), AgentConfig(model=model, max_steps=max_steps))
        return ChatSession(runner, memory=shared_memory)

    server = make_server(MessageGateway(factory), host, port)
    console.print(
        f"[bold]Chimera gateway[/bold] on http://{host}:{port}  "
        "[dim](POST /chat, GET /health). Ctrl+C to stop.[/dim]"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")
    finally:
        server.shutdown()


@app.command()
def fuse(
    prompt: str = typer.Argument(..., help="The prompt to run through the fusion engine."),
    show_panel: bool = typer.Option(False, "--show-panel", help="Show panel answers + judge analysis."),
) -> None:
    """Run a prompt through the LLM-Fusion engine (panel -> judge -> synthesizer)."""
    from chimera.fusion import FusionEngine
    from chimera.providers import LLMGateway, Message, MissingCredentialsError

    try:
        engine = FusionEngine(LLMGateway())
        trace = engine.run([Message(role="user", content=prompt)])
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if show_panel:
        for response in trace.panel:
            body = response.error or response.content
            console.print(Panel(body, title=f"panel: {response.model}", title_align="left"))
        console.print(Panel(trace.judge_analysis, title="judge", title_align="left"))
        console.print(Panel(trace.final, title="[bold green]final[/bold green]", title_align="left"))
    else:
        console.print(trace.final)


@app.command()
def solve(
    task: str = typer.Argument(..., help="The task to solve autonomously."),
    verify: str = typer.Option(None, "--verify", help="Verification command (exit 0 == success)."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_attempts: int = typer.Option(3, "--max-attempts", help="Max verify-or-revert attempts."),
    max_steps: int = typer.Option(8, "--max-steps", help="Max tool-calling steps per attempt."),
    no_plan: bool = typer.Option(False, "--no-plan", help="Skip the planning step."),
    no_manager: bool = typer.Option(False, "--no-manager", help="Skip Manager review."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    guard: bool = typer.Option(False, "--guard", help="Gate tool calls through the governance kernel."),
    collect: bool = typer.Option(
        True, "--collect/--no-collect", help="Record trajectories for opt-in model evolution."
    ),
) -> None:
    """Tier-2: autonomously solve a task with plan + verify-or-revert. Requires a key."""
    from chimera.core import (
        Agent,
        AgentConfig,
        AutonomousAgent,
        AutonomousConfig,
        Manager,
        Planner,
        WorkspaceGuard,
    )
    from chimera.core.verify import CommandVerifier
    from chimera.ecosystem import TrajectoryCollector
    from chimera.evolution import ExperienceBuffer
    from chimera.providers import LLMGateway, MissingCredentialsError, SupportsComplete
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    workspace_path = Path(workspace)
    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    planner_backend: SupportsComplete = gateway
    if fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        engine = FusionEngine(gateway)
        backend = RoutedBackend(gateway, engine)
        # Planning is a deep, tool-free reasoning turn — exactly where fusion pays
        # off — so route the plan through fusion directly (the worker keeps the
        # router: single-model for tool turns, fusion only for tool-free reasoning).
        planner_backend = engine

    registry = default_registry(workspace_path)
    if guard:
        from chimera.governance import AuditLog, TrustKernel, govern_registry

        registry = govern_registry(registry, TrustKernel(audit=AuditLog(settings.home / "audit.jsonl")))
    worker = Agent(backend, registry, AgentConfig(model=model, max_steps=max_steps))
    auto = AutonomousAgent(
        worker,
        planner=None if no_plan else Planner(planner_backend, model),
        manager=None if no_manager else Manager(gateway, model),
        verifier=CommandVerifier(verify, workspace_path) if verify else None,
        guard=WorkspaceGuard(workspace_path),
        experience=ExperienceBuffer(settings.home / "experience.json"),
        trajectories=TrajectoryCollector(settings.home / "trajectories.jsonl") if collect else None,
        spine_workspace=workspace_path,
        config=AutonomousConfig(
            max_attempts=max_attempts, use_planner=not no_plan, use_manager=not no_manager
        ),
    )

    try:
        result = auto.run(task)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(result.answer)
    status = "[green]success[/green]" if result.success else "[red]failed[/red]"
    console.print(f"[dim]{status} after {len(result.attempts)} attempt(s)[/dim]")
    if not result.success:
        raise typer.Exit(code=1)


@app.command()
def tools(workspace: str = typer.Option(".", "--workspace", "-w")) -> None:
    """List the built-in native tools."""
    from chimera.tools import default_registry

    registry = default_registry(Path(workspace))
    table = Table(title="Native tools", show_header=True, header_style="bold")
    table.add_column("Tool")
    table.add_column("Description")
    for tool in registry.tools():
        table.add_row(tool.name, tool.description)
    console.print(table)


@app.command()
def skills() -> None:
    """List the built-in skills."""
    from chimera.skills import default_registry as skills_registry

    registry = skills_registry()
    table = Table(title="Built-in skills", show_header=True, header_style="bold")
    table.add_column("Skill")
    table.add_column("Version")
    table.add_column("Description")
    for skill in registry.skills():
        table.add_row(skill.name, skill.version, skill.description)
    console.print(table)


@app.command()
def migrate(
    source: str = typer.Argument(..., help="Source agent: hermes | openclaw."),
    path: str = typer.Argument(..., help="Path to the source agent's home directory."),
    apply: bool = typer.Option(False, "--apply", help="Write artifacts (default: dry-run preview)."),
    home: str = typer.Option(None, "--home", help="Target Chimera home (default: from config)."),
) -> None:
    """Import config + skills from another agent; --apply also merges long-term memory."""
    from chimera.migration import get_importer

    try:
        importer = get_importer(source, Path(path))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    target = Path(home) if home else get_settings().home
    if apply:
        from chimera.memory import MemoryManager, MemoryStore

        manager = MemoryManager(MemoryStore(target / "memory.json"))
        result = importer.apply(target, memory_manager=manager)
    else:
        result = importer.scan()

    table = Table(title=f"Migration: {source}", show_header=False, title_style="bold")
    table.add_row("Mode", "apply" if apply else "dry-run")
    table.add_row("Default model", result.default_model or "[dim]none[/dim]")
    table.add_row("Skills", ", ".join(result.skills) or "[dim]none[/dim]")
    table.add_row("Memory files", ", ".join(result.memory_files) or "[dim]none[/dim]")
    if result.memory_merged is not None:
        table.add_row("Memory merged", str(result.memory_merged))
    console.print(table)
    for note in result.notes:
        console.print(f"[yellow]note:[/yellow] {note}")
    if not apply:
        console.print("[dim]Re-run with --apply to write the imported artifacts.[/dim]")


# --- cron subcommands ---------------------------------------------------------

cron_app = typer.Typer(help="Manage scheduled jobs (crons and event SOPs).", no_args_is_help=True)
app.add_typer(cron_app, name="cron")


def _cron_store() -> CronStore:
    from chimera.scheduler import CronStore

    path = get_settings().home / "scheduler" / "jobs.json"
    return CronStore(path)


@cron_app.command("list")
def cron_list() -> None:
    """List scheduled jobs."""
    store = _cron_store()
    if len(store) == 0:
        console.print("[dim]no scheduled jobs[/dim]")
        return
    table = Table(title="Scheduled jobs", show_header=True, header_style="bold")
    for col in ("id", "name", "trigger", "schedule", "by", "enabled"):
        table.add_column(col)
    for job in store.list():
        table.add_row(
            job.id, job.name, job.trigger, job.schedule, job.created_by, str(job.enabled)
        )
    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Argument(..., help="A human-readable name."),
    schedule: str = typer.Argument(..., help="Cron expression, or event name with --event."),
    action: str = typer.Argument(..., help="What to do (task description / skill)."),
    event: bool = typer.Option(False, "--event", help="Treat SCHEDULE as an event name."),
) -> None:
    """Add a cron or event-triggered job."""
    import time

    from chimera.scheduler import Scheduler

    sched = Scheduler(_cron_store())
    if event:
        job = sched.schedule_event(name, schedule, action)
    else:
        try:
            job = sched.schedule_cron(name, schedule, action, now=time.time())
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
    console.print(f"[green]added[/green] job {job.id} ({job.name})")


@cron_app.command("remove")
def cron_remove(job_id: str = typer.Argument(..., help="The job id to remove.")) -> None:
    """Remove a scheduled job by id."""
    store = _cron_store()
    if job_id not in store:
        console.print(f"[yellow]no job with id {job_id}[/yellow]")
        raise typer.Exit(code=1)
    store.remove(job_id)
    console.print(f"[green]removed[/green] {job_id}")


@cron_app.command("enable")
def cron_enable(job_id: str = typer.Argument(..., help="The job id to enable.")) -> None:
    """Enable a job (e.g. an agent-proposed one) and schedule its next run."""
    import time

    from chimera.scheduler import Scheduler

    store = _cron_store()
    if job_id not in store:
        console.print(f"[yellow]no job with id {job_id}[/yellow]")
        raise typer.Exit(code=1)
    Scheduler(store).enable(job_id, now=time.time())
    console.print(f"[green]enabled[/green] {job_id}")


@cron_app.command("disable")
def cron_disable(job_id: str = typer.Argument(..., help="The job id to disable.")) -> None:
    """Disable a job without deleting it."""
    from chimera.scheduler import Scheduler

    store = _cron_store()
    if job_id not in store:
        console.print(f"[yellow]no job with id {job_id}[/yellow]")
        raise typer.Exit(code=1)
    Scheduler(store).disable(job_id)
    console.print(f"[green]disabled[/green] {job_id}")


@cron_app.command("learn")
def cron_learn(min_occurrences: int = typer.Option(3, "--min", help="Min repeats to propose.")) -> None:
    """Propose crons from recurring tasks in the experience buffer (disabled, pending approval)."""
    from chimera.evolution import ExperienceBuffer
    from chimera.scheduler import CronLearner, Scheduler

    history = [e.task for e in ExperienceBuffer(get_settings().home / "experience.json").all()]
    proposals = CronLearner(min_occurrences=min_occurrences).analyze(history)
    if not proposals:
        console.print("[dim]no recurring tasks found in history[/dim]")
        return
    jobs = CronLearner(min_occurrences=min_occurrences).register_proposals(
        Scheduler(_cron_store()), proposals
    )
    console.print(f"proposed {len(jobs)} cron(s) (disabled — enable with 'chimera cron enable ID'):")
    for job in jobs:
        console.print(f"  [cyan]{job.id}[/cyan] {job.name} (seen {job.metadata['occurrences']}x)")


# --- memory subcommands -------------------------------------------------------

memory_app = typer.Typer(help="Curated long-term memory.", no_args_is_help=True)
app.add_typer(memory_app, name="memory")


def _memory_manager() -> MemoryManager:
    from chimera.memory import MemoryManager, MemoryStore

    return MemoryManager(MemoryStore(get_settings().home / "memory.json"))


@memory_app.command("add")
def memory_add(
    content: str = typer.Argument(..., help="The fact to remember."),
    key: str = typer.Option(None, "--key", help="Optional dedup key."),
) -> None:
    """Remember a fact (ADD / UPDATE / NOOP, deduped)."""
    op, item = _memory_manager().remember(content, key=key)
    console.print(f"[green]{op}[/green] {item.id}")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Search query."),
    k: int = typer.Option(5, "--k", help="Max results."),
) -> None:
    """Search memory (keyword)."""
    hits = _memory_manager().search(query, k=k)
    if not hits:
        console.print("[dim]no matches[/dim]")
        return
    for item in hits:
        console.print(f"[cyan]{item.id}[/cyan] ({item.source}) {item.content}")


@memory_app.command("list")
def memory_list() -> None:
    """List all memory items."""
    items = _memory_manager().store.all()
    if not items:
        console.print("[dim]memory is empty[/dim]")
        return
    table = Table(title="Memory", show_header=True, header_style="bold")
    for col in ("id", "kind", "source", "content"):
        table.add_column(col)
    for item in items:
        table.add_row(item.id, item.kind, item.source, item.content)
    console.print(table)


evolve_app = typer.Typer(
    help="Opt-in model evolution (curate trajectories -> LoRA/DPO recipe).", no_args_is_help=True
)
app.add_typer(evolve_app, name="evolve")


def _collector(path: str | None) -> TrajectoryCollector:
    from chimera.ecosystem import TrajectoryCollector

    target = Path(path) if path else get_settings().home / "trajectories.jsonl"
    return TrajectoryCollector(target)


@evolve_app.command("status")
def evolve_status(
    traj: str = typer.Option(None, "--traj", help="Trajectory JSONL (default: <home>/trajectories.jsonl)."),
    min_reward: float = typer.Option(0.0, "--min-reward", help="Drop examples below this reward."),
    min_examples: int = typer.Option(30, "--min-examples", help="Examples needed before training is worth it."),
) -> None:
    """Show how much training signal the collected trajectories hold."""
    from chimera.ecosystem import CurationConfig, assess

    readiness = assess(_collector(traj), CurationConfig(min_reward=min_reward), min_examples=min_examples)
    table = Table(title="Model-evolution readiness", show_header=False, title_style="bold")
    table.add_row("trajectories", str(readiness.total))
    table.add_row("successes / failures", f"{readiness.successes} / {readiness.failures}")
    table.add_row("SFT examples", str(readiness.sft_examples))
    table.add_row("DPO pairs", str(readiness.dpo_pairs))
    table.add_row("ready to train", "[green]yes[/green]" if readiness.ready else "[yellow]no[/yellow]")
    console.print(table)
    console.print(f"[dim]{readiness.reason}[/dim]")


@evolve_app.command("export")
def evolve_export(
    out: str = typer.Option(..., "--out", help="Output JSONL path."),
    fmt: str = typer.Option("sft", "--format", help="sft | dpo"),
    traj: str = typer.Option(None, "--traj", help="Trajectory JSONL (default: <home>/trajectories.jsonl)."),
    min_reward: float = typer.Option(0.0, "--min-reward", help="Drop examples below this reward."),
    no_dedup: bool = typer.Option(False, "--no-dedup", help="Keep duplicate examples."),
    min_margin: float = typer.Option(0.0, "--min-margin", help="DPO: min reward margin chosen − rejected."),
) -> None:
    """Export a curated SFT or DPO dataset from trajectories."""
    from chimera.ecosystem import CurationConfig, curate_dpo, curate_sft, write_jsonl

    if fmt not in ("sft", "dpo"):
        console.print("[red]--format must be 'sft' or 'dpo'.[/red]")
        raise typer.Exit(code=1)
    config = CurationConfig(min_reward=min_reward, dedup=not no_dedup, min_margin=min_margin)
    items = _collector(traj).all()
    rows = curate_sft(items, config) if fmt == "sft" else curate_dpo(items, config)
    count = write_jsonl(Path(out), rows)
    console.print(f"[green]wrote {count} {fmt} example(s)[/green] to {out}")


@evolve_app.command("recipe")
def evolve_recipe(
    out: str = typer.Option(..., "--out", help="Directory for the training recipe."),
    fmt: str = typer.Option("sft", "--format", help="sft | dpo"),
    base_model: str = typer.Option("meta-llama/Llama-3.1-8B-Instruct", "--base-model"),
    dataset: str = typer.Option("dataset.jsonl", "--dataset", help="Dataset filename the script reads."),
) -> None:
    """Emit a runnable LoRA training recipe (train.py + README + requirements)."""
    from chimera.ecosystem import write_recipe

    try:
        files = write_recipe(Path(out), base_model=base_model, fmt=fmt, dataset=dataset)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    for path in files:
        console.print(f"[green]wrote[/green] {path}")
    console.print("[dim]Training is external + opt-in: run it on a GPU, review the result before use.[/dim]")


pet_app = typer.Typer(help="Your virtual companion — a chimera that needs care.", no_args_is_help=True)
app.add_typer(pet_app, name="pet")


def _pet_store() -> PetStore:
    from chimera.pet import PetStore

    return PetStore(get_settings().home / "pet.json")


def _render_pet(pet: Pet) -> None:
    from chimera.pet import mood

    table = Table(title=f"🐾 {pet.name} the {pet.species}", show_header=False, title_style="bold")
    table.add_row("mood", mood(pet))
    table.add_row("fullness", f"{pet.fullness:.0f}/100")
    table.add_row("happiness", f"{pet.happiness:.0f}/100")
    table.add_row("energy", f"{pet.energy:.0f}/100")
    console.print(table)


def _pet_interact(action: str | None) -> None:
    from datetime import datetime

    from chimera.pet import apply_decay, feed, play, rest

    moves = {"feed": feed, "play": play, "rest": rest}
    store = _pet_store()
    pet = apply_decay(store.load(), datetime.now(UTC))
    if action:
        moves[action](pet)
    store.save(pet)
    _render_pet(pet)


@pet_app.command("status")
def pet_status() -> None:
    """Check on your companion (stats drift while you're away)."""
    _pet_interact(None)


@pet_app.command("feed")
def pet_feed() -> None:
    """Feed it (raises fullness)."""
    _pet_interact("feed")


@pet_app.command("play")
def pet_play() -> None:
    """Play with it (raises happiness; costs energy + a little fullness)."""
    _pet_interact("play")


@pet_app.command("rest")
def pet_rest() -> None:
    """Let it rest (restores energy)."""
    _pet_interact("rest")


@pet_app.command("new")
def pet_new(
    name: str = typer.Option("Chimi", "--name", help="Companion name."),
    species: str = typer.Option("chimera", "--species", help="Companion species."),
) -> None:
    """Adopt a fresh companion (resets stats)."""
    from chimera.pet import Pet

    store = _pet_store()
    store.save(Pet(name=name, species=species))
    _render_pet(store.load())


@app.command()
def bench(
    limit: int = typer.Option(0, "--limit", help="Limit number of demo tasks (0 = all)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    fuse: bool = typer.Option(False, "--fuse", help="Use the fusion engine as the solver."),
    chain: bool = typer.Option(False, "--chain", help="Run the stateful chained benchmark (error propagation)."),
) -> None:
    """Run the continuous-evolution benchmark on a demo task set. Requires a key."""
    from chimera.eval import (
        SingleModelSolver,
        demo_chain,
        demo_tasks,
        run_chain,
        run_continuous,
    )
    from chimera.providers import LLMGateway, SupportsComplete

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        backend = RoutedBackend(gateway, FusionEngine(gateway))

    solver = SingleModelSolver(backend, model)
    if chain:
        report = run_chain(solver, demo_chain(limit or 8), initial_state="0")
        title = "Chained continuous-evolution benchmark"
    else:
        tasks = demo_tasks()
        if limit > 0:
            tasks = tasks[:limit]
        report = run_continuous(
            solver,
            tasks,
            on_task=lambda o: console.print(
                f"  {'[green]PASS[/green]' if o.passed else '[red]FAIL[/red]'} {o.id}"
            ),
        )
        title = "Continuous-evolution benchmark"

    summary = report.summary()
    table = Table(title=title, show_header=False, title_style="bold")
    for key, value in summary.items():
        table.add_row(key, str(value))
    console.print(table)


@app.command()
def evoclaw(
    length: int = typer.Option(12, "--length", help="Number of chained steps."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    retries: int = typer.Option(2, "--retries", help="Verify-or-revert retries per step (guarded)."),
) -> None:
    """Stress-test continuous-evolution degradation: naive vs guarded. Requires a key."""
    from chimera.eval import SingleModelSolver, compare, counter_chain
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    try:
        comparison = compare(
            lambda: SingleModelSolver(gateway, model),
            counter_chain(length),
            initial_state="0",
            max_retries=retries,
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title="EvoClaw stress test (naive vs guarded)", show_header=True, header_style="bold")
    table.add_column("metric")
    table.add_column("naive", justify="right")
    table.add_column("guarded", justify="right")
    naive, guarded = comparison.naive.summary(), comparison.guarded.summary()
    for key in ("pass_rate", "first_half", "second_half", "degradation", "longest_streak"):
        table.add_row(key, str(naive[key]), str(guarded[key]))
    console.print(table)
    console.print(
        f"[bold]degradation gap:[/bold] {comparison.degradation_gap} "
        "[dim](naive − guarded; >0 means the countermeasures held)[/dim]"
    )


@app.command()
def scenarios(
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
) -> None:
    """Run the daily right-hand scenario suite (live). Requires a key."""
    from chimera.eval import SingleModelSolver, daily_scenarios, run_scenarios
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    try:
        report = run_scenarios(
            SingleModelSolver(gateway, model),
            daily_scenarios(),
            on_result=lambda o: console.print(
                f"  {'[green]PASS[/green]' if o.passed else '[red]FAIL[/red]'} {o.id}"
            ),
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[bold]{report.passed}/{report.total} passed[/bold] "
        f"(pass_rate {report.summary()['pass_rate']})"
    )


@app.command()
def crew(
    task: str = typer.Argument(..., help="The task for the crew."),
    mode: str = typer.Option("sequential", "--mode", help="sequential | supervisor"),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    fuse: bool = typer.Option(False, "--fuse", help="Use the fusion engine as the backend."),
) -> None:
    """Run a multi-agent crew on a task (Tier 3). Requires a provider key."""
    from chimera.orchestration import Role, RoleAgent, SupervisorCrew, demo_crew
    from chimera.providers import LLMGateway, SupportsComplete

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        backend = RoutedBackend(gateway, FusionEngine(gateway))

    if mode == "supervisor":
        supervisor = RoleAgent(
            Role("supervisor", "You coordinate a team and synthesize the single best final answer."),
            backend,
        )
        workers = [
            RoleAgent(Role("analyst", "You analyze the task and surface the key facts and trade-offs."), backend),
            RoleAgent(Role("engineer", "You propose a concrete, practical implementation."), backend),
            RoleAgent(Role("skeptic", "You find flaws, risks and missing cases in the approach."), backend),
        ]
        result = SupervisorCrew(supervisor, workers).run(task)
    else:
        result = demo_crew(backend).run(task)

    console.print(result.answer)
    console.print(f"[dim]({mode} crew, {len(result.transcript)} agent messages)[/dim]")


@app.command()
def meta(
    task: str = typer.Argument(..., help="The task to design a specialized agent for."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
) -> None:
    """Meta-agent: design a specialized agent blueprint for a task. Requires a key."""
    from chimera.ecosystem import MetaAgent
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    if not get_settings().has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    allowed = default_registry().names()
    blueprint = MetaAgent(LLMGateway(), allowed_tools=allowed, model=model).design(task)
    if blueprint is None:
        console.print("[red]the meta-agent could not produce a valid blueprint[/red]")
        raise typer.Exit(code=1)

    table = Table(title="Agent blueprint", show_header=False, title_style="bold")
    table.add_row("Name", blueprint.name)
    table.add_row("Tools", ", ".join(blueprint.tools) or "[dim]none[/dim]")
    table.add_row("Role prompt", blueprint.role_prompt)
    console.print(table)


if __name__ == "__main__":
    app()
