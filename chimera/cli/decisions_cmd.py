"""``chimera decisions`` — choose the System One model, read the decision log, label an answer, report,
refit (study 22, phase 2).

The log is ``<home>/decisions/decisions.jsonl`` (`chimera/decisions/log.py`): every answer a decider
gave, with its raw number. A label says what was true — for ``governance.danger``, whether the
action **was dangerous**, which is not whether it was approved: a person approves a dangerous action
they meant to run. ``refit`` fits the deployment's own map on the labelled rows and writes it only
with ``--write``.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

decisions_app = typer.Typer(
    help="Typed decisions: which model answers them, the log of what they answered, labels, a report and a refit.",
    no_args_is_help=True,
)
console = Console()


def _home() -> Path:
    from chimera.config import get_settings

    return Path(get_settings().home)


def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


@decisions_app.command("log")
def show_log(
    limit: int = typer.Option(20, "--limit", "-n", help="How many of the latest answers."),
    unlabelled: bool = typer.Option(False, "--unlabelled", help="Only answers without a label."),
) -> None:
    """The latest answers, newest last, with their label when one was given."""
    from chimera.decisions.log import log_path, read

    rows = read(log_path(_home()))
    if unlabelled:
        rows = [r for r in rows if r.label is None]
    if not rows:
        console.print("[dim]the decision log is empty — no decider has answered on this home yet[/dim]")
        return
    table = Table(title="Decision log")
    for column in ("id", "when", "decision", "p", "cal", "label", "state"):
        table.add_column(column)
    for r in rows[-limit:]:
        a = r.answer
        p = a.get("p")
        table.add_row(
            r.id, time.strftime("%m-%d %H:%M", time.localtime(float(a.get("at") or 0))), str(a.get("decision", "")),
            "halt" if a.get("halt") else _fmt(float(p) if isinstance(p, (int, float)) else None, 2),
            "yes" if a.get("calibrated") else "no",
            "—" if r.label is None else f"{r.label} ({r.source})", str(a.get("state", ""))[:70].replace("\n", " "),
        )
    console.print(table)
    console.print("[dim]label one with: chimera decisions label <id> --yes | --no[/dim]")


@decisions_app.command("label")
def label(
    entry_id: str = typer.Argument(..., help="The id from `chimera decisions log` or the approval card."),
    yes: bool = typer.Option(False, "--yes", "-y", help="The question's event happened (governance: it WAS dangerous)."),
    no: bool = typer.Option(False, "--no", "-n", help="It did not (governance: it was NOT dangerous)."),
    note: str = typer.Option("", "--note", help="Why, for whoever reads the label later."),
) -> None:
    """Say what was true for one answer. A later label for the same id replaces an earlier one."""
    from chimera.decisions.log import DecisionLog, find, log_path

    if yes == no:
        console.print("[yellow]say which: --yes or --no[/yellow]")
        raise typer.Exit(code=1)
    path = log_path(_home())
    if find(path, entry_id) is None:
        console.print(f"[yellow]no answer with id {entry_id} in the decision log[/yellow]")
        raise typer.Exit(code=1)
    if not DecisionLog(path).outcome(entry_id, yes, source="cli", note=note):
        console.print("[red]could not write the label[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]labelled[/green] {entry_id} = {1 if yes else 0}")


@decisions_app.command("report")
def show_report() -> None:
    """What the log holds: availability, the review budget, label coverage, and — where labels exist —
    catch and false refusal at the REVIEW threshold."""
    from chimera.config import get_settings
    from chimera.decisions.labels import report
    from chimera.decisions.log import log_path, read

    settings = get_settings()
    review_at = float(settings.governance_band_review_at)
    allow_below = float(settings.governance_band_allow_below)
    rows = read(log_path(Path(settings.home)))
    if not rows:
        console.print("[dim]the decision log is empty — no decider has answered on this home yet "
                      "(the REVIEW band is off unless CHIMERA_GOVERNANCE_BAND=on)[/dim]")
        return
    for g in report(rows, review_at=review_at, allow_below=allow_below):
        decision, backend, model, digest, build = g.key
        console.print(f"[bold]{decision}[/bold]  {backend}/{model} {build or ''}  instrument {digest}")
        console.print(
            f"  answers {g.answers} · halts {g.halts} · cached {g.cached} · calibrated {g.calibrated}"
        )
        console.print(
            f"  band (review ≥ {review_at:.2f}, allow < {allow_below:.2f}): review {g.regions['review']} · "
            f"uncertain {g.regions['uncertain']} · allow {g.regions['allow']} · no number {g.regions['no_p']}"
            f"  →  review budget {_fmt(g.review_per_100, 1)} cards / 100 decisions"
        )
        by = g.labelled_by_region
        console.print(
            f"  labelled {g.labelled} ({g.positives} positive) — by region: review {by['review']} · "
            f"uncertain {by['uncertain']} · allow {by['allow']} · no number {by['no_p']}"
        )
        if g.labelled and by["review"] == g.labelled:
            console.print("  [yellow]every label comes from the REVIEW region: nothing here speaks for the "
                          "ALLOW region (label a few with `chimera decisions log --unlabelled`)[/yellow]")
        if g.catch is not None or g.false_refusal is not None:
            catch = f"{g.catch[0]}/{g.catch[1]}" if g.catch else "—"
            fr = f"{g.false_refusal[0]}/{g.false_refusal[1]}" if g.false_refusal else "—"
            console.print(f"  at review_at: catch {catch} · false refusal {fr} · "
                          f"Brier {_fmt(g.brier)} · ECE {_fmt(g.ece)} (in-sample)")
    from chimera.decisions.drift import alerts, describe

    found = alerts(rows, review_at=review_at, allow_below=allow_below)
    if found:
        console.print("[bold]drift alerts[/bold] [dim](annotations from the log alone; nothing is gated by them)[/dim]")
        for alert in found:
            console.print(f"  [yellow]{alert.kind}[/yellow]  {describe(alert)}")


@decisions_app.command("refit")
def do_refit(
    write: bool = typer.Option(False, "--write", help="Save the refitted maps to <home>/decisions/maps.json."),
) -> None:
    """Fit this deployment's own map on its labelled answers — pooled with the shipped rows while it
    has fewer than 20 labels of a class. Prints before writing; writes only with --write."""
    from chimera.config import get_settings
    from chimera.decisions.calibration import CalibrationMaps
    from chimera.decisions.factory import maps_path
    from chimera.decisions.labels import refit
    from chimera.decisions.log import log_path, read

    settings = get_settings()
    rows = read(log_path(Path(settings.home)))
    path = maps_path(settings)
    own = CalibrationMaps.load(path)
    current = CalibrationMaps.shipped().merged(own)
    results = refit(rows, current)
    if not results:
        console.print("[dim]no labelled answers yet — nothing to fit[/dim]")
        return
    fitted = []
    for r in results:
        decision, backend, model, digest, build = r.key
        console.print(f"[bold]{decision}[/bold]  {backend}/{model} {build or ''}  instrument {digest}")
        console.print(f"  {r.reason}")
        if r.map is None:
            # "No map" from this refit does not remove a map an earlier refit wrote: a deployment
            # that ran `--write` before the separation guards existed may hold the very step they now
            # refuse, and the Decider keeps applying it. Say so, or the output reads as "none active".
            earlier = own.find(decision, backend, model, digest)
            if earlier is None:
                console.print("  [yellow]no map[/yellow]")
            else:
                console.print(
                    f"  [yellow]no new map — but the map written on {earlier.fitted_at} "
                    f"(a={earlier.a:.4f} b={earlier.b:.4f}, {earlier.n} rows) stays active[/yellow]; "
                    f"the Decider keeps applying it. To drop it, delete the entry with id {earlier.id} from {path}"
                )
            continue
        console.print(
            f"  a={r.map.a:.4f} b={r.map.b:.4f} on {r.map.n} rows · on our own rows, in-sample: "
            f"Brier {_fmt(r.brier_before)} → {_fmt(r.brier_after)}, ECE {_fmt(r.ece_before)} → {_fmt(r.ece_after)}"
        )
        fitted.append(r.map)
    if not write:
        console.print("[dim]nothing written — re-run with --write to save[/dim]")
        return
    for m in fitted:
        own.add(m)
    own.save(path)
    console.print(f"[green]wrote {len(fitted)} map(s)[/green] to {path}")


@decisions_app.command("models")
def show_models() -> None:
    """The System One models OpenRouter lists, which one is active, and which carry a calibration map."""
    from chimera.config import get_settings
    from chimera.decisions.calibration import CalibrationMaps
    from chimera.decisions.factory import default_model_for, maps_path
    from chimera.decisions.system_one import BACKEND, list_models

    settings = get_settings()
    backend = (settings.decision_backend or "local_logprob").strip()
    model = (settings.decision_model or "").strip()
    listing = list_models(CalibrationMaps.shipped().merged(CalibrationMaps.load(maps_path(settings))))
    active = (model or default_model_for(settings, backend)) if backend == BACKEND else ""
    table = Table(title="System One models (OpenRouter, output: decisions)")
    for column in ("", "model", "$/1M in", "context", "questions", "calibrated", "choosable"):
        table.add_column(column)
    for m in listing.models:
        price = "—" if m.input_per_m is None else ("free" if m.input_per_m == 0 else f"{m.input_per_m:g}")
        table.add_row(
            "*" if m.slug == active else "", m.slug, price, "—" if m.context is None else str(m.context),
            ", ".join(m.questions) or "—", "yes" if m.calibrated else "no",
            "yes" if m.selectable else f"no ({m.refusal.replace('_', ' ')})",
        )
    console.print(table)
    if listing.stale:
        console.print(f"[yellow]OpenRouter's index was not reached ({listing.reason}); showing the default only[/yellow]")
    console.print(f"active: [bold]{backend}[/bold] / {model or default_model_for(settings, backend) + ' (default)'}")
    console.print(
        "[dim]uncalibrated = no map for that model yet: its confidence is read raw. "
        "Choose with: chimera decisions use <backend> [model][/dim]"
    )


@decisions_app.command("use")
def use(
    backend: str = typer.Argument(..., help="local_logprob | hosted_verbalized | openrouter_decisions"),
    model: str = typer.Argument("", help="Empty = the backend's measured default. For openrouter_decisions, a listed slug."),
) -> None:
    """Choose the backend (and model) that answers typed decisions — written to ``.env`` in this folder,
    the same pair and the same check as the desktop's System One card."""
    from chimera.api.config_api import patch_config
    from chimera.config import get_settings
    from chimera.decisions.factory import default_model_for, openrouter_key_set

    try:
        # Both keys, always: naming a backend without a model means its default, and leaving the old
        # model in place would hand, say, a Jev slug to Ollama.
        patch_config(
            {"CHIMERA_DECISION_BACKEND": backend.strip(), "CHIMERA_DECISION_MODEL": model.strip()},
            env_path=Path.cwd() / ".env",
        )
    except ValueError as exc:
        console.print(f"[red]refused:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    settings = get_settings()
    shown = model.strip() or f"{default_model_for(settings, backend.strip())} (default)"
    console.print(f"[green]Set[/green] decisions: {backend.strip()} / {shown}")
    if backend.strip() == "openrouter_decisions" and not openrouter_key_set(settings):
        console.print("[yellow]no OpenRouter key is set — every decision will halt until one is[/yellow]")
