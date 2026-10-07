"""Curated long-term memory: the ``chimera memory`` group and the helpers that build it.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.markup import escape
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.memory import EmbedFn, MemoryGraph, MemoryManager
    from chimera.memory.extract import MemoryExtractor
    from chimera.orchestration.metering import MeteredBackend



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
                f"[yellow]{escape(nudge.task)}[/yellow]"
                "[dim] → chimera solve reuses it automatically[/dim]"
            )


def _maybe_autoconsolidate(
    memory: MemoryManager | None, settings: Settings, usage_id: str = ""
) -> None:
    """On session end, consolidate memory if it outgrew the budget (opt-in).

    The merges are model calls, metered and written to the usage log under ``usage_id``, the
    conversation that just ended, as :data:`chimera.api.usage.TIDY_KIND`: added to its spend and
    not counted as a turn of it. A merge that failed part-way still bills the merges it paid for.
    """
    if memory is None or not settings.auto_consolidate:
        return
    from chimera.orchestration.metering import MeteredBackend as _Meter

    meter: MeteredBackend | None = None
    try:
        from chimera.memory.consolidate import model_summarizer
        from chimera.providers import LLMGateway

        meter = _Meter(LLMGateway(), label="tidy")
        removed = memory.autoconsolidate(model_summarizer(meter), max_items=settings.memory_budget)
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup, never break exit
        _record_tidy_spend(settings, meter, usage_id)
        console.print(f"[dim]auto-consolidate skipped: {exc}[/dim]")
        return
    _record_tidy_spend(settings, meter, usage_id)
    if removed:
        console.print(f"[dim]🧹 consolidated {removed} redundant memory item(s)[/dim]")


def _record_tidy_spend(settings: Settings, meter: MeteredBackend | None, usage_id: str) -> None:
    """Write what the tidy's merges cost, when it made any (see `_maybe_autoconsolidate`)."""
    from chimera.api.usage import TIDY_KIND

    _record_merge_spend(settings, meter, usage_id or "memory-tidy", route_kind=TIDY_KIND)


def _record_merge_spend(
    settings: Settings, meter: MeteredBackend | None, usage_id: str, *, route_kind: str | None
) -> None:
    """One usage row for the memory merges ``meter`` saw, or none when it saw no call returned."""
    if meter is None or not meter.calls:
        return
    from chimera.api.usage import record_spend

    record_spend(
        settings.home, session_id=usage_id, model=meter.last_model,
        prompt_tokens=meter.prompt_tokens, completion_tokens=meter.completion_tokens,
        usd=meter.usd, route_kind=route_kind,
    )


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


def _memory_extractor(
    settings: Settings, memory: MemoryManager | None, usage_id: str | Callable[[], str]
) -> MemoryExtractor | None:
    """The after-turn extractor for a terminal conversation, or None (study 25 S13).

    None unless ``CHIMERA_MEMORY_EXTRACT`` is on and there is a memory to write to. A terminal is a
    conversation with the owner, which is what makes the user's words theirs to keep; the messaging
    bots are not wired, because anyone who can reach the bot would be writing the owner's memory.

    ``usage_id`` is what the turns of this conversation are filed under in the usage log, so the
    extraction's cost lands beside them. A callable where ``/new`` can replace the thread.
    """
    if memory is None or not settings.memory_extract:
        return None
    from chimera.memory.extract import MemoryExtractor

    return MemoryExtractor(memory, usage_home=Path(settings.home), usage_id=usage_id)


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
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Only list the clusters that would be merged (no model call, no write)."
    ),
) -> None:
    """Merge clusters of similar memories into one LLM-summarised fact (opt-in write)."""
    from uuid import uuid4

    if dry_run:
        # Before this, seeing what would be merged meant merging it — a model call per cluster.
        # The clustering is free; only the summary costs.
        groups = _memory_manager().consolidation_groups(threshold=threshold)
        if not groups:
            console.print("[dim]nothing to merge at this threshold[/dim]")
            return
        for number, group in enumerate(groups, 1):
            console.print(f"[bold]group {number}[/bold] ({group[0].kind}, {len(group)} facts)")
            for item in group:
                console.print(f"  [cyan]{item.id}[/cyan] {escape(item.content)}")
        console.print("[dim]Re-run without --dry-run to merge them (one model call per group).[/dim]")
        return

    from chimera.memory.consolidate import model_summarizer
    from chimera.orchestration.metering import MeteredBackend as _Meter
    from chimera.providers import LLMGateway, MissingCredentialsError

    if not get_settings().can_answer():
        console.print("[red]no provider API key configured, and no local model[/red] — set one to summarise")
        raise typer.Exit(1)
    # Each merge is a model call, metered here and written as one row of this command's own: it
    # belongs to no conversation, and the Cost screen could not see it. Written on the way out
    # whatever happened, so a run that failed part-way bills the merges it paid for.
    meter = _Meter(LLMGateway(), label="consolidate")
    usage_id = f"consolidate:{uuid4().hex[:12]}"
    try:
        removed = _memory_manager().consolidate(model_summarizer(meter), threshold=threshold)
    except MissingCredentialsError as exc:
        _record_merge_spend(get_settings(), meter, usage_id, route_kind=None)
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception:
        _record_merge_spend(get_settings(), meter, usage_id, route_kind=None)
        raise
    _record_merge_spend(get_settings(), meter, usage_id, route_kind=None)
    # "$0.0000" and "cost unknown" are different answers, and a missing line was neither.
    cost = "cost unknown (a merge had no price)" if meter.usd is None else f"${meter.usd:.4f}"
    console.print(f"consolidated: merged away {removed} redundant memory item(s) · {cost}")


@memory_app.command("export")
def memory_export(
    fmt: str = typer.Option("json", "--format", help="json | markdown"),
    out: str = typer.Option(None, "--out", help="Write to this file (default: print to stdout)."),
) -> None:
    """Export all memory as JSON or Markdown, locally. Secrets are masked; metadata is left out."""
    from datetime import UTC, datetime

    from chimera.memory.export import export_memory

    try:
        text = export_memory(
            _memory_manager().store.all(), fmt,
            exported_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if out:
        Path(out).write_text(text, encoding="utf-8")
        console.print(f"wrote {out}")
        return
    # Plain print, not Rich: the output is a file's content and Rich would read `[...]` as markup.
    typer.echo(text)


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
