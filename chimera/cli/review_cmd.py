"""``chimera review`` — an experimental code review of a change, from outside the author's family.

Opt-in by being a command: nothing else in the product calls it, so it changes no existing
behaviour. It says it is experimental in its help, in its first line of output and in the JSON
(``"experimental": true``), because its only measurement so far is `bench/review_seeded`: one
seeded-bug set of this repository's own diffs, not a public benchmark.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

from chimera.providers.gateway import SupportsComplete

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.review import ReviewerChoice
    from chimera.review.effort import Effort

#: Progress and errors go to stderr, so with ``--json`` stdout holds the report and nothing else.
console = Console(stderr=True)


def _backend() -> SupportsComplete:
    """The model backend. A function so a test can hand the command a fake without a network."""
    from chimera.providers.gateway import LLMGateway

    return LLMGateway()


def _reviewer(author: str, explicit: str) -> ReviewerChoice:
    from chimera.config import get_settings
    from chimera.providers.catalog import CATALOG, resolve_tiers
    from chimera.providers.discovery import is_local_model
    from chimera.review.family import MEASURED_REVIEWERS, choose_reviewer

    settings = get_settings()
    measured = list(MEASURED_REVIEWERS)
    ladder = resolve_tiers(settings)
    rungs = [ladder.top, ladder.mid, ladder.weak]
    panel = list(settings.fusion_panel)
    catalogue = [e.slug for tier in ("top", "mid") for e in CATALOG if e.tier == tier]
    providers = settings.configured_providers()
    reachable = None
    if providers:
        # Only a slug the configured keys can call; the same test the tier resolver applies.
        reachable = {
            m for m in (*measured, *rungs, *panel, *catalogue)
            if m.split("/", 1)[0] in providers or is_local_model(m)
        }
    return choose_reviewer(
        author, explicit=explicit, setting=settings.review_model, measured=measured,
        ladder=rungs, panel=panel, catalogue=catalogue, reachable=reachable,
    )


def _effort(effort: str | None, no_verify: bool) -> Effort:
    """The level asked for. ``--no-verify`` is ``low``; naming it beside another level is refused
    rather than letting one flag silently win over the other."""
    from chimera.review.effort import DEFAULT_EFFORT, EFFORTS

    if effort is None:
        return "low" if no_verify else DEFAULT_EFFORT
    for level in EFFORTS:
        if effort.strip().lower() == level:
            if no_verify and level != "low":
                console.print(f"review: --no-verify is --effort low, and --effort {level} asks "
                              "for the verifier; pass one of them", style="red", markup=False,
                              highlight=False)
                raise typer.Exit(code=2)
            return level
    console.print(f"review: --effort takes low, medium or high, not {effort!r}", style="red",
                  markup=False, highlight=False)
    raise typer.Exit(code=2)


def review(
    revision_range: str = typer.Argument(
        None, metavar="[RANGE]",
        help="A revision range, such as main..HEAD. Omit it to review the working tree.",
    ),
    base: str = typer.Option(
        None, "--base",
        help="Review the working tree against its merge base with this ref (default: main).",
    ),
    # A string, not a Path: the command snapshot records a default by its repr, and a Path's repr
    # names the platform (WindowsPath / PosixPath), so the snapshot would differ between machines.
    repo: str = typer.Option(".", "--repo", help="The repository to review."),
    as_json: bool = typer.Option(
        False, "--json", help="Print only the report, as JSON (schema chimera.review/1)."
    ),
    reviewer_model: str = typer.Option(
        "", "--reviewer-model",
        help="Review with this model (default: CHIMERA_REVIEW_MODEL, else another family's).",
    ),
    author_model: str = typer.Option(
        "", "--author-model",
        help="The model that wrote the change (default: CHIMERA_DEFAULT_MODEL).",
    ),
    effort: str = typer.Option(
        None, "--effort",
        help="low: finder only. medium: also hide findings under confidence 0.8, then verify. "
        "high (default): finder and verifier, no cut. See bench/review_confidence_cut.",
    ),
    no_verify: bool = typer.Option(
        False, "--no-verify",
        help="Show every located finding, without the second-stage check (same as --effort low).",
    ),
    show_dropped: bool = typer.Option(
        False, "--show-dropped", help="Also list the findings the checks dropped, with the reason."
    ),
    context: int = typer.Option(10, "--context", help="Lines of context around each change."),
) -> None:
    """[experimental] Review a change: findings first, P0 to P3, from a model of another family.

    A finder reports every defect it sees with a confidence; a separate verifier drops a finding
    only when the diff does not show the code it describes or contradicts it. Each finding carries
    file:line, the evidence and the consequence. When nothing survives, the review says "no
    findings" and lists the residual risks and untested paths; a review that could not finish says
    "incomplete" instead. Untracked files are not reviewed.

    --effort sets how much checking runs: low skips the verifier, high (the default) runs it, and
    medium also hides, before the verifier, the findings the finder itself rated under 0.8. Each
    hidden finding is listed by --show-dropped.
    """
    from chimera.config import get_settings
    from chimera.review import CautiousVerifier, DiffError, KeepAll, collect, render_text, untracked
    from chimera.review import review as run_review
    from chimera.review.effort import confidence_cut, verifies

    level = _effort(effort, no_verify)
    settings = get_settings()
    if not settings.can_answer():
        console.print(
            "[red]No provider key configured, and the default model is not a local one. "
            "Run 'chimera doctor'.[/red]"
        )
        raise typer.Exit(code=1)
    root = Path(repo)
    try:
        diff = collect(root, base=base, revision_range=revision_range, context=context)
    except DiffError as exc:
        console.print(f"review: {exc}", style="red", markup=False, highlight=False)
        raise typer.Exit(code=2) from exc

    choice = _reviewer(author_model or settings.default_model, reviewer_model)
    backend = _backend()
    verifier = CautiousVerifier(backend, choice.model) if verifies(level) else KeepAll()
    skipped = 0 if revision_range else len(untracked(root))
    if not as_json:
        console.print(
            f"[dim]review (experimental): {len(diff.files)} file(s), reviewer {choice.model}, "
            f"effort {level}…[/dim]"
        )
    report = run_review(
        diff, backend, choice, verifier, untracked_skipped=skipped,
        confidence_cut=confidence_cut(level), effort=level,
    )
    # Written as is, not through Rich: a console wraps at its width and reads brackets as markup,
    # and every line here is a model's quote of somebody's code.
    text = report.to_json() + "\n" if as_json else render_text(report, show_dropped=show_dropped)
    sys.stdout.write(text)
