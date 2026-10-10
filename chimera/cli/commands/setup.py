"""Setup and status commands: version, init, doctor, models, agents, profile, features.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from chimera import __version__
from chimera.cli.commands._shared import _set_env_var, app, console
from chimera.config import config_env_files, get_settings


@app.command()
def version() -> None:
    """Show the Chimera version."""
    console.print(f"chimera [bold cyan]{__version__}[/bold cyan]")


@app.command()
def init(
    provider: str = typer.Option(
        "openrouter", "--provider", help="Which provider the key is for (openrouter, openai, ...)."
    ),
    key: str = typer.Option(None, "--key", help="API key for --provider."),
    openrouter_key: str = typer.Option(
        None, "--openrouter-key", help="Your OpenRouter API key (same as --provider openrouter --key)."
    ),
    model: str = typer.Option(None, "--model", help="Default model slug to set (optional)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non-interactive: never prompt."),
    home: str = typer.Option(None, "--home", help="Project dir for the .env (default: cwd)."),
) -> None:
    """First-run setup: create .env, set a provider key, and point you at a real example."""
    import os

    from chimera.providers.catalog import PROVIDERS_BY_NAME, provider_names

    chosen = PROVIDERS_BY_NAME.get(provider.strip().lower())
    if chosen is None:
        console.print(
            f"[red]Unknown provider[/red] '{provider}'. Known: {', '.join(provider_names())}.\n"
            "Any other provider LiteLLM supports also works — set <NAME>_API_KEY in .env directly."
        )
        raise typer.Exit(code=2)

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
    secret = (key or openrouter_key or "").strip()
    if not secret and not yes:
        console.print(f"Get a key at [bold]{chosen.keys_url}[/bold].")
        secret = typer.prompt(
            f"Paste your {chosen.label} API key (leave blank to skip)", default="", show_default=False
        ).strip()
    if secret:
        _set_env_var(env_path, chosen.env, secret)
        os.environ[chosen.env] = secret  # so the check below sees it immediately
        console.print(f"[green]Set[/green] {chosen.env} in .env")
    if model:
        _set_env_var(env_path, "CHIMERA_DEFAULT_MODEL", model)
        os.environ["CHIMERA_DEFAULT_MODEL"] = model
        console.print(f"[green]Set[/green] CHIMERA_DEFAULT_MODEL={model}")
    elif secret and chosen.env != "OPENROUTER_API_KEY":
        # Without this the tier presets stay on OpenRouter slugs and the ladder points at a vendor
        # this key cannot reach — the first call then fails with a 401 naming the wrong provider.
        # OpenRouter is skipped because the built-in default already matches: writing it would freeze
        # THIS release's default into the user's .env and stop them inheriting the next one.
        _set_env_var(env_path, "CHIMERA_DEFAULT_MODEL", chosen.default_model)
        os.environ["CHIMERA_DEFAULT_MODEL"] = chosen.default_model
        console.print(f"[green]Set[/green] CHIMERA_DEFAULT_MODEL={chosen.default_model}")

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
                f"({chosen.env}=...) and run [bold]chimera doctor[/bold].",
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


@app.command("context-curve", hidden=True)
def context_curve_cmd(
    traces: str = typer.Option(None, "--traces", help="Path to traces.jsonl (default: CHIMERA_HOME)."),
    runs: str = typer.Option(None, "--runs", help="Path to runs.jsonl (default: CHIMERA_HOME)."),
    json_out: bool = typer.Option(False, "--json", help="Print the raw result instead of a table."),
) -> None:
    """Did runs carrying more context do worse? Measured on THIS machine's own logs.

    Answers with "not enough data" until the pre-registered floors are met — see
    `bench/context_curve/PREREGISTRATION.md`, which fixed those floors before any data existed.
    """
    import json as _json

    from chimera.eval.context_curve import context_curve

    settings = get_settings()
    result = context_curve(
        Path(traces) if traces else settings.home / "traces.jsonl",
        Path(runs) if runs else settings.home / "runs.jsonl",
    )
    if json_out:
        console.print_json(_json.dumps(result.as_dict()))
        return

    table = Table(title="Success vs peak context", header_style="bold")
    for col in ("context (prompt tokens)", "attempts", "success", "rate", "95% CI"):
        table.add_column(col, justify="right")
    for bucket in result.as_dict()["buckets"]:
        rate = bucket["rate"]
        ci = bucket["ci95"]
        table.add_row(
            str(bucket["context_tokens"]),
            str(bucket["runs"]),
            str(bucket["successes"]),
            "—" if rate is None else f"{rate:.1%}",
            "—" if ci is None else f"[{ci[0]:.1%}, {ci[1]:.1%}]",
        )
    console.print(table)
    console.print(f"[bold]{result.verdict()}[/bold]")
    if result.unjoinable_attempts or result.unjoinable_traces:
        # Said out loud rather than swallowed: a silently dropped half of the data is how a real
        # effect gets measured away.
        console.print(
            f"[dim]{result.unjoinable_attempts} attempt(s) and {result.unjoinable_traces} trace(s) "
            "could not be joined — they predate the run id, or ran without tracing.[/dim]"
        )


@app.command()
def doctor(
    fix: bool = typer.Option(False, "--fix", help="Auto-repair safe setup issues (state dir, .env scaffold)."),
    probe: bool = typer.Option(
        False, "--probe", help="Actually call the provider once, instead of trusting the key's name."
    ),
) -> None:
    """Check the environment and configuration. With --fix, repair safe setup issues.

    `--probe` is off by default and that is deliberate: `doctor` should stay instant, offline and
    free. What it buys when you ask for it is the difference between a claim and a measurement —
    "Ready" below is an assertion about the NAME of an environment variable, so a revoked key, an
    account with no credit, or a value pasted with a trailing space all pass it and fail on the
    first real call. The argument for measuring is already written in `config_api.pricing_capability`
    a few files over: the time to find out is while reading the doctor, not when a 3 a.m. cron stalls.
    """
    settings = get_settings()

    if fix:
        for note in _doctor_fixes(settings):
            console.print(f"[green]fixed[/green] {note}")
        get_settings.cache_clear()
        settings = get_settings()

    providers = settings.configured_providers()
    # The gate accepts a credential on the strength of its NAME, which is fast and permissive: a
    # typo'd GROK_API_KEY is indistinguishable from a provider. Diagnostics is the one command where
    # paying LiteLLM's import to check is worth it — and where an unavailable LiteLLM has to degrade
    # to saying nothing, never to a warning that might be wrong. The five with settings fields are
    # never questioned; only what was discovered from the environment.
    from chimera.core import state_version
    from chimera.providers.discovery import generic_providers, litellm_known

    discovered = generic_providers()
    known = litellm_known(discovered)
    shown = [
        f"{p} [yellow](unknown to LiteLLM)[/yellow]"
        if p in discovered and not known.get(p, True)
        else p
        for p in providers
    ]

    table = Table(title="Chimera doctor", show_header=False, title_style="bold")
    table.add_row("Chimera version", __version__)
    table.add_row("Python", platform.python_version())
    table.add_row("Platform", platform.platform())
    table.add_row("Home (state dir)", str(settings.home))
    table.add_row("Environment files", ", ".join(str(path) for path in config_env_files()) or "none")
    # WHICH version wrote that directory, which nothing recorded until now: ~27 artefacts live under
    # it and not one carried a version, so every question about an upgrade was answered by guessing.
    # Stamped here rather than at import: `doctor` is the command whose job is to know the state of
    # this machine, and stamping from a library import would write to disk on `import chimera`.
    marca = state_version.stamp(settings.home)
    if not marca.known:
        estado = "[yellow]not recorded before now[/yellow]"
    elif marca.chimera_version == __version__:
        estado = marca.chimera_version
    else:
        estado = f"[yellow]{marca.chimera_version} -> {__version__}[/yellow]"
    table.add_row("State written by", estado)
    table.add_row("Default model", settings.default_model)
    table.add_row(
        "Configured providers",
        ", ".join(shown) if shown else "[yellow]none[/yellow]",
    )
    # Where commands run, and why. It lived only in a WARNING logged at the first `get_sandbox()`
    # of every process — which meant the REPLs opened with a four-line log block above their banner
    # and this command, the one whose job is to describe the machine, did not mention the sandbox at
    # all. The banner now says one line and points here; this is the "here".
    from chimera.sandbox.os_sandbox import unavailable_reason

    # Two separate facts, neither inferred from the other: what is CONFIGURED, and what this
    # machine can actually provide. `CHIMERA_SANDBOX=docker` with no daemon is a configuration that
    # falls back to the host, so printing one of these as if it were the other would be the kind of
    # confident wrong answer a doctor exists to prevent.
    no_sandbox = unavailable_reason()
    table.add_row("Sandbox (configured)", settings.sandbox or "auto")
    table.add_row(
        "OS sandbox",
        f"[yellow]unavailable — {no_sandbox}[/yellow]" if no_sandbox else "available",
    )
    table.add_row("Host execution", (settings.host_exec or "ask").lower())
    console.print(table)

    # Capability by capability, measured HERE. The app ships as an installer, so this is the one
    # place someone can find out whether the editor's diagnostics, its completion model and the
    # external agents actually work on their machine — and, when they do not, the command that
    # changes that. "Unavailable" without a remedy is a shrug.
    from chimera.acp.agents import available_agents
    from chimera.api.config_api import editor_capabilities, pricing_capability

    caps = Table(title="Capabilities", show_header=True, header_style="bold")
    caps.add_column("Capability")
    caps.add_column("Status")
    caps.add_column("How to get it")
    for row in [pricing_capability(settings), *editor_capabilities(settings), *available_agents()]:
        ready = bool(row.get("available"))
        # "configured" and "available" are different claims and get different words. A completion
        # model nobody has reached is not available; saying so here would be a promise the editor
        # then fails to keep, and the user would go looking for the fault in the wrong place.
        probed = bool(row.get("probed", True))
        status = (
            ("[green]available[/green]" if probed else "[green]configured[/green]")
            if ready
            else "[yellow]not here[/yellow]"
        )
        caps.add_row(
            str(row.get("label") or row.get("key")),
            status,
            str(row.get("detail") or "") if ready and not probed
            else ("" if ready else str(row.get("hint") or row.get("install_hint") or "")),
        )
    console.print(caps)

    # What this install keeps on disk — the same rows the app's Storage card and its Copy button
    # show (`chimera.core.storage.summary_rows`), so the terminal and the screen cannot disagree.
    # Here because the VPS has no screen, and the drive that fills there is the same kind of drive.
    from chimera.core.storage import measure, summary_rows

    disk = Table(title="Storage", show_header=False, title_style="bold")
    # The workspace every CLI command defaults to (`-w .`): where "worktree location" is decided for.
    for label, value in summary_rows(measure(settings.home, Path.cwd())):
        disk.add_row(label, value)
    console.print(disk)

    if providers and probe:
        # One token, on the default model, through the same gateway a real run uses — including its
        # failover, so "the primary is down and a fallback answered" reads as ready, which it is.
        from chimera.providers import LLMGateway

        try:
            LLMGateway().quick("ok", model=settings.default_model)
        except Exception as exc:  # noqa: BLE001 — every provider failure is an answer here
            from chimera.core.redact import redact

            console.print(
                f"[red]Not ready[/red] — the provider refused: {redact(str(exc))[:300]}"
            )
        else:
            console.print(
                f"[green]Ready[/green] — {settings.default_model} answered a live call."
            )
    elif providers:
        console.print(
            "[green]Ready[/green] — at least one provider key is configured "
            "[dim](a name, not a call — use --probe to check)[/dim]."
        )
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


_WIRE_PATH_OPTION = typer.Option(None, "--wire", help="Wire JSONL path (default: CHIMERA_HOME/wire.jsonl).")
_STEPLOG_PATH_OPTION = typer.Option(None, "--steplog", help="Run trace JSONL path (default: CHIMERA_HOME/traces.jsonl).")
_RUN_ID_OPTION = typer.Option(
    None, "--run", help="Reconcile only this run id (the wire log is shared by every gateway user)."
)
audit_app = typer.Typer(help="Reconcile independent gateway records with saved run traces.")


@audit_app.command("reconcile")
def audit_reconcile(
    wire: Path | None = _WIRE_PATH_OPTION,
    steplog: Path | None = _STEPLOG_PATH_OPTION,
    run: str | None = _RUN_ID_OPTION,
) -> None:
    """Compare metadata-only gateway observations with the saved steplogs."""
    from chimera.governance.reconcile import reconcile

    settings = get_settings()
    result = reconcile(
        wire or settings.home / "wire.jsonl", steplog or settings.home / "traces.jsonl", run_id=run
    )
    console.print_json(json.dumps(result, ensure_ascii=False))
    if not result["clean"]:
        raise typer.Exit(code=1)


app.add_typer(audit_app, name="audit")


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
    # The cast `--fuse` convenes, which is the ladder unless a panel was named — not the raw
    # `CHIMERA_FUSION_PANEL`, whose frontier default no product surface convenes any more.
    from chimera.fusion.factory import fusion_config

    fused = fusion_config(settings)
    table.add_row("fusion panel", "\n".join(fused.panel))
    table.add_row("fusion judge", fused.judge)
    table.add_row("fusion synthesizer", fused.synthesizer)
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


agents_app = typer.Typer(
    help="The agents you dispatch work to — as distinct from the one you converse with."
)


@agents_app.command("list")
def agents_list() -> None:
    """Show the registry."""
    from chimera.core.registry import load as load_agents

    entries = load_agents(get_settings().home)
    if not entries:
        console.print(
            "[dim]No agents yet — add one with "
            '`chimera agents set <id> --name "..." --instructions "..."`.[/dim]'
        )
        return
    table = Table(box=None)
    table.add_column("id", style="bold")
    table.add_column("name")
    table.add_column("model")
    table.add_column("tools")
    for entry in entries:
        table.add_row(
            entry.id,
            entry.label,
            entry.model or "[dim]ladder[/dim]",
            ", ".join(entry.allowed_tools) or "[dim]all[/dim]",
        )
    console.print(table)


@agents_app.command("set")
def agents_set(
    agent_id: str = typer.Argument(..., help="Its handle: a lowercase slug. Also its Kanban lane."),
    name: str = typer.Option("", "--name", help="What to call it on screen."),
    instructions: str = typer.Option("", "--instructions", help="Its role, in your words."),
    model: str = typer.Option("", "--model", help="Pin a model; empty inherits the ladder."),
    tools: str = typer.Option(
        "", "--tools", help="Comma-separated allowlist; empty means NO restriction."
    ),
) -> None:
    """Add an agent, or replace the one with this id.

    Replace rather than merge, matching the API: a partial write that kept what you left out would
    make clearing a pinned model impossible.
    """
    from chimera.core.registry import AgentDef
    from chimera.core.registry import upsert as upsert_agent

    try:
        entry = AgentDef(
            id=agent_id,
            name=name,
            instructions=instructions,
            model=model,
            allowed_tools=[t.strip() for t in tools.split(",") if t.strip()],
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    upsert_agent(get_settings().home, entry)
    console.print(f"[green]Saved[/green] {entry.id}")
    agents_list()


@agents_app.command("rm")
def agents_rm(agent_id: str = typer.Argument(..., help="The agent to forget.")) -> None:
    """Forget an agent. Cards already filed under its lane are left exactly where they are."""
    from chimera.core.registry import get as get_agent
    from chimera.core.registry import remove as remove_agent

    home = get_settings().home
    if get_agent(home, agent_id) is None:
        console.print(f"[yellow]No agent[/yellow] {agent_id}")
        raise typer.Exit(1)
    remove_agent(home, agent_id)
    console.print(f"[green]Removed[/green] {agent_id}")


app.add_typer(agents_app, name="agents")


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
        # escape(): these strings carry pip extras — `pip install 'chimera-agent[stt]'` — and Rich
        # parses `[stt]` as style markup and deletes it. The instruction then reads
        # `pip install 'chimera-agent'`, which installs the package WITHOUT the thing the user came
        # for, and does it silently. This table's whole job is telling people what to install, so a
        # blocker/how that quietly drops its extra is worse than printing nothing.
        state = "[green]ready[/green]" if status.ready else f"[yellow]{escape(status.blocker)}[/yellow]"
        table.add_row(escape(status.feature.name), state, escape(status.feature.how))
    console.print(table)
