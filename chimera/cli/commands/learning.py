"""Learning artifacts: playbook, lessons, rubric-grade, migrate, decisions, decide, review, code.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.markup import escape
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.core.autonomous import AutonomousResult
    from chimera.evolution import ExperienceBuffer, Playbook



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


def _curation_outcome(result: AutonomousResult, *, from_errors: bool) -> str:
    """Build the reflect-step text the ACE curator sees after a run.

    Baseline = verdict + final answer. With ``from_errors`` (Level-2 P3) also feed the concrete error
    evidence the curator was previously blind to: the failing verifier output from the last failed
    attempt (why it failed) and the diff that ultimately passed (what worked). A curator instructed to
    generalise turns that into process pitfalls ("run the given test first", "re-check a second case")
    rather than platitudes. Bounded so the curation prompt stays small.
    """
    verdict = "succeeded" if result.success else "failed"
    parts = [f"The task {verdict} after {len(result.attempts)} attempt(s)."]
    if from_errors and result.attempts:
        failed = [a for a in result.attempts if not a.success and (a.verify_output or a.feedback)]
        if failed:
            why = (failed[-1].verify_output or failed[-1].feedback).strip()
            if why:
                parts.append(f"What went wrong (verifier output from a failed attempt):\n{why[:400]}")
        if result.success and (fix := (result.attempts[-1].diff_summary or "").strip()):
            parts.append(f"What fixed it (the diff that passed):\n{fix[:400]}")
    parts.append(f"Final answer: {result.answer[:300]}")
    return "\n\n".join(parts)


@playbook_app.command("show")
def playbook_show(
    ids: bool = typer.Option(False, "--ids", help="Show each bullet's id (for `playbook vouch`)."),
) -> None:
    """Print the current active playbook (top strategies by score)."""
    text = _load_playbook().render(max_items=100, with_ids=ids)
    console.print(
        escape(text) if text else "[dim]Playbook is empty — add bullets or curate from a run outcome.[/dim]"
    )


@playbook_app.command("vouch")
def playbook_vouch(
    item_id: str = typer.Argument(..., help="The bullet's id, from `chimera playbook show --ids`."),
) -> None:
    """Mark a bullet learned under taint as clean: you have read it and it is yours to keep.

    Its [unverified] label goes and it no longer arms a run. `playbook add` with the same text
    cannot do this: it reinforces the bullet and keeps its provenance, so that a run restating a
    poisoned bullet does not launder it.
    """
    playbook = _load_playbook()
    item = playbook.vouch(item_id)
    if item is None:
        console.print(f"[red]No bullet with id {escape(item_id)}.[/red]")
        raise typer.Exit(code=1)
    _save_playbook(playbook)
    console.print(f"[green]Vouched[/green] {escape(item.id)}: {escape(item.content)}")


lessons_app = typer.Typer(
    help="Experience lessons the autonomous loop recalls into later runs on similar tasks.",
    no_args_is_help=True,
)
app.add_typer(lessons_app, name="lessons")


def _lessons_buffer() -> ExperienceBuffer:
    from chimera.evolution import ExperienceBuffer

    return ExperienceBuffer(get_settings().home / "experience.json")


@lessons_app.command("show")
def lessons_show(
    tainted: bool = typer.Option(False, "--tainted", help="Only the lessons learned under taint."),
) -> None:
    """List the recorded lessons with their seq, newest last."""
    rows = [e for e in _lessons_buffer().all() if not tainted or e.provenance == "tainted"]
    if not rows:
        console.print("[dim]No lessons recorded.[/dim]")
        return
    for exp in rows:
        mark = " [yellow][unverified][/yellow]" if exp.provenance == "tainted" else ""
        detail = f" — {escape(exp.detail[:120])}" if exp.detail else ""
        console.print(f"{exp.seq:>5}  [{exp.outcome}] {escape(exp.task[:80])}{detail}{mark}")


@lessons_app.command("vouch")
def lessons_vouch(
    seq: int = typer.Argument(..., help="The lesson's seq, from `chimera lessons show --tainted`."),
) -> None:
    """Mark a lesson learned under taint as clean: its label goes and it no longer arms a run."""
    if not _lessons_buffer().vouch(seq):
        console.print(f"[red]No lesson with seq {seq}.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Vouched[/green] lesson {seq}.")


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

    if not get_settings().can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    playbook = _load_playbook()
    curator = PlaybookCurator(BackendDeltaProposer(LLMGateway(), model))
    applied = curator.curate(playbook, task, outcome)
    _save_playbook(playbook)
    console.print(f"[green]Applied {applied} delta(s)[/green] — {len(playbook.active())} active bullets.")


@app.command("rubric-grade", hidden=True)
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

    if not get_settings().can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
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
    source: str = typer.Argument(..., help="Source agent: hermes | openclaw | claude."),
    path: str = typer.Argument(
        ..., help="Path to the source agent's home directory (for claude: ~/.claude or a project)."
    ),
    apply: bool = typer.Option(False, "--apply", help="Write artifacts (default: dry-run preview)."),
    home: str = typer.Option(None, "--home", help="Target Chimera home (default: from config)."),
) -> None:
    """Import config + skills from another agent; --apply also merges long-term memory.

    ``claude`` imports memory only (CLAUDE.md and memory/*.md), as unverified facts, never persona:
    the dry-run lists every fact it would write.
    """
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
    from chimera.migration import ClaudeImporter

    if isinstance(importer, ClaudeImporter):
        from chimera.migration.importers import registered_project_keys

        # So a note from one repository's Claude memory is filed under that repository when it is
        # registered here, rather than recalled in every folder (see ClaudeImporter).
        importer.projects = tuple(registered_project_keys(Path(target)))
    if apply:
        from chimera.evolution.wiring import semantic_embed
        from chimera.memory import MemoryManager
        from chimera.memory.backend import open_memory_store

        # Honor the configured backend + embedder, or the merge writes to a store the agent never
        # reads (json while it recalls from sqlite) — a silent no-op that reports success. Through
        # the same opener every surface uses, pointed at `--home` when one was given.
        _s = get_settings()
        if Path(target) != Path(_s.home):
            _s = _s.model_copy(update={"home": Path(target)})
        manager = MemoryManager(open_memory_store(_s), embed=semantic_embed(_s))
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
    if result.candidates and not apply:
        # The review IS this list: --apply writes exactly these, so they are shown in full rather
        # than counted. Escaped, because a note can hold text Rich would read as markup.
        console.print(f"[bold]{len(result.candidates)} fact(s) would be imported as unverified:[/bold]")
        # Each with where it would apply: a note about one repository that no registered folder
        # matches would be recalled everywhere, and the review is the only place to see that.
        scopes = {i.content: i.project for i in importer.memory_items()}
        for number, fact in enumerate(result.candidates, 1):
            where = scopes.get(fact)
            label = f"project {where}" if where else "everywhere"
            console.print(f"  {number:>4}. {escape(fact)} [dim]({escape(label)})[/dim]")
    for note in result.notes:
        console.print(f"[yellow]note:[/yellow] {note}")
    if not apply:
        console.print("[dim]Re-run with --apply to write the imported artifacts.[/dim]")


from chimera.cli.decisions_cmd import decisions_app  # noqa: E402

app.add_typer(decisions_app, name="decisions")
from chimera.cli.decide_cmd import decide as _decide  # noqa: E402

app.command("decide")(_decide)
from chimera.cli.review_cmd import review as _review  # noqa: E402

app.command("review")(_review)
from chimera.cli.code_cmd import code_app  # noqa: E402

app.add_typer(code_app, name="code")
