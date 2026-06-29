"""Chimera command-line interface (CLI-first).

Commands implemented in M0/M1:
  - ``chimera version``  show the version
  - ``chimera doctor``   check the environment (Python, keys, model config)
  - ``chimera models``   show the default model and the fusion panel
  - ``chimera run``      run a single-shot Tier-1 completion (needs a provider key)

Richer commands (``chimera agent``, ``chimera fuse``, ``chimera migrate``, ...) land
in later milestones.
"""

from __future__ import annotations

import platform

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from chimera import __version__
from chimera.config import get_settings

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
def run(
    prompt: str = typer.Argument(..., help="The prompt to send."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    system: str = typer.Option(None, "--system", "-s", help="Optional system prompt."),
) -> None:
    """Run a single-shot Tier-1 completion (no fusion). Requires a provider key."""
    # Imported here so `version`/`doctor` stay fast and key-free.
    from chimera.providers import LLMGateway, MissingCredentialsError

    try:
        gateway = LLMGateway()
        answer = gateway.quick(prompt, model=model, system=system)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(answer)


if __name__ == "__main__":
    app()
