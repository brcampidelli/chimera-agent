"""Chimera command-line interface (CLI-first).

Commands:
  version / doctor / models     status & configuration
  run PROMPT                     single-shot Tier-1 completion
  fuse PROMPT                    LLM-Fusion (panel -> judge -> synthesizer)
  agent TASK                     ReAct agent loop with native tools
  solve TASK                     Tier-2 autonomous (plan + verify-or-revert)
  solve-batch TASKS...           Tier-3: solve tasks in parallel, each in its own worktree
  explore QUERY                  locate code via the isolated Context Explorer subagent
  crew-isolated TASK -W ...       tool-using workers split one task in parallel worktrees
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
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from chimera import __version__
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.core import AgentEvent
    from chimera.ecosystem import TrajectoryCollector
    from chimera.evolution import Playbook
    from chimera.kanban import KanbanBoard
    from chimera.memory import EmbedFn, MemoryGraph, MemoryManager
    from chimera.pet import Pet, PetStore
    from chimera.providers import SupportsComplete
    from chimera.scheduler import CronStore
    from chimera.server import MessageGateway


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


def _set_env_var(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file, replacing the line if present, appending otherwise."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _apply_tool_allowlist(
    registry: Any,
    *,
    allow: str | None,
    deny: str | None,
    settings: Settings,
    audit: Any | None = None,
) -> Any:
    """Filter a registry by the per-session tool allowlist (CLI option over env).

    A ``--allow-tools``/``--deny-tools`` CLI value wins over the ``CHIMERA_TOOL_*``
    env lists; an empty allowlist means "no restriction". Returns the registry
    unchanged when nothing is restricted, so the common path stays a no-op.
    """
    from chimera.governance import restrict_registry

    def _csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    allow_names = _csv(allow) if allow is not None else (settings.tool_allowlist or None)
    deny_names = _csv(deny) if deny is not None else list(settings.tool_denylist)
    if allow_names is None and not deny_names:
        return registry
    return restrict_registry(registry, allow=allow_names, deny=deny_names, audit=audit)


def _stream_sink(event: AgentEvent) -> None:
    """Print one live progress event during ``solve --stream`` (dim, one line each)."""
    if event.kind == "final":
        return  # the final answer is printed by the command itself
    icon = {"status": "•", "attempt": "▸"}.get(event.kind, "•")
    if event.kind == "result":
        icon = "✓" if event.data.get("success") else "✗"
    console.print(f"[dim]{icon} {event.text}[/dim]")


# Module-level so the list-typed default isn't a call-in-default (ruff B008).
_IMAGE_OPTION = typer.Option(
    None, "--image", help="Attach an image (path or URL); repeatable. Needs a vision model."
)
_BATCH_TASKS_ARG = typer.Argument(..., help="Tasks to solve in parallel, each isolated.")
_CREW_WORKER_OPT = typer.Option(
    None, "--worker", "-W", help="A worker as 'name:instruction'; repeatable. Each edits in its own worktree."
)


@app.command()
def version() -> None:
    """Show the Chimera version."""
    console.print(f"chimera [bold cyan]{__version__}[/bold cyan]")


@app.command()
def init(
    openrouter_key: str = typer.Option(
        None, "--openrouter-key", help="Your OpenRouter API key (sk-or-...); one key = 100+ models."
    ),
    model: str = typer.Option(None, "--model", help="Default model slug to set (optional)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non-interactive: never prompt."),
    home: str = typer.Option(None, "--home", help="Project dir for the .env (default: cwd)."),
) -> None:
    """First-run setup: create .env, set a provider key, and point you at a real example."""
    import os

    root = Path(home) if home else Path.cwd()
    env_path = root / ".env"
    example = root / ".env.example"

    # 1. Ensure a .env exists — copy the example, never clobber an existing one.
    if env_path.exists():
        console.print(f".env already exists at [bold]{env_path}[/bold] — leaving it in place.")
    elif example.exists():
        env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        console.print(f"[green]Created[/green] {env_path} from .env.example")
    else:
        env_path.write_text("# Chimera configuration\n", encoding="utf-8")
        console.print(f"[green]Created[/green] {env_path}")

    # 2. Provider key (flag wins; prompt only when interactive).
    key = (openrouter_key or "").strip()
    if not key and not yes:
        console.print("Get a key at [bold]https://openrouter.ai/keys[/bold] (a free tier exists).")
        key = typer.prompt(
            "Paste your OpenRouter API key (leave blank to skip)", default="", show_default=False
        ).strip()
    if key:
        _set_env_var(env_path, "OPENROUTER_API_KEY", key)
        os.environ["OPENROUTER_API_KEY"] = key  # so the check below sees it immediately
        console.print("[green]Set[/green] OPENROUTER_API_KEY in .env")
    if model:
        _set_env_var(env_path, "CHIMERA_DEFAULT_MODEL", model)
        os.environ["CHIMERA_DEFAULT_MODEL"] = model
        console.print(f"[green]Set[/green] CHIMERA_DEFAULT_MODEL={model}")

    # 3. Verify + point at something real.
    get_settings.cache_clear()
    providers = get_settings().configured_providers()
    if providers:
        console.print(f"[green]Ready[/green] — providers configured: {', '.join(providers)}")
        console.print(
            Panel.fit(
                "Try it now:\n"
                "  [bold]chimera run[/bold] \"Explain what you can do in 3 bullets\"\n"
                "  [bold]chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace[/bold]"
                "   (real: inbox → digest)\n"
                "  [bold]chimera redteam[/bold]   (see the injection defenses' measured coverage)",
                title="[green]You're set up[/green]",
            )
        )
    else:
        console.print(
            Panel.fit(
                f"No provider key yet. Add one to [bold]{env_path}[/bold] "
                "(OPENROUTER_API_KEY=...) and run [bold]chimera doctor[/bold].",
                title="[yellow]One more step[/yellow]",
            )
        )


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
    from chimera.providers import LLMGateway, MissingCredentialsError

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
    allow_tools: str = typer.Option(
        None, "--allow-tools", help="Per-session allowlist: only these tools (comma-separated)."
    ),
    deny_tools: str = typer.Option(
        None, "--deny-tools", help="Per-session denylist: drop these tools (comma-separated)."
    ),
) -> None:
    """Run the ReAct agent loop with native tools. Requires a provider key."""
    from chimera.core import Agent, AgentConfig
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.tools import default_registry

    try:
        gateway = LLMGateway()
        backend: SupportsComplete = gateway
        if fuse:
            from chimera.fusion import FusionEngine, RoutedBackend

            backend = RoutedBackend(gateway, FusionEngine(gateway))
        registry = default_registry(Path(workspace))
        registry = _apply_tool_allowlist(
            registry, allow=allow_tools, deny=deny_tools, settings=get_settings()
        )
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
    from chimera.providers import LLMGateway, MissingCredentialsError
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
    mem = None if no_memory else _memory_manager()
    session = ChatSession(
        agent, memory=mem, graph=_recall_graph(mem), profile=mem.profile() if mem is not None else ""
    )
    skill_names = _learned_skill_labels(settings)

    console.print(
        "[bold]Chimera chat[/bold] — your terminal right-hand. "
        "[cyan]/model <slug>[/cyan] to switch, [cyan]/reset[/cyan] to clear, [cyan]/exit[/cyan] to quit."
    )
    nudged: set[str] = set()  # preferences already suggested this session
    skill_nudged: set[str] = set()  # recurring tasks already suggested as skills
    while True:
        try:
            message = console.input("[bold green]you ›[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings)
            break
        if not message:
            continue
        if message in ("/exit", "/quit", "/q"):
            console.print("[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings)
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
        if mem is not None:
            recent = [turn.user for turn in session.turns[-4:]]
            for fact in mem.nudges(recent):
                if fact not in nudged:
                    nudged.add(fact)
                    console.print(
                        f"[dim]💡 remember this? [/dim][yellow]{fact}[/yellow]"
                        f"[dim] → memory add --persona \"{fact}\"[/dim]"
                    )
        _emit_skill_nudges(session, skill_names, skill_nudged)


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
    from chimera.providers import LLMGateway
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
    mem = None if no_memory else _memory_manager()
    session = ChatSession(
        agent, memory=mem, graph=_recall_graph(mem), profile=mem.profile() if mem is not None else ""
    )
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
    discord: bool = typer.Option(False, "--discord", help="Serve on Discord (needs CHIMERA_DISCORD_BOT_TOKEN + the 'messaging' extra)."),
    telegram: bool = typer.Option(False, "--telegram", help="Serve on Telegram (needs CHIMERA_TELEGRAM_BOT_TOKEN)."),
    slack: bool = typer.Option(False, "--slack", help="Serve on Slack (needs CHIMERA_SLACK_BOT_TOKEN + CHIMERA_SLACK_APP_TOKEN + the 'messaging' extra)."),
    signal: bool = typer.Option(False, "--signal", help="Serve on Signal via a signal-cli-rest-api bridge (CHIMERA_SIGNAL_API_URL + CHIMERA_SIGNAL_NUMBER)."),
    cron: bool = typer.Option(False, "--cron", help="Also run the cron daemon: fire scheduled jobs on the real clock (proactivity)."),
    cron_tick: int = typer.Option(30, "--cron-tick", help="Seconds between cron scheduler ticks."),
    mcp: bool = typer.Option(False, "--mcp", help="Serve Chimera AS an MCP server over stdio (solve/fuse/memory as tools)."),
    a2a: bool = typer.Option(False, "--a2a", help="Also expose an A2A endpoint on HTTP (agent card + task lifecycle)."),
) -> None:
    """Run the messaging gateway on HTTP, Discord, Telegram, Slack or Signal. Requires a key.

    Add ``--cron`` to also fire scheduled jobs on a real clock — turning the reactive gateway
    into an agent that acts on time (the daemon that makes proactivity real). Pass ``--mcp`` to
    instead expose Chimera *as* an MCP server on stdio, so any MCP client (Claude Desktop, an
    IDE, another agent) can call ``chimera_solve`` / ``chimera_fuse`` / ``chimera_memory_search``.
    """
    from chimera.core import Agent, AgentConfig
    from chimera.interface import ChatSession
    from chimera.providers import LLMGateway
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

    if mcp:
        _serve_mcp(backend, llm, model, max_steps, workspace_path, recall=not no_memory)
        return
    cron_stop = _start_cron_daemon(backend, model, max_steps, workspace_path, cron_tick) if cron else None
    shared_memory = None if no_memory else _memory_manager()
    shared_graph = _recall_graph(shared_memory)
    shared_profile = shared_memory.profile() if shared_memory is not None else ""

    platform = (
        "discord" if discord
        else "telegram" if telegram
        else "slack" if slack
        else "signal" if signal
        else None
    )
    if platform is not None:
        adapter = _messaging_adapter(settings, platform)
        _serve_platform(adapter, settings, backend, model, max_steps, workspace_path, shared_memory, shared_graph)
        return

    from chimera.integrations import SendMessageTool

    push_senders = _sender_registry(settings)  # e.g. WhatsApp send, available over HTTP too
    http_send_tool = SendMessageTool(push_senders) if push_senders.platforms() else None

    def factory() -> ChatSession:
        registry = default_registry(workspace_path)
        if http_send_tool is not None:
            registry.register(http_send_tool)
        runner = Agent(backend, registry, AgentConfig(model=model, max_steps=max_steps))
        return ChatSession(runner, memory=shared_memory, graph=shared_graph, profile=shared_profile)

    message_gateway = MessageGateway(factory)
    a2a_pair = _build_a2a(backend, model, max_steps, workspace_path, host, port) if a2a else None
    server = make_server(
        message_gateway, host, port,
        webhooks=_webhook_handler(message_gateway),
        whatsapp=_whatsapp_webhook(settings, message_gateway),
        a2a=a2a_pair,
    )
    a2a_note = "  [dim]· A2A: GET /.well-known/agent.json, POST /a2a[/dim]" if a2a else ""
    console.print(
        f"[bold]Chimera gateway[/bold] on http://{host}:{port}  "
        f"[dim](POST /chat, POST /webhook/<hook>, GET /health). Ctrl+C to stop.[/dim]{a2a_note}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")
    finally:
        server.shutdown()
        if cron_stop is not None:
            cron_stop.set()


def _start_cron_daemon(
    backend: SupportsComplete, model: str | None, max_steps: int, workspace: Path, tick: int
) -> Any:
    """Start the cron daemon in a background thread; return its stop event."""
    import json
    import time

    from chimera.core import Agent, AgentConfig
    from chimera.scheduler import CronDaemon, CronJob, Scheduler, make_agent_dispatch
    from chimera.tools import default_registry

    scheduler = Scheduler(_cron_store())

    def run_task(task: str) -> str:
        agent = Agent(backend, default_registry(workspace), AgentConfig(model=model, max_steps=max_steps))
        return agent.run(task).answer

    results_path = get_settings().home / "scheduler" / "cron_results.jsonl"

    def deliver(job: CronJob, answer: str) -> None:
        """Durable sink: append every cron result so nothing is silently lost."""
        results_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "at": time.time(),
            "id": job.id,
            "name": job.name,
            "action": job.action,
            "deliver_to": job.deliver_to,
            "answer": answer,
        }
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    daemon = CronDaemon(scheduler, make_agent_dispatch(run_task, deliver), tick_seconds=tick)
    _thread, stop = daemon.start()
    jobs = len(scheduler.store.list())
    console.print(f"[dim]cron daemon on (tick {tick}s, {jobs} job(s) scheduled)[/dim]")
    return stop


def _messaging_adapter(settings: Settings, platform: str) -> Any:
    """Build the requested platform adapter (Discord/Telegram/Slack) or exit with guidance."""
    if platform == "discord":
        if not settings.discord_bot_token:
            console.print("[red]Set CHIMERA_DISCORD_BOT_TOKEN to run the Discord adapter.[/red]")
            raise typer.Exit(code=1)
        from chimera.server import DiscordAdapter

        return DiscordAdapter(settings.discord_bot_token)
    if platform == "telegram":
        if not settings.telegram_bot_token:
            console.print("[red]Set CHIMERA_TELEGRAM_BOT_TOKEN to run the Telegram adapter.[/red]")
            raise typer.Exit(code=1)
        from chimera.server import TelegramAdapter

        return TelegramAdapter(settings.telegram_bot_token)
    if platform == "slack":
        if not (settings.slack_bot_token and settings.slack_app_token):
            console.print("[red]Set CHIMERA_SLACK_BOT_TOKEN and CHIMERA_SLACK_APP_TOKEN to run the Slack adapter.[/red]")
            raise typer.Exit(code=1)
        from chimera.server import SlackAdapter

        return SlackAdapter(settings.slack_bot_token, settings.slack_app_token)
    if not (settings.signal_api_url and settings.signal_number):
        console.print("[red]Set CHIMERA_SIGNAL_API_URL and CHIMERA_SIGNAL_NUMBER (run a signal-cli-rest-api bridge).[/red]")
        raise typer.Exit(code=1)
    from chimera.server import SignalAdapter

    return SignalAdapter(settings.signal_api_url, settings.signal_number)


def _sender_registry(settings: Settings, primary: Any = None) -> Any:
    """A SenderRegistry with the primary adapter (if any) plus configured push senders (WhatsApp)."""
    from chimera.integrations import SenderRegistry

    registry = SenderRegistry()
    if primary is not None:
        registry.register(primary)
    if settings.whatsapp_access_token and settings.whatsapp_phone_number_id:
        from chimera.server import WhatsAppSender

        registry.register(WhatsAppSender(settings.whatsapp_access_token, settings.whatsapp_phone_number_id))
    return registry


def _serve_mcp(
    backend: SupportsComplete,
    gateway: Any,  # LLMGateway (for the fusion engine)
    model: str | None,
    max_steps: int,
    workspace_path: Path,
    *,
    recall: bool,
) -> None:
    """Expose Chimera as an MCP server on stdio: solve/fuse/memory-search become MCP tools.

    stdio is the MCP wire, so nothing here may write to stdout — the notice goes to stderr.
    """
    import sys

    from chimera.core import Agent, AgentConfig, AutonomousAgent, AutonomousConfig
    from chimera.fusion import FusionEngine
    from chimera.providers import Message
    from chimera.server import ChimeraMCP
    from chimera.tools import default_registry

    def _solve(task: str) -> str:
        registry = default_registry(workspace_path)
        worker = Agent(backend, registry, AgentConfig(model=model, max_steps=max_steps))
        auto = AutonomousAgent(
            worker,
            memory=_memory_manager() if recall else None,
            config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
        )
        return auto.run(task).answer or "(no answer)"

    def _fuse(prompt: str) -> str:
        return FusionEngine(gateway).run([Message(role="user", content=prompt)]).final

    def _search(query: str, k: int) -> list[str]:
        return [item.content for item in _memory_manager().search(query, k=k)]

    bridge = ChimeraMCP(solve=_solve, fuse=_fuse, memory_search=_search)
    print(
        "chimera MCP server on stdio — tools: chimera_solve, chimera_fuse, chimera_memory_search",
        file=sys.stderr,
    )
    try:
        bridge.serve_stdio()
    except ModuleNotFoundError as exc:
        print(f"MCP SDK missing — install with: pip install 'chimera-agent[mcp]' ({exc})", file=sys.stderr)
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)


def _build_a2a(
    backend: SupportsComplete,
    model: str | None,
    max_steps: int,
    workspace_path: Path,
    host: str,
    port: int,
) -> tuple[Any, dict[str, Any]]:
    """Build the (A2AServer, agent_card) pair whose ``solve`` runs the autonomous agent."""
    from chimera.core import Agent, AgentConfig, AutonomousAgent, AutonomousConfig
    from chimera.integrations import A2AServer, chimera_agent_card
    from chimera.tools import default_registry

    def _solve(task: str) -> str:
        registry = default_registry(workspace_path)
        worker = Agent(backend, registry, AgentConfig(model=model, max_steps=max_steps))
        auto = AutonomousAgent(
            worker, config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False)
        )
        return auto.run(task).answer or "(no answer)"

    url = f"http://{host}:{port}/a2a"
    return A2AServer(_solve), chimera_agent_card(url, version=__version__)


@app.command("a2a-card")
def a2a_card(
    url: str = typer.Option("http://127.0.0.1:8765/a2a", "--url", help="The A2A endpoint URL to advertise."),
) -> None:
    """Print Chimera's A2A Agent Card JSON (serve it at /.well-known/agent.json)."""
    import json as _json

    from chimera.integrations import chimera_agent_card

    console.print_json(_json.dumps(chimera_agent_card(url, version=__version__)))


def _serve_platform(
    adapter: Any,  # an Adapter that is also a MessageSender (Discord/Telegram/Slack)
    settings: Settings,
    backend: SupportsComplete,
    model: str | None,
    max_steps: int,
    workspace_path: Path,
    memory: MemoryManager | None,
    graph: MemoryGraph | None,
) -> None:
    """Serve the gateway over a platform adapter: one session per chat; the agent can send."""
    from chimera.core import Agent, AgentConfig
    from chimera.integrations import SendMessageTool
    from chimera.interface import ChatSession
    from chimera.server import MessageGateway
    from chimera.tools import default_registry

    senders = _sender_registry(settings, adapter)  # this platform + any configured push senders
    send_tool = SendMessageTool(senders)

    def factory() -> ChatSession:
        registry = default_registry(workspace_path)
        registry.register(send_tool)
        runner = Agent(backend, registry, AgentConfig(model=model, max_steps=max_steps))
        return ChatSession(runner, memory=memory, graph=graph)

    gateway = MessageGateway(factory)
    console.print(
        f"[bold]Chimera on {adapter.platform}[/bold] "
        "[dim]— message the bot; each chat is its own session. Ctrl+C to stop.[/dim]"
    )
    try:
        adapter.start(gateway.on_message)  # blocking until interrupted
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")
    except ImportError:
        console.print("[red]This platform needs an extra dependency: uv sync --extra messaging[/red]")
        raise typer.Exit(code=1) from None
    finally:
        adapter.stop()


def _whatsapp_webhook(settings: Settings, gateway: MessageGateway) -> Any:
    """A WhatsAppWebhook (Meta verification + inbound routing) when configured, else None."""
    if not (
        settings.whatsapp_access_token
        and settings.whatsapp_phone_number_id
        and settings.whatsapp_verify_token
    ):
        return None
    from chimera.server import WhatsAppSender, WhatsAppWebhook

    sender = WhatsAppSender(settings.whatsapp_access_token, settings.whatsapp_phone_number_id)
    return WhatsAppWebhook(sender, settings.whatsapp_verify_token, gateway.on_message)


def _webhook_handler(gateway: MessageGateway) -> Any:
    """Fire scheduler webhook jobs (added via `cron add --webhook`) through the gateway."""
    import json as _json
    import time

    from chimera.scheduler import Scheduler
    from chimera.server import InboundMessage

    scheduler = Scheduler(_cron_store())

    def webhooks(hook: str, payload: dict[str, Any]) -> list[str]:
        results: list[str] = []

        def dispatch(job: Any) -> None:
            prompt = job.action
            if payload:
                prompt = f"{prompt}\n\nWebhook payload:\n{_json.dumps(payload)}"
            results.append(
                gateway.on_message(
                    InboundMessage(text=prompt, chat_id=f"webhook:{hook}", platform="webhook")
                )
            )

        scheduler.fire_webhook(hook, time.time(), dispatch)
        return results

    return webhooks


@app.command()
def fuse(
    prompt: str = typer.Argument(..., help="The prompt to run through the fusion engine."),
    show_panel: bool = typer.Option(False, "--show-panel", help="Show panel answers + judge analysis."),
    selective: bool | None = typer.Option(
        None, "--selective/--full", help="Override selective fusion (default: from settings)."
    ),
    best_of: int = typer.Option(
        1, "--best-of", help="Cheap fusion: sample ONE model N times and take the consensus (self-consistency), instead of a multi-model panel."
    ),
    verify_select: bool = typer.Option(
        False, "--verify-select", help="With --best-of: pick the best sample by a verifier score instead of majority vote (Weaver-lite)."
    ),
    model: str = typer.Option(None, "--model", "-m", help="Model for --best-of self-consistency."),
    show_cost: bool = typer.Option(
        False, "--show-cost", help="Print the itemized receipt: per-advisor cost at each model's rate."
    ),
    receipt: str = typer.Option(
        None, "--receipt", help="Append the run's cost receipt to this JSONL (for cost×quality analysis)."
    ),
) -> None:
    """Run a prompt through the LLM-Fusion engine (panel -> judge -> synthesizer)."""
    from chimera.fusion import FusionConfig, FusionEngine
    from chimera.providers import LLMGateway, Message, MissingCredentialsError

    # --best-of N: self-consistency over a single model — cheaper than the full panel when you
    # just want to stabilize one (weak) model rather than combine several.
    if best_of >= 2:
        from chimera.fusion import SelfConsistency, VerifierSelector, llm_scorer

        try:
            gw = LLMGateway()
            selector = VerifierSelector([llm_scorer(gw, model)]) if verify_select else None
            sc = SelfConsistency(gw, n=best_of, model=model, selector=selector)
            result = sc.complete([Message(role="user", content=prompt)])
        except MissingCredentialsError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        console.print(result.content)
        sc_total = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if sc_total:
            console.print(f"[dim]self-consistency over {best_of} samples · total tokens: {sc_total}[/dim]")
        return

    config = FusionConfig.from_settings()
    if selective is not None:
        config.mode = "selective" if selective else "full"

    try:
        engine = FusionEngine(LLMGateway(), config)
        trace = engine.run([Message(role="user", content=prompt)])
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    total = trace.total_tokens()
    if show_panel:
        for response in trace.panel:
            body = response.error or response.content
            console.print(Panel(body, title=f"panel: {response.model}", title_align="left"))
        if trace.early_stopped:
            console.print("[dim]probe models agreed — skipped the rest of the panel + judge[/dim]")
        else:
            console.print(Panel(trace.judge_analysis, title="judge", title_align="left"))
        console.print(Panel(trace.final, title="[bold green]final[/bold green]", title_align="left"))
        if total is not None:
            by = trace.by_stage()
            rows = "  ".join(
                f"{stage}={by[stage][0]}/{by[stage][1]}"
                for stage in ("panel", "judge", "synth")
                if stage in by
            )
            console.print(f"[dim]tokens in/out — {rows}  ·  total {total}[/dim]")
    else:
        console.print(trace.final)
        if total is not None:
            note = " (early-stopped)" if trace.early_stopped else ""
            console.print(f"[dim]fusion total tokens: {total}{note}[/dim]")

    # M15-B3 "receipts": price each stage at its own model rate, show and/or persist it.
    if show_cost or receipt:
        from chimera.fusion import append_receipt, receipt_from_trace

        rcpt = receipt_from_trace(trace)
        if show_cost:
            for stage in rcpt.stages:
                usd = f"${stage.usd:.6f}" if stage.usd is not None else "unknown"
                console.print(
                    f"[dim]{stage.stage:<6} {stage.model:<34} "
                    f"{(stage.prompt_tokens or 0)}/{(stage.completion_tokens or 0)} tok  {usd}[/dim]"
                )
            total_usd = f"${rcpt.total_usd:.6f}" if rcpt.total_usd is not None else "unknown (some model unpriced)"
            console.print(f"[bold]receipt total: {total_usd}[/bold]  [dim]({rcpt.total_tokens} tokens)[/dim]")
        if receipt:
            append_receipt(Path(receipt), rcpt)
            console.print(f"[green]receipt appended[/green] {receipt}")


@app.command(name="fusion-receipts")
def fusion_receipts(
    path: str = typer.Argument(..., help="JSONL of receipts written by `fuse --receipt`."),
) -> None:
    """Summarize persisted fusion receipts into an honest cost×quality curve."""
    from chimera.fusion import format_summary, load_receipts, summarize

    receipts = load_receipts(Path(path))
    if not receipts:
        console.print(f"[yellow]no receipts in {path}[/yellow]")
        return
    console.print(format_summary(summarize(receipts)))


@app.command(name="fusion-bench")
def fusion_bench(
    tasks: str = typer.Option("hard", "--tasks", help="Task suite: hard | demo."),
) -> None:
    """A/B the fusion engine: full vs selective (tokens + accuracy). Calls real models."""
    from chimera.eval.continuous import demo_tasks
    from chimera.eval.fusion_ab import run_fusion_ab
    from chimera.eval.hard import hard_tasks
    from chimera.providers import LLMGateway, MissingCredentialsError

    suite = hard_tasks() if tasks == "hard" else demo_tasks()
    try:
        report = run_fusion_ab(LLMGateway(), suite)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"fusion A/B — {tasks} ({len(report.rows)} tasks)")
    table.add_column("task")
    table.add_column("full", justify="center")
    table.add_column("selective", justify="center")
    table.add_column("early-stop", justify="center")
    table.add_column("full tok", justify="right")
    table.add_column("sel tok", justify="right")
    for row in report.rows:
        table.add_row(
            row.task_id,
            "[green]ok[/green]" if row.full_ok else "[red]x[/red]",
            "[green]ok[/green]" if row.selective_ok else "[red]x[/red]",
            "yes" if row.early_stopped else "",
            str(row.full_tokens if row.full_tokens is not None else "-"),
            str(row.selective_tokens if row.selective_tokens is not None else "-"),
        )
    console.print(table)
    summary = report.summary()
    for key, value in summary.items():
        console.print(f"[dim]{key}[/dim]: {value}")
    delta = summary.get("accuracy_delta_pp", 0.0)
    verdict = "PASS" if delta >= -1.0 else "REGRESSION"
    color = "green" if verdict == "PASS" else "red"
    console.print(
        f"\nverdict: [{color}]{verdict}[/{color}] "
        f"(selective accuracy within 1pp of full: {delta:+.1f}pp)"
    )


@app.command(name="skillcard-bench")
def skillcard_bench(
    tasks: str = typer.Option("hard", "--tasks", help="Task suite: hard | demo."),
    k: int = typer.Option(3, "--k", help="How many cards to retrieve per task."),
    use_store: bool = typer.Option(
        False, "--use-store", help="Bench your own learned cards (skills.json) instead of the demo set."
    ),
) -> None:
    """A/B reasoning with vs without injected TRS skill cards. Calls real models."""
    from chimera.eval.continuous import demo_tasks
    from chimera.eval.hard import hard_tasks
    from chimera.eval.skillcard_ab import demo_cards, run_skillcard_ab
    from chimera.evolution import SkillStore
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    suite = hard_tasks() if tasks == "hard" else demo_tasks()
    if use_store:
        cards = [c for c in SkillStore(settings.home / "skills.json").skills() if c.has_card()]
        if not cards:
            console.print("[red]No cards with content in skills.json — run some solves first, "
                          "or drop --use-store to bench the demo set.[/red]")
            raise typer.Exit(code=1)
    else:
        cards = demo_cards()

    try:
        report = run_skillcard_ab(LLMGateway(), suite, cards, k=k)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"skill-card A/B — {tasks} ({len(report.rows)} tasks, {len(cards)} cards)")
    table.add_column("task")
    table.add_column("no cards", justify="center")
    table.add_column("with cards", justify="center")
    table.add_column("hit", justify="center")
    table.add_column("base tok", justify="right")
    table.add_column("card tok", justify="right")
    for row in report.rows:
        table.add_row(
            row.task_id,
            "[green]ok[/green]" if row.base_ok else "[red]x[/red]",
            "[green]ok[/green]" if row.card_ok else "[red]x[/red]",
            "yes" if row.hit else "",
            str(row.base_tokens if row.base_tokens is not None else "-"),
            str(row.card_tokens if row.card_tokens is not None else "-"),
        )
    console.print(table)
    summary = report.summary()
    for key, value in summary.items():
        console.print(f"[dim]{key}[/dim]: {value}")
    delta = summary.get("accuracy_delta_pp", 0.0)
    verdict = "PASS" if delta >= -1.0 else "REGRESSION"
    color = "green" if verdict == "PASS" else "red"
    console.print(
        f"\nverdict: [{color}]{verdict}[/{color}] "
        f"(card accuracy within 1pp of no-cards: {delta:+.1f}pp; "
        f"token delta {summary.get('token_delta_pct', 0.0):+.1f}%)"
    )


@app.command(name="schema-bench")
def schema_bench(
    openapi: str = typer.Option(
        None, "--openapi", help="Path or URL to an OpenAPI spec to include (its tools are verbose)."
    ),
    demo: bool = typer.Option(
        False, "--demo", help="Include a couple of synthetic verbose tools to show the effect."
    ),
    model: str = typer.Option(None, "--model", "-m", help="Tokenizer model (default: your default)."),
) -> None:
    """Measure tool-schema token cost, full vs compacted (advertise-time). No model calls."""
    from chimera.eval.schema_ab import demo_bloated_schemas, run_schema_ab
    from chimera.integrations.openapi import tools_from_openapi
    from chimera.tools import default_registry

    settings = get_settings()
    schemas: list[dict[str, Any]] = default_registry(Path(".")).to_openai_schema()
    if demo:
        schemas += demo_bloated_schemas()
    if openapi:
        import json

        if openapi.startswith(("http://", "https://")):
            import httpx

            text = httpx.get(openapi, timeout=30.0).text
        else:
            text = Path(openapi).read_text(encoding="utf-8")
        try:
            spec = json.loads(text)
        except json.JSONDecodeError:
            import yaml

            spec = yaml.safe_load(text)
        schemas += [tool.to_openai_schema() for tool in tools_from_openapi(spec)]

    report = run_schema_ab(schemas, model=model or settings.default_model)
    table = Table(title=f"tool-schema compaction ({len(report.rows)} tools)")
    table.add_column("tool")
    table.add_column("full tok", justify="right")
    table.add_column("compact tok", justify="right")
    table.add_column("saved", justify="right")
    for row in report.rows:
        saved = row.full_tokens - row.compact_tokens
        pct = f"{saved / row.full_tokens * 100:.0f}%" if row.full_tokens else "0%"
        table.add_row(row.tool, str(row.full_tokens), str(row.compact_tokens), f"{saved} ({pct})")
    console.print(table)
    s = report.summary()
    console.print(
        f"[dim]full {int(s['full_tokens'])} tok → compact {int(s['compact_tokens'])} tok · "
        f"reduction {s['reduction_pct']}% across {int(s['tools'])} tools[/dim]"
    )


@app.command(name="sandbox-bench")
def sandbox_bench(
    workspace: str = typer.Option(".sandbox-bench", "--workspace", "-w", help="Dir to run sandboxed tasks in."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(8, "--max-steps", help="Max tool-calling steps per task."),
) -> None:
    """State-based bench: grade the final workspace state + count harmful side effects.

    Unlike the text benches, this measures what the agent DID (files it changed), and flags
    mutations outside each task's allowed set. Uses real models + file tools.
    """
    from chimera.core import Agent, AgentConfig
    from chimera.eval.sandbox import StatefulRunner, demo_stateful_tasks, run_stateful
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    gateway = LLMGateway()

    def factory(ws: Path) -> StatefulRunner:
        return Agent(gateway, default_registry(ws), AgentConfig(model=model, max_steps=max_steps))

    report = run_stateful(factory, demo_stateful_tasks(), Path(workspace))
    table = Table(title="sandbox bench (final-state grading + side effects)")
    table.add_column("task")
    table.add_column("goal", justify="center")
    table.add_column("harmful side effects")
    for outcome in report.outcomes:
        table.add_row(
            outcome.id,
            "[green]met[/green]" if outcome.passed else "[red]missed[/red]",
            ", ".join(outcome.side_effects) if outcome.side_effects else "[dim]none[/dim]",
        )
    console.print(table)
    summary = report.summary()
    console.print(
        f"[dim]pass rate {summary['pass_rate']} · side-effect rate "
        f"{summary['side_effect_rate']} across {int(summary['tasks'])} tasks[/dim]"
    )


@app.command()
def solve(
    task: str = typer.Argument(None, help="The task to solve autonomously (omit with --approve/--deny)."),
    verify: str = typer.Option(None, "--verify", help="Verification command (exit 0 == success)."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_attempts: int = typer.Option(3, "--max-attempts", help="Max verify-or-revert attempts."),
    max_steps: int = typer.Option(8, "--max-steps", help="Max tool-calling steps per attempt."),
    no_plan: bool = typer.Option(False, "--no-plan", help="Skip the planning step."),
    no_manager: bool = typer.Option(False, "--no-manager", help="Skip Manager review."),
    rubric: bool = typer.Option(False, "--rubric", help="Manager reviews via the cascade rubric."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    guard: bool = typer.Option(False, "--guard", help="Gate tool calls through the governance kernel."),
    allow_tools: str = typer.Option(
        None, "--allow-tools", help="Per-session allowlist: only these tools (comma-separated)."
    ),
    deny_tools: str = typer.Option(
        None, "--deny-tools", help="Per-session denylist: drop these tools (comma-separated)."
    ),
    taint: bool = typer.Option(
        False, "--taint", help="Track a capability ledger + review execution of tainted input."
    ),
    collect: bool = typer.Option(
        True, "--collect/--no-collect", help="Record trajectories for opt-in model evolution."
    ),
    no_remember: bool = typer.Option(
        False, "--no-remember", help="Don't auto-write a long-term memory fact on success."
    ),
    no_evolve_skills: bool = typer.Option(
        False, "--no-evolve-skills", help="Don't auto-propose a learned skill when a task recurs."
    ),
    isolate: bool = typer.Option(
        False, "--isolate", help="Run in an isolated git worktree; changes copied back only on success."
    ),
    explorer: bool = typer.Option(
        False, "--explorer", help="Give the agent an isolated Context Explorer for repo search (FastContext-style)."
    ),
    subagents: bool = typer.Option(
        False, "--subagents", help="Give the agent spawn_subagent to delegate subtasks to isolated subagents."
    ),
    repo_map: bool = typer.Option(
        False, "--repo-map", help="Prepend a structural map of the workspace (files + top-level symbols) to the agent's context."
    ),
    progress_ledger: bool = typer.Option(
        False, "--progress-ledger", help="After a failed attempt, run a structured self-check that steers the retry (helps weak models)."
    ),
    checklist: bool = typer.Option(
        False, "--checklist", help="Extract the task's atomic requirements and grade each attempt's coverage (catches dropped constraints)."
    ),
    playbook: bool = typer.Option(
        False, "--playbook", help="Inject the stored ACE strategy playbook into context, then curate it from this run's outcome (closed loop)."
    ),
    agreement: int = typer.Option(
        1, "--agreement", help="With --fuse: sample K cheap answers per turn; escalate to fusion when they disagree (free confidence signal)."
    ),
    strong_verify: str = typer.Option(
        None, "--strong-verify", help="Model slug of a stronger, independent judge that grades hard-turn (retried) results before accepting them."
    ),
    replan: bool = typer.Option(
        False, "--replan", help="On a stall, rebuild the plan from accumulated failure causes (dual-ledger) instead of just nudging."
    ),
    contract: str = typer.Option(
        None, "--contract", help="Machine-checkable success clauses, comma-separated: file_exists:PATH | file_contains:PATH:REGEX | answer_matches:REGEX."
    ),
    stream: bool = typer.Option(
        False, "--stream", help="Print live progress events (attempt/result/status) as the run proceeds."
    ),
    thread: str = typer.Option(
        None, "--thread", help="Checkpoint this run under a thread id; re-run with the same id to resume after a crash."
    ),
    pause_on_taint: bool = typer.Option(
        False, "--pause-on-taint", help="Pause for human approval before finalizing a run that consumed untrusted content (needs --thread)."
    ),
    approve: str = typer.Option(
        None, "--approve", help="HITL accept: finalize a paused run as-is, by thread id (no task needed)."
    ),
    deny: str = typer.Option(
        None, "--deny", help="HITL ignore: discard a paused run by thread id (no task needed)."
    ),
    respond: str = typer.Option(
        None, "--respond", help="HITL respond: resume a paused run by thread id with --feedback guidance."
    ),
    feedback_text: str = typer.Option(
        None, "--feedback", help="Guidance for --respond (fed back so the run tries again)."
    ),
    edit: str = typer.Option(
        None, "--edit", help="HITL edit: finalize a paused run with the corrected --answer, by thread id."
    ),
    answer_text: str = typer.Option(
        None, "--answer", help="The human-corrected answer for --edit."
    ),
) -> None:
    """Tier-2: autonomously solve a task with plan + verify-or-revert. Requires a key."""
    from chimera.core import (
        Agent,
        AgentConfig,
        AutonomousAgent,
        AutonomousConfig,
        CompletionContract,
        Manager,
        Planner,
        ProgressLedger,
        RequirementChecklist,
        RunCheckpointer,
        StrongVerifier,
        WorkspaceGuard,
    )
    from chimera.core.autonomous import AutonomousResult
    from chimera.core.verify import CommandVerifier
    from chimera.ecosystem import TrajectoryCollector
    from chimera.evolution import (
        AutoSkillEvolver,
        CardRetriever,
        CollectiveSkillEvolver,
        ExperienceBuffer,
        SkillEvolver,
        SkillStore,
    )
    from chimera.governance.validator import SkillValidator
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.tools import default_registry

    settings = get_settings()

    # Human-in-the-loop envelope (LangGraph {accept, edit, respond, ignore}) over the taint-pause.
    # 'ignore' (deny) drops the run without touching a model (no key needed).
    if deny:
        RunCheckpointer(settings.home / "runs.db").delete(deny)
        console.print(f"[yellow]Discarded[/yellow] paused run {deny!r}.")
        return
    if approve or respond or edit:
        cp = RunCheckpointer(settings.home / "runs.db")
        thread = approve or respond or edit
        if approve:
            ok, verb = cp.respond(thread, "accept"), "Approved"
        elif edit:
            ok, verb = cp.respond(thread, "edit", answer=answer_text), "Edited"
        else:
            ok, verb = cp.respond(thread, "respond", feedback=feedback_text), "Responding to"
        if not ok:
            console.print(f"[red]No paused run awaiting approval for thread {thread!r}.[/red]")
            raise typer.Exit(code=1)
        saved = cp.load(thread)
        task = str((saved or {}).get("task", ""))
        console.print(f"[green]{verb}[/green] {thread!r} — {'resuming' if respond else 'finalizing'}.")
    elif not task:
        console.print("[red]Provide a task, or use --approve/--deny/--respond/--edit <thread>.[/red]")
        raise typer.Exit(code=1)

    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    workspace_path = Path(workspace)
    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    planner_backend: SupportsComplete = gateway
    escalate_backend: SupportsComplete | None = None
    # --fuse (explicit) or CHIMERA_AUTO_FUSE (production default) both route the worker
    # through the cost-aware router, so deep/error-sensitive turns fuse and cheap/tool
    # turns stay single-model.
    if fuse or settings.auto_fuse:
        from chimera.fusion import FusionEngine, RoutedBackend, RoutingPolicy

        engine = FusionEngine(gateway)
        # --agreement K: sample K cheap answers per turn; disagreement escalates to fusion
        # (a free confidence signal). K=1 (default) keeps the a-priori routing unchanged.
        backend = RoutedBackend(gateway, engine, agreement_k=agreement)
        # Observed-difficulty escalation (issue #3): a fusion-forced backend for retrying a
        # task that already failed verification — "the review surface is where the difficulty
        # signal lives". The AutonomousAgent uses it only after an attempt fails.
        escalate_backend = RoutedBackend(gateway, engine, RoutingPolicy(mode="always"))
        # Planning is a deep, tool-free reasoning turn — exactly where fusion pays off —
        # so an explicit --fuse routes the plan through fusion directly. Auto-fuse keeps
        # planning single unless asked, to bound cost.
        if fuse:
            planner_backend = engine

    # ACE playbook (--playbook): load the stored playbook once so it is injected into the run
    # and curated back afterwards. Kept outside _run_solve so the worktree path doesn't shadow it.
    stored_playbook = _load_playbook() if playbook else None

    def _run_solve(ws: Path) -> AutonomousResult:
        registry = default_registry(ws)
        # Per-session grant first (issue #4): scope the native tools before the meta-tools
        # (explorer/subagents) are added, so subagents inherit the same allowlist.
        from chimera.governance import AuditLog

        allow_audit = AuditLog(settings.home / "audit.jsonl") if (guard or taint) else None
        registry = _apply_tool_allowlist(
            registry, allow=allow_tools, deny=deny_tools, settings=settings, audit=allow_audit
        )
        if explorer:
            from chimera.core import ExploreRepositoryTool

            # Cheap explorer model: a narrow localization task doesn't need the worker's model.
            registry.register(ExploreRepositoryTool(gateway, ws, max_turns=max_steps))
        if subagents:
            from chimera.core import SubAgentTool

            # The subagent draws from the tools registered so far (minus spawn itself).
            registry.register(SubAgentTool(gateway, registry, model=model, max_turns=max_steps))
        if guard:
            from chimera.governance import TrustKernel, govern_registry

            registry = govern_registry(
                registry, TrustKernel(audit=AuditLog(settings.home / "audit.jsonl"))
            )
        ledger = None
        if taint:
            # Outermost wrapper (issues #2/#5): sees the same calls the kernel does, records the
            # capability ledger, and escalates execution/self-mod on tainted input to review.
            from chimera.governance import TaintLedger, ledger_registry

            ledger = TaintLedger()
            # narrow_on_taint: once the run consumes untrusted content, dangerous tools
            # (shell/write/exec/email) require approval for the rest of the run (M9b).
            registry = ledger_registry(
                registry, ledger, audit=allow_audit, narrow_on_taint=True
            )
        # insist_on_action: solve is autonomous task completion, so a described-but-unexecuted plan
        # is pushed back to actually run — the fix for the worker narrating instead of acting.
        _worker_cfg = AgentConfig(model=model, max_steps=max_steps, insist_on_action=True)
        worker = Agent(backend, registry, _worker_cfg)
        escalate_worker = (
            Agent(escalate_backend, registry, _worker_cfg)
            if escalate_backend is not None
            else None
        )
        from chimera.evolution import StagnationDetector

        auto = AutonomousAgent(
            worker,
            escalate_worker=escalate_worker,
            # Pivot the retry when attempts keep failing the same way (short budget → window 2).
            stagnation=StagnationDetector(window=2),
            # Structured per-attempt self-check (Magentic-One): turns "it failed" into a concrete
            # next_focus for the retry — the lift for weak models. Opt-in via --progress-ledger.
            progress_ledger=ProgressLedger(gateway, model) if progress_ledger else None,
            # Requirement checklist (--checklist): extract atomic requirements + grade coverage
            # per attempt, so a weak model can't silently drop a "must include / must not" clause.
            checklist=RequirementChecklist(gateway, model) if checklist else None,
            # Independent strong verification (--strong-verify MODEL): a stronger judge grades
            # hard-turn (retried) results before they're accepted. Uses the same gateway, other model.
            strong_verifier=StrongVerifier(gateway, strong_verify) if strong_verify else None,
            # ACE playbook (--playbook): inject accumulated, delta-curated strategy bullets as
            # advisory context; the run is curated back into it afterwards (closed loop).
            playbook=stored_playbook,
            # Dual-ledger re-plan (--replan): on a stall, rebuild the plan from accumulated
            # failure causes rather than just nudging. Needs the planner (so not with --no-plan).
            replan_on_stall=replan,
            # HITL (--pause-on-taint): pause for approval before finalizing a tainted run.
            pause_on_taint=pause_on_taint,
            # Repo-map (--repo-map): front-load a structural map of the workspace into context.
            repo_map=repo_map,
            # Declared, machine-checkable success clauses (--contract): an AND gate on top of
            # verify-or-revert that catches the model claiming a result the artifacts don't show.
            contract=(
                CompletionContract.from_specs([c.strip() for c in contract.split(",")], ws)
                if contract
                else None
            ),
            # Provenance gate: artifacts born from a tainted run are marked/held pending.
            taint=ledger,
            planner=None if no_plan else Planner(planner_backend, model),
            manager=None if no_manager else Manager(gateway, model, use_rubric=rubric),
            verifier=CommandVerifier(verify, ws) if verify else None,
            guard=WorkspaceGuard(ws),
            experience=ExperienceBuffer(settings.home / "experience.json"),
            trajectories=TrajectoryCollector(settings.home / "trajectories.jsonl") if collect else None,
            memory=None if no_remember else _memory_manager(),
            auto_evolver=(
                None
                if no_evolve_skills
                else AutoSkillEvolver(
                    SkillEvolver(gateway, model),
                    SkillStore(settings.home / "skills.json"),
                    validator=SkillValidator(),
                    audit=allow_audit,
                    # With fusion on and a real panel, evolve skills across the panel and
                    # keep the most transferable one (OpenClaw-Skill) instead of a
                    # single-model proposal.
                    collective=(
                        CollectiveSkillEvolver(gateway, settings.fusion_panel, validator=SkillValidator())
                        if fuse and len(settings.fusion_panel) >= 2
                        else None
                    ),
                    accept_mode=settings.skill_accept_mode,
                )
            ),
            cards=(
                CardRetriever(SkillStore(settings.home / "skills.json"), k=settings.skill_cards_k)
                if settings.skill_cards
                else None
            ),
            spine_workspace=ws,
            on_event=_stream_sink if stream else None,
            # Durable execution (--thread): checkpoint the loop to SQLite so a crash can resume.
            checkpointer=RunCheckpointer(settings.home / "runs.db") if thread else None,
            config=AutonomousConfig(
                max_attempts=max_attempts, use_planner=not no_plan, use_manager=not no_manager
            ),
        )
        outcome = auto.run(task, thread_id=thread)
        if ledger is not None:
            ledger.dump(settings.home / "ledger.jsonl")
            summary = ledger.capability_summary()
            console.print(
                f"[dim]capability ledger: {summary['events']} events, "
                f"{len(summary['tainted_writes'])} tainted write(s), "
                f"{len(summary['escalations'])} taint escalation(s) "
                f"-> {settings.home / 'ledger.jsonl'}[/dim]"
            )
        return outcome

    try:
        if isolate:
            from chimera.core.worktree import run_in_worktree

            result = run_in_worktree(workspace_path, _run_solve, succeeded=lambda r: r.success)
        else:
            result = _run_solve(workspace_path)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(result.answer)
    status = "[green]success[/green]" if result.success else "[red]failed[/red]"
    console.print(f"[dim]{status} after {len(result.attempts)} attempt(s)[/dim]")

    # Close the ACE loop: reflect on this run's outcome (success or failure) and curate the
    # playbook with incremental deltas, so the next run starts from the improved guidance.
    if stored_playbook is not None:
        from chimera.evolution import BackendDeltaProposer, PlaybookCurator

        verdict = "succeeded" if result.success else "failed"
        outcome_text = (
            f"The task {verdict} after {len(result.attempts)} attempt(s). "
            f"Final answer: {result.answer[:500]}"
        )
        applied = PlaybookCurator(BackendDeltaProposer(gateway, model)).curate(
            stored_playbook, task, outcome_text
        )
        _save_playbook(stored_playbook)
        console.print(
            f"[dim]playbook curated: {applied} delta(s) -> {len(stored_playbook.active())} active bullets[/dim]"
        )

    if not result.success:
        raise typer.Exit(code=1)


@app.command(name="solve-batch")
def solve_batch(
    tasks: list[str] = _BATCH_TASKS_ARG,
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root (a git repo, to isolate)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per task."),
    max_attempts: int = typer.Option(2, "--max-attempts", help="Max verify-or-revert attempts per task."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max concurrent isolated workers."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
) -> None:
    """Solve several tasks concurrently, each in its own git worktree (Tier-3 isolation).

    Every task runs against an isolated checkout, so parallel edits never collide. On
    merge-back, a file two tasks both changed is reported as a conflict and left for you
    to resolve rather than silently overwritten. Needs a git repo to isolate.
    """
    from chimera.core import (
        Agent,
        AgentConfig,
        AutonomousAgent,
        AutonomousConfig,
        Manager,
        Planner,
        WorkspaceGuard,
    )
    from chimera.core.autonomous import AutonomousResult
    from chimera.orchestration import run_isolated
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse or settings.auto_fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        backend = RoutedBackend(gateway, FusionEngine(gateway))

    def make_runner(one_task: str) -> Callable[[Path], AutonomousResult]:
        def run(ws: Path) -> AutonomousResult:
            from chimera.tools import default_registry

            worker = Agent(backend, default_registry(ws), AgentConfig(model=model, max_steps=max_steps))
            auto = AutonomousAgent(
                worker,
                planner=Planner(gateway, model),
                manager=Manager(gateway, model),
                guard=WorkspaceGuard(ws),
                spine_workspace=ws,
                config=AutonomousConfig(max_attempts=max_attempts),
            )
            return auto.run(one_task)

        return run

    units = [(f"task{i + 1}", make_runner(task)) for i, task in enumerate(tasks)]
    try:
        batch = run_isolated(
            Path(workspace), units, succeeded=lambda r: r.success, max_workers=max_workers
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    for (name, _), result in zip(units, batch.results, strict=True):
        status = "[green]ok[/green]" if result.ok else f"[red]failed[/red] ({result.error or 'unsolved'})"
        console.print(f"[bold]{name}[/bold]: {status}")
    console.print(
        f"[dim]merged {batch.merged} file(s) across {len(units)} task(s)[/dim]"
    )
    if batch.conflicts:
        console.print(f"[yellow]conflicts (not merged):[/yellow] {', '.join(batch.conflicts)}")
    if not batch.ok:
        raise typer.Exit(code=1)


@app.command(name="crew-isolated")
def crew_isolated(
    task: str = typer.Argument(..., help="The shared task the workers divide."),
    worker: list[str] = _CREW_WORKER_OPT,
    workspace: str = typer.Option(".", "--workspace", "-w", help="Repository root (a git repo, to isolate)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    verify: str = typer.Option(None, "--verify", help="Per-worker gate: shell command run in each worktree (exit 0 to merge)."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per worker."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max concurrent isolated workers."),
    synthesize: bool = typer.Option(False, "--synthesize", help="A supervisor folds the merged results into one unified report."),
    fuse: bool = typer.Option(False, "--fuse", help="Route worker turns through fusion."),
) -> None:
    """Tier-3: tool-using workers split ONE task, each in its own git worktree, verify-gated.

    Define workers with repeated --worker 'name:instruction'. Each runs a real agent loop
    (search/read/edit) against an isolated checkout; non-conflicting edits that pass --verify
    merge back, files two workers both changed are flagged as conflicts, and a worker whose
    check fails is rejected (its edits discarded). Needs a git repo to isolate.
    """
    from chimera.orchestration import IsolatedCrew, IsolatedWorker, Role, RoleAgent
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    if not worker:
        console.print("[red]give at least one --worker 'name:instruction'[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse or settings.auto_fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        backend = RoutedBackend(gateway, FusionEngine(gateway))

    workers = []
    for i, spec in enumerate(worker):
        name, sep, instruction = spec.partition(":")
        name = (name.strip() if sep else "") or f"worker{i + 1}"
        prompt = (instruction.strip() if sep else spec.strip()) or "Do your part of the task."
        workers.append(
            IsolatedWorker(Role(name, prompt), lambda ws: default_registry(ws), max_steps=max_steps)
        )

    supervisor = None
    if synthesize:
        supervisor = RoleAgent(
            Role("supervisor", "You coordinate a team and write a single, unified final report "
                 "from the merged worker outputs. Be concise and note any conflicts or rejects."),
            backend,
        )
    crew = IsolatedCrew(backend, workers, supervisor=supervisor, max_workers=max_workers)
    try:
        result = crew.run(task, Path(workspace), verify=verify)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    for msg in result.transcript:
        console.print(f"[green]✓ {msg.sender}[/green] merged")
    for name in result.rejected:
        console.print(f"[yellow]✗ {name}[/yellow] rejected (failed --verify)")
    for name, err in result.failures.items():
        console.print(f"[red]✗ {name}[/red] crashed: {err}")
    if result.conflicts:
        console.print(f"[yellow]conflicts (not merged):[/yellow] {', '.join(result.conflicts)}")
    console.print(f"[dim]merged {result.merged} file(s) from {len(result.transcript)} worker(s)[/dim]")
    if result.summary:
        console.print(Panel(result.summary, title="unified report", border_style="cyan"))
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def explore(
    query: str = typer.Argument(..., help="What to locate in the repository."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Repository root to explore."),
    model: str = typer.Option(None, "--model", "-m", help="Model for the explorer (a cheap one is fine)."),
    max_turns: int = typer.Option(8, "--max-turns", help="Max exploration turns."),
) -> None:
    """Locate relevant code via the isolated Context Explorer subagent (FastContext-style).

    Returns only a compact file:line evidence block — the exploration turns never touch your
    context. A cheap model is usually the right call here; localization is a narrow task.
    """
    from chimera.core import ContextExplorer
    from chimera.providers import LLMGateway, MissingCredentialsError

    if not get_settings().has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    explorer = ContextExplorer(LLMGateway(), Path(workspace), model=model, max_turns=max_turns)
    try:
        result = explorer.explore(query)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if not result.evidence:
        console.print("[dim]no relevant locations found[/dim]")
        return
    for ev in result.evidence:
        loc = f"[cyan]{ev.path}[/cyan]" + (f":[yellow]{ev.lines}[/yellow]" if ev.lines else "")
        console.print(f"  {loc}" + (f" [dim]— {ev.note}[/dim]" if ev.note else ""))
    console.print(f"[dim]{len(result.evidence)} location(s) in {result.turns} turn(s), {result.tool_calls} tool call(s)[/dim]")


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


@app.command("skills-pending")
def skills_pending() -> None:
    """List learned skills held for review (e.g. distilled during a tainted run)."""
    from chimera.evolution import SkillStore

    store = SkillStore(get_settings().home / "skills.json")
    pending = store.pending()
    if not pending:
        console.print("[green]No pending skills — nothing awaiting review.[/green]")
        return
    table = Table(title="Pending learned skills (review required)", header_style="bold")
    table.add_column("Skill")
    table.add_column("Provenance")
    table.add_column("Description")
    for skill in pending:
        table.add_row(skill.name, skill.provenance, skill.description)
    console.print(table)
    console.print("[dim]Approve with: chimera skills-approve <name>[/dim]")


@app.command("skills-stats")
def skills_stats() -> None:
    """Per-skill usage stats (uses, successes, win rate) + retirement candidates."""
    from chimera.evolution import SkillStore

    store = SkillStore(get_settings().home / "skills.json")
    rows = store.stats()
    if not rows:
        console.print("[dim]No learned skills yet.[/dim]")
        return
    retire = set(store.retirement_candidates())
    table = Table(title="Learned skill stats", show_header=True, header_style="bold")
    for column in ("Skill", "Kind", "Status", "Provenance", "Uses", "Wins", "Rate", ""):
        table.add_column(column)
    for row in rows:
        rate = row["rate"]
        table.add_row(
            str(row["name"]),
            str(row["kind"]),
            str(row["status"]),
            str(row["provenance"]),
            str(row["uses"]),
            str(row["successes"]),
            "-" if rate is None else f"{rate:.0%}",
            "[yellow]retire?[/yellow]" if row["name"] in retire else "",
        )
    console.print(table)
    if retire:
        console.print(
            "[dim]'retire?' = used often with a low win rate — a prune/rewrite candidate. "
            "Nothing is deleted automatically.[/dim]"
        )


@app.command("skills-approve")
def skills_approve(
    name: str = typer.Argument(..., help="Name of the pending or retired skill to activate."),
) -> None:
    """Approve/reactivate a learned skill after review (activates retrieval).

    Works for both a pending skill (held from a tainted run) and a retired one (un-retire).
    """
    from chimera.evolution import SkillStore

    store = SkillStore(get_settings().home / "skills.json")
    if not store.approve(name):
        console.print(f"[red]No skill named {name!r} in the store.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Approved[/green] {name} — now active and retrievable.")


@app.command("skills-export")
def skills_export(
    name: str = typer.Argument(..., help="Name of the learned skill to export."),
    out: str = typer.Option(None, "--out", "-o", help="Write to this path (default: <name>/SKILL.md)."),
) -> None:
    """Export a learned skill to the open SKILL.md format (portable to the agent-skills ecosystem)."""
    from chimera.evolution import SkillStore
    from chimera.skills.skill_md import from_learned, render_skill_md

    store = SkillStore(get_settings().home / "skills.json")
    skill = store.get(name)
    if skill is None:
        console.print(f"[red]No skill named {name!r} in the store.[/red]")
        raise typer.Exit(code=1)
    md = render_skill_md(from_learned(skill))
    target = Path(out) if out else Path(name) / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(md, encoding="utf-8")
    console.print(f"[green]exported[/green] {name} -> {target}")


@app.command("skills-import")
def skills_import(
    path: str = typer.Argument(..., help="Path to a SKILL.md (or a skill directory containing one)."),
) -> None:
    """Import a SKILL.md into the store. A tainted-provenance skill is held pending for review."""
    from chimera.evolution import SkillStore
    from chimera.skills.skill_md import parse_skill_md, to_learned

    src = Path(path)
    if src.is_dir():
        src = src / "SKILL.md"
    if not src.exists():
        console.print(f"[red]No SKILL.md at {src}.[/red]")
        raise typer.Exit(code=1)
    skill = to_learned(parse_skill_md(src.read_text(encoding="utf-8")))
    store = SkillStore(get_settings().home / "skills.json")
    store.add(skill)
    note = " [yellow](held pending — tainted provenance)[/yellow]" if skill.status == "pending" else ""
    console.print(f"[green]imported[/green] {skill.name}{note}")


@app.command("skills-retire")
def skills_retire(
    name: str = typer.Argument(None, help="Skill to retire; omit to act on all candidates."),
    apply: bool = typer.Option(False, "--apply", help="Actually retire (default: dry-run preview)."),
    min_uses: int = typer.Option(5, "--min-uses", help="Only propose skills used at least this often."),
    max_rate: float = typer.Option(
        1 / 3, "--max-rate", help="Only propose skills whose win rate is at or below this."
    ),
) -> None:
    """Propose retiring under-performing skills — review-gated, never a delete.

    Retiring only flips status to 'retired' (excluded from retrieval, still inspectable and
    reactivatable with ``skills-approve``). With no name, acts on the ``retirement_candidates``
    signal (used often, low win rate). Dry-run by default; pass ``--apply`` to commit.
    """
    from chimera.evolution import SkillStore

    store = SkillStore(get_settings().home / "skills.json")
    if name is not None:
        targets = [name] if name in store else []
        if not targets:
            console.print(f"[red]No skill named {name!r} in the store.[/red]")
            raise typer.Exit(code=1)
    else:
        targets = store.retirement_candidates(min_uses=min_uses, max_rate=max_rate)
        if not targets:
            console.print("[dim]No retirement candidates — every skill is pulling its weight.[/dim]")
            return

    if not apply:
        console.print("[bold]Would retire (review-gated, reversible):[/bold]")
        for target in targets:
            console.print(f"  • {target}")
        console.print("[dim]Re-run with --apply to retire; reactivate later with skills-approve.[/dim]")
        return

    for target in targets:
        store.retire(target)
    console.print(
        f"[green]Retired[/green] {len(targets)} skill(s) — excluded from retrieval, "
        "reactivate with: chimera skills-approve <name>"
    )


@app.command("skills-evolve")
def skills_evolve(
    name: str = typer.Argument(..., help="Name of the learned skill whose prompt to GEPA-evolve."),
    instances: str = typer.Option(
        ..., "--instances", help="JSON file: a list of {\"input\": {...}, \"expect\": \"substring\"}."
    ),
    budget: int = typer.Option(20, "--budget", help="Rollout budget (evaluations across the search)."),
    model: str = typer.Option(None, "--model", help="Model slug for the executor + reflector."),
    apply: bool = typer.Option(False, "--apply", help="Save the improved skill (default: dry-run)."),
) -> None:
    """Reflectively evolve a skill's prompt template against graded instances (GEPA).

    Each instance is a `{input, expect}` pair; the (simple, honest) scorer gives 1.0 when the
    produced output contains the `expect` substring, else 0.0. GEPA reflects on a failing case to
    rewrite the template and keeps a Pareto frontier of candidates. Dry-run by default: the
    improved skill is only written back to the store with ``--apply``, and only if it beats the seed.
    """
    import json

    from chimera.evolution import SkillStore, evolve_skill
    from chimera.evolution.gepa import TaskInstance
    from chimera.providers import LLMGateway

    store = SkillStore(get_settings().home / "skills.json")
    gateway = LLMGateway()
    matches = [s for s in store.skills(gateway, model) if s.name == name]
    if not matches:
        console.print(f"[red]No skill named {name!r} in the store.[/red]")
        raise typer.Exit(code=1)
    skill = matches[0]
    if not skill.prompt_template.strip():
        console.print(f"[red]{name!r} is an advisory card with no prompt_template — nothing to evolve.[/red]")
        raise typer.Exit(code=1)

    try:
        raw = json.loads(Path(instances).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]Could not read instances file: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    task_instances = [
        TaskInstance(
            input={str(k): str(v) for k, v in item.get("input", {}).items()},
            scorer=_expect_scorer(str(item.get("expect", ""))),
        )
        for item in raw
    ]
    if not task_instances:
        console.print("[red]The instances file is empty.[/red]")
        raise typer.Exit(code=1)

    improved, result = evolve_skill(gateway, skill, task_instances, model=model, budget=budget)
    console.print(
        f"GEPA on [bold]{name}[/bold]: seed {result.seed_mean:.0%} -> best {result.best_mean:.0%} "
        f"across {len(task_instances)} instances in {result.rollouts} rollouts "
        f"({len(result.candidates)} candidates)."
    )
    if not result.improved:
        console.print("[yellow]No lift found — the seed template is kept (nothing adopted).[/yellow]")
        return
    console.print("[green]Improved template:[/green]")
    console.print(f"[dim]{result.best_template}[/dim]")
    if not apply:
        console.print("[dim]Dry-run — re-run with --apply to save the improved skill to the store.[/dim]")
        return
    store.add(improved)
    console.print(f"[green]Saved[/green] {name} v{improved.version} to the store.")


def _expect_scorer(expect: str) -> Callable[[str], float]:
    """A simple substring grader: 1.0 if the expected text appears in the output, else 0.0."""
    needle = expect.strip()
    return lambda out: 1.0 if needle and needle in out else 0.0


playbook_app = typer.Typer(
    help="ACE strategy playbook — incremental, delta-curated guidance for the agent.",
    no_args_is_help=True,
)
app.add_typer(playbook_app, name="playbook")


def _playbook_path() -> Path:
    return get_settings().home / "playbook.json"


def _load_playbook() -> Playbook:
    import json

    from chimera.evolution import Playbook

    path = _playbook_path()
    if not path.exists():
        return Playbook()
    return Playbook.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _save_playbook(playbook: Playbook) -> None:
    import json

    path = _playbook_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(playbook.to_dict(), indent=2), encoding="utf-8")


@playbook_app.command("show")
def playbook_show() -> None:
    """Print the current active playbook (top strategies by score)."""
    text = _load_playbook().render(max_items=100)
    console.print(text or "[dim]Playbook is empty — add bullets or curate from a run outcome.[/dim]")


@playbook_app.command("add")
def playbook_add(
    content: str = typer.Argument(..., help="The strategy/pitfall bullet to add."),
    section: str = typer.Option("strategy", "--section", help="strategy | pitfall | check."),
) -> None:
    """Manually add a bullet (a near-duplicate reinforces the existing one)."""
    playbook = _load_playbook()
    item = playbook.add(content, section=section)
    if item is None:
        console.print("[red]Empty content — nothing added.[/red]")
        raise typer.Exit(code=1)
    _save_playbook(playbook)
    console.print(f"[green]Added[/green] {item.id}: {item.content}")


@playbook_app.command("refine")
def playbook_refine() -> None:
    """Grow-and-refine: merge duplicate bullets and cap the size (deprecates the weakest)."""
    playbook = _load_playbook()
    before = len(playbook.active())
    playbook.refine()
    _save_playbook(playbook)
    console.print(f"Active bullets: {before} -> {len(playbook.active())} after refine.")


@playbook_app.command("curate")
def playbook_curate(
    task: str = typer.Option(..., "--task", help="The task the outcome is for."),
    outcome: str = typer.Option(..., "--outcome", help="What happened (success/failure + details)."),
    model: str = typer.Option(None, "--model", help="Model slug for the reflect+curate call."),
) -> None:
    """Reflect on a run outcome and apply incremental deltas (add/reinforce/deprecate)."""
    from chimera.evolution import BackendDeltaProposer, PlaybookCurator
    from chimera.providers import LLMGateway

    playbook = _load_playbook()
    curator = PlaybookCurator(BackendDeltaProposer(LLMGateway(), model))
    applied = curator.curate(playbook, task, outcome)
    _save_playbook(playbook)
    console.print(f"[green]Applied {applied} delta(s)[/green] — {len(playbook.active())} active bullets.")


@app.command("rubric-grade")
def rubric_grade(
    rubric_file: str = typer.Option(..., "--rubric", help="JSON rubric: {criteria:[{text,weight,required}], pass_threshold, required_gate}."),
    task: str = typer.Option(..., "--task", help="The task the answer is for."),
    answer: str = typer.Option(None, "--answer", help="The answer text (or use --answer-file)."),
    answer_file: str = typer.Option(None, "--answer-file", help="Read the answer from this file."),
    model: str = typer.Option(None, "--model", help="Model slug for the grader."),
) -> None:
    """Grade an answer against an authorable rubric — weighted criteria with a required-criterion veto.

    Produces a per-criterion breakdown, a single weighted score, and a pass/fail verdict. A required
    criterion that falls below the gate vetoes the outcome regardless of the weighted score.
    """
    import json

    from chimera.eval import Rubric, model_grader
    from chimera.providers import LLMGateway

    try:
        rubric = Rubric.from_dict(json.loads(Path(rubric_file).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        console.print(f"[red]Could not read rubric: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    if answer_file:
        answer = Path(answer_file).read_text(encoding="utf-8")
    if not answer:
        console.print("[red]Provide --answer or --answer-file.[/red]")
        raise typer.Exit(code=1)

    outcome = model_grader(LLMGateway(), model).grade(task, answer, rubric)
    table = Table(title="Rubric grade", show_header=True, header_style="bold")
    table.add_column("Criterion")
    table.add_column("Score", justify="right")
    for criterion in rubric.criteria:
        score = outcome.scores.get(criterion.text, 0.0)
        flag = " [red](required)[/red]" if criterion.required else ""
        table.add_row(f"{criterion.text}{flag}", f"{score:.2f}")
    console.print(table)
    verdict = "[green]PASS[/green]" if outcome.passed else "[red]FAIL[/red]"
    console.print(f"weighted {outcome.weighted:.0%} vs threshold {rubric.pass_threshold:.0%} -> {verdict}")
    if outcome.failed_required:
        console.print(f"[red]vetoed by required criteria:[/red] {', '.join(outcome.failed_required)}")


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
    schedule: str = typer.Argument(..., help="Cron expression, or an event/webhook name."),
    action: str = typer.Argument(..., help="What to do (task description / skill)."),
    event: bool = typer.Option(False, "--event", help="Treat SCHEDULE as an event name."),
    webhook: bool = typer.Option(False, "--webhook", help="Fire on POST /webhook/<SCHEDULE> (needs 'chimera serve')."),
) -> None:
    """Add a cron, event- or webhook-triggered job."""
    import time

    from chimera.scheduler import Scheduler

    sched = Scheduler(_cron_store())
    if webhook:
        job = sched.schedule_webhook(name, schedule, action)
    elif event:
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
def cron_learn(
    min_occurrences: int = typer.Option(3, "--min", help="Min repeats to propose."),
    schedule: str = typer.Option(None, "--schedule", help="Override the suggested cron schedule."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Create every proposal without prompting."),
) -> None:
    """Propose crons from recurring tasks and create the ones you confirm.

    Each proposal is shown for explicit confirmation (the human-in-the-loop approval
    that keeps automation creation under control); confirmed jobs are validated and
    created enabled. ``--yes`` confirms all (use deliberately).
    """
    from chimera.evolution import ExperienceBuffer
    from chimera.governance.validator import ScheduleValidator
    from chimera.scheduler import CronLearner, Scheduler

    settings = get_settings()
    history = [e.task for e in ExperienceBuffer(settings.home / "experience.json").all()]
    learner = CronLearner(min_occurrences=min_occurrences)
    proposals = learner.analyze(history)
    if not proposals:
        console.print("[dim]no recurring tasks found in history[/dim]")
        return

    scheduler = Scheduler(_cron_store())
    validator = ScheduleValidator()
    created = 0
    for proposal in proposals:
        sched = schedule or proposal.suggested_schedule
        if not validator.validate(sched).accepted:
            console.print(f"[yellow]skip[/yellow] {proposal.name}: invalid schedule '{sched}'")
            continue
        summary = f"[cyan]{proposal.name}[/cyan] (seen {proposal.occurrences}x) → '{sched}'"
        if yes or typer.confirm(f"Create cron {summary} for: {proposal.action}?", default=False):
            job = learner.build_job(proposal, enabled=True, schedule=sched)
            scheduler.store.add(job)
            created += 1
            console.print(f"  [green]created[/green] {job.id} {job.name} (enabled)")
        else:
            console.print(f"  [dim]skipped[/dim] {proposal.name}")
    console.print(f"created {created} cron(s) of {len(proposals)} proposed.")


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
    lane: str = typer.Option("solve", "--lane", "-l", help="Worker lane: solve | crew."),
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
) -> None:
    """Dispatch backlog cards through their lanes (solve/crew). Requires a key."""
    from chimera.kanban import LaneRunner, dispatch
    from chimera.kanban.lanes import CrewLane, SolveLane

    if not get_settings().has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    board = _board()
    runners: dict[str, LaneRunner] = {
        "solve": SolveLane(workspace=Path(workspace), model=model),
        "crew": CrewLane(model=model),
    }
    outcomes = dispatch(board, runners, limit=limit)
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


# --- memory subcommands -------------------------------------------------------

memory_app = typer.Typer(help="Curated long-term memory.", no_args_is_help=True)
app.add_typer(memory_app, name="memory")


def _semantic_embed() -> EmbedFn | None:
    """The gateway embedder when semantic memory is on, else None (keyword recall)."""
    settings = get_settings()
    if not settings.semantic_memory:
        return None
    from chimera.providers import LLMGateway

    return LLMGateway(settings).embed


def _memory_manager() -> MemoryManager:
    from chimera.memory import MemoryManager, MemoryStore, SqliteMemoryStore

    settings = get_settings()
    embed = _semantic_embed()
    if settings.memory_backend == "sqlite":
        return MemoryManager(SqliteMemoryStore(settings.home / "memory.db"), embed=embed)
    return MemoryManager(MemoryStore(settings.home / "memory.json"), embed=embed)


def _learned_skill_labels(settings: Settings) -> list[str]:
    """Name + description strings of stored learned skills (to avoid re-nudging)."""
    from chimera.evolution import SkillStore

    return SkillStore(settings.home / "skills.json").labels()


def _emit_skill_nudges(session: object, known_skills: list[str], already: set[str]) -> None:
    """Suggest saving a recurring in-session task as a reusable skill (once each)."""
    from chimera.evolution import detect_skill_nudges

    tasks = [turn.user for turn in session.turns]  # type: ignore[attr-defined]
    for nudge in detect_skill_nudges(tasks, known_skills):
        if nudge.task not in already:
            already.add(nudge.task)
            console.print(
                f"[dim]🛠️  done this {nudge.count}× — save as a skill? [/dim]"
                f"[yellow]{nudge.task}[/yellow][dim] → chimera solve reuses it automatically[/dim]"
            )


def _maybe_autoconsolidate(memory: MemoryManager | None, settings: Settings) -> None:
    """On session end, consolidate memory if it outgrew the budget (opt-in)."""
    if memory is None or not settings.auto_consolidate:
        return
    try:
        from chimera.memory.consolidate import model_summarizer
        from chimera.providers import LLMGateway

        removed = memory.autoconsolidate(
            model_summarizer(LLMGateway()), max_items=settings.memory_budget
        )
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup, never break exit
        console.print(f"[dim]auto-consolidate skipped: {exc}[/dim]")
        return
    if removed:
        console.print(f"[dim]🧹 consolidated {removed} redundant memory item(s)[/dim]")


def _recall_graph(memory: MemoryManager | None) -> MemoryGraph | None:
    """Build an entity-relation graph from stored memory, for entity-aware recall."""
    if memory is None:
        return None
    from chimera.memory import build_graph

    return build_graph([item.content for item in memory.store.all()])


@memory_app.command("add")
def memory_add(
    content: str = typer.Argument(..., help="The fact to remember."),
    key: str = typer.Option(None, "--key", help="Optional dedup key."),
    persona: bool = typer.Option(False, "--persona", help="Store as a persona fact (part of the cross-session profile)."),
) -> None:
    """Remember a fact (ADD / UPDATE / NOOP, deduped)."""
    op, item = _memory_manager().remember(content, "persona" if persona else "semantic", key=key)
    console.print(f"[green]{op}[/green] {item.id}")


@memory_app.command("profile")
def memory_profile() -> None:
    """Show the consolidated cross-session user profile (persona facts)."""
    text = _memory_manager().profile()
    console.print(text or "[dim]no persona facts yet — add some with `memory add --persona`[/dim]")


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


@memory_app.command("prune")
def memory_prune(
    max_items: int = typer.Option(50, "--max", help="Keep the N highest-value memories."),
) -> None:
    """Prune low-value memory under a budget (multi-factor value model)."""
    removed = _memory_manager().prune(max_items)
    console.print(f"pruned {removed} low-value memory item(s)")


@memory_app.command("consolidate")
def memory_consolidate(
    threshold: float = typer.Option(
        0.5, "--threshold", help="Similarity (Jaccard) to cluster facts; lower = merges more."
    ),
) -> None:
    """Merge clusters of similar memories into one LLM-summarised fact (opt-in write)."""
    from chimera.memory.consolidate import model_summarizer
    from chimera.providers import LLMGateway, MissingCredentialsError

    if not get_settings().has_any_key():
        console.print("[red]no provider API key configured[/red] — set one to summarise")
        raise typer.Exit(1)
    try:
        removed = _memory_manager().consolidate(
            model_summarizer(LLMGateway()), threshold=threshold
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"consolidated: merged away {removed} redundant memory item(s)")


@memory_app.command("graph")
def memory_graph(
    entity: str = typer.Option(None, "--entity", "-e", help="Show relations for one entity."),
) -> None:
    """Build an entity-relation graph from long-term memory and show it."""
    from chimera.memory import build_graph

    settings = get_settings()
    texts = [item.content for item in _memory_manager().store.all()]
    graph = build_graph(texts)
    graph.save(settings.home / "memory_graph.json")

    if entity:
        relations = graph.relations_of(entity)
        if not relations:
            console.print(f"[dim]no relations for '{entity}'[/dim]")
            return
        for relation in relations:
            console.print(f"  {relation.source} [cyan]{relation.relation}[/cyan] {relation.target}")
        return

    console.print(
        f"[bold]{len(graph)} relation(s) across {len(graph.entities())} entit(y/ies)[/bold]"
    )
    for relation in graph.relations()[:30]:
        console.print(f"  {relation.source} [cyan]{relation.relation}[/cyan] {relation.target}")


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
    min_steps: int = typer.Option(0, "--min-steps", help="Recipe: keep only traces with >= N steps."),
    diverse: bool = typer.Option(False, "--diverse", help="Recipe: at most one SFT example per task."),
    min_process: float = typer.Option(
        None, "--min-process", help="Keep only traces whose step-following score >= this (SkillCoach)."
    ),
) -> None:
    """Export a curated SFT or DPO dataset from trajectories."""
    from chimera.ecosystem import CurationConfig, curate_dpo, curate_sft, write_jsonl

    if fmt not in ("sft", "dpo"):
        console.print("[red]--format must be 'sft' or 'dpo'.[/red]")
        raise typer.Exit(code=1)
    config = CurationConfig(
        min_reward=min_reward,
        dedup=not no_dedup,
        min_margin=min_margin,
        min_steps=min_steps,
        max_per_prompt=1 if diverse else 0,
        min_process=min_process if min_process is not None else get_settings().sft_min_process,
    )
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


def _read_results(path: str) -> list[bool]:
    """Read a JSON pass/fail list (a bench run's per-trial results)."""
    import json

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a JSON list of booleans")
    return [bool(x) for x in raw]


@evolve_app.command("rft")
def evolve_rft(
    baseline: str = typer.Option(..., "--baseline", help="JSON list of baseline bench pass/fail."),
    candidate: str = typer.Option(..., "--candidate", help="JSON list of candidate bench pass/fail."),
    traj: str = typer.Option(None, "--traj", help="Trajectory JSONL (default: <home>/trajectories.jsonl)."),
    min_reward: float = typer.Option(0.5, "--min-reward", help="Rejection-sampling reward bar."),
    min_examples: int = typer.Option(30, "--min-examples", help="Accepted examples needed to gate."),
    top_k: int = typer.Option(0, "--top-k", help="Keep at most this many accepted per prompt (0 = all)."),
    out: str = typer.Option(None, "--out", help="If promoted, write dataset + recipe here."),
    force: bool = typer.Option(False, "--force", help="Export even if the round is not promoted."),
) -> None:
    """One rejection-sampling fine-tuning round, gated by an honest A/B on two bench result files.

    Rejection-samples the collected trajectories (successes at/above the reward bar), then promotes
    the round ONLY if the candidate beats the baseline with a confidence interval that excludes zero
    — no lift, no promotion, no training on noise. Artifacts are withheld for an unpromoted round
    unless ``--force``. Feed ``--baseline``/``--candidate`` the pass/fail lists two bench runs produce.
    """
    from chimera.ecosystem import RejectionSamplingLoop, StaticEvaluator

    try:
        baseline_passed = _read_results(baseline)
        candidate_passed = _read_results(candidate)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Could not read results: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    loop = RejectionSamplingLoop(
        _collector(traj),
        StaticEvaluator(baseline_passed, candidate_passed),
        min_reward=min_reward,
        min_examples=min_examples,
        top_k_per_prompt=top_k,
    )
    result = loop.run()
    console.print(
        f"RFT round: {result.accepted_examples} accepted "
        f"({result.accept_rate:.0%} accept rate), ready={result.ready}"
    )
    if result.ab is not None:
        from chimera.eval.bench_ab import format_report

        console.print(format_report(result.ab))
    verdict = "[green]PROMOTED[/green]" if result.promoted else "[yellow]WITHHELD[/yellow]"
    console.print(f"{verdict} — {result.reason}")
    if out:
        written = loop.export(result, Path(out), force=force)
        if written:
            for path in written:
                console.print(f"[green]wrote[/green] {path}")
            console.print("[dim]Training is external + opt-in: run on a GPU, review before use.[/dim]")
        else:
            console.print("[dim]Nothing written — round not promoted (use --force to override).[/dim]")


@evolve_app.command("tune")
def evolve_tune(
    rounds: int = typer.Option(2, "--rounds", help="Meta-search rounds."),
    model: str = typer.Option(None, "--model", help="Base model for the spec."),
    max_steps: int = typer.Option(8, "--max-steps", help="Initial runtime step budget."),
) -> None:
    """Self-optimize the agent spec (OpenJarvis meta-search) against the daily scenarios.

    Each round a model proposes a coordinated edit to the spec; the candidate is scored on
    the daily scenarios and kept only on non-regression. Uses real model calls.
    """
    from chimera.core.agent import DEFAULT_SYSTEM_PROMPT, Agent, AgentConfig
    from chimera.ecosystem import AgentSpec, model_proposer, search_spec
    from chimera.eval import daily_scenarios, scenario_scorer
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    registry = default_registry(Path("."))

    class _SpecSolver:
        def __init__(self, spec: AgentSpec) -> None:
            self._agent = Agent(
                gateway,
                registry,
                AgentConfig(
                    model=spec.model,
                    max_steps=spec.max_steps,
                    system_prompt=spec.system_prompt or DEFAULT_SYSTEM_PROMPT,
                ),
            )

        def solve(self, prompt: str) -> str:
            return self._agent.run(prompt).answer

    scorer = scenario_scorer(_SpecSolver, daily_scenarios())
    initial = AgentSpec(model=model, max_steps=max_steps)
    try:
        result = search_spec(initial, scorer, model_proposer(gateway, model), rounds=rounds)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title="Spec meta-search", show_header=True)
    table.add_column("round")
    table.add_column("score")
    table.add_column("kept")
    for index, step in enumerate(result.history):
        table.add_row(str(index), f"{step.score:.3f}", "✓" if step.accepted else "·")
    console.print(table)
    console.print(f"[green]best score[/green] {result.best_score:.3f}")
    console.print(f"[dim]best spec:[/dim] {result.best.to_dict()}")


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
    hard: bool = typer.Option(False, "--hard", help="Use the hard suite (traps / propagating chain)."),
    rounds: int = typer.Option(
        1, "--rounds", help="Re-run the suite N times; report stagnation + cost trend across rounds."
    ),
) -> None:
    """Run the continuous-evolution benchmark on a demo task set. Requires a key."""
    from chimera.eval import (
        SingleModelSolver,
        demo_chain,
        demo_tasks,
        hard_chain,
        hard_tasks,
        run_chain,
        run_continuous,
        run_evolution,
    )
    from chimera.eval.hard import HARD_CHAIN_START
    from chimera.providers import LLMGateway

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse:
        # The benchmark measures the fusion engine itself, so use it directly — the
        # cost-aware RoutedBackend would decline to fuse these short prompts (they fall
        # under its length/keyword gate) and silently collapse back to single-model.
        from chimera.fusion import FusionEngine

        backend = FusionEngine(gateway)

    solver = SingleModelSolver(backend, model)
    report: Any  # EvolutionReport | ChainReport | RoundedEvolutionReport — all have .summary()
    if chain:
        steps = hard_chain() if hard else demo_chain(limit or 8)
        start = HARD_CHAIN_START if hard else "0"
        report = run_chain(solver, steps, initial_state=start)
        title = ("Hard " if hard else "") + "Chained continuous-evolution benchmark"
    else:
        tasks = hard_tasks() if hard else demo_tasks()
        if limit > 0:
            tasks = tasks[:limit]
        on_task = lambda o: console.print(  # noqa: E731
            f"  {'[green]PASS[/green]' if o.passed else '[red]FAIL[/red]'} {o.id}"
        )
        if rounds > 1:
            report = run_evolution(solver, tasks, rounds=rounds, on_task=on_task)
            title = f"Continuous-evolution benchmark ({rounds} rounds: stagnation + cost)"
        else:
            report = run_continuous(solver, tasks, on_task=on_task)
            title = "Continuous-evolution benchmark"

    summary = report.summary()
    table = Table(title=title, show_header=False, title_style="bold")
    for key, value in summary.items():
        table.add_row(key, str(value))
    console.print(table)


@app.command()
def redteam() -> None:
    """Red-team the injection defenses: attack success rate with vs without them.

    No key needed — measures whether the governance layer blocks a harmful tool call
    once a run is tainted (defense-in-depth coverage), not model susceptibility.
    """
    from chimera.eval import default_attacks, run_redteam

    attacks = default_attacks()
    undef = run_redteam(attacks, defended=False).summary()
    defense = run_redteam(attacks, defended=True)
    defended = defense.summary()

    table = Table(title="Injection red-team (attack success rate — lower is better)", header_style="bold")
    table.add_column("Metric")
    table.add_column("No defenses", justify="right")
    table.add_column("With --taint", justify="right")
    keys = ["attacks", "attack_success_rate", "block_rate"] + sorted(
        k for k in defended if k.startswith("asr_")
    )
    for key in keys:
        table.add_row(key, str(undef.get(key, "-")), str(defended.get(key, "-")))
    console.print(table)
    leaks = defense.leaks()
    if leaks:
        console.print(
            f"[yellow]Still gets through even defended:[/yellow] {', '.join(leaks)} "
            "— named honestly; the general data-vs-instructions problem (#5) stays open."
        )


@app.command("bench-compare")
def bench_compare(
    baseline: str = typer.Argument(..., help="JSON file of the baseline arm's per-task pass/fail (list of bools, or {task: bool})."),
    treatment: str = typer.Argument(..., help="JSON file of the treatment arm's per-task pass/fail."),
    baseline_name: str = typer.Option("baseline", "--baseline-name", help="Label for the baseline arm."),
    treatment_name: str = typer.Option("chimera", "--treatment-name", help="Label for the treatment arm."),
    paired: bool = typer.Option(
        False, "--paired", help="Paired (McNemar) test: item i in both files is the SAME task replayed from an identical forked state — a tighter CI."
    ),
) -> None:
    """Report the honest A/B delta (+95% CI) between two benchmark result files.

    Feed it the pass/fail from two runs on the SAME task IDs (e.g. a terminal-bench free-model
    baseline vs the same model driven by Chimera). Prints each arm's Wilson-bounded pass rate,
    the delta, its Newcombe CI, and whether the difference is significant. This is the number
    that proves (or doesn't) that the scaffolding lifts a weak model.

    With --paired, the two lists are treated as *aligned pairs* (each index is one task replayed
    from an identical forked checkpoint), and the tighter McNemar/Wilson interval is reported —
    the payoff of running both arms from the same forked state.
    """
    import json

    def _load(path: str) -> list[bool]:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        values = raw.values() if isinstance(raw, dict) else raw
        return [bool(v) for v in values]

    try:
        base_passed, treat_passed = _load(baseline), _load(treatment)
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]could not read results: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if paired:
        from chimera.eval.paired import compare_paired
        from chimera.eval.paired import format_report as format_paired

        if len(base_passed) != len(treat_passed):
            console.print("[red]--paired needs two equal-length, aligned lists (same tasks, same order).[/red]")
            raise typer.Exit(code=1)
        presult = compare_paired(
            base_passed, treat_passed, baseline_name=baseline_name, treatment_name=treatment_name
        )
        console.print(format_paired(presult))
        return

    from chimera.eval import compare_ab, format_report

    result = compare_ab(
        base_passed, treat_passed, baseline_name=baseline_name, treatment_name=treatment_name
    )
    console.print(format_report(result))
    if not result.significant:
        console.print(
            "[dim]not significant — a larger task subset / more seeds, or the feature genuinely "
            "doesn't move the number. Report it honestly either way.[/dim]"
        )


@app.command("swe-bench-compare")
def swe_bench_compare(
    baseline: str = typer.Argument(..., help="SWE-bench evaluation report JSON for the model-only arm."),
    treatment: str = typer.Argument(..., help="SWE-bench evaluation report JSON for the model+Chimera arm."),
    instances: str = typer.Option(..., "--instances", help="JSONL of the instances both arms ran (fixes the id set)."),
) -> None:
    """Honest A/B over two SWE-bench Verified-Mini reports on the SAME instance ids.

    Reads the official evaluation reports (``resolved_ids`` or a per-instance map) for a free model
    alone vs the same model driven by Chimera, projects both onto the shared instance list (a missing
    id counts as unresolved), and prints the delta + 95% CI. This is the second standard scoreboard
    for the weak-model-lift thesis; the pass/fail comes from SWE-bench's tests, never self-reported.
    """
    import json

    from chimera.eval import compare_arms, format_report, load_instances

    try:
        ids = [inst.instance_id for inst in load_instances(Path(instances))]
        baseline_report = json.loads(Path(baseline).read_text(encoding="utf-8"))
        treatment_report = json.loads(Path(treatment).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        console.print(f"[red]could not read inputs: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    if not ids:
        console.print("[red]The instances file is empty.[/red]")
        raise typer.Exit(code=1)
    result = compare_arms(baseline_report, treatment_report, ids)
    console.print(f"[dim]{len(ids)} instances[/dim]")
    console.print(format_report(result))
    if not result.significant:
        console.print(
            "[dim]not significant — a larger slice / more instances, or the scaffolding genuinely "
            "doesn't move SWE-bench. Report it honestly either way.[/dim]"
        )


@app.command("memory-bench")
def memory_bench(
    sizes: str = typer.Option("50,200,1000", "--sizes", help="Comma-separated memory sizes to sweep."),
    semantic: bool = typer.Option(
        False, "--semantic", help="Use embedding recall (needs an embeddings key) to measure the lift."
    ),
) -> None:
    """Measure recall@k as memory grows — lexical vs paraphrase.

    Default (keyword search, no key needed) surfaces the honest ceiling: exact-token recall
    holds at scale, but paraphrase recall collapses. Pass ``--semantic`` to re-run with the
    embedding recall path and watch the paraphrase column lift — that delta is the whole
    point of M11b.
    """
    import tempfile

    from chimera.eval import memory_sweep
    from chimera.memory import MemoryManager, MemoryStore

    size_list = [int(s) for s in sizes.split(",") if s.strip().isdigit()] or [50, 200, 1000]
    tmp = Path(tempfile.mkdtemp(prefix="chimera-membench-"))
    counter = {"n": 0}

    embed = None
    if semantic:
        from chimera.providers import LLMGateway, MissingCredentialsError

        settings = get_settings()
        if not settings.has_any_key():
            console.print("[red]--semantic needs a provider key with embeddings access.[/red]")
            raise typer.Exit(1)
        try:
            embed = LLMGateway(settings).embed
        except MissingCredentialsError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    def factory() -> MemoryManager:
        counter["n"] += 1
        return MemoryManager(MemoryStore(tmp / f"m{counter['n']}.json"), embed=embed)

    mode = "semantic (embeddings)" if semantic else "keyword search"
    reports = memory_sweep(factory, size_list)
    table = Table(title=f"Memory recall@k vs scale ({mode})", header_style="bold")
    for col in ("facts", "recall@k", "lexical", "paraphrase"):
        table.add_column(col, justify="right")
    for report in reports:
        s = report.summary()
        table.add_row(
            str(int(s["n_facts"])), f"{s['recall@k']:.2f}",
            f"{s['recall@k_lexical']:.2f}", f"{s['recall@k_paraphrase']:.2f}",
        )
    console.print(table)
    console.print(
        "[dim]Lexical recall holds at scale; paraphrase recall is the keyword ceiling — "
        "opt-in semantic retrieval (embeddings) is what lifts it. Re-run with --semantic.[/dim]"
    )


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
    from chimera.providers import LLMGateway

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse or settings.auto_fuse:  # explicit --fuse or the CHIMERA_AUTO_FUSE default
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
def lifecycle(
    task: str = typer.Argument(..., help="The feature/task to take through the SDLC."),
    verify: str = typer.Option(None, "--verify", help="Test command for the test stage (exit 0)."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_attempts: int = typer.Option(2, "--max-attempts", help="Build/test verify-or-revert budget."),
) -> None:
    """SDLC crew: plan -> build -> test -> review with verify-or-revert. Requires a key."""
    from chimera.orchestration import lifecycle_crew
    from chimera.providers import LLMGateway, MissingCredentialsError

    if not get_settings().has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    crew = lifecycle_crew(
        LLMGateway(),
        workspace=Path(workspace),
        verify=verify,
        model=model,
        max_build_attempts=max_attempts,
    )
    try:
        result = crew.run(task)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    for stage in result.stages:
        mark = "[green]✓[/green]" if stage.passed else "[red]✗[/red]"
        console.print(f"{mark} [bold]{stage.name}[/bold]")
        console.print(f"  {stage.output.strip()[:500]}")
    status = "[green]success[/green]" if result.success else "[red]failed[/red]"
    console.print(f"\nlifecycle: {status}")


@app.command()
def workflow(
    file: str = typer.Argument(..., help="Workflow YAML file (declarative loop)."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
) -> None:
    """Run a declarative workflow — a designed loop — from a YAML file. Requires a key."""
    from chimera.workflow import load_workflow, run_workflow
    from chimera.workflow.executors import build_executors

    if not get_settings().has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    flow = load_workflow(file)
    result = run_workflow(flow, build_executors(workspace=Path(workspace), model=model))
    console.print(f"[bold]{result.name}[/bold]")
    for run in result.runs:
        if run.skipped:
            console.print(f"  [dim]– {run.name} (skipped)[/dim]")
            continue
        mark = "[green]✓[/green]" if run.success else "[red]✗[/red]"
        extra = f" ×{run.attempts}" if run.attempts > 1 else ""
        console.print(f"  {mark} {run.name} [{run.uses}]{extra}")
    status = "[green]success[/green]" if result.success else "[red]failed[/red]"
    console.print(f"workflow: {status}")


@app.command()
def drift(
    spec: str = typer.Argument(..., help="Spec YAML file."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
) -> None:
    """Drift gate: check the workspace against a spec (Spec Growth). Exit 1 on drift."""
    from chimera.governance import check_drift, load_spec

    report = check_drift(load_spec(spec), Path(workspace))
    console.print(f"[bold]{report.name}[/bold]")
    for result in report.results:
        mark = "[green]✓[/green]" if result.satisfied else "[red]✗[/red]"
        detail = f" — {result.detail}" if result.detail else ""
        console.print(f"  {mark} {result.id}{detail}")
    if report.aligned:
        console.print("[green]aligned[/green] — spec and code are in sync")
    else:
        console.print("[red]drift[/red] — spec and code are out of sync")
        raise typer.Exit(code=1)


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
