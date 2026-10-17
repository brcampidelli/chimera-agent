"""``chimera plugin`` — Claude Code plugins, installed pending and switched on only by the owner.

Hidden while the feature is incomplete (no HTTP routes or screen yet): ``--help`` does not list it,
and the reference page does not document it. See :mod:`chimera.plugins.registry` for the rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import typer
from rich.markup import escape
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.config import get_settings

plugin_app = typer.Typer(
    help="Claude Code plugins: validate, install (pending), enable, disable, update, remove.",
    no_args_is_help=True,
)
app.add_typer(plugin_app, name="plugin", hidden=True)
marketplace_app = typer.Typer(help="Plugin marketplaces you added.", no_args_is_help=True)
plugin_app.add_typer(marketplace_app, name="marketplace")

_VALIDATE_PATH_ARG = typer.Argument(..., help="A plugin or marketplace folder.")
_ADD_MCP_OPT = typer.Option(
    None, "--add-mcp", help="Also add this MCP server of the plugin (repeatable). None is added otherwise."
)


def _home() -> Path:
    return Path(get_settings().home)


def _fail(message: str) -> NoReturn:
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(code=1)


def _capability_line(caps: dict[str, int], inert: int) -> str:
    if not (caps.get("network") or caps.get("process") or caps.get("exec")):
        return "instructions only"
    parts = []
    if caps.get("process") or caps.get("network"):
        parts.append(f"{caps.get('process', 0) + caps.get('network', 0)} server(s), added only on request")
    if inert:
        parts.append(f"{inert} inert item(s): hooks/bin/monitors are never run")
    return "; ".join(parts) or "instructions only"


@plugin_app.command("validate")
def plugin_validate(path: Path = _VALIDATE_PATH_ARG) -> None:
    """Read a plugin (or a marketplace) folder and report what Claude Code would refuse. Runs nothing."""
    from chimera.plugins import read_marketplace, read_plugin
    from chimera.plugins.marketplace import MARKETPLACE
    from chimera.plugins.model import Severity

    is_market = (path / MARKETPLACE).is_file() and not (path / ".claude-plugin" / "plugin.json").is_file()
    report = read_marketplace(path) if is_market else read_plugin(path)
    for problem in report.problems:
        colour = "red" if problem.severity is Severity.ERROR else "yellow"
        console.print(f"[{colour}]{problem.severity.value}[/{colour}] {escape(problem.where)}: {escape(problem.message)}")
    if not report.ok:
        raise typer.Exit(code=1)
    console.print("[green]valid[/green]")


@plugin_app.command("install")
def plugin_install(
    source: str = typer.Argument(..., help="<folder> | github:owner/repo@<sha>[:folder] | https://…#sha256=<hex> | <plugin>@<marketplace>"),
) -> None:
    """Install a plugin PENDING. Nothing reaches a prompt until you run `chimera plugin enable`."""
    from chimera.plugins import marketplaces
    from chimera.plugins.registry import PluginError, install

    home = _home()
    try:
        name, at, market = source.partition("@")
        is_ref = at and not source.lower().startswith(("github:", "https://")) and not Path(source).exists()
        if is_ref:
            spec = marketplaces.resolve(home, name, market)
            record = install(spec, home, marketplace=market, fallback_name=name)
        else:
            record = install(source, home)
    except PluginError as exc:
        _fail(str(exc))
    console.print(f"[green]installed[/green] {escape(record.name)} — [yellow]pending[/yellow]; "
                  f"review it, then `chimera plugin enable {escape(record.name)}`")


@plugin_app.command("list")
def plugin_list() -> None:
    """Installed plugins: state, source, pinned ref and what each one would do."""
    from chimera.plugins.registry import inert_suggestions, installed

    found = installed(_home())
    if not found:
        console.print("[dim]no plugins installed[/dim]")
        return
    table = Table(title="Plugins", header_style="bold")
    for column in ("name", "state", "source", "ref", "capabilities"):
        table.add_column(column)
    for record in found:
        state = "denied by policy" if record.denied else record.status
        table.add_row(escape(record.name), state, escape(record.source), (record.ref or record.tree_sha256)[:12],
                      _capability_line(record.capabilities, len(inert_suggestions(record))))
    console.print(table)


@plugin_app.command("enable")
def plugin_enable(name: str = typer.Argument(...), add_mcp: list[str] = _ADD_MCP_OPT) -> None:
    """Switch a plugin on (owner only). Its skills, commands and agents appear, namespaced and tainted."""
    from chimera.plugins.registry import PluginError, enable, inert_suggestions

    try:
        record = enable(_home(), name, add_mcp=list(add_mcp or []))
    except PluginError as exc:
        _fail(str(exc))
    console.print(f"[green]enabled[/green] {escape(record.name)}")
    for server in record.mcp_added:
        console.print(f"  MCP server [cyan]{escape(server)}[/cyan] added; its tools are pinned at first mount")
    for item in inert_suggestions(record):
        console.print(f"  [dim]inert {escape(str(item['kind']))} {escape(str(item['name']))} — shown, never run[/dim]")


@plugin_app.command("disable")
def plugin_disable(name: str = typer.Argument(...)) -> None:
    """Switch a plugin off: its components disappear and the MCP servers it added are removed."""
    from chimera.plugins.registry import PluginError, disable

    try:
        disable(_home(), name)
    except PluginError as exc:
        _fail(str(exc))
    console.print(f"[green]disabled[/green] {escape(name)}")


@plugin_app.command("update")
def plugin_update(
    name: str = typer.Argument(...),
    source: str | None = typer.Option(None, "--source", help="A new pinned source; default: re-read the recorded one."),
) -> None:
    """Re-read a plugin's source. Any change leaves it held (governed) or pending (prose only)."""
    from chimera.plugins.registry import PluginError, update

    try:
        result = update(_home(), name, source)
    except PluginError as exc:
        _fail(str(exc))
    if not result.changed:
        console.print(f"{escape(name)}: unchanged")
        return
    console.print(f"{escape(name)}: changed — now [yellow]{result.status}[/yellow]")
    for reason in result.diff.get("reasons", []):
        console.print(f"  governed: {escape(str(reason))}")


@plugin_app.command("remove")
def plugin_remove(name: str = typer.Argument(...)) -> None:
    """Delete an installed plugin and the MCP servers it added."""
    from chimera.plugins.registry import remove

    if not remove(_home(), name):
        _fail(f"no installed plugin named {name!r}")
    console.print(f"[green]removed[/green] {escape(name)}")


@marketplace_app.command("add")
def marketplace_add(source: str = typer.Argument(..., help="<folder> | github:owner/repo@<sha>")) -> None:
    """Remember a marketplace (its marketplace.json is read now; no install counts exist or are shown)."""
    from chimera.plugins import marketplaces
    from chimera.plugins.registry import PluginError

    try:
        record = marketplaces.add(_home(), source)
    except PluginError as exc:
        _fail(str(exc))
    console.print(f"[green]added[/green] marketplace {escape(record['name'])} ({len(record['plugins'])} plugins)")


@marketplace_app.command("list")
def marketplace_list() -> None:
    """The marketplaces you added and what each lists, with why an entry cannot be installed."""
    from chimera.plugins import marketplaces

    found = marketplaces.load(_home())
    if not found:
        console.print("[dim]no marketplaces added[/dim]")
        return
    for market in found:
        console.print(f"[bold]{escape(str(market.get('name')))}[/bold] [dim]{escape(str(market.get('description', '')))}[/dim]")
        for entry in market.get("plugins", []):
            note = "" if entry.get("installable") else f" [red]({escape(str(entry.get('refusal')))})[/red]"
            console.print(f"  {escape(str(entry.get('name')))} — {escape(str(entry.get('description', '')))}{note}")
