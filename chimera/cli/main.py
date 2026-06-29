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

import platform
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from chimera import __version__
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.memory import MemoryManager
    from chimera.scheduler import CronStore

app = typer.Typer(
    name="chimera",
    help="Self-evolving AI agent with an LLM-Fusion reasoning core.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


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
) -> None:
    """Run a single-shot Tier-1 completion (no fusion). Requires a provider key."""
    from chimera.providers import LLMGateway, MissingCredentialsError

    try:
        gateway = LLMGateway()
        answer = gateway.quick(prompt, model=model, system=system)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(answer)


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
    if fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        backend = RoutedBackend(gateway, FusionEngine(gateway))

    registry = default_registry(workspace_path)
    if guard:
        from chimera.governance import AuditLog, TrustKernel, govern_registry

        registry = govern_registry(registry, TrustKernel(audit=AuditLog(settings.home / "audit.jsonl")))
    worker = Agent(backend, registry, AgentConfig(model=model, max_steps=max_steps))
    auto = AutonomousAgent(
        worker,
        planner=None if no_plan else Planner(gateway, model),
        manager=None if no_manager else Manager(gateway, model),
        verifier=CommandVerifier(verify, workspace_path) if verify else None,
        guard=WorkspaceGuard(workspace_path),
        experience=ExperienceBuffer(settings.home / "experience.json"),
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
    """Import config + skills from another agent. Memory merge arrives in M4."""
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


@app.command()
def bench(
    limit: int = typer.Option(0, "--limit", help="Limit number of demo tasks (0 = all)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    fuse: bool = typer.Option(False, "--fuse", help="Use the fusion engine as the solver."),
) -> None:
    """Run the continuous-evolution benchmark on a demo task set. Requires a key."""
    from chimera.eval import SingleModelSolver, demo_tasks, run_continuous
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

    tasks = demo_tasks()
    if limit > 0:
        tasks = tasks[:limit]

    report = run_continuous(
        SingleModelSolver(backend, model),
        tasks,
        on_task=lambda o: console.print(
            f"  {'[green]PASS[/green]' if o.passed else '[red]FAIL[/red]'} {o.id}"
        ),
    )
    summary = report.summary()
    table = Table(title="Continuous-evolution benchmark", show_header=False, title_style="bold")
    for key, value in summary.items():
        table.add_row(key, str(value))
    console.print(table)


if __name__ == "__main__":
    app()
