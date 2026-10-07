"""LLM-Fusion commands: fuse, maturity, fusion-receipts, orchestrate, brief, delegations.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape
from rich.panel import Panel

from chimera.cli.commands._shared import app, console
from chimera.config import get_settings


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
    from chimera.fusion import FusionEngine, FusionFailed
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
        except (MissingCredentialsError, FusionFailed) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        console.print(escape(str(result.content)))
        sc_total = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if sc_total:
            console.print(f"[dim]self-consistency over {best_of} samples · total tokens: {sc_total}[/dim]")
        return

    # The factory, not `FusionConfig.from_settings()`: the bare builder reads CHIMERA_FUSION_PANEL,
    # whose default is three frontier models, so `chimera fuse` convened Opus + GPT-5.5 + Gemini under
    # every cost mode while `chimera models` named the ladder as "the cast --fuse convenes".
    from chimera.fusion.factory import fusion_config

    config = fusion_config(get_settings())
    if selective is not None:
        config.mode = "selective" if selective else "full"

    try:
        engine = FusionEngine(LLMGateway(), config)
        trace = engine.run([Message(role="user", content=prompt)])
    except (MissingCredentialsError, FusionFailed) as exc:
        # FusionFailed: every panelist errored or came back blank, so there is nothing to fuse.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    total = trace.total_tokens()
    # A fallback is one panelist's answer, not a fusion: the desktop badges it, and the terminal has
    # to say it too, or a judge outage reads as a fused result. The reason is already redacted.
    fell_back = trace.aggregation == "fallback"
    if fell_back:
        console.print(
            f"[yellow]fusion {trace.fallback_stage} failed ({escape(trace.fallback_reason)}); "
            "this is a panel answer, not a fused one[/yellow]"
        )
    final_title = (
        "[bold yellow]panel answer (aggregation failed)[/bold yellow]"
        if fell_back
        else "[bold green]final[/bold green]"
    )
    if show_panel:
        for response in trace.panel:
            body = response.error or response.content
            # Panel(str) parses Rich markup like console.print does, so "[/]" in model text is the
            # same MarkupError the plain prints were fixed for — after the whole fusion was paid for.
            console.print(Panel(escape(str(body)), title=f"panel: {response.model}", title_align="left"))
        if trace.early_stopped:
            console.print("[dim]probe models agreed — skipped the rest of the panel + judge[/dim]")
        elif fell_back and not trace.judge_analysis:
            console.print("[dim]judge: no analysis (see the warning above)[/dim]")
        else:
            console.print(Panel(escape(str(trace.judge_analysis)), title="judge", title_align="left"))
        console.print(Panel(escape(str(trace.final)), title=final_title, title_align="left"))
        if total is not None:
            by = trace.by_stage()
            rows = "  ".join(
                f"{stage}={by[stage][0]}/{by[stage][1]}"
                for stage in ("panel", "judge", "synth")
                if stage in by
            )
            console.print(f"[dim]tokens in/out — {rows}  ·  total {total}[/dim]")
    else:
        console.print(escape(str(trace.final)))
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
    """Render the maturity scorecard: which coverage-IDs have their test file (presence, not passing)."""
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
    from chimera.fusion.factory import fusion_engine
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.budget import EffortPolicy
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
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
        fusion=fusion_engine(gateway),
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

    console.print(escape(str(result.answer)))
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
    from chimera.fusion.factory import fusion_engine
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.brief import brief_task, load_brief, specs_from_brief
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
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
        fusion=fusion_engine(gateway),
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
