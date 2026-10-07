"""Model evolution: the ``chimera evolve`` group.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.cli.commands.misc import _right_hand_builder
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.ecosystem import TrajectoryCollector
    from chimera.eval.scenarios import SessionBuilder



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
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
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
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
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
    k: int = typer.Option(
        3,
        "--k",
        help="Suite runs per candidate — one samples, two alert, three decide. Multiplies cost.",
    ),
) -> None:
    """Self-optimize the agent spec (OpenJarvis meta-search) against the daily scenarios.

    Each round a model proposes a coordinated edit to the spec; the candidate is scored on
    the daily scenarios and kept only on non-regression. Uses real model calls.
    """
    from chimera.core.agent import DEFAULT_SYSTEM_PROMPT
    from chimera.ecosystem import AgentSpec, model_proposer, search_spec
    from chimera.eval import daily_scenarios, scenario_scorer
    from chimera.eval.scenarios import CONTROL
    from chimera.eval.spec_tuning import DEFAULT_HOLDOUT, ControlRowFailed, SplitScore
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()

    # Scored through the SAME builder `chimera scenarios` uses. This command and that one used to
    # run different solvers over the same seven prompts — two rulers under one name — and since the
    # suite sat at 7/7 the non-regression criterion here could only ever tie.
    def _spec_builder(spec: AgentSpec) -> SessionBuilder:
        return _right_hand_builder(
            spec.model or model,
            spec.max_steps,
            system_prompt=spec.system_prompt or DEFAULT_SYSTEM_PROMPT,
        )

    scenarios = daily_scenarios()
    splits: list[SplitScore] = []
    scorer = scenario_scorer(_spec_builder, scenarios, k=k, on_split=splits.append)
    # The rows the objective is actually computed over: control rows are a validity gate and the
    # holdout is withheld, so neither belongs in the trial count the significance gate uses.
    graded_rows = len(scenarios) - len(DEFAULT_HOLDOUT) - sum(
        1 for s in scenarios if getattr(s, "block", "") == CONTROL
    )
    initial = AgentSpec(model=model, max_steps=max_steps)
    try:
        result = search_spec(
            initial,
            scorer,
            model_proposer(gateway, model),
            rounds=rounds,
            # The number the gate needs to be a decision instead of a comparison. Without it the
            # promotion rule is `>` on a fraction quantised in steps of 1/len(scenarios), and a
            # candidate identical to the incumbent cleared that 29.6% of the time.
            trials=graded_rows * max(1, k),
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except ControlRowFailed as exc:
        # Not a bad round — no round. The control rows are the validity gate, and a run whose ruler
        # failed has no score to report rather than a low one.
        console.print(f"[red]invalid run:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title="Spec meta-search", show_header=True)
    table.add_column("round")
    table.add_column("score")
    table.add_column("holdout")
    table.add_column("no regression")
    table.add_column("kept")
    for index, step in enumerate(result.history):
        # Two columns because they are two facts, and printing the first under the second's heading
        # is what let a TIE — the most common outcome against a saturated ruler — read as "kept".
        # The holdout beside the objective, never folded into it. A holdout that tracks the score is
        # a tuner generalising; one that stays flat while the score climbs is a tuner learning the
        # rows, and the objective alone cannot tell those apart.
        away = splits[index].holdout if index < len(splits) else None
        table.add_row(
            str(index),
            f"{step.score:.3f}",
            "—" if away is None else f"{away:.3f}",
            "✓" if step.accepted else "·",
            "✓" if step.advanced else "·",
        )
    console.print(table)
    console.print(f"[green]best score[/green] {result.best_score:.3f}")
    if splits:
        console.print(
            f"[dim]scored on {splits[0].graded_rows} rows; "
            f"{splits[0].holdout_rows} held out and never optimised against; "
            f"control rows are a gate, not points.[/dim]"
        )
    if result.undecidable:
        # Loud, because "nothing was promoted" and "nothing could have been promoted" look identical
        # in the table above and call for opposite responses.
        console.print(f"[yellow]the gate could not decide:[/yellow] {result.undecidable}")
    console.print(f"[dim]best spec:[/dim] {result.best.to_dict()}")
