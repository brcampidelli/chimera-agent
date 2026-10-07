"""Tools and skills: ``chimera tools`` and the ``chimera skills`` group.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.config import get_settings


def _print_defer_saving(workspace: Path) -> None:
    """``chimera tools --defer-saving``: the token half of the two deferral switches, measured here.

    For the machine with no Settings screen. MCP servers are connected only when autoload is on —
    the same gate a conversation uses, so this spawns nothing a chat would not — and the figure can
    be a loss, which is printed as one.
    """
    from chimera.integrations import mcp_pool
    from chimera.tools.defer_saving import McpState, saving_report

    settings = get_settings()
    pool = mcp_pool.connectors(settings) if settings.mcp_autoload else None
    state: McpState = (
        "autoload_off"
        if not settings.mcp_autoload
        else "measured" if pool is not None else "no_servers"
    )
    report = saving_report(settings, workspace, pool=pool, mcp_state=state)

    def line(label: str, half: dict[str, Any], switch: str, on: bool) -> None:
        pct = half["saving_pct"]
        verdict = f"{pct:.1f}% less" if pct >= 0 else f"{-pct:.1f}% MORE (a loss)"
        console.print(
            f"{label}: {half['declared_chars']:,} → {half['deferred_chars']:,} schema chars per step "
            f"({verdict}); {half['deferred']} of {half['tools']} tools deferred · "
            f"{switch}={'on' if on else 'off'}"
        )

    line("built-in", report["builtin"], "CHIMERA_DEFER_TOOLS", settings.defer_tools)
    if report["mcp"] is not None:
        line("MCP", report["mcp"], "CHIMERA_MCP_DEFER", settings.mcp_defer)
    else:
        why = {
            "autoload_off": "CHIMERA_MCP_AUTOLOAD is off, so no server is connected",
            "no_servers": "no MCP server connected",
            "unavailable": "a connected server did not answer its tool listing",
        }.get(report["mcp_state"], report["mcp_state"])
        console.print(f"MCP: not measured — {why}")
    # The other half, said every time: the saving is tokens, the risk is a tool not being found.
    console.print(
        "[dim]Token half only. Whether a deferred tool is still found was inconclusive for the "
        "built-in half (bench/tool_defer/RESULT.md: 18/30 vs 15/30 completed, McNemar p = 0.125) "
        "and is unmeasured for MCP.[/dim]"
    )


@app.command()
def tools(
    workspace: str = typer.Option(".", "--workspace", "-w"),
    defer_saving: bool = typer.Option(
        False,
        "--defer-saving",
        help="Report what CHIMERA_DEFER_TOOLS / CHIMERA_MCP_DEFER would save on this install.",
    ),
) -> None:
    """List the built-in native tools."""
    if defer_saving:
        _print_defer_saving(Path(workspace))
        return
    from chimera.tools import default_registry

    registry = default_registry(Path(workspace))
    table = Table(title="Native tools", show_header=True, header_style="bold")
    table.add_column("Tool")
    table.add_column("Description")
    for tool in registry.tools():
        table.add_row(tool.name, tool.description)
    console.print(table)
    # `tools` lists only what is switched on, which reads as "this is everything Chimera has" — the
    # route by which a user concludes it cannot search the web. `features` is the full checklist.
    console.print("\n[dim]More capabilities ship in the box but need a key or an extra. See[/dim] "
                  "[bold]chimera features[/bold]")


#: `chimera skills` is a group since S30-70: run bare it still lists the built-in skills, and the
#: fifteen `skills-*` commands are its subcommands (`chimera skills library`, `... import`, ...).
#: Every old `skills-*` name stays registered, hidden from `--help`, so no script or doc breaks.
skills_app = typer.Typer(
    help="List the built-in skills, or browse, install and curate skill cards with a subcommand."
)
app.add_typer(skills_app, name="skills")


@skills_app.callback(invoke_without_command=True)
def _skills_group(ctx: typer.Context) -> None:
    """List the built-in skills, or browse, install and curate skill cards with a subcommand."""
    if ctx.invoked_subcommand is None:
        skills()


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
    # These six are LLM-backed procedures, and they are not what the README means by "the skill
    # library" — that is the curated markdown in `skills/`, which this command never mentioned and
    # which had no listing command of its own. Someone reading "Built-in skills" here concluded
    # there were six.
    console.print("\n[dim]The curated skill cards are a separate set. See[/dim] "
                  "[bold]chimera skills-library[/bold]")


@skills_app.command("library")
@app.command("skills-library", hidden=True)
def skills_library(
    name: str = typer.Argument(None, help="Show one card in full; omit to list the library."),
) -> None:
    """Browse the curated skill cards that ship with Chimera.

    Data, not code: each is a markdown page of Trigger/Do/Avoid/Check/Risk. Load one into your own
    store with ``chimera skills-import <name>``. The agent reads a matching card from that store into
    its prompt only with CHIMERA_SKILL_CARDS=on (or ``chimera solve --skill-cards``); it is off by
    default, so an imported card is otherwise reference for you, not advice to the agent.
    """
    from chimera.skills.library import load_card, load_library

    if name:
        card = load_card(name)
        if card is None:
            console.print(f"[red]No curated card named {name!r}.[/red] See: chimera skills-library")
            raise typer.Exit(code=1)
        console.print(Panel(escape(card.instructions), title=escape(card.manifest.name)))
        console.print(f"[dim]Import it with:[/dim] chimera skills-import {name}")
        return

    cards = load_library()
    if not cards:
        # Not "you have no skills": the library is shipped, never earned, so an empty one means the
        # build lost it rather than that the user has not done anything yet.
        console.print("[yellow]This build ships no curated skill library.[/yellow] "
                      "Expected it at chimera/_skill_library or skills/ in a source checkout.")
        return
    table = Table(title=f"Curated skill cards ({len(cards)})", show_header=True, header_style="bold")
    for column in ("Card", "Stage", "Topic", "Description"):
        table.add_column(column)
    for card in cards:
        m = card.manifest
        table.add_row(m.name, m.stage or "-", m.topic or "-", m.description)
    console.print(table)
    console.print("[dim]Read one: chimera skills-library <name> · Load one: "
                  "chimera skills-import <name>[/dim]")


@skills_app.command("catalog")
@app.command("skills-catalog", hidden=True)
def skills_catalog(
    query: str = typer.Argument(None, help="Filter by name or description."),
    topic: str = typer.Option(None, "--topic", help="Only this topic."),
) -> None:
    """Browse the installable skills from the wider Agent Skills ecosystem.

    These are other people's skills, fetched from their repositories on request — not bundled here.
    The table says what each one NEEDS, because most were written for a different harness and a
    catalogue that hid that would be advertising features that fail after the download.
    """
    from chimera.skills.catalog import CATALOG, license_is_permissive, search

    if not CATALOG:
        console.print("[yellow]This build ships no skill catalogue.[/yellow]")
        return
    found = search(query or "", topic=topic or "")
    if not found:
        console.print(f"[yellow]Nothing matches {query!r}.[/yellow] See: chimera skills-catalog")
        return
    table = Table(title=f"Installable skills ({len(found)}/{len(CATALOG)})", show_header=True,
                  header_style="bold")
    for column in ("Skill", "Topic", "Works here", "Licence", "Description"):
        table.add_column(column)
    for entry in found:
        # The licence column earns its width: an entry with no licence found is not the same as a
        # permissive one, and the person deciding to download deserves to see which they have.
        lic = entry.license or "[red]none found[/red]"
        if entry.license and not license_is_permissive(entry.license):
            lic = f"[yellow]{escape(entry.license)}[/yellow]"
        works = entry.portability.value.replace("_", " ")
        table.add_row(entry.name, entry.topic or "-", works, lic, entry.description)
    console.print(table)
    console.print("[dim]Details: chimera skills-catalog <name> · Install: "
                  "chimera skills-install <name>[/dim]")


@skills_app.command("install")
@app.command("skills-install", hidden=True)
def skills_install(
    name: str = typer.Argument(..., help="A skill name from `chimera skills-catalog`."),
    force: bool = typer.Option(False, "--force", help="Replace it if it is already installed."),
) -> None:
    """Download a skill bundle from its source repository into your skills directory.

    Fetches; runs nothing. The bundle lands **pending**: its files are on disk and no part of it
    reaches a prompt until you approve it. That is the same rule an imported card follows — a skill
    from a stranger has the standing of an instruction from the owner — and a bundle is that plus
    executable scripts, so it holds with more reason, not less.
    """
    from chimera.skills.bundles import BundleError, install
    from chimera.skills.catalog import find, license_is_permissive

    entry = find(name)
    if entry is None:
        console.print(f"[red]No skill named {name!r} in the catalogue.[/red] "
                      "See: chimera skills-catalog")
        raise typer.Exit(code=1)

    if entry.requires:
        # Before the download, not after: this is the list that decides whether the thing will run
        # at all, and finding it out afterwards means finding it out from a failure.
        console.print(f"[yellow]Needs:[/yellow] {escape(', '.join(entry.requires))}")
    if not license_is_permissive(entry.license):
        terms = entry.license or "no licence file found"
        console.print(f"[yellow]Licence:[/yellow] {escape(terms)} — read it before you rely on this.")

    try:
        record = install(entry, get_settings().home, force=force)
    except BundleError as exc:
        console.print(f"[red]Not installed:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]installed[/green] {record.name} "
                  f"({len(record.files)} files) [yellow]— pending[/yellow]")
    console.print(f"[dim]from {escape(record.source)}[/dim]")
    console.print(f"[dim]at {escape(record.ref[:12])}[/dim]")
    console.print(f"[dim]Read it, then:[/dim] chimera skills-bundle-enable {record.name}")


@skills_app.command("bundles")
@app.command("skills-bundles", hidden=True)
def skills_bundles() -> None:
    """List the skill bundles installed on this machine, and where each came from."""
    from chimera.skills.bundles import bundles_root, installed

    home = get_settings().home
    found = installed(home)
    if not found:
        console.print("[dim]No skill bundles installed.[/dim] "
                      "Browse them with: chimera skills-catalog")
        return
    table = Table(title=f"Installed bundles ({len(found)})", show_header=True, header_style="bold")
    for column in ("Skill", "Status", "Files", "Licence", "Source"):
        table.add_column(column)
    for bundle in found:
        tone = "green" if bundle.status == "active" else "yellow"
        label = f"{bundle.status} (switch on again)" if bundle.reconfirm else bundle.status
        table.add_row(bundle.name, f"[{tone}]{label}[/{tone}]", str(len(bundle.files)),
                      bundle.license or "-", bundle.source or "-")
    console.print(table)
    if any(b.reconfirm for b in found):
        console.print("[yellow]Some bundles were switched on while a switched-on bundle reached no "
                      "prompt. They reach nothing until switched on again: "
                      "chimera skills-bundle-enable <name>[/yellow]")
    console.print(f"[dim]On disk at {escape(str(bundles_root(home)))}[/dim]")


@skills_app.command("bundle-enable")
@app.command("skills-bundle-enable", hidden=True)
def skills_bundle_enable(
    name: str = typer.Argument(..., help="An installed bundle from `chimera skills-bundles`."),
) -> None:
    """Switch an installed bundle on, so the agent may use it.

    Do this after reading it. An enabled bundle's name and description reach the agent's prompt
    when they match a task, and its instructions can tell the agent to run the scripts that came
    with it — which is why nothing is on by default.
    """
    from chimera.skills.bundles import set_status

    if not set_status(name, get_settings().home, "active"):
        console.print(f"[red]No installed bundle named {name!r}.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]on[/green] {name}")


@skills_app.command("bundle-disable")
@app.command("skills-bundle-disable", hidden=True)
def skills_bundle_disable(
    name: str = typer.Argument(..., help="An installed bundle from `chimera skills-bundles`."),
) -> None:
    """Switch a bundle off, keeping it on disk.

    Off is not uninstalled, deliberately: trying several and leaving two running is the normal
    way to use these, and making "off" mean "delete" would charge a download for every change of
    mind. Use ``skills-uninstall`` when you want the files gone.
    """
    from chimera.skills.bundles import set_status

    if not set_status(name, get_settings().home, "inactive"):
        console.print(f"[red]No installed bundle named {name!r}.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[dim]off[/dim] {name} [dim](still installed)[/dim]")


@skills_app.command("uninstall")
@app.command("skills-uninstall", hidden=True)
def skills_uninstall(
    name: str = typer.Argument(..., help="An installed bundle from `chimera skills-bundles`."),
) -> None:
    """Delete an installed skill bundle and its files."""
    from chimera.skills.bundles import remove

    if not remove(name, get_settings().home):
        console.print(f"[red]No installed bundle named {name!r}.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]removed[/green] {name}")


@skills_app.command("pending")
@app.command("skills-pending", hidden=True)
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


@skills_app.command("stats")
@app.command("skills-stats", hidden=True)
def skills_stats() -> None:
    """Per-skill usage stats (uses, successes, win rate) + retirement candidates."""
    from chimera.evolution import SkillStore

    store = SkillStore(get_settings().home / "skills.json")
    rows = store.stats_overview()
    if not rows:
        console.print("[dim]No learned skills yet.[/dim]")
        return
    retire = set(store.retirement_candidates_any_context())
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


@skills_app.command("approve")
@app.command("skills-approve", hidden=True)
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


@skills_app.command("export")
@app.command("skills-export", hidden=True)
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


@skills_app.command("import")
@app.command("skills-import", hidden=True)
def skills_import(
    path: str = typer.Argument(..., help="A curated card name, or a path to a SKILL.md / its directory."),
) -> None:
    """Import a SKILL.md into the store. A file imported by path is held pending for review.

    Accepts the plain name of a curated card (``chimera skills-import verify-before-claiming``) as
    well as a path. The documented form was ``skills/<name>``, a repo-relative path that resolves
    only inside a checkout — so the one line the README gives for using the shipped library failed
    for everybody who installed Chimera instead of cloning it.

    A file by path lands tainted and pending whatever its frontmatter says, until
    ``chimera skills-approve <name>``. Its ``provenance`` and ``status`` were written by its author,
    so reading them let the stranger decide whether anybody reads the card before the agent does.
    Only a curated card keeps what it declares: by name, or by a path whose content is exactly the
    shipped card.

    Validated on the way in. This is the only path by which a skill written by somebody else enters
    the store, and it was the only one that skipped the validator the agent's own proposals must
    pass — the gate was applied to the code we wrote and not to the code we were handed, which is
    backwards. A skill card ends up in the system prompt, so an unvalidated one is an instruction
    from a stranger with the standing of an instruction from the owner.
    """
    from chimera.evolution import SkillStore
    from chimera.governance import SkillValidator
    from chimera.skills.library import is_shipped_card, load_card
    from chimera.skills.skill_md import parse_skill_md, to_learned, untrusted

    src = Path(path)
    if src.is_dir():
        src = src / "SKILL.md"
    # A real file on disk wins over a same-named curated card: an argument that names something the
    # user can see in their own directory must not silently import a different file from the wheel.
    if not src.exists():
        curated = load_card(path)
        if curated is None:
            console.print(f"[red]No SKILL.md at {src}[/red] and no curated card named {path!r}.")
            raise typer.Exit(code=1)
        parsed = curated
    else:
        text = src.read_text(encoding="utf-8")
        parsed = parse_skill_md(text)
        if not is_shipped_card(parsed.manifest.name, text):
            parsed = untrusted(parsed)
    skill = to_learned(parsed)
    verdict = SkillValidator().validate(skill.to_dict())
    if not verdict.accepted:
        console.print(f"[red]Refused[/red] {skill.name}: {'; '.join(verdict.reasons)}")
        raise typer.Exit(code=1)
    store = SkillStore(get_settings().home / "skills.json")
    store.add(skill)
    if skill.status != "pending":
        console.print(f"[green]imported[/green] {skill.name}")
        return
    console.print(f"[green]imported[/green] {skill.name} "
                  "[yellow](held pending — a file cannot vouch for itself)[/yellow]")
    console.print(f"[dim]Read it, then:[/dim] chimera skills-approve {skill.name}")


@skills_app.command("retire")
@app.command("skills-retire", hidden=True)
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
        targets = store.retirement_candidates_any_context(min_uses=min_uses, max_rate=max_rate)
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


@skills_app.command("lifecycle")
@app.command("skills-lifecycle", hidden=True)
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
    decisions = policy.decide_slices(store.stats_slices())
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


@skills_app.command("evolve")
@app.command("skills-evolve", hidden=True)
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
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
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
