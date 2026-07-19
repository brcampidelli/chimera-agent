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
    # Atomic write: a crash mid-write to .env must not truncate the user's secrets/config.
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


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


def _session_profile(mem: Any) -> str:
    """The chat/assist preamble: stable user profile first, volatile memory facts after.

    Byte-stable for the same profile (the cacheable prefix); memory-derived facts go
    in a separated volatile section so they never break the stable prefix.
    """
    from chimera.interface.profile import load_profile, profile_path, render_profile

    settings = get_settings()
    stored = load_profile(profile_path(settings.home))
    memory_part = mem.profile() if mem is not None else ""
    return render_profile(stored, memory_part)


def _cascade_backend(gateway: SupportsComplete, settings: Any) -> SupportsComplete:
    """Build the FrugalGPT cascade (weak -> gate -> mid -> gate -> fusion) over the tier ladder.

    Route decisions are appended to ``<home>/routes.jsonl`` (hash+tokens only, never prompt
    text) — the per-session cost receipt and the future router's training data.
    """
    from chimera.fusion import FusionEngine
    from chimera.fusion.cascade import CascadeBackend, CascadeConfig

    ladder = settings.tier_ladder()
    config = CascadeConfig(
        weak=ladder.weak,
        mid=ladder.mid,
        entry=ladder.entry,
        log_path=Path(settings.home) / "routes.jsonl",
    )
    return CascadeBackend(gateway, FusionEngine(gateway), config)


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

    # 2b. Cost mode: how the weak/mid/top tier ladder is filled unless the user pins
    # models per role (`chimera models set ...`). Vendor-agnostic — any slug, any role.
    if not yes:
        console.print(
            "Cost mode for the model tiers: [bold]cheap[/bold] (free-first), "
            "[bold]balanced[/bold] (economic), [bold]premium[/bold] (frontier), "
            "[bold]auto[/bold] (prioritizes the mid tier)."
        )
        mode = typer.prompt(
            "Cost mode [cheap/balanced/premium/auto]", default="auto"
        ).strip().lower()
        if mode in ("cheap", "balanced", "premium", "auto"):
            if mode != "auto":
                _set_env_var(env_path, "CHIMERA_COST_MODE", mode)
                os.environ["CHIMERA_COST_MODE"] = mode
            console.print(
                f"[green]Cost mode:[/green] {mode} — tune per role anytime with "
                "[bold]chimera models set <weak|mid|top> <slug>[/bold] (any vendor)."
            )
        else:
            console.print(f"[yellow]Unknown mode {mode!r} — keeping 'auto'.[/yellow]")

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


def _doctor_fixes(settings: Any, *, cwd: Path | None = None) -> list[str]:
    """Perform safe, secret-free setup repairs (OpenClaw `doctor --fix`). Returns what it did.

    Never writes a secret — a missing provider key is reported, never invented. The safe repairs are
    creating the state dir and scaffolding a ``.env`` from ``.env.example`` for the user to fill in.
    """
    import shutil

    root = cwd or Path.cwd()
    done: list[str] = []
    home = Path(settings.home)
    if not home.exists():
        home.mkdir(parents=True, exist_ok=True)
        done.append(f"created state dir {home}")
    env, example = root / ".env", root / ".env.example"
    if not env.exists() and example.exists():
        shutil.copyfile(example, env)
        done.append(f"scaffolded {env} from .env.example — set a provider key in it")
    return done


@app.command()
def doctor(
    fix: bool = typer.Option(False, "--fix", help="Auto-repair safe setup issues (state dir, .env scaffold)."),
) -> None:
    """Check the environment and configuration. With --fix, repair safe setup issues."""
    settings = get_settings()

    if fix:
        for note in _doctor_fixes(settings):
            console.print(f"[green]fixed[/green] {note}")
        get_settings.cache_clear()
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


models_app = typer.Typer(
    help="Model assignment: tier ladder (weak/mid/top), cost mode, and the multi-vendor catalog.",
    invoke_without_command=True,
)


def _render_models() -> None:
    """The active model assignment: tier ladder + cost mode + fusion roles."""
    settings = get_settings()
    ladder = settings.tier_ladder()
    table = Table(title="Models", show_header=True, header_style="bold")
    table.add_column("Role")
    table.add_column("Model(s)")
    pinned = {
        "weak": bool(settings.weak_model),
        "mid": bool(settings.mid_model),
        "top": bool(settings.orchestrator_model),
    }

    def _mark(tier: str, slug: str) -> str:
        origin = "pinned" if pinned[tier] else f"cost_mode={settings.cost_mode}"
        entry = "  ← entry" if ladder.entry == tier else ""
        return f"{slug}  [dim]({origin}){entry}[/dim]"

    table.add_row("default (Tier 1)", settings.default_model)
    table.add_row("tier: weak", _mark("weak", ladder.weak))
    table.add_row("tier: mid", _mark("mid", ladder.mid))
    table.add_row("tier: top (orchestrator)", _mark("top", ladder.top))
    table.add_row("fusion panel", "\n".join(settings.fusion_panel))
    table.add_row("fusion judge", settings.fusion_judge)
    table.add_row("fusion synthesizer", settings.fusion_synthesizer)
    console.print(table)
    console.print(
        "[dim]Any LiteLLM/OpenRouter slug fits any role — pin with "
        "`chimera models set <weak|mid|top> <slug>`, browse with `chimera models catalog`, "
        "or pick a mode with `chimera models set mode <cheap|balanced|premium|auto>`.[/dim]"
    )


@models_app.callback(invoke_without_command=True)
def models_main(ctx: typer.Context) -> None:
    """Show the active model assignment (tier ladder, cost mode, fusion roles)."""
    if ctx.invoked_subcommand is None:
        _render_models()


@models_app.command("catalog")
def models_catalog(
    tier: str = typer.Option(None, "--tier", help="Filter: weak, mid, or top."),
    vendor: str = typer.Option(None, "--vendor", help="Filter by vendor substring."),
) -> None:
    """Browse the curated multi-vendor catalog (suggestions — any slug works)."""
    from chimera.providers.catalog import entries

    tier_arg = tier if tier in ("weak", "mid", "top") else None
    if tier and tier_arg is None:
        console.print(f"[red]Unknown tier {tier!r}[/red] — use weak, mid, or top.")
        raise typer.Exit(code=1)
    found = entries(tier=tier_arg, vendor=vendor)  # type: ignore[arg-type]
    table = Table(title="Model catalog (data — verify prices before trusting)", header_style="bold")
    table.add_column("Tier")
    table.add_column("Model")
    table.add_column("Vendor")
    table.add_column("$/1M in→out")
    table.add_column("Tools")
    table.add_column("Ctx")
    table.add_column("Notes")
    for e in found:
        if e.input_per_m is None:
            price = "[dim]unknown[/dim]"
        elif e.input_per_m == 0.0 and e.output_per_m == 0.0:
            price = "[green]free[/green]"
        else:
            price = f"{e.input_per_m:g} → {e.output_per_m:g}"
        table.add_row(
            e.tier, e.slug, e.vendor, price, "yes" if e.tools else "no", f"{e.context_k}k", e.notes
        )
    console.print(table)
    console.print(
        "[dim]The catalog is curated data, not a restriction — any LiteLLM/OpenRouter "
        "slug can occupy any role.[/dim]"
    )


_MODELS_ROLE_ENV = {
    "weak": "CHIMERA_WEAK_MODEL",
    "mid": "CHIMERA_MID_MODEL",
    "top": "CHIMERA_ORCHESTRATOR_MODEL",
    "orchestrator": "CHIMERA_ORCHESTRATOR_MODEL",
    "mode": "CHIMERA_COST_MODE",
}


@models_app.command("set")
def models_set(
    role: str = typer.Argument(..., help="weak | mid | top (alias: orchestrator) | mode"),
    value: str = typer.Argument(
        ..., help="A model slug (any vendor), 'auto' to unpin, or a cost mode for 'mode'."
    ),
) -> None:
    """Pin a tier to a model (or set the cost mode). Explicit pins always beat the mode."""
    import os

    from chimera.providers.catalog import COST_MODES

    key = _MODELS_ROLE_ENV.get(role.lower())
    if key is None:
        console.print(f"[red]Unknown role {role!r}[/red] — use weak, mid, top, or mode.")
        raise typer.Exit(code=1)
    if role.lower() == "mode" and value not in COST_MODES:
        console.print(
            f"[red]Unknown cost mode {value!r}[/red] — use cheap, balanced, premium, or auto."
        )
        raise typer.Exit(code=1)
    env_value = "" if value.lower() == "auto" and role.lower() != "mode" else value
    _set_env_var(Path.cwd() / ".env", key, env_value)
    if env_value:
        os.environ[key] = env_value
    else:
        os.environ.pop(key, None)
    get_settings.cache_clear()
    shown = env_value or "auto (cost mode decides)"
    console.print(f"[green]Set[/green] {key}={shown}")
    _render_models()


app.add_typer(models_app, name="models")


profile_app = typer.Typer(help="Persistent user profile — the assistant's stable, cacheable preamble.")


@profile_app.command("show")
def profile_show() -> None:
    """Show the stored profile and the exact preamble sessions will receive."""
    from chimera.interface.profile import load_profile, profile_path, render_profile

    settings = get_settings()
    stored = load_profile(profile_path(settings.home))
    if stored.is_empty():
        console.print(
            "[dim]No profile yet — add facts with "
            "`chimera profile set <preference|project|context> \"...\"` "
            "or `chimera profile set name \"...\"`.[/dim]"
        )
        return
    console.print(Panel.fit(render_profile(stored), title="session preamble (stable prefix)"))


@profile_app.command("set")
def profile_set(
    kind: str = typer.Argument(..., help="name | preference | project | context"),
    value: str = typer.Argument(..., help="The fact to store."),
) -> None:
    """Add a profile fact (name replaces; the list kinds append with dedup)."""
    from chimera.interface.profile import load_profile, profile_path, save_profile

    settings = get_settings()
    path = profile_path(settings.home)
    stored = load_profile(path)
    if kind.lower() == "name":
        stored.name = value.strip()
        changed = True
    else:
        changed = stored.add(kind, value)
    if not changed:
        console.print(f"[yellow]Nothing stored[/yellow] — unknown kind {kind!r} or duplicate value.")
        raise typer.Exit(code=1)
    save_profile(path, stored)
    console.print(f"[green]Stored[/green] {kind.lower()}: {value.strip()}")


@profile_app.command("forget")
def profile_forget(
    value: str = typer.Argument(..., help="The exact fact to remove (or 'name' to clear the name)."),
) -> None:
    """Remove a stored fact."""
    from chimera.interface.profile import load_profile, profile_path, save_profile

    settings = get_settings()
    path = profile_path(settings.home)
    stored = load_profile(path)
    if value.strip().lower() == "name" and stored.name:
        stored.name = ""
        removed = True
    else:
        removed = stored.forget(value)
    if not removed:
        console.print(f"[yellow]Not found:[/yellow] {value!r}")
        raise typer.Exit(code=1)
    save_profile(path, stored)
    console.print(f"[green]Forgot[/green] {value.strip()!r}")


app.add_typer(profile_app, name="profile")


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
    cascade: bool = typer.Option(
        False, "--cascade", help="Tiered routing: weak -> gate -> mid -> gate -> fusion (cheap by default)."
    ),
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
    if cascade or settings.cascade:
        backend = _cascade_backend(gateway, settings)
    elif fuse:
        from chimera.fusion import FusionEngine, RoutedBackend

        backend = RoutedBackend(gateway, FusionEngine(gateway))
    agent = Agent(backend, default_registry(Path(workspace)), AgentConfig(model=model, max_steps=max_steps))
    mem = None if no_memory else _memory_manager()
    session = ChatSession(
        agent, memory=mem, graph=_recall_graph(mem), profile=_session_profile(mem)
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
def assist(
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
    no_cascade: bool = typer.Option(
        False, "--no-cascade", help="Disable tiered routing (single default model instead)."
    ),
) -> None:
    """Your daily-driver assistant: cheap by default, escalates when it must.

    Assist = chat with the second-brain defaults ON: the tier cascade routes
    chit-chat to cheap models and escalates hard asks; your persistent profile
    (chimera profile) is the stable preamble; memory, nudges and end-of-session
    consolidation are active. On exit it prints the session cost receipt —
    tier distribution + measured tokens — so 'cheap by default' is a number.
    """
    import time as _time

    from chimera.core import Agent, AgentConfig
    from chimera.fusion.route_log import format_route_summary, load_routes, summarize_routes
    from chimera.interface import ChatSession
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    session_start = _time.time()
    routes_path = Path(settings.home) / "routes.jsonl"
    gateway = LLMGateway()
    backend: SupportsComplete = gateway if no_cascade else _cascade_backend(gateway, settings)
    agent = Agent(backend, default_registry(Path(workspace)), AgentConfig(model=model, max_steps=max_steps))
    # Second-brain defaults: memory + graph + profile preamble always on (unless opted out).
    mem = None if no_memory else _memory_manager()
    session = ChatSession(
        agent, memory=mem, graph=_recall_graph(mem), profile=_session_profile(mem)
    )
    skill_names = _learned_skill_labels(settings)

    def _session_receipt() -> None:
        records = [r for r in load_routes(routes_path) if r.ts >= session_start]
        if records:
            console.print(
                Panel.fit(format_route_summary(summarize_routes(records)), title="session receipt")
            )

    ladder = settings.tier_ladder()
    console.print(
        "[bold]Chimera assist[/bold] — your right-hand, cheap by default. "
        f"[dim]tiers: {ladder.weak.split('/')[-1]} → {ladder.mid.split('/')[-1]} → fusion "
        f"(entry: {ladder.entry})[/dim]\n"
        "[cyan]/task <hard ask>[/cyan] full-power route, [cyan]/profile <kind>: <fact>[/cyan] remember, "
        "[cyan]/reset[/cyan] clear, [cyan]/exit[/cyan] quit."
    )
    nudged: set[str] = set()
    skill_nudged: set[str] = set()
    while True:
        try:
            message = console.input("[bold green]you ›[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings)
            _session_receipt()
            break
        if not message:
            continue
        if message in ("/exit", "/quit", "/q"):
            console.print("[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings)
            _session_receipt()
            break
        if message == "/reset":
            session.reset()
            console.print("[dim]context cleared[/dim]")
            continue
        if message.startswith("/profile"):
            # "/profile preference: answer in PT-BR" (kinds: preference|project|context|name)
            from chimera.interface.profile import load_profile, profile_path, save_profile

            body = message[len("/profile") :].strip()
            kind, sep, value = body.partition(":")
            if not sep or not value.strip():
                console.print("[dim]usage: /profile <preference|project|context|name>: <fact>[/dim]")
                continue
            path = profile_path(settings.home)
            stored = load_profile(path)
            if kind.strip().lower() == "name":
                stored.name, changed = value.strip(), True
            else:
                changed = stored.add(kind.strip(), value.strip())
            if changed:
                save_profile(path, stored)
                session.profile = _session_profile(mem)  # takes effect next turn
                console.print(f"[green]stored[/green] {kind.strip()}: {value.strip()}")
            else:
                console.print("[yellow]not stored (unknown kind or duplicate)[/yellow]")
            continue
        if message.startswith("/task"):
            # Full-power route for a hard ask: fusion-forced, one shot, no cascade climb.
            task_text = message[len("/task") :].strip()
            if not task_text:
                console.print("[dim]usage: /task <the hard ask>[/dim]")
                continue
            from chimera.fusion import FusionEngine

            try:
                with console.status("[dim]full-power (fusion)…[/dim]"):
                    fused = FusionEngine(gateway).complete(
                        [{"role": "user", "content": task_text}]
                    )
                console.print(f"[bold magenta]chimera ›[/bold magenta] {fused.content}")
            except Exception as exc:  # noqa: BLE001 — keep the REPL alive
                console.print(f"[red]error: {exc}[/red]")
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
                        f"[dim] → /profile preference: {fact}[/dim]"
                    )
        _emit_skill_nudges(session, skill_names, skill_nudged)


@app.command()
def tui(
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
    stream: bool = typer.Option(
        True, "--stream/--no-stream", help="Live token streaming (single-model path only)."
    ),
) -> None:
    """Launch the full-screen TUI — your right-hand. Requires a key."""
    from chimera.core import Agent, AgentConfig
    from chimera.interface import ChatSession
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    try:
        from chimera.tui.app import ChimeraTUI
    except ImportError:  # Textual is a base dep, but degrade gracefully if the install was slimmed.
        console.print(
            "[yellow]Textual isn't installed — falling back to 'chimera chat'. "
            "Install it with: pip install textual[/yellow]"
        )
        return chat(model=model, max_steps=max_steps, workspace=workspace, fuse=fuse, no_memory=no_memory)

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
        agent, memory=mem, graph=_recall_graph(mem), profile=_session_profile(mem)
    )
    ChimeraTUI(
        session, model_label=model or settings.default_model, stream=stream, fuse=fuse
    ).run()


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
        token=settings.server_token,
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


def _bind_app_socket(host: str, port: int) -> tuple[Any, int]:
    """Bind the app's listening socket, falling back to a free port if ``port`` is taken.

    Returns the bound socket (handed straight to uvicorn so there is no close-then-rebind race) and
    the actual port. ``port=0`` asks the OS for any free port. A fixed port that is already in use
    no longer crashes the app — it drops to an OS-assigned free port (so a second `chimera app`, or a
    Tauri sidecar, just works). No ``SO_REUSEADDR`` on purpose: on Windows that would let the bind
    succeed on a port another server already holds, defeating the busy-detection.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError:
        if port == 0:  # asked for any free port and still failed — surface it
            sock.close()
            raise
        sock.bind((host, 0))  # requested port busy → OS picks a free one
    return sock, sock.getsockname()[1]


@app.command(name="app")
def desktop_app(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host (localhost by default)."),
    port: int = typer.Option(8765, "--port", help="Bind port (0 = any free port; a busy port falls back to free)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route turns through fusion (no token streaming)."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the app in your browser."),
    emit_port_file: str = typer.Option(
        None,
        "--emit-port-file",
        help="Write the final http://host:port URL to this file once bound (for a parent/sidecar).",
    ),
) -> None:
    """Run the Chimera Desktop app: the HTTP+SSE API + the built React UI (needs the 'desktop' extra).

    Serves the same-origin SPA (``apps/desktop/dist``) and a streaming chat API over the real agent
    stack. Install with ``pip install 'chimera-agent[desktop]'`` and build the UI once with
    ``pnpm --dir apps/desktop build``.
    """
    try:
        import uvicorn

        from chimera.api import build_api_app
    except ImportError:
        console.print(
            "[red]The desktop app needs the 'desktop' extra.[/red]\n"
            "  pip install 'chimera-agent[desktop]'"
        )
        raise typer.Exit(code=1) from None

    from chimera.core import Agent, AgentConfig
    from chimera.interface import ChatSession
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.has_any_key():
        # Unlike run/solve/fuse (which need a model to do their job and stay strict), the desktop app
        # can BOOT keyless: LLMGateway() below is lazy (no model call), and the UI opens a first-run
        # setup screen that adds + live-tests a key from the browser. So notify and CONTINUE, don't exit.
        console.print(
            "[yellow]No provider key yet[/yellow] — the app opens a setup screen; "
            "add a key there to start chatting."
        )

    llm = LLMGateway()
    from chimera.fusion import FusionEngine, RoutedBackend

    # Always available for the per-turn "Fuse this turn" toggle (cheap to construct; runs only on
    # request). Reused as the fusion arm of RoutedBackend when the whole session runs under --fuse.
    fuse_backend = FusionEngine(llm)
    backend: SupportsComplete = llm
    if settings.cascade:
        # Honor the Settings "Cascade" toggle: tiered routing (weak -> mid -> fusion).
        backend = _cascade_backend(llm, settings)
    elif fuse:
        backend = RoutedBackend(llm, fuse_backend)

    workspace_path = Path(workspace)
    shared_memory = None if no_memory else _memory_manager()
    shared_graph = _recall_graph(shared_memory)
    shared_profile = shared_memory.profile() if shared_memory is not None else ""

    # Opt-in MCP autoload: connect the configured MCP servers ONCE at app start and reuse their tools
    # across sessions. Off by default (fast, no subprocess). A broken server is skipped gracefully so
    # it can never break boot; toggling this needs a restart to take effect. Connect eagerly here (not
    # per-session) so the subprocesses aren't respawned for every new chat.
    mcp_connectors = None
    if settings.mcp_autoload:
        from chimera.integrations import ConnectorRegistry, MCPConnector, StdioMCPSession
        from chimera.integrations.mcp_config import load_servers

        mcp_connectors = ConnectorRegistry()
        for cfg in load_servers(settings.home / "mcp.json"):
            try:
                sess = StdioMCPSession(
                    cfg.command, cfg.args or None, cfg.env or None, connect_timeout=10.0
                ).start()
                mcp_connectors.register(MCPConnector(cfg.name, sess, name_prefix=f"{cfg.name}_"))
            except Exception as exc:  # noqa: BLE001 — a broken server must never break app boot
                console.print(f"[yellow]MCP: skipping '{cfg.name}' ({type(exc).__name__})[/yellow]")
        loaded = len(mcp_connectors.names())
        console.print(f"[dim]MCP autoload: {loaded} server(s) connected[/dim]")

    def factory() -> ChatSession:
        registry = default_registry(workspace_path)
        if mcp_connectors is not None:
            mcp_connectors.into_tool_registry(registry)  # MCP tools alongside the builtins
        runner = Agent(backend, registry, AgentConfig(model=model, max_steps=max_steps))
        return ChatSession(runner, memory=shared_memory, graph=shared_graph, profile=shared_profile)

    # The built SPA, if present, is served same-origin (no CORS). Absent = API-only (dev uses Vite).
    # Prefer a source-checkout build (live `pnpm build` output); fall back to the copy bundled in the
    # wheel (chimera/_desktop_dist) so a pip-installed `[desktop]` user gets the UI, not just the API.
    _dev_dist = Path(__file__).resolve().parents[2] / "apps" / "desktop" / "dist"
    _pkg_dist = Path(__file__).resolve().parent.parent / "_desktop_dist"
    dist = _dev_dist if (_dev_dist / "index.html").exists() else _pkg_dist
    static_dir = dist if (dist / "index.html").exists() else None
    api = build_api_app(
        factory,
        settings=settings,
        static_dir=static_dir,
        fuse_backend=fuse_backend,
        workspace=workspace_path,
    )

    # Bind BEFORE announcing so the URL reflects the real port (a busy 8765 falls back to a free one
    # instead of crashing). The bound socket is handed to uvicorn, so there is no close-then-rebind gap.
    sock, port = _bind_app_socket(host, port)
    url = f"http://{host}:{port}"
    if emit_port_file:  # discovery channel for a parent process (the Tauri sidecar reads this)
        Path(emit_port_file).write_text(url, encoding="utf-8")
    ui_note = "" if static_dir is not None else "  [yellow](UI not built — API only; run 'pnpm --dir apps/desktop build')[/yellow]"
    console.print(f"[bold]Chimera Desktop[/bold] on {url}  [dim](API at /api). Ctrl+C to stop.[/dim]{ui_note}")
    if open_browser and static_dir is not None:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # Server.run(sockets=[...]) uses our already-bound socket (uvicorn.run rebinds host/port itself).
    uvicorn.Server(uvicorn.Config(api, log_level="warning")).run(sockets=[sock])


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
    return WhatsAppWebhook(
        sender, settings.whatsapp_verify_token, gateway.on_message,
        app_secret=settings.whatsapp_app_secret,
    )


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

        # Pick up webhook jobs added via `cron add --webhook` after the server started — the cron
        # daemon reloads each tick, but this handler holds a frozen store, so without this a newly
        # registered hook would silently do nothing until a restart.
        scheduler.store.reload_if_changed()
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


@app.command(name="maturity")
def maturity(
    tests_dir: str = typer.Option("tests", "--tests", help="Path to the tests directory (the evidence base)."),
) -> None:
    """Render the maturity scorecard: surfaces × coverage-IDs proven by real tests."""
    from chimera.eval.maturity import format_scorecard, score_repo

    card = score_repo(Path(tests_dir))
    console.print(format_scorecard(card))


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
    if verdict != "PASS":
        raise typer.Exit(code=1)  # a REGRESSION verdict must fail the process so CI catches it


@app.command()
def orchestrate(
    task: str = typer.Argument(..., help="The task (read-heavy multi-part tasks benefit most)."),
    max_workers: int = typer.Option(4, "--max-workers", help="Parallel worker cap."),
    budget: int = typer.Option(None, "--budget", help="Token budget per delegation (default: settings)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show classification + decomposition + estimate; zero worker spend."
    ),
    verify_model: str = typer.Option(
        None, "--verify-model", help="Model slug for the spot-check auditor (a DISTINCT/cross-provider model that grades a worker's summary against its raw output). Default: the weak tier."
    ),
) -> None:
    """Hierarchical run: top model decomposes/synthesizes, budgeted mid workers execute.

    Write-shaped and trivial tasks FALL BACK to the single-agent path by design
    (the evidence says multi-agent loses there); the fallback is logged with its
    counterfactual so `chimera delegations` shows the decision.
    """
    from chimera.fusion import FusionEngine
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.budget import EffortPolicy
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    ladder = settings.tier_ladder()
    per_delegation = budget or settings.delegation_budget
    gateway = LLMGateway()
    from chimera.evolution import build_evolution_context

    orchestrator = HierarchicalOrchestrator(
        gateway,
        weak_model=ladder.weak,
        mid_model=ladder.mid,
        top_model=ladder.top,
        store=ArtifactStore(Path(settings.home) / "artifacts"),
        verifier_model=verify_model,
        fusion=FusionEngine(gateway),
        receipts_path=Path(settings.home) / "delegations.jsonl",
        config=HierarchyConfig(
            max_workers=max_workers,
            effort=EffortPolicy(complex_budget=per_delegation),
        ),
        # M19-A4: read recalled facts/cards into synthesis + record the run (telemetry only, no
        # skill distillation — a fan-out has no verify-or-revert signal).
        evolution=build_evolution_context(
            settings, gateway, None, home=settings.home,
            evolve_skills=False, include_memory=True,
        ),
    )
    try:
        if dry_run:
            plan = orchestrator.dry_run(task)
            for key, value in plan.items():
                console.print(f"[dim]{key}[/dim]: {value}")
            return
        result = orchestrator.run(task)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(result.answer)
    tag = "[yellow]fell back to single-agent[/yellow]" if result.fell_back else (
        f"[green]{len(result.envelopes)} worker(s)[/green]"
    )
    tokens = f"{result.total_tokens} tokens" if result.total_tokens is not None else "tokens unknown"
    line = f"\n[dim]shape={result.shape} · {tag} · {tokens}"
    if result.counterfactual_tokens:
        line += f" · counterfactual inline ≈ {result.counterfactual_tokens} tokens"
    console.print(line + "[/dim]")


@app.command()
def brief(
    recipe: str = typer.Option(
        "examples/morning_brief/brief.yaml", "--recipe", help="Brief recipe (YAML with topics)."
    ),
    out: str = typer.Option(None, "--out", help="Write the digest to this file (default: print only)."),
    max_workers: int = typer.Option(4, "--max-workers", help="Parallel research workers."),
) -> None:
    """Morning brief: parallel topic research through the hierarchy, one synthesized digest.

    The recipe IS the decomposition (no top-model decompose call). Delegation
    receipts land in <home>/delegations.jsonl — `chimera delegations` shows what
    the brief cost vs the inline counterfactual, measured.
    """
    from chimera.fusion import FusionEngine
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.brief import brief_task, load_brief, specs_from_brief
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    try:
        loaded = load_brief(Path(recipe))
    except (ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    ladder = settings.tier_ladder()
    gateway = LLMGateway()
    from chimera.evolution import build_evolution_context

    orchestrator = HierarchicalOrchestrator(
        gateway,
        weak_model=ladder.weak,
        mid_model=ladder.mid,
        top_model=ladder.top,
        store=ArtifactStore(Path(settings.home) / "artifacts"),
        fusion=FusionEngine(gateway),
        receipts_path=Path(settings.home) / "delegations.jsonl",
        config=HierarchyConfig(max_workers=max_workers),
        # M19-A4: a recipe brief is a production path — read recalled facts + record the run.
        evolution=build_evolution_context(
            settings, gateway, None, home=settings.home,
            evolve_skills=False, include_memory=True,
        ),
    )
    specs = specs_from_brief(loaded, max_tokens=settings.delegation_budget)
    console.print(f"[dim]{loaded.name}: {len(specs)} topic(s) in parallel on {ladder.mid}[/dim]")
    try:
        result = orchestrator.run_prepared(brief_task(loaded), specs)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(Panel.fit(result.answer, title=loaded.name))
    if out:
        Path(out).write_text(result.answer + "\n", encoding="utf-8")
        console.print(f"[green]Written[/green] {out}")
    tokens = f"{result.total_tokens} tokens" if result.total_tokens is not None else "tokens unknown"
    line = f"[dim]{tokens} measured"
    if result.counterfactual_tokens:
        line += f" · inline counterfactual ≈ {result.counterfactual_tokens} tokens"
    console.print(line + " · details: chimera delegations[/dim]")


@app.command()
def delegations(
    path: str = typer.Option(None, "--path", help="Receipts file (default: <home>/delegations.jsonl)."),
) -> None:
    """Measured vs counterfactual across delegations — what the hierarchy actually saved."""
    from chimera.orchestration.receipts import (
        format_delegation_summary,
        load_delegations,
        summarize_delegations,
    )

    settings = get_settings()
    receipts_path = Path(path) if path else Path(settings.home) / "delegations.jsonl"
    receipts = load_delegations(receipts_path)
    console.print(format_delegation_summary(summarize_delegations(receipts)))


@app.command(name="cascade-bench")
def cascade_bench(
    tasks: str = typer.Option("hard", "--tasks", help="Task suite: hard | demo."),
) -> None:
    """Four-arm bench: weak-only vs mid-only vs cascade vs fusion. Calls real models.

    Published criterion (stated up front): cascade >= mid-only pass rate at materially
    lower tokens-per-pass. The number reported is whatever is measured.
    """
    from chimera.eval.cascade_bench import ARMS, run_cascade_bench
    from chimera.eval.continuous import demo_tasks
    from chimera.eval.hard import hard_tasks
    from chimera.fusion import FusionEngine
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    ladder = settings.tier_ladder()
    suite = hard_tasks() if tasks == "hard" else demo_tasks()
    gateway = LLMGateway()
    try:
        report = run_cascade_bench(
            gateway, FusionEngine(gateway), suite,
            weak=ladder.weak, mid=ladder.mid, entry=ladder.entry,
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"cascade bench — {tasks} ({len(report.rows)} tasks)")
    table.add_column("task")
    for arm in ARMS:
        table.add_column(arm, justify="center")
        table.add_column("tok", justify="right")
    for row in report.rows:
        cells: list[str] = [row.task_id]
        for arm in ARMS:
            cells.append("[green]ok[/green]" if row.ok.get(arm) else "[red]x[/red]")
            tok = row.tokens.get(arm)
            cells.append(str(tok) if tok is not None else "-")
        table.add_row(*cells)
    console.print(table)
    summary = report.summary()
    for key, value in summary.items():
        console.print(f"[dim]{key}[/dim]: {value}")
    cascade_obj = summary.get("cascade_pass_rate")
    mid_obj = summary.get("mid_pass_rate")
    cascade_rate = cascade_obj if isinstance(cascade_obj, int | float) else 0.0
    mid_rate = mid_obj if isinstance(mid_obj, int | float) else 0.0
    verdict = "PASS" if cascade_rate >= mid_rate else "BELOW MID"
    color = "green" if verdict == "PASS" else "red"
    console.print(f"\nverdict: [{color}]{verdict}[/{color}] (cascade {cascade_rate:.0%} vs mid {mid_rate:.0%})")
    if verdict != "PASS":
        raise typer.Exit(code=1)  # a BELOW-MID verdict must fail the process so CI catches it


def _run_multistep_hierarchy_bench(gateway: Any, model: str, only: set[str], out: str | None) -> None:
    """Multi-step hierarchy A/B: one growing context vs per-step scoped workers, over large docs.

    The regime where the hierarchy actually saves tokens — the single agent re-sends every document on
    every turn (cost ~ Q·ΣΣdocs), scoped workers pay each doc ~once. Also prices the MEASURED cache
    reduction via the caching model (`cache_cost`) when the provider reports cache accounting.
    """
    import json as _json

    from chimera.eval.cache_cost import dollar_cost, measured_dollar_reduction
    from chimera.eval.hierarchy_ab import ArmOutcome, format_token_report, run_hierarchy_ab
    from chimera.eval.hierarchy_multistep import (
        MultiStepTask,
        multistep_tasks,
        run_baseline,
        run_scoped,
    )
    from chimera.eval.paired import format_report
    from chimera.fusion.receipts import resolve_price
    from chimera.orchestration.receipts import estimate_tokens

    tasks = [t for t in multistep_tasks() if not only or t.id in only]
    if not tasks:
        console.print("[red]No multi-step tasks matched.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[dim]model={model} tasks={len(tasks)} (multi-step, large docs)[/dim]")
    cache = {"base_cr": 0, "base_cw": 0, "scoped_cr": 0, "scoped_cw": 0, "base_reg": 0, "scoped_reg": 0}

    def complete(messages: list[Any]) -> tuple[str, int, int, int]:
        result = gateway.complete(messages, model=model)
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if tokens == 0:
            tokens = estimate_tokens("".join(str(m["content"]) for m in messages) + (result.content or ""))
        return (result.content or "", tokens, result.cache_read_tokens or 0, result.cache_write_tokens or 0)

    def baseline(task: MultiStepTask) -> ArmOutcome:
        run = run_baseline(task, complete)
        cache["base_cr"] += run.cache_read
        cache["base_cw"] += run.cache_write
        cache["base_reg"] += run.tokens - run.cache_read
        return ArmOutcome(passed=run.passed, tokens=run.tokens)

    def treatment(task: MultiStepTask) -> ArmOutcome:
        run = run_scoped(task, complete)
        cache["scoped_cr"] += run.cache_read
        cache["scoped_cw"] += run.cache_write
        cache["scoped_reg"] += run.tokens - run.cache_read
        return ArmOutcome(passed=run.passed, tokens=run.tokens)

    report = run_hierarchy_ab(
        tasks, restore=lambda _t: None, baseline=baseline, treatment=treatment,
        baseline_name="single-context", treatment_name="scoped",
    )
    console.print(format_report(report.paired))
    console.print(format_token_report(report))

    # Turn the "caching narrows the win" caveat into a measured number when the provider reports it.
    price = resolve_price(model)
    total_cr = cache["base_cr"] + cache["scoped_cr"]
    if price is not None and total_cr > 0:
        base_usd = dollar_cost(regular_input=cache["base_reg"], output=0, cache_read=cache["base_cr"],
                               input_per_m=price.input_per_m, output_per_m=price.output_per_m)
        scoped_usd = dollar_cost(regular_input=cache["scoped_reg"], output=0, cache_read=cache["scoped_cr"],
                                 input_per_m=price.input_per_m, output_per_m=price.output_per_m)
        console.print(
            f"[dim]measured dollar reduction (cache reads priced 0.1x): "
            f"{measured_dollar_reduction(base_usd, scoped_usd):+.1%} "
            f"(token reduction {report.summary().get('token_reduction')})[/dim]"
        )
    else:
        console.print(
            "[dim]no cache tokens reported — $ == tokens here; caching narrows the win only where the "
            "provider caches the single agent's repeated context.[/dim]"
        )
    if out:
        Path(out).write_text(_json.dumps(report.summary(), indent=2, default=str), encoding="utf-8")
        console.print(f"[dim]wrote {out}[/dim]")


@app.command(name="hierarchy-bench")
def hierarchy_bench(
    model: str = typer.Option(None, "--model", "-m", help="Mid/worker model — BOTH arms use it, to isolate orchestration. Defaults to the tier ladder's mid."),
    top_model: str = typer.Option(None, "--top-model", help="Top model for synthesis. Defaults to --model (same family keeps the isolation)."),
    tasks: str = typer.Option("", "--tasks", help="Comma-separated task ids to filter (default: all 10 synthetic tasks)."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max concurrent workers in the hierarchy arm."),
    out: str = typer.Option(None, "--out", help="Write the JSON summary to this path."),
    multistep: bool = typer.Option(False, "--multistep", help="Run the MULTI-STEP suite instead (single growing context vs per-step scoped workers, over large docs) — the regime where the hierarchy actually saves tokens. Also reports a caching-aware dollar reduction."),
) -> None:
    """Paired A/B: single-agent (all docs inline) vs the hierarchy (one worker per doc). Calls real models.

    Both arms run on the SAME model so the comparison isolates the ORCHESTRATION (minimal-context
    scoping + budgets + contracts), not model strength. Quality = paired McNemar/Wilson (the only place
    "significant" appears); tokens = measured totals per arm, with no significance claim on cost.

    `--multistep` switches to the companion suite where the token crossover lives: a single agent
    re-sends every document on every turn (cost grows with turns), while scoped workers pay each doc
    ~once — and prices the measured cache reduction via the caching model.
    """
    import json as _json
    import tempfile

    from chimera.eval.hierarchy_ab import (
        ArmOutcome,
        HierarchyTask,
        baseline_prompt,
        format_token_report,
        make_specs,
        run_hierarchy_ab,
        synthetic_tasks,
    )
    from chimera.eval.paired import format_report
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.envelope_verify import EnvelopeVerifier
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
    from chimera.orchestration.receipts import estimate_tokens
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    ladder = settings.tier_ladder()
    mid = model or ladder.mid
    top = top_model or mid
    only = {t.strip() for t in tasks.split(",") if t.strip()}
    gateway = LLMGateway()

    if multistep:
        try:
            _run_multistep_hierarchy_bench(gateway, mid, only, out)
        except MissingCredentialsError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        return

    suite = [t for t in synthetic_tasks() if not only or t.id in only]
    if not suite:
        console.print(f"[red]No tasks matched {tasks!r}.[/red]")
        raise typer.Exit(code=1)

    workdir = Path(tempfile.mkdtemp(prefix="hierarchy-bench-"))
    console.print(f"[dim]model={mid} top={top} tasks={len(suite)} artifacts={workdir}[/dim]")

    def baseline(task: HierarchyTask) -> ArmOutcome:
        prompt = baseline_prompt(task)
        result = gateway.complete([{"role": "user", "content": prompt}], model=mid)
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if tokens == 0:  # provider reported nothing — estimate, and the token row says so
            tokens = estimate_tokens(prompt + (result.content or ""))
        return ArmOutcome(passed=task.check(result.content or ""), tokens=tokens)

    def treatment(task: HierarchyTask) -> ArmOutcome:
        store = ArtifactStore(workdir / task.id)
        orchestrator = HierarchicalOrchestrator(
            gateway,
            weak_model=mid,
            mid_model=mid,
            top_model=top,
            store=store,
            verifier=EnvelopeVerifier(store=store, backend=None, spot_rate=0.0),
            config=HierarchyConfig(max_workers=max_workers, fuse_final=False),
        )
        result = orchestrator.run_prepared(task.question, make_specs(task))
        return ArmOutcome(passed=task.check(result.answer or ""), tokens=result.total_tokens)

    try:
        report = run_hierarchy_ab(suite, restore=lambda _t: None, baseline=baseline, treatment=treatment)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(format_report(report.paired))
    console.print(format_token_report(report))
    if out:
        Path(out).write_text(_json.dumps(report.summary(), indent=2, default=str), encoding="utf-8")
        console.print(f"[dim]wrote {out}[/dim]")


@app.command(name="skillcard-bench")
def skillcard_bench(
    tasks: str = typer.Option("hard", "--tasks", help="Task suite: hard | big | demo. 'big' = 24 traps for a tighter paired CI."),
    k: int = typer.Option(1, "--k", help="How many cards to retrieve per task."),
    min_overlap: int = typer.Option(
        2, "--min-overlap", help="Relevance gate: inject a card only on >= N shared query terms (0=off)."
    ),
    max_lines: int = typer.Option(3, "--max-lines", help="Render budget: max lines per injected card."),
    use_store: bool = typer.Option(
        False, "--use-store", help="Bench your own learned cards (skills.json) instead of the demo set."
    ),
) -> None:
    """A/B reasoning with vs without injected TRS skill cards. Calls real models."""
    from chimera.eval.continuous import demo_tasks
    from chimera.eval.hard import hard_tasks, hard_tasks_plus
    from chimera.eval.skillcard_ab import demo_cards, run_skillcard_ab
    from chimera.evolution import SkillStore
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if tasks == "big":
        suite = hard_tasks_plus()
    elif tasks == "hard":
        suite = hard_tasks()
    else:
        suite = demo_tasks()
    if use_store:
        cards = [c for c in SkillStore(settings.home / "skills.json").skills() if c.has_card()]
        if not cards:
            console.print("[red]No cards with content in skills.json — run some solves first, "
                          "or drop --use-store to bench the demo set.[/red]")
            raise typer.Exit(code=1)
    else:
        cards = demo_cards()

    try:
        report = run_skillcard_ab(
            LLMGateway(), suite, cards, k=k, min_overlap=min_overlap, max_lines=max_lines
        )
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

    # The registered M19-A1 default-flip gate: the paired McNemar accuracy CI lower bound >= 0 AND
    # token overhead < +50%. Reported so the flip decision is reproducible from the run, not by hand.
    paired = report.paired()
    lo, hi = paired.diff_ci
    tok = summary.get("token_delta_pct", 0.0)
    acc_ok, tok_ok = lo >= 0.0, tok < 50.0
    flip = "JUSTIFIED" if (acc_ok and tok_ok) else "NOT justified"
    fcolor = "green" if (acc_ok and tok_ok) else "yellow"
    console.print(
        f"[bold]A1 flip gate:[/bold] [{fcolor}]{flip}[/{fcolor}]  "
        f"(paired Δ {paired.delta * 100:+.1f}pp, 95% CI [{lo * 100:+.1f}%, {hi * 100:+.1f}%] "
        f"→ acc {'✓' if acc_ok else '✗'}; tokens {tok:+.1f}% → {'✓' if tok_ok else '✗'}; "
        f"n={paired.n}, discordant={paired.discordant})"
    )
    if verdict != "PASS":
        raise typer.Exit(code=1)  # a REGRESSION verdict must fail the process so CI catches it


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
    cascade: bool = typer.Option(
        False, "--cascade", help="Tiered routing: weak -> gate -> mid -> gate -> fusion (cheap by default)."
    ),
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
    gen_tests: bool = typer.Option(
        False, "--gen-tests", help="With no --verify: generate executable pytest grounded in the task's requirements and use it as the gate (catches wrong code the coverage grade rubber-stamps)."
    ),
    write_region: str = typer.Option(
        None, "--write-region", help="Comma-separated globs the file-writers may touch (e.g. 'src/**,*.py'). A write outside is refused — blocks an injected instruction from rewriting an unrelated file."
    ),
    probe_log: bool = typer.Option(
        False, "--probe-log", help="Log (arm, proxy=manager-judgment, reward=verified) per attempt to <home>/probe.jsonl for PROBE best-arm selection (see `chimera probe-select --from-log`). Needs --verify + a manager."
    ),
    normalize_task: bool = typer.Option(
        False, "--normalize-task", help="Reshape a long, rambling bug-report task into a salient-facts-first form (location/repro/expected-vs-actual/fix-hint) before planning. No-op on non-bug or short tasks."
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
        SpecTestGenerator,
        StrongVerifier,
        WorkspaceGuard,
    )
    from chimera.core.autonomous import AutonomousResult
    from chimera.core.verify import CommandVerifier
    from chimera.evolution import build_evolution_context
    from chimera.fusion.probe_log import ProbeLog as _ProbeLog
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.tools import default_registry

    settings = get_settings()

    # Human-in-the-loop envelope (LangGraph {accept, edit, respond, ignore}) over the taint-pause.
    # 'ignore' (deny) drops the run without touching a model (no key needed).
    if sum(bool(x) for x in (deny, approve, respond, edit)) > 1:
        console.print("[red]Use only one of --approve / --respond / --edit / --deny.[/red]")
        raise typer.Exit(code=1)
    if edit and not answer_text:
        # An edit finalizes the reviewed answer as-is; without --answer it would commit an empty one.
        console.print("[red]--edit requires --answer with the revised text.[/red]")
        raise typer.Exit(code=1)
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
    # --cascade (or CHIMERA_CASCADE): tiered routing weak -> gate -> mid -> gate -> fusion.
    # Takes precedence over --fuse (the cascade already has fusion as its top rung).
    if cascade or settings.cascade:
        backend = _cascade_backend(gateway, settings)
        from chimera.fusion import FusionEngine, RoutedBackend, RoutingPolicy

        escalate_backend = RoutedBackend(gateway, FusionEngine(gateway), RoutingPolicy(mode="always"))
    # --fuse (explicit) or CHIMERA_AUTO_FUSE (production default) both route the worker
    # through the cost-aware router, so deep/error-sensitive turns fuse and cheap/tool
    # turns stay single-model.
    elif fuse or settings.auto_fuse:
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

    # Collective (cross-model) skill evolution is meaningful whenever the run's reasoning peak is
    # fusion over a multi-model panel — true for BOTH --fuse and --cascade (the cascade's top rung is
    # the same fusion panel). Share the gate instead of tying it to --fuse alone (P2-cascade), so a
    # cascade run keeps the most transferable proposal across the panel, not a single-model one.
    panel_evolution = (fuse or cascade or settings.cascade) and len(settings.fusion_panel) >= 2

    # ACE playbook (--playbook): load the stored playbook once so it is injected into the run
    # and curated back afterwards. Kept outside _run_solve so the worktree path doesn't shadow it.
    stored_playbook = _load_playbook() if playbook else None

    def _run_solve(ws: Path) -> AutonomousResult:
        from chimera.tools.write_region import WriteRegion

        region = WriteRegion(write_region.split(","), ws) if write_region else None
        registry = default_registry(ws, write_region=region)
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

        # The six learning seams (experience, trajectories, memory, auto_evolver, cards, playbook)
        # assembled once by the shared factory (M19-A0), so the flywheel is a property of the agent
        # stack, not this one command. `memory`/`playbook` are injected — their construction pulls
        # CLI-local helpers. Behaviour-preserving: same seams, same conditions as the old inline block.
        evo = build_evolution_context(
            settings,
            gateway,
            model,
            home=settings.home,
            collect=collect,
            evolve_skills=not no_evolve_skills,
            panel_evolution=panel_evolution,
            audit=allow_audit,
            memory=None if no_remember else _memory_manager(),
            playbook=stored_playbook,
        )

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
            # --gen-tests also needs the extracted requirements, so it turns extraction on too.
            checklist=RequirementChecklist(gateway, model) if (checklist or gen_tests) else None,
            # Spec-grounded test generation (--gen-tests): with no --verify command, generate
            # executable pytest grounded in the extracted requirements and use it as the gate,
            # replacing the weak LLM coverage grade that rubber-stamps wrong code.
            spec_test_generator=SpecTestGenerator(gateway, model) if gen_tests else None,
            workspace=ws,
            # Independent strong verification (--strong-verify MODEL): a stronger judge grades
            # hard-turn (retried) results before they're accepted. Uses the same gateway, other model.
            strong_verifier=StrongVerifier(gateway, strong_verify) if strong_verify else None,
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
            # PROBE (M18-5): record (arm, cheap manager proxy, verified reward) per attempt.
            probe_log=_ProbeLog(settings.home / "probe.jsonl") if probe_log else None,
            guard=WorkspaceGuard(ws),
            # The six learning seams (experience, trajectories, memory, auto_evolver, cards, playbook)
            # from the shared factory above (M19-A0).
            **evo.apply_to(),
            spine_workspace=ws,
            on_event=_stream_sink if stream else None,
            # Durable execution (--thread): checkpoint the loop to SQLite so a crash can resume.
            checkpointer=RunCheckpointer(settings.home / "runs.db") if thread else None,
            # Run receipt: persist how this run proved its work (verify-or-revert per attempt) to an
            # append-only log the desktop "Runs" screen reads read-only. Best-effort — never fails a run.
            run_log=settings.home / "runs.jsonl",
            config=AutonomousConfig(
                max_attempts=max_attempts,
                use_planner=not no_plan,
                use_manager=not no_manager,
                normalize_task=normalize_task,
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


def _report_collusion(ledgers: dict[str, Any]) -> bool:
    """Run the aggregate cross-agent monitor over per-worker events; print findings. Returns True if
    any collusion was flagged.

    A per-worker monitor is blind to a split flow under fan-out (one worker fetches untrusted, a
    different worker sinks it — the fetch and the sink live in separate ledgers). The shared taint view
    now arms narrowing *live* for that flow; this monitor is the aggregate backstop (and the only place
    the fan-out-volume pattern is visible). No-op without ledgers.
    """
    if not ledgers:
        return False
    from chimera.governance import AggregateMonitor

    findings = AggregateMonitor().assess({name: led.events for name, led in ledgers.items()})
    if not findings:
        console.print("[dim]cross-agent monitor: no collusion signals across workers[/dim]")
        return False
    console.print("[yellow]⚠ cross-agent monitor flagged (review):[/yellow]")
    for finding in findings:
        agents = f" — agents: {', '.join(finding.agents)}" if finding.agents else ""
        console.print(f"  [yellow]- {finding.kind}[/yellow]: {finding.detail}{agents}")
    return True


@app.command(name="solve-batch")
def solve_batch(
    tasks: list[str] = _BATCH_TASKS_ARG,
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root (a git repo, to isolate)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per task."),
    max_attempts: int = typer.Option(2, "--max-attempts", help="Max verify-or-revert attempts per task."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max concurrent isolated workers."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    taint: bool = typer.Option(False, "--taint", help="Arm each worker's adaptive allowlist (dangerous-when-tainted tools require approval). The cross-agent collusion monitor runs regardless — it's always on for fan-out."),
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
    from chimera.governance import TaintLedger, ledger_registry
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

    # Per-worker capability ledgers, so the aggregate cross-agent monitor can see the whole fan-out
    # (a split exfiltration — one worker fetches untrusted, another sinks it — lives BETWEEN workers,
    # invisible to any single-worker monitor). ALWAYS ON for fan-out: recording is pure observability
    # and changes no behaviour; the monitor only escalates a review note at the end. --taint additionally
    # arms each worker's adaptive allowlist (dangerous-when-tainted tools require approval).
    ledgers: dict[str, TaintLedger] = {}
    # NO shared taint view here: solve-batch runs INDEPENDENT tasks in SEPARATE workspaces. Worker B
    # cannot see worker A's fetched content, so arming B's narrowing because A fetched (even benign)
    # content would block B's legitimate work for zero security benefit. Each worker's ledger is its
    # own; the AggregateMonitor still runs post-hoc for observability + the non-zero exit below. (The
    # live cross-worker gate is for crew-isolated, where workers collaborate on ONE task + workspace.)

    def make_runner(name: str, one_task: str) -> Callable[[Path], AutonomousResult]:
        def run(ws: Path) -> AutonomousResult:
            from chimera.tools import default_registry

            ledger = TaintLedger()
            ledgers[name] = ledger
            registry = ledger_registry(default_registry(ws), ledger, narrow_on_taint=taint)
            worker = Agent(backend, registry, AgentConfig(model=model, max_steps=max_steps))
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

    units = [(f"task{i + 1}", make_runner(f"task{i + 1}", task)) for i, task in enumerate(tasks)]
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
    colluded = _report_collusion(ledgers)
    # Under --taint the aggregate finding is a real consequence, not just a note: the run exits
    # non-zero so a caller/CI treats the fan-out as needing review before its merged output is trusted.
    if colluded and taint:
        console.print("[red]cross-agent collusion under --taint — exiting non-zero for review.[/red]")
        raise typer.Exit(code=2)
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
    taint: bool = typer.Option(False, "--taint", help="Arm each worker's adaptive allowlist (dangerous-when-tainted tools require approval). The cross-agent collusion monitor runs regardless — it's always on for fan-out."),
) -> None:
    """Tier-3: tool-using workers split ONE task, each in its own git worktree, verify-gated.

    Define workers with repeated --worker 'name:instruction'. Each runs a real agent loop
    (search/read/edit) against an isolated checkout; non-conflicting edits that pass --verify
    merge back, files two workers both changed are flagged as conflicts, and a worker whose
    check fails is rejected (its edits discarded). Needs a git repo to isolate.
    """
    from chimera.governance import SharedTaint, TaintLedger, ledger_registry
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

    # Per-worker capability ledgers for the aggregate cross-agent monitor — always on for fan-out
    # (pure observability; --taint additionally arms each worker's adaptive allowlist). ONE shared
    # taint view here (unlike solve-batch): crew workers COLLABORATE on a single task and merge into a
    # shared workspace, so untrusted content one worker fetched can flow to another — a fetch in any
    # worker arms the narrowing in all of them live, the cross-agent gate. (Batch tasks are
    # independent, so sharing there would only false-block; that's why it's crew-only.)
    ledgers: dict[str, Any] = {}
    shared_taint = SharedTaint()

    def make_factory(wname: str) -> Callable[[Path], Any]:
        def factory(ws: Path) -> Any:
            ledger = TaintLedger(shared=shared_taint)
            ledgers[wname] = ledger
            return ledger_registry(default_registry(ws), ledger, narrow_on_taint=taint)

        return factory

    workers = []
    for i, spec in enumerate(worker):
        name, sep, instruction = spec.partition(":")
        name = (name.strip() if sep else "") or f"worker{i + 1}"
        prompt = (instruction.strip() if sep else spec.strip()) or "Do your part of the task."
        workers.append(
            IsolatedWorker(Role(name, prompt), make_factory(name), max_steps=max_steps)
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
    colluded = _report_collusion(ledgers)
    if colluded and taint:
        console.print("[red]cross-agent collusion under --taint — exiting non-zero for review.[/red]")
        raise typer.Exit(code=2)
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


@app.command("skills-lifecycle")
def skills_lifecycle(
    apply: bool = typer.Option(False, "--apply", help="Actually promote/demote (default: dry-run preview)."),
    promote_min_uses: int = typer.Option(5, "--promote-min-uses", help="Provisional probation length."),
    promote_min_rate: float = typer.Option(0.7, "--promote-min-rate", help="Win rate to promote a provisional skill."),
    demote_min_uses: int = typer.Option(5, "--demote-min-uses", help="Uses before a skill can be demoted."),
    demote_max_rate: float = typer.Option(1 / 3, "--demote-max-rate", help="Win rate at/below which a skill is demoted."),
) -> None:
    """Run the measured skill-lifecycle loop (M18-4): promote proven provisionals, demote regressions.

    Decisions come from the store's MEASURED usage stats (never a model's self-report): a provisional
    skill that earns a high win rate over enough uses is promoted to active; a provisional that fails
    probation or an active skill whose win rate regresses is retired (kept for review). Dry-run by
    default; ``--apply`` closes the loop — cron it for a hands-off promote/demote cycle.
    """
    from chimera.evolution import SkillLifecyclePolicy, SkillStore

    store = SkillStore(get_settings().home / "skills.json")
    policy = SkillLifecyclePolicy(
        promote_min_uses=promote_min_uses, promote_min_rate=promote_min_rate,
        demote_min_uses=demote_min_uses, demote_max_rate=demote_max_rate,
    )
    decisions = policy.decide(store.stats())
    if not decisions.promote and not decisions.demote:
        console.print("[dim]No lifecycle changes — every skill is where the measured evidence puts it.[/dim]")
        return
    for name in decisions.promote:
        console.print(f"[green]promote[/green] {name}  (provisional -> active: proven)")
    for name in decisions.demote:
        console.print(f"[red]demote[/red]  {name}  (-> retired: failed probation / regressed)")
    if not apply:
        console.print("\n[dim]Dry-run. Pass --apply to commit the promote/demote decisions.[/dim]")
        return
    for name in decisions.promote:
        store.promote(name)
    for name in decisions.demote:
        store.retire(name)
    console.print(
        f"\n[green]Applied[/green] {len(decisions.promote)} promotion(s), {len(decisions.demote)} demotion(s)."
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

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    store = SkillStore(settings.home / "skills.json")
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
    from chimera.evolution.wiring import playbook_path

    return playbook_path(get_settings())


def _load_playbook() -> Playbook:
    from chimera.evolution.wiring import load_playbook

    return load_playbook(get_settings())


def _save_playbook(playbook: Playbook) -> None:
    from chimera.evolution.wiring import save_playbook

    save_playbook(get_settings(), playbook)


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

    if not get_settings().has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
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

    if not get_settings().has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
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

    if not Path(path).is_dir():
        # Without this a typo'd/wrong path scans to an empty result and exits 0 — a silent no-op
        # reported as success (and with --apply even writes an empty imported/ dir).
        console.print(f"[red]source path does not exist or is not a directory: {path}[/red]")
        raise typer.Exit(code=1)

    try:
        importer = get_importer(source, Path(path))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    target = Path(home) if home else get_settings().home
    if apply:
        from chimera.evolution.wiring import semantic_embed
        from chimera.memory import MemoryManager, MemoryStore, SqliteMemoryStore

        # Honor the configured backend + embedder, or the merge writes to a store the agent never
        # reads (json while it recalls from sqlite) — a silent no-op that reports success.
        _s = get_settings()
        _store = (
            SqliteMemoryStore(target / "memory.db")
            if _s.memory_backend == "sqlite"
            else MemoryStore(target / "memory.json")
        )
        manager = MemoryManager(_store, embed=semantic_embed(_s))
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
    command: str = typer.Option(..., "--command", "-c", help="The launch command (e.g. npx, uvx, python)."),
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
    cfg = McpServerConfig(name=name, command=command, args=list(arg or []), env=env_map)
    add_server(_mcp_path(), cfg)
    console.print(f"[green]added[/green] MCP server [cyan]{name}[/cyan] ({command})")


@mcp_app.command("list")
def mcp_list() -> None:
    """List configured MCP servers (name, command + args, env key names). No connect."""
    from chimera.integrations.mcp_config import load_servers

    servers = load_servers(_mcp_path())
    if not servers:
        console.print("[dim]no MCP servers configured — add one with `chimera mcp add`[/dim]")
        return
    table = Table(title="MCP servers", show_header=True, header_style="bold")
    for col in ("name", "command", "env"):
        table.add_column(col)
    for s in servers:
        cmd = " ".join([s.command, *s.args])
        table.add_row(s.name, cmd, ", ".join(sorted(s.env)) or "-")
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
    table = Table(title=f"{name}: {len(tools)} tool(s)", show_header=True, header_style="bold")
    table.add_column("tool")
    table.add_column("description")
    for tool in tools:
        table.add_row(tool["name"], tool["description"])
    console.print(table)


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


# --- memory subcommands -------------------------------------------------------

memory_app = typer.Typer(help="Curated long-term memory.", no_args_is_help=True)
app.add_typer(memory_app, name="memory")


def _semantic_embed() -> EmbedFn | None:
    """The gateway embedder when semantic memory is on, else None (keyword recall)."""
    from chimera.evolution.wiring import semantic_embed

    return semantic_embed(get_settings())


def _memory_manager() -> MemoryManager:
    from chimera.evolution.wiring import build_memory_manager

    return build_memory_manager(get_settings())


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

    # Only CLEAN memories feed the graph: entity-linked facts skip the keyword-similarity path that
    # tags a tainted fact "[unverified]", so a tainted fact recalled via the graph would otherwise
    # reach the prompt unlabeled. Excluding them keeps entity recall honest (they still recall via
    # search, which labels them).
    return build_graph([i.content for i in memory.store.all() if i.provenance == "clean"])


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
    apply: bool = typer.Option(
        False, "--apply", help="Actually delete. Default is a dry-run preview (no data lost)."
    ),
) -> None:
    """Prune low-value memory under a budget. Dry-run by default; persona/profile facts are never pruned."""
    mgr = _memory_manager()
    would_remove = mgr.prune(max_items, dry_run=True)
    if would_remove == 0:
        console.print("[dim]nothing to prune — memory is within budget[/dim]")
        return
    if not apply:
        # Deletion is irreversible; never remove memory on the plain command. Show the count and
        # require --apply, mirroring the reversible skills-retire's dry-run-by-default discipline.
        console.print(
            f"[yellow]{would_remove} low-value memory item(s) would be pruned[/yellow] "
            "(persona/profile facts are kept). Re-run with [bold]--apply[/bold] to delete."
        )
        return
    removed = mgr.prune(max_items)
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
    texts = [i.content for i in _memory_manager().store.all() if i.provenance == "clean"]
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


@evolve_app.command("refine")
def evolve_refine(
    skill_name: str = typer.Argument(..., help="Name of the learned skill to refine."),
    traj: str = typer.Option(None, "--traj", help="Trajectory JSONL (default: <home>/trajectories.jsonl)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model."),
    budget: int = typer.Option(20, "--budget", help="GEPA rollout budget."),
    min_reward: float = typer.Option(
        1.0, "--min-reward", help="Only mine trajectories at/above this reward (1.0 = verified)."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Persist the refined skill IF it passes the transfer gate."
    ),
) -> None:
    """GEPA-refine a skill from verified trajectories, gated on non-regressing transfer (M19-A5)."""
    from chimera.evolution import SkillStore, instances_from_trajectories, refine_skill
    from chimera.evolution.gepa import BackendExecutor, BackendReflector
    from chimera.providers import LLMGateway

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    store = SkillStore(settings.home / "skills.json")
    skill = store.get(skill_name)
    if skill is None:
        console.print(f"[red]no skill {skill_name!r} in the store[/red]")
        raise typer.Exit(code=1)
    instances = instances_from_trajectories(_collector(traj).all(), min_reward=min_reward)
    if not instances:
        console.print(
            "[yellow]no verified trajectories to refine on (need successful, productive runs).[/yellow]"
        )
        raise typer.Exit(code=1)
    # Disjoint holdout: every 3rd verified instance is held out (the same-capability transfer slice).
    holdout = instances[::3]
    tuned = [inst for i, inst in enumerate(instances) if i % 3 != 0]
    if not tuned:  # too few to split — refine on all, but then transfer is not measured (dry-run)
        tuned, holdout = instances, []
    gateway = LLMGateway()
    outcome = refine_skill(
        skill, tuned,
        executor=BackendExecutor(gateway, model), reflector=BackendReflector(gateway, model),
        holdout=holdout or None, budget=budget,
    )
    console.print(
        f"[bold]{skill_name}[/bold] refine: seed {outcome.result.seed_mean:.2f} -> "
        f"best {outcome.result.best_mean:.2f} ({outcome.result.rollouts} rollouts)"
    )
    console.print(f"[dim]{outcome.decision.reason}[/dim]")
    if outcome.promoted and apply:
        store.add(outcome.skill)
        console.print(
            f"[green]applied[/green] refined template -> {outcome.skill.name} v{outcome.skill.version}"
        )
    elif outcome.promoted:
        console.print("[yellow]promotable[/yellow] — re-run with --apply to persist.")
    else:
        console.print("[dim]not promoted (dry-run without a holdout, or the gate was not cleared).[/dim]")


@evolve_app.command("guard")
def evolve_guard(
    limit: int = typer.Option(0, "--limit", help="Limit demo tasks (0 = all)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    cost_drift_tol: float = typer.Option(
        None, "--cost-drift-tol", help="Also roll back if second-half mean cost exceeds first by more than this."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Retire the most recent skill IF a SIGNIFICANT regression is measured."
    ),
) -> None:
    """Watch evolution health; retract the most recent skill on a SIGNIFICANT regression (M19-A6)."""
    from chimera.eval import SingleModelSolver, demo_tasks, run_continuous
    from chimera.evolution import SkillStore, apply_rollback, assess_rollback
    from chimera.providers import LLMGateway

    settings = get_settings()
    if not settings.has_any_key():
        console.print("[red]No provider key configured. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    tasks = list(demo_tasks())
    if limit:
        tasks = tasks[:limit]
    report = run_continuous(SingleModelSolver(LLMGateway(), model), tasks)
    store = SkillStore(settings.home / "skills.json")
    recent = [s.name for s in store.skills(status="active")]
    decision = assess_rollback(report, recent_artifacts=recent, cost_drift_tol=cost_drift_tol)
    console.print(
        f"pass rate [cyan]{report.pass_rate:.0%}[/cyan]  "
        f"degradation [cyan]{report.degradation:+.0%}[/cyan] "
        f"(CI {report.degradation_ci()[0]:+.0%}..{report.degradation_ci()[1]:+.0%})"
    )
    console.print(f"[dim]{decision.reason}[/dim]")
    if decision.should_rollback and decision.target and apply:
        if apply_rollback(store, decision):
            console.print(
                f"[yellow]retired[/yellow] {decision.target} "
                f"(reversible: chimera skills-approve {decision.target})"
            )
    elif decision.should_rollback and decision.target:
        console.print(
            f"[yellow]would retire[/yellow] {decision.target} — re-run with --apply to act."
        )
    else:
        console.print("[green]healthy[/green] — nothing to roll back.")


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


@app.command("probe-select")
def probe_select(
    data: str = typer.Argument(None, help='JSON: {"arm": [[proxy, reward-or-null], ...], ...}. Omit when using --from-log.'),
    from_log: str = typer.Option(None, "--from-log", help="Read observations from a ProbeLog JSONL (e.g. <home>/probe.jsonl written by `solve --probe-log`)."),
    delta: float = typer.Option(0.1, "--delta", help="Confidence level (smaller = stricter)."),
    min_reward: int = typer.Option(2, "--min-reward", help="Expensive rewards required per arm before deciding."),
) -> None:
    """PROBE best-arm identification with a cheap-proxy control variate (M18-5).

    "Which model/config is best?" where each expensive reward (a real grade) is paired with a cheap
    proxy (a weak judge) of unknown correlation. PROBE uses the proxy as a control variate so the
    estimate needs FEWER expensive draws the better the proxy correlates — and stays unbiased when the
    proxy is useless. Prints each arm's adjusted mean ± interval, the winner, and — if not yet
    confident — the arm to sample next. Feed it recorded (proxy, reward) observations from a bench.
    """
    import json

    from chimera.eval.probe import ProbeBestArm

    if from_log:
        from chimera.fusion.probe_log import ProbeLog

        arms = ProbeLog(Path(from_log)).observations()
    elif data:
        raw = json.loads(Path(data).read_text(encoding="utf-8"))
        arms = {
            str(name): [(float(o[0]), None if o[1] is None else float(o[1])) for o in obs]
            for name, obs in raw.items()
        }
    else:
        console.print("[red]Provide a JSON data file or --from-log <probe.jsonl>.[/red]")
        raise typer.Exit(code=1)
    if not arms:
        console.print("[dim]No observations yet.[/dim]")
        return
    decision = ProbeBestArm(delta=delta, min_reward=min_reward).select(arms)
    for e in decision.estimates:
        hw = "±∞" if e.half_width == float("inf") else f"±{e.half_width:.3f}"
        console.print(
            f"  {e.arm:<16} mean {e.mean:6.3f} {hw}   (rewards={e.n_reward}, proxy={e.n_proxy}, ρ={e.rho:.2f})"
        )
    if decision.best is None:
        console.print("[dim]No arms.[/dim]")
        return
    if decision.confident:
        console.print(f"\n[green]best: {decision.best}[/green] (δ-confident)")
    else:
        console.print(
            f"\n[yellow]best so far: {decision.best}[/yellow] — not δ-confident; "
            f"sample [bold]{decision.next_arm}[/bold] next"
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


@app.command("transfer-gate")
def transfer_gate(
    tuned_baseline: str = typer.Argument(..., help="JSON pass/fail of the baseline on the TUNED slice (list of bools, or {task: bool})."),
    tuned_treatment: str = typer.Argument(..., help="JSON pass/fail of the candidate on the TUNED slice (aligned, same order)."),
    holdout_baseline: str = typer.Option(None, "--holdout-baseline", help="JSON pass/fail of the baseline on a DISJOINT same-capability holdout."),
    holdout_treatment: str = typer.Option(None, "--holdout-treatment", help="JSON pass/fail of the candidate on the holdout (aligned)."),
    require_significant: bool = typer.Option(False, "--require-significant", help="Require the tuned gain's paired CI to exclude 0, not just Δ>0."),
    tol: float = typer.Option(0.0, "--tol", help="Max tolerated pass-rate drop on the holdout before promotion is blocked."),
) -> None:
    """Promote a learned change only if it helps its tuned slice AND doesn't regress a holdout.

    Guards against *negative transfer* — a GEPA prompt / ACE delta / distilled skill that raises the pass
    rate on the tasks it was tuned against but REGRESSES on other tasks sharing the capability. Feed the
    tuned slice's paired pass/fail (baseline vs candidate) and, ideally, a disjoint same-capability
    holdout's; the verdict is PROMOTE / BLOCK with the paired evidence (exit 1 on BLOCK, for CI).
    Without a holdout it promotes on the tuned gain alone but flags that transfer was NOT measured.
    """
    import json

    from chimera.eval.transfer import transfer_gated_promotion

    def _load(path: str) -> list[bool]:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        values = raw.values() if isinstance(raw, dict) else raw
        return [bool(v) for v in values]

    if (holdout_baseline is None) != (holdout_treatment is None):
        console.print("[red]give BOTH --holdout-baseline and --holdout-treatment, or neither.[/red]")
        raise typer.Exit(code=1)
    try:
        tb, tt = _load(tuned_baseline), _load(tuned_treatment)
        hb = _load(holdout_baseline) if holdout_baseline else None
        ht = _load(holdout_treatment) if holdout_treatment else None
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]could not read results: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    decision = transfer_gated_promotion(
        tuned_baseline=tb,
        tuned_treatment=tt,
        holdout_baseline=hb,
        holdout_treatment=ht,
        require_tuned_significant=require_significant,
        holdout_regression_tol=tol,
    )
    verdict = "[green]PROMOTE[/green]" if decision.promote else "[red]BLOCK[/red]"
    console.print(f"{verdict} — {decision.reason}")
    if not decision.transfer_measured:
        console.print(
            "[yellow]transfer NOT measured (no holdout) — promotion rests on the tuned slice alone.[/yellow]"
        )
    if not decision.promote:
        raise typer.Exit(code=1)


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
    if not result.success:
        raise typer.Exit(code=1)  # a gate must fail the process, not just print red (CI/scripts)


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
    if not result.success:
        raise typer.Exit(code=1)  # advertised as a production entrypoint — a failed run must exit non-zero


@app.command()
def drift(
    spec: str = typer.Argument(..., help="Spec YAML file."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
    only: str = typer.Option(None, "--only", help="Check only this requirement id (project cards)."),
) -> None:
    """Drift gate: check the workspace against a spec (Spec Growth). Exit 1 on drift."""
    from chimera.governance import check_drift, load_spec
    from chimera.governance.drift import Spec

    spec_obj = load_spec(spec)
    if only is not None:
        # A per-requirement gate: the project orchestrator's cards verify with `--only <id>` so
        # each card's success maps to exactly one requirement of the spec.
        matched = [r for r in spec_obj.requirements if r.id == only]
        if not matched:
            console.print(f"[red]no requirement {only!r} in spec {spec_obj.name!r}[/red]")
            raise typer.Exit(code=2)
        spec_obj = Spec(name=spec_obj.name, requirements=matched)
    report = check_drift(spec_obj, Path(workspace))
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
