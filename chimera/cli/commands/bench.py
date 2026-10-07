"""Benchmarks and rulers: ``chimera bench`` and its subcommands, ``chimera measure``.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.markup import escape
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.cli.commands.learning import rubric_grade
from chimera.cli.commands.setup import context_curve_cmd
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.providers import SupportsComplete



# Named `measure`, not `bench`: `chimera bench` already exists (the A/B harness at the bottom of
# this file), and mounting a Typer sub-app under that name SHADOWS it — `chimera bench` stopped
# exiting 1 without a key and started exiting 2 for a missing subcommand. Caught by the existing
# `test_bench_without_key_exits`, which is the only reason I know the command was there at all.
#: `chimera bench` is a group since S30-70. Run bare (with its own options) it is the
#: continuous-evolution benchmark it always was; every other ruler is a subcommand of it — `chimera
#: bench fusion`, `chimera bench compare`, ... Each ruler's old top-level name stays registered,
#: hidden from `--help`, with the same options, so no script, doc or CI step breaks.
#: Defined first because the rulers below register on it as they are declared.
bench_app = typer.Typer(help="The continuous-evolution benchmark, and every other ruler as a subcommand.")

measure_app = typer.Typer(help="Run the rulers this project measures itself with.")

#: Module-level singletons, matching the rest of this file: `typer.Argument(...)` evaluated in a
#: default is a call at import time, which ruff's B008 flags for the reason it always does — the
#: object is shared across every invocation.
_BENCH_ROOT = typer.Argument(..., help="Folder to index and probe.")
_BENCH_CORPUS = typer.Argument(..., help="JSONL of {query, text, success} records.")
_BENCH_K = typer.Option(10, help="Retrieve this many chunks per probe.")
_BENCH_PROBES = typer.Option(200, help="Cap the probe count; each one is a query.")
_RERANK_K = typer.Option(5, help="Rank cut-off for the leave-one-out scoring.")


@measure_app.command("rag")
def bench_rag(
    root: Path = _BENCH_ROOT,
    k: int = _BENCH_K,
    max_probes: int = _BENCH_PROBES,
    semantic: bool = typer.Option(
        False, "--semantic", help="Measure the vector and hybrid arms too. Costs money."
    ),
) -> None:
    """Recall@k of each retriever over a real folder — lexical, and vector when an embedder is set.

    This is the measurement `chimera/rag/__init__.py` names when it says the retriever's existence
    is not a claim that it helps. That sentence pointed at a module you could not run: `rag_bench`
    had no caller outside its own test and was not exported from `chimera.eval`.

    Without `--semantic` no embedder is passed, so the vector and hybrid figures come back as None
    rather than zero — an embedder that was never called did not fail, and printing 0.0 invites the
    wrong conclusion.

    With it, the run that `bench/rag/RESULTS.md` reports is reproducible from the CLI rather than
    from a script somebody has to write. It costs an embedding pass over the corpus: about two cents
    for this repository's 3,459 chunks and 400 probes, and the figure it produces belongs to the
    embedder that produced it — vector spaces do not convert between models.
    """
    import tempfile

    from chimera.eval import run_rag_bench

    # `ignore_cleanup_errors` because the index is SQLite and this is Windows.
    #
    # The bench leaves its connection open, and Windows refuses to delete a file another handle
    # holds — so the cleanup raised `PermissionError(13)` and took the whole measurement with it,
    # after the measurement had already succeeded. Caught by the Windows CI job, which exists for
    # exactly this family of Unix assumption.
    #
    # The scratch index is worth nothing once the report is printed. Failing to remove it must not
    # fail the thing it was for.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        embed = None
        if semantic:
            from chimera.evolution.wiring import semantic_embed

            embed = semantic_embed(get_settings(), force=True)
            console.print(
                f"[dim]embedding with {get_settings().embed_model} — this costs money[/dim]"
            )
        report = run_rag_bench(
            Path(root),
            index_path=Path(tmp) / "index.db",
            embed=embed,
            embedder=get_settings().embed_model if semantic else "",
            k=k,
            max_probes=max_probes,
        )
    table = Table(title=f"RAG recall@{k} over {root}", show_header=True, header_style="bold")
    table.add_column("retriever")
    table.add_column("recall", justify="right")
    for name, value in (
        ("keyword", report.keyword_recall),
        ("vector", report.vector_recall),
        ("hybrid", report.hybrid_recall),
    ):
        # "not measured" rather than a dash: the reader has to be able to tell an absent embedder
        # from a retriever that scored nothing.
        shown = f"{value:.3f}" if isinstance(value, float) else "[dim]not measured[/dim]"
        table.add_row(name, shown)
    console.print(table)
    console.print(f"[dim]{report.probes} probes over {report.chunks} chunks[/dim]")
    if report.embedder:
        # Inside the report, not in a footnote: a recall figure without the model that produced it
        # is a number about nothing in particular.
        console.print(f"[dim]embedder: {report.embedder} ({report.dimensions} dimensions)[/dim]")
    # The number that says whether a semantic layer could help at all: the share of probes keyword
    # retrieval misses. A headroom near zero means the embedding bill buys nothing here.
    console.print(f"[dim]headroom for a semantic layer: {report.headroom:.1%}[/dim]")
    for note in report.notes:
        console.print(f"[dim]{escape(note)}[/dim]")


@measure_app.command("reranker")
def bench_reranker(
    corpus: Path = _BENCH_CORPUS,
    k: int = _RERANK_K,
) -> None:
    """Leave-one-out AUC of the success reranker — does it discriminate, or is it noise?

    `chimera/evolution/reranker.py` says to measure with this BEFORE putting the reranker in a hot
    path. It was prose pointing at an unreachable module.

    AUC of 0.5 is a coin flip. A reranker at 0.5 is not a weak reranker, it is not a reranker.
    """
    import json

    from chimera.eval import run_reranker_ab
    from chimera.eval.reranker_ab import format_report

    records = []
    for line in Path(corpus).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        records.append((row["query"], row["text"], bool(row["success"])))
    if not records:
        console.print("[yellow]empty corpus — nothing to measure[/yellow]")
        raise typer.Exit(1)
    console.print(format_report(run_reranker_ab(records, k=k)))


app.add_typer(measure_app, name="measure")


@bench_app.command("fusion")
@app.command("fusion-bench", hidden=True)
def fusion_bench(
    tasks: str = typer.Option("hard", "--tasks", help="Task suite: hard | demo."),
) -> None:
    """A/B the fusion engine: full vs selective (tokens + accuracy). Calls real models."""
    from chimera.eval.continuous import demo_tasks
    from chimera.eval.fusion_ab import run_fusion_ab
    from chimera.eval.hard import hard_tasks
    from chimera.providers import LLMGateway, MissingCredentialsError

    suite = hard_tasks() if tasks == "hard" else demo_tasks()
    try:
        report = run_fusion_ab(LLMGateway(), suite)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"fusion A/B — {tasks} ({len(report.rows)} tasks)")
    table.add_column("task")
    table.add_column("full", justify="center")
    table.add_column("selective", justify="center")
    table.add_column("early-stop", justify="center")
    table.add_column("full tok", justify="right")
    table.add_column("sel tok", justify="right")
    for row in report.rows:
        table.add_row(
            row.task_id,
            "[green]ok[/green]" if row.full_ok else "[red]x[/red]",
            "[green]ok[/green]" if row.selective_ok else "[red]x[/red]",
            "yes" if row.early_stopped else "",
            str(row.full_tokens if row.full_tokens is not None else "-"),
            str(row.selective_tokens if row.selective_tokens is not None else "-"),
        )
    console.print(table)
    summary = report.summary()
    for key, value in summary.items():
        console.print(f"[dim]{key}[/dim]: {value}")
    delta = summary.get("accuracy_delta_pp", 0.0)
    verdict = "PASS" if delta >= -1.0 else "REGRESSION"
    color = "green" if verdict == "PASS" else "red"
    console.print(
        f"\nverdict: [{color}]{verdict}[/{color}] "
        f"(selective accuracy within 1pp of full: {delta:+.1f}pp)"
    )
    if verdict != "PASS":
        raise typer.Exit(code=1)  # a REGRESSION verdict must fail the process so CI catches it


@bench_app.command("cascade")
@app.command("cascade-bench", hidden=True)
def cascade_bench(
    tasks: str = typer.Option("hard", "--tasks", help="Task suite: hard | demo."),
) -> None:
    """Four-arm bench: weak-only vs mid-only vs cascade vs fusion. Calls real models.

    Published criterion (stated up front): cascade >= mid-only pass rate at materially
    lower tokens-per-pass. The number reported is whatever is measured.
    """
    from chimera.eval.cascade_bench import ARMS, run_cascade_bench
    from chimera.eval.continuous import demo_tasks
    from chimera.eval.hard import hard_tasks
    from chimera.fusion import FusionEngine
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    ladder = settings.tier_ladder()
    suite = hard_tasks() if tasks == "hard" else demo_tasks()
    gateway = LLMGateway()
    try:
        report = run_cascade_bench(
            gateway, FusionEngine(gateway), suite,
            weak=ladder.weak, mid=ladder.mid, entry=ladder.entry,
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"cascade bench — {tasks} ({len(report.rows)} tasks)")
    table.add_column("task")
    for arm in ARMS:
        table.add_column(arm, justify="center")
        table.add_column("tok", justify="right")
    for row in report.rows:
        cells: list[str] = [row.task_id]
        for arm in ARMS:
            cells.append("[green]ok[/green]" if row.ok.get(arm) else "[red]x[/red]")
            tok = row.tokens.get(arm)
            cells.append(str(tok) if tok is not None else "-")
        table.add_row(*cells)
    console.print(table)
    summary = report.summary()
    for key, value in summary.items():
        console.print(f"[dim]{key}[/dim]: {value}")
    cascade_obj = summary.get("cascade_pass_rate")
    mid_obj = summary.get("mid_pass_rate")
    cascade_rate = cascade_obj if isinstance(cascade_obj, int | float) else 0.0
    mid_rate = mid_obj if isinstance(mid_obj, int | float) else 0.0
    verdict = "PASS" if cascade_rate >= mid_rate else "BELOW MID"
    color = "green" if verdict == "PASS" else "red"
    console.print(f"\nverdict: [{color}]{verdict}[/{color}] (cascade {cascade_rate:.0%} vs mid {mid_rate:.0%})")
    if verdict != "PASS":
        raise typer.Exit(code=1)  # a BELOW-MID verdict must fail the process so CI catches it


def _run_multistep_hierarchy_bench(gateway: Any, model: str, only: set[str], out: str | None) -> None:
    """Multi-step hierarchy A/B: one growing context vs per-step scoped workers, over large docs.

    The regime where the hierarchy actually saves tokens — the single agent re-sends every document on
    every turn (cost ~ Q·ΣΣdocs), scoped workers pay each doc ~once. Also prices the MEASURED cache
    reduction via the caching model (`cache_cost`) when the provider reports cache accounting.
    """
    import json as _json

    from chimera.eval.cache_cost import dollar_cost, measured_dollar_reduction
    from chimera.eval.hierarchy_ab import ArmOutcome, format_token_report, run_hierarchy_ab
    from chimera.eval.hierarchy_multistep import (
        MultiStepTask,
        multistep_tasks,
        run_baseline,
        run_scoped,
    )
    from chimera.eval.paired import format_report
    from chimera.fusion.receipts import resolve_price
    from chimera.orchestration.receipts import estimate_tokens

    tasks = [t for t in multistep_tasks() if not only or t.id in only]
    if not tasks:
        console.print("[red]No multi-step tasks matched.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[dim]model={model} tasks={len(tasks)} (multi-step, large docs)[/dim]")
    cache = {"base_cr": 0, "base_cw": 0, "scoped_cr": 0, "scoped_cw": 0, "base_reg": 0, "scoped_reg": 0}

    def complete(messages: list[Any]) -> tuple[str, int, int, int]:
        result = gateway.complete(messages, model=model)
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if tokens == 0:
            tokens = estimate_tokens("".join(str(m["content"]) for m in messages) + (result.content or ""))
        return (result.content or "", tokens, result.cache_read_tokens or 0, result.cache_write_tokens or 0)

    def baseline(task: MultiStepTask) -> ArmOutcome:
        run = run_baseline(task, complete)
        cache["base_cr"] += run.cache_read
        cache["base_cw"] += run.cache_write
        cache["base_reg"] += run.tokens - run.cache_read
        return ArmOutcome(passed=run.passed, tokens=run.tokens)

    def treatment(task: MultiStepTask) -> ArmOutcome:
        run = run_scoped(task, complete)
        cache["scoped_cr"] += run.cache_read
        cache["scoped_cw"] += run.cache_write
        cache["scoped_reg"] += run.tokens - run.cache_read
        return ArmOutcome(passed=run.passed, tokens=run.tokens)

    report = run_hierarchy_ab(
        tasks, restore=lambda _t: None, baseline=baseline, treatment=treatment,
        baseline_name="single-context", treatment_name="scoped",
    )
    console.print(format_report(report.paired))
    console.print(format_token_report(report))

    # Turn the "caching narrows the win" caveat into a measured number when the provider reports it.
    price = resolve_price(model)
    total_cr = cache["base_cr"] + cache["scoped_cr"]
    if price is not None and total_cr > 0:
        base_usd = dollar_cost(regular_input=cache["base_reg"], output=0, cache_read=cache["base_cr"],
                               input_per_m=price.input_per_m, output_per_m=price.output_per_m)
        scoped_usd = dollar_cost(regular_input=cache["scoped_reg"], output=0, cache_read=cache["scoped_cr"],
                                 input_per_m=price.input_per_m, output_per_m=price.output_per_m)
        console.print(
            f"[dim]measured dollar reduction (cache reads priced 0.1x): "
            f"{measured_dollar_reduction(base_usd, scoped_usd):+.1%} "
            f"(token reduction {report.summary().get('token_reduction')})[/dim]"
        )
    else:
        console.print(
            "[dim]no cache tokens reported — $ == tokens here; caching narrows the win only where the "
            "provider caches the single agent's repeated context.[/dim]"
        )
    if out:
        Path(out).write_text(_json.dumps(report.summary(), indent=2, default=str), encoding="utf-8")
        console.print(f"[dim]wrote {out}[/dim]")


@bench_app.command("hierarchy")
@app.command("hierarchy-bench", hidden=True)
def hierarchy_bench(
    model: str = typer.Option(None, "--model", "-m", help="Mid/worker model — BOTH arms use it, to isolate orchestration. Defaults to the tier ladder's mid."),
    top_model: str = typer.Option(None, "--top-model", help="Top model for synthesis. Defaults to --model (same family keeps the isolation)."),
    tasks: str = typer.Option("", "--tasks", help="Comma-separated task ids to filter (default: all 10 synthetic tasks)."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max concurrent workers in the hierarchy arm."),
    out: str = typer.Option(None, "--out", help="Write the JSON summary to this path."),
    multistep: bool = typer.Option(False, "--multistep", help="Run the MULTI-STEP suite instead (single growing context vs per-step scoped workers, over large docs) — the regime where the hierarchy actually saves tokens. Also reports a caching-aware dollar reduction."),
) -> None:
    """Paired A/B: single-agent (all docs inline) vs the hierarchy (one worker per doc). Calls real models.

    Both arms run on the SAME model so the comparison isolates the ORCHESTRATION (minimal-context
    scoping + budgets + contracts), not model strength. Quality = paired McNemar/Wilson (the only place
    "significant" appears); tokens = measured totals per arm, with no significance claim on cost.

    `--multistep` switches to the companion suite where the token crossover lives: a single agent
    re-sends every document on every turn (cost grows with turns), while scoped workers pay each doc
    ~once — and prices the measured cache reduction via the caching model.
    """
    import json as _json
    import tempfile

    from chimera.eval.hierarchy_ab import (
        ArmOutcome,
        HierarchyTask,
        baseline_prompt,
        format_token_report,
        make_specs,
        run_hierarchy_ab,
        synthetic_tasks,
    )
    from chimera.eval.paired import format_report
    from chimera.orchestration.artifacts import ArtifactStore
    from chimera.orchestration.envelope_verify import EnvelopeVerifier
    from chimera.orchestration.hierarchy import HierarchicalOrchestrator, HierarchyConfig
    from chimera.orchestration.receipts import estimate_tokens
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    ladder = settings.tier_ladder()
    mid = model or ladder.mid
    top = top_model or mid
    only = {t.strip() for t in tasks.split(",") if t.strip()}
    gateway = LLMGateway()

    if multistep:
        try:
            _run_multistep_hierarchy_bench(gateway, mid, only, out)
        except MissingCredentialsError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        return

    suite = [t for t in synthetic_tasks() if not only or t.id in only]
    if not suite:
        console.print(f"[red]No tasks matched {tasks!r}.[/red]")
        raise typer.Exit(code=1)

    workdir = Path(tempfile.mkdtemp(prefix="hierarchy-bench-"))
    console.print(f"[dim]model={mid} top={top} tasks={len(suite)} artifacts={workdir}[/dim]")

    def baseline(task: HierarchyTask) -> ArmOutcome:
        prompt = baseline_prompt(task)
        result = gateway.complete([{"role": "user", "content": prompt}], model=mid)
        tokens = (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        if tokens == 0:  # provider reported nothing — estimate, and the token row says so
            tokens = estimate_tokens(prompt + (result.content or ""))
        return ArmOutcome(passed=task.check(result.content or ""), tokens=tokens)

    def treatment(task: HierarchyTask) -> ArmOutcome:
        store = ArtifactStore(workdir / task.id)
        orchestrator = HierarchicalOrchestrator(
            gateway,
            weak_model=mid,
            mid_model=mid,
            top_model=top,
            store=store,
            verifier=EnvelopeVerifier(store=store, backend=None, spot_rate=0.0),
            config=HierarchyConfig(max_workers=max_workers, fuse_final=False),
        )
        result = orchestrator.run_prepared(task.question, make_specs(task))
        return ArmOutcome(passed=task.check(result.answer or ""), tokens=result.total_tokens)

    try:
        report = run_hierarchy_ab(suite, restore=lambda _t: None, baseline=baseline, treatment=treatment)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(format_report(report.paired))
    console.print(format_token_report(report))
    if out:
        Path(out).write_text(_json.dumps(report.summary(), indent=2, default=str), encoding="utf-8")
        console.print(f"[dim]wrote {out}[/dim]")


@bench_app.command("skillcard")
@app.command("skillcard-bench", hidden=True)
def skillcard_bench(
    tasks: str = typer.Option("hard", "--tasks", help="Task suite: hard | big | demo. 'big' = 24 traps for a tighter paired CI."),
    k: int = typer.Option(1, "--k", help="How many cards to retrieve per task."),
    min_overlap: int = typer.Option(
        2, "--min-overlap", help="Relevance gate: inject a card only on >= N shared query terms (0=off)."
    ),
    max_lines: int = typer.Option(3, "--max-lines", help="Render budget: max lines per injected card."),
    use_store: bool = typer.Option(
        False, "--use-store", help="Bench your own learned cards (skills.json) instead of the demo set."
    ),
) -> None:
    """A/B reasoning with vs without injected TRS skill cards. Calls real models."""
    from chimera.eval.continuous import demo_tasks
    from chimera.eval.hard import hard_tasks, hard_tasks_plus
    from chimera.eval.skillcard_ab import demo_cards, run_skillcard_ab
    from chimera.evolution import SkillStore
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if tasks == "big":
        suite = hard_tasks_plus()
    elif tasks == "hard":
        suite = hard_tasks()
    else:
        suite = demo_tasks()
    if use_store:
        cards = [c for c in SkillStore(settings.home / "skills.json").skills() if c.has_card()]
        if not cards:
            console.print("[red]No cards with content in skills.json — run some solves first, "
                          "or drop --use-store to bench the demo set.[/red]")
            raise typer.Exit(code=1)
    else:
        cards = demo_cards()

    try:
        report = run_skillcard_ab(
            LLMGateway(), suite, cards, k=k, min_overlap=min_overlap, max_lines=max_lines
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"skill-card A/B — {tasks} ({len(report.rows)} tasks, {len(cards)} cards)")
    table.add_column("task")
    table.add_column("no cards", justify="center")
    table.add_column("with cards", justify="center")
    table.add_column("hit", justify="center")
    table.add_column("base tok", justify="right")
    table.add_column("card tok", justify="right")
    for row in report.rows:
        table.add_row(
            row.task_id,
            "[green]ok[/green]" if row.base_ok else "[red]x[/red]",
            "[green]ok[/green]" if row.card_ok else "[red]x[/red]",
            "yes" if row.hit else "",
            str(row.base_tokens if row.base_tokens is not None else "-"),
            str(row.card_tokens if row.card_tokens is not None else "-"),
        )
    console.print(table)
    summary = report.summary()
    for key, value in summary.items():
        console.print(f"[dim]{key}[/dim]: {value}")
    delta = summary.get("accuracy_delta_pp", 0.0)
    verdict = "PASS" if delta >= -1.0 else "REGRESSION"
    color = "green" if verdict == "PASS" else "red"
    console.print(
        f"\nverdict: [{color}]{verdict}[/{color}] "
        f"(card accuracy within 1pp of no-cards: {delta:+.1f}pp; "
        f"token delta {summary.get('token_delta_pct', 0.0):+.1f}%)"
    )

    # The registered M19-A1 default-flip gate: the paired McNemar accuracy CI lower bound >= 0 AND
    # token overhead < +50%. Reported so the flip decision is reproducible from the run, not by hand.
    paired = report.paired()
    lo, hi = paired.diff_ci
    tok = summary.get("token_delta_pct", 0.0)
    acc_ok, tok_ok = lo >= 0.0, tok < 50.0
    flip = "JUSTIFIED" if (acc_ok and tok_ok) else "NOT justified"
    fcolor = "green" if (acc_ok and tok_ok) else "yellow"
    console.print(
        f"[bold]A1 flip gate:[/bold] [{fcolor}]{flip}[/{fcolor}]  "
        f"(paired Δ {paired.delta * 100:+.1f}pp, 95% CI [{lo * 100:+.1f}%, {hi * 100:+.1f}%] "
        f"→ acc {'✓' if acc_ok else '✗'}; tokens {tok:+.1f}% → {'✓' if tok_ok else '✗'}; "
        f"n={paired.n}, discordant={paired.discordant})"
    )
    if verdict != "PASS":
        raise typer.Exit(code=1)  # a REGRESSION verdict must fail the process so CI catches it


@bench_app.command("schema")
@app.command("schema-bench", hidden=True)
def schema_bench(
    openapi: str = typer.Option(
        None, "--openapi", help="Path or URL to an OpenAPI spec to include (its tools are verbose)."
    ),
    demo: bool = typer.Option(
        False, "--demo", help="Include a couple of synthetic verbose tools to show the effect."
    ),
    model: str = typer.Option(None, "--model", "-m", help="Tokenizer model (default: your default)."),
) -> None:
    """Measure tool-schema token cost, full vs compacted (advertise-time). No model calls."""
    from chimera.eval.schema_ab import demo_bloated_schemas, run_schema_ab
    from chimera.integrations.openapi import tools_from_openapi
    from chimera.tools import default_registry

    settings = get_settings()
    schemas: list[dict[str, Any]] = default_registry(Path(".")).to_openai_schema()
    if demo:
        schemas += demo_bloated_schemas()
    if openapi:
        import json

        if openapi.startswith(("http://", "https://")):
            import httpx

            text = httpx.get(openapi, timeout=30.0).text
        else:
            text = Path(openapi).read_text(encoding="utf-8")
        try:
            spec = json.loads(text)
        except json.JSONDecodeError:
            import yaml

            spec = yaml.safe_load(text)
        schemas += [tool.to_openai_schema() for tool in tools_from_openapi(spec)]

    report = run_schema_ab(schemas, model=model or settings.default_model)
    table = Table(title=f"tool-schema compaction ({len(report.rows)} tools)")
    table.add_column("tool")
    table.add_column("full tok", justify="right")
    table.add_column("compact tok", justify="right")
    table.add_column("saved", justify="right")
    for row in report.rows:
        saved = row.full_tokens - row.compact_tokens
        pct = f"{saved / row.full_tokens * 100:.0f}%" if row.full_tokens else "0%"
        table.add_row(row.tool, str(row.full_tokens), str(row.compact_tokens), f"{saved} ({pct})")
    console.print(table)
    s = report.summary()
    console.print(
        f"[dim]full {int(s['full_tokens'])} tok → compact {int(s['compact_tokens'])} tok · "
        f"reduction {s['reduction_pct']}% across {int(s['tools'])} tools[/dim]"
    )


@bench_app.command("sandbox")
@app.command("sandbox-bench", hidden=True)
def sandbox_bench(
    workspace: str = typer.Option(".sandbox-bench", "--workspace", "-w", help="Dir to run sandboxed tasks in."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(8, "--max-steps", help="Max tool-calling steps per task."),
) -> None:
    """State-based bench: grade the final workspace state + count harmful side effects.

    Unlike the text benches, this measures what the agent DID (files it changed), and flags
    mutations outside each task's allowed set. Uses real models + file tools.
    """
    from chimera.core import Agent, AgentConfig
    from chimera.eval.sandbox import StatefulRunner, demo_stateful_tasks, run_stateful
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    gateway = LLMGateway()

    def factory(ws: Path) -> StatefulRunner:
        return Agent(gateway, default_registry(ws), AgentConfig(model=model, max_steps=max_steps))

    report = run_stateful(factory, demo_stateful_tasks(), Path(workspace))
    table = Table(title="sandbox bench (final-state grading + side effects)")
    table.add_column("task")
    table.add_column("goal", justify="center")
    table.add_column("harmful side effects")
    for outcome in report.outcomes:
        table.add_row(
            outcome.id,
            "[green]met[/green]" if outcome.passed else "[red]missed[/red]",
            ", ".join(outcome.side_effects) if outcome.side_effects else "[dim]none[/dim]",
        )
    console.print(table)
    summary = report.summary()
    console.print(
        f"[dim]pass rate {summary['pass_rate']} · side-effect rate "
        f"{summary['side_effect_rate']} across {int(summary['tasks'])} tasks[/dim]"
    )


app.add_typer(bench_app, name="bench")


@bench_app.callback(invoke_without_command=True)
def bench(
    ctx: typer.Context,
    limit: int = typer.Option(0, "--limit", help="Limit number of demo tasks (0 = all)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    fuse: bool = typer.Option(False, "--fuse", help="Use the fusion engine as the solver."),
    chain: bool = typer.Option(False, "--chain", help="Run the stateful chained benchmark (error propagation)."),
    hard: bool = typer.Option(False, "--hard", help="Use the hard suite (traps / propagating chain)."),
    rounds: int = typer.Option(
        1, "--rounds", help="Re-run the suite N times; report stagnation + cost trend across rounds."
    ),
) -> None:
    """Run the continuous-evolution benchmark on a demo task set. Requires a key.

    With a subcommand (`chimera bench fusion`, `chimera bench compare`, ...) that ruler runs instead.
    """
    if ctx.invoked_subcommand is not None:
        return
    from chimera.eval import (
        SingleModelSolver,
        demo_chain,
        demo_tasks,
        hard_chain,
        hard_tasks,
        run_chain,
        run_continuous,
        run_evolution,
    )
    from chimera.eval.hard import HARD_CHAIN_START
    from chimera.providers import LLMGateway

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse:
        # The benchmark measures the fusion engine itself, so use it directly — the
        # cost-aware RoutedBackend would decline to fuse these short prompts (they fall
        # under its length/keyword gate) and silently collapse back to single-model.
        from chimera.fusion import FusionEngine

        backend = FusionEngine(gateway)

    solver = SingleModelSolver(backend, model)
    report: Any  # EvolutionReport | ChainReport | RoundedEvolutionReport — all have .summary()
    if chain:
        steps = hard_chain() if hard else demo_chain(limit or 8)
        start = HARD_CHAIN_START if hard else "0"
        report = run_chain(solver, steps, initial_state=start)
        title = ("Hard " if hard else "") + "Chained continuous-evolution benchmark"
    else:
        tasks = hard_tasks() if hard else demo_tasks()
        if limit > 0:
            tasks = tasks[:limit]
        on_task = lambda o: console.print(  # noqa: E731
            f"  {'[green]PASS[/green]' if o.passed else '[red]FAIL[/red]'} {o.id}"
        )
        if rounds > 1:
            report = run_evolution(solver, tasks, rounds=rounds, on_task=on_task)
            title = f"Continuous-evolution benchmark ({rounds} rounds: stagnation + cost)"
        else:
            report = run_continuous(solver, tasks, on_task=on_task)
            title = "Continuous-evolution benchmark"

    summary = report.summary()
    table = Table(title=title, show_header=False, title_style="bold")
    for key, value in summary.items():
        table.add_row(key, str(value))
    console.print(table)


@app.command()
def redteam() -> None:
    """Red-team the injection defenses: attack success rate with vs without them.

    No key needed — measures whether the governance layer blocks a harmful tool call
    once a run is tainted (defense-in-depth coverage), not model susceptibility.
    """
    from chimera.eval import default_attacks, run_redteam

    attacks = default_attacks()
    undef = run_redteam(attacks, defended=False).summary()
    defense = run_redteam(attacks, defended=True)
    defended = defense.summary()

    table = Table(title="Injection red-team (attack success rate — lower is better)", header_style="bold")
    table.add_column("Metric")
    table.add_column("No defenses", justify="right")
    table.add_column("With --taint", justify="right")
    keys = ["attacks", "attack_success_rate", "block_rate"] + sorted(
        k for k in defended if k.startswith("asr_")
    )
    for key in keys:
        table.add_row(key, str(undef.get(key, "-")), str(defended.get(key, "-")))
    console.print(table)
    leaks = defense.leaks()
    if leaks:
        console.print(
            f"[yellow]Still gets through even defended:[/yellow] {', '.join(leaks)} "
            "— named honestly; the general data-vs-instructions problem (#5) stays open."
        )

    # The other half, and it is not optional. A defense scored on attacks alone has a trivial
    # maximum — refuse everything — so the block rate above is unreadable without this number
    # beside it. See `bench/injection/PREREGISTRATION.md` for the gate both must pass.
    from chimera.eval.injection import run_posture

    posture = run_posture(defended=True)
    benign = posture.benign.summary()
    cost = Table(title="What the defense costs in legitimate work", header_style="bold")
    cost.add_column("Legitimate task set")
    cost.add_column("Refused", justify="right")
    cost.add_row(
        "reads its own repo first (control — must stay 0%)",
        f"{benign.get('over_block_workspace', 0.0):.0%}",
    )
    cost.add_row(
        "reads something external first (docs, issue, release notes)",
        f"{benign.get('over_block_fetch', 0.0):.0%}",
    )
    cost.add_row("[bold]all legitimate work[/bold]", f"[bold]{benign['over_block_rate']:.0%}[/bold]")
    console.print(cost)
    passed, why = posture.gate()
    console.print(f"[{'green' if passed else 'red'}]gate: {'pass' if passed else 'FAIL'}[/] — {why}")
    if posture.benign.refusals():
        console.print(
            f"[yellow]Legitimate work refused:[/yellow] {', '.join(posture.benign.refusals())}"
        )


@bench_app.command("memory-poison")
@app.command("memory-poison", hidden=True)
def memory_poison() -> None:
    """Ablate the memory-poisoning defenses: what reaches a LATER run's prompt, and unmarked.

    No key needed, nothing leaves the machine. `redteam` measures one run — content arrives
    untrusted, the harmful call is refused, and the whole picture ends with the process. This
    measures the other shape: run A stores what it "learned" from a poisoned page, run B asks an
    unrelated question days later, and recall hands the planted fact to the model.

    The headline is what arrives **unmarked**, not what is blocked. A poisoned fact carrying its
    origin is one the model was warned about; an unlabelled one is indistinguishable from something
    the agent verified itself. Each of the three layers (taint / gate / label) is switched off in
    turn, because a single number would be compatible with any of them doing nothing.

    See `bench/memory_poison/PREREGISTRATION.md` for the thresholds, fixed before the first run.
    """
    from chimera.eval.memory_poison import ABLATION, run_posture

    table = Table(
        title="Memory poisoning — persistent, across runs (lower is better)", header_style="bold"
    )
    table.add_column("Defenses")
    table.add_column("Poison recalled", justify="right")
    table.add_column("Poison UNMARKED", justify="right")
    table.add_column("Honest memory lost", justify="right")
    table.add_column("Gate", justify="right")

    shipped = None
    for defenses in ABLATION:
        report = run_posture(defenses=defenses)
        summary = report.summary()
        passed, _ = report.gate()
        name = defenses.name
        if name == "all":
            shipped = report
        table.add_row(
            f"[bold]{name}[/bold]" if name == "all" else name,
            f"{summary['poison_recall_rate']:.0%}",
            f"{summary['poison_unmarked_rate']:.0%}",
            f"{summary['benign_loss_rate']:.0%}",
            f"[{'green' if passed else 'red'}]{'pass' if passed else 'FAIL'}[/]",
        )
    console.print(table)

    if shipped is None:  # pragma: no cover - ABLATION always contains the shipped config
        return
    passed, why = shipped.gate()
    console.print(f"[{'green' if passed else 'red'}]shipped config: {'pass' if passed else 'FAIL'}[/] — {why}")
    unmarked = shipped.poison.unmarked()
    if unmarked:
        console.print(
            f"[yellow]Reaches a later prompt with no origin attached:[/yellow] {', '.join(unmarked)}"
        )
    lost = shipped.benign.lost()
    if lost:
        console.print(
            f"[yellow]Honest memory the defense destroyed:[/yellow] {', '.join(lost)} "
            "— text that quotes an attack in order to explain it. A pattern matcher on content "
            "cannot tell the quote from the command."
        )

    # The second hop (study 30 S30-25, pre-registered 2026-10-05): a clean run recalls the fact,
    # writes back what it concluded, and a third run recalls that. Same threshold as above.
    from chimera.eval.memory_poison import run_two_hop

    # Both configurations: with a ledger (`--taint`) and without one, which is how most callers
    # build the agent and the one the first reading of this row never exercised.
    for with_ledger in (True, False):
        hop = run_two_hop(with_ledger=with_ledger)
        hop_passed, hop_why = hop.gate()
        console.print(
            f"[{'green' if hop_passed else 'red'}]two hops: {'pass' if hop_passed else 'FAIL'}[/] — {hop_why}"
        )
        if hop.unmarked():
            console.print(
                f"[yellow]Survives one clean rewrite with no origin:[/yellow] {', '.join(hop.unmarked())}"
            )


@bench_app.command("probe-select")
@app.command("probe-select", hidden=True)
def probe_select(
    data: str = typer.Argument(None, help='JSON: {"arm": [[proxy, reward-or-null], ...], ...}. Omit when using --from-log.'),
    from_log: str = typer.Option(None, "--from-log", help="Read observations from a ProbeLog JSONL (e.g. <home>/probe.jsonl written by `solve --probe-log`)."),
    delta: float = typer.Option(0.1, "--delta", help="Confidence level (smaller = stricter)."),
    min_reward: int = typer.Option(2, "--min-reward", help="Expensive rewards required per arm before deciding."),
) -> None:
    """PROBE best-arm identification with a cheap-proxy control variate (M18-5).

    "Which model/config is best?" where each expensive reward (a real grade) is paired with a cheap
    proxy (a weak judge) of unknown correlation. PROBE uses the proxy as a control variate so the
    estimate needs FEWER expensive draws the better the proxy correlates — and stays unbiased when the
    proxy is useless. Prints each arm's adjusted mean ± interval, the winner, and — if not yet
    confident — the arm to sample next. Feed it recorded (proxy, reward) observations from a bench.
    """
    import json

    from chimera.eval.probe import ProbeBestArm

    if from_log:
        from chimera.fusion.probe_log import ProbeLog

        arms = ProbeLog(Path(from_log)).observations()
    elif data:
        raw = json.loads(Path(data).read_text(encoding="utf-8"))
        arms = {
            str(name): [(float(o[0]), None if o[1] is None else float(o[1])) for o in obs]
            for name, obs in raw.items()
        }
    else:
        console.print("[red]Provide a JSON data file or --from-log <probe.jsonl>.[/red]")
        raise typer.Exit(code=1)
    if not arms:
        console.print("[dim]No observations yet.[/dim]")
        return
    decision = ProbeBestArm(delta=delta, min_reward=min_reward).select(arms)
    for e in decision.estimates:
        hw = "±∞" if e.half_width == float("inf") else f"±{e.half_width:.3f}"
        console.print(
            f"  {e.arm:<16} mean {e.mean:6.3f} {hw}   (rewards={e.n_reward}, proxy={e.n_proxy}, ρ={e.rho:.2f})"
        )
    if decision.best is None:
        console.print("[dim]No arms.[/dim]")
        return
    if decision.confident:
        console.print(f"\n[green]best: {decision.best}[/green] (δ-confident)")
    else:
        console.print(
            f"\n[yellow]best so far: {decision.best}[/yellow] — not δ-confident; "
            f"sample [bold]{decision.next_arm}[/bold] next"
        )


@bench_app.command("compare")
@app.command("bench-compare", hidden=True)
def bench_compare(
    baseline: str = typer.Argument(..., help="JSON file of the baseline arm's per-task pass/fail (list of bools, or {task: bool})."),
    treatment: str = typer.Argument(..., help="JSON file of the treatment arm's per-task pass/fail."),
    baseline_name: str = typer.Option("baseline", "--baseline-name", help="Label for the baseline arm."),
    treatment_name: str = typer.Option("chimera", "--treatment-name", help="Label for the treatment arm."),
    paired: bool = typer.Option(
        False, "--paired", help="Paired (McNemar) test: item i in both files is the SAME task replayed from an identical forked state — a tighter CI."
    ),
) -> None:
    """Report the honest A/B delta (+95% CI) between two benchmark result files.

    Feed it the pass/fail from two runs on the SAME task IDs (e.g. a terminal-bench free-model
    baseline vs the same model driven by Chimera). Prints each arm's Wilson-bounded pass rate,
    the delta, its Newcombe CI, and whether the difference is significant. This is the number
    that proves (or doesn't) that the scaffolding lifts a weak model.

    With --paired, the two lists are treated as *aligned pairs* (each index is one task replayed
    from an identical forked checkpoint), and the tighter McNemar/Wilson interval is reported —
    the payoff of running both arms from the same forked state.
    """
    import json

    def _load(path: str) -> list[bool]:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        values = raw.values() if isinstance(raw, dict) else raw
        return [bool(v) for v in values]

    try:
        base_passed, treat_passed = _load(baseline), _load(treatment)
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]could not read results: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if paired:
        from chimera.eval.paired import compare_paired
        from chimera.eval.paired import format_report as format_paired

        if len(base_passed) != len(treat_passed):
            console.print("[red]--paired needs two equal-length, aligned lists (same tasks, same order).[/red]")
            raise typer.Exit(code=1)
        presult = compare_paired(
            base_passed, treat_passed, baseline_name=baseline_name, treatment_name=treatment_name
        )
        console.print(format_paired(presult))
        return

    from chimera.eval import compare_ab, format_report

    result = compare_ab(
        base_passed, treat_passed, baseline_name=baseline_name, treatment_name=treatment_name
    )
    console.print(format_report(result))
    if not result.significant:
        console.print(
            "[dim]not significant — a larger task subset / more seeds, or the feature genuinely "
            "doesn't move the number. Report it honestly either way.[/dim]"
        )


@bench_app.command("transfer-gate")
@app.command("transfer-gate", hidden=True)
def transfer_gate(
    tuned_baseline: str = typer.Argument(..., help="JSON pass/fail of the baseline on the TUNED slice (list of bools, or {task: bool})."),
    tuned_treatment: str = typer.Argument(..., help="JSON pass/fail of the candidate on the TUNED slice (aligned, same order)."),
    holdout_baseline: str = typer.Option(None, "--holdout-baseline", help="JSON pass/fail of the baseline on a DISJOINT same-capability holdout."),
    holdout_treatment: str = typer.Option(None, "--holdout-treatment", help="JSON pass/fail of the candidate on the holdout (aligned)."),
    require_significant: bool = typer.Option(False, "--require-significant", help="Require the tuned gain's paired CI to exclude 0, not just Δ>0."),
    tol: float = typer.Option(0.0, "--tol", help="Max tolerated pass-rate drop on the holdout before promotion is blocked."),
) -> None:
    """Promote a learned change only if it helps its tuned slice AND doesn't regress a holdout.

    Guards against *negative transfer* — a GEPA prompt / ACE delta / distilled skill that raises the pass
    rate on the tasks it was tuned against but REGRESSES on other tasks sharing the capability. Feed the
    tuned slice's paired pass/fail (baseline vs candidate) and, ideally, a disjoint same-capability
    holdout's; the verdict is PROMOTE / BLOCK with the paired evidence (exit 1 on BLOCK, for CI).
    Without a holdout it promotes on the tuned gain alone but flags that transfer was NOT measured.
    """
    import json

    from chimera.eval.transfer import transfer_gated_promotion

    def _load(path: str) -> list[bool]:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        values = raw.values() if isinstance(raw, dict) else raw
        return [bool(v) for v in values]

    if (holdout_baseline is None) != (holdout_treatment is None):
        console.print("[red]give BOTH --holdout-baseline and --holdout-treatment, or neither.[/red]")
        raise typer.Exit(code=1)
    try:
        tb, tt = _load(tuned_baseline), _load(tuned_treatment)
        hb = _load(holdout_baseline) if holdout_baseline else None
        ht = _load(holdout_treatment) if holdout_treatment else None
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]could not read results: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    decision = transfer_gated_promotion(
        tuned_baseline=tb,
        tuned_treatment=tt,
        holdout_baseline=hb,
        holdout_treatment=ht,
        require_tuned_significant=require_significant,
        holdout_regression_tol=tol,
    )
    verdict = "[green]PROMOTE[/green]" if decision.promote else "[red]BLOCK[/red]"
    console.print(f"{verdict} — {decision.reason}")
    if not decision.transfer_measured:
        console.print(
            "[yellow]transfer NOT measured (no holdout) — promotion rests on the tuned slice alone.[/yellow]"
        )
    if not decision.promote:
        raise typer.Exit(code=1)


@bench_app.command("swe-compare")
@app.command("swe-bench-compare", hidden=True)
def swe_bench_compare(
    baseline: str = typer.Argument(..., help="SWE-bench evaluation report JSON for the model-only arm."),
    treatment: str = typer.Argument(..., help="SWE-bench evaluation report JSON for the model+Chimera arm."),
    instances: str = typer.Option(..., "--instances", help="JSONL of the instances both arms ran (fixes the id set)."),
) -> None:
    """Honest A/B over two SWE-bench Verified-Mini reports on the SAME instance ids.

    Reads the official evaluation reports (``resolved_ids`` or a per-instance map) for a free model
    alone vs the same model driven by Chimera, projects both onto the shared instance list (a missing
    id counts as unresolved), and prints the delta + 95% CI. This is the second standard scoreboard
    for the weak-model-lift thesis; the pass/fail comes from SWE-bench's tests, never self-reported.
    """
    import json

    from chimera.eval import compare_arms, format_report, load_instances

    try:
        ids = [inst.instance_id for inst in load_instances(Path(instances))]
        baseline_report = json.loads(Path(baseline).read_text(encoding="utf-8"))
        treatment_report = json.loads(Path(treatment).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        console.print(f"[red]could not read inputs: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    if not ids:
        console.print("[red]The instances file is empty.[/red]")
        raise typer.Exit(code=1)
    result = compare_arms(baseline_report, treatment_report, ids)
    console.print(f"[dim]{len(ids)} instances[/dim]")
    console.print(format_report(result))
    if not result.significant:
        console.print(
            "[dim]not significant — a larger slice / more instances, or the scaffolding genuinely "
            "doesn't move SWE-bench. Report it honestly either way.[/dim]"
        )


@bench_app.command("memory")
@app.command("memory-bench", hidden=True)
def memory_bench(
    sizes: str = typer.Option("50,200,1000", "--sizes", help="Comma-separated memory sizes to sweep."),
    semantic: bool = typer.Option(
        False, "--semantic", help="Use embedding recall (needs an embeddings key) to measure the lift."
    ),
) -> None:
    """Measure recall@k as memory grows — lexical vs paraphrase.

    Default (keyword search, no key needed) surfaces the honest ceiling: exact-token recall
    holds at scale, but paraphrase recall collapses. Pass ``--semantic`` to re-run with the
    embedding recall path and watch the paraphrase column lift — that delta is the whole
    point of M11b.
    """
    import tempfile

    from chimera.eval import memory_sweep
    from chimera.memory import MemoryManager, MemoryStore

    size_list = [int(s) for s in sizes.split(",") if s.strip().isdigit()] or [50, 200, 1000]
    tmp = Path(tempfile.mkdtemp(prefix="chimera-membench-"))
    counter = {"n": 0}

    embed = None
    if semantic:
        from chimera.providers import LLMGateway, MissingCredentialsError

        settings = get_settings()
        if not settings.has_any_key():
            console.print("[red]--semantic needs a provider key with embeddings access.[/red]")
            raise typer.Exit(1)
        try:
            embed = LLMGateway(settings).embed
        except MissingCredentialsError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    def factory() -> MemoryManager:
        counter["n"] += 1
        return MemoryManager(MemoryStore(tmp / f"m{counter['n']}.json"), embed=embed)

    mode = "semantic (embeddings)" if semantic else "keyword search"
    reports = memory_sweep(factory, size_list)
    table = Table(title=f"Memory recall@k vs scale ({mode})", header_style="bold")
    for col in ("facts", "recall@k", "lexical", "paraphrase"):
        table.add_column(col, justify="right")
    for report in reports:
        s = report.summary()
        table.add_row(
            str(int(s["n_facts"])), f"{s['recall@k']:.2f}",
            f"{s['recall@k_lexical']:.2f}", f"{s['recall@k_paraphrase']:.2f}",
        )
    console.print(table)
    console.print(
        "[dim]Lexical recall holds at scale; paraphrase recall is the keyword ceiling — "
        "opt-in semantic retrieval (embeddings) is what lifts it. Re-run with --semantic.[/dim]"
    )


@bench_app.command("evoclaw")
@app.command("evoclaw", hidden=True)
def evoclaw(
    length: int = typer.Option(12, "--length", help="Number of chained steps."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    retries: int = typer.Option(2, "--retries", help="Verify-or-revert retries per step (guarded)."),
) -> None:
    """Stress-test continuous-evolution degradation: naive vs guarded. Requires a key."""
    from chimera.eval import SingleModelSolver, compare, counter_chain
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    try:
        comparison = compare(
            lambda: SingleModelSolver(gateway, model),
            counter_chain(length),
            initial_state="0",
            max_retries=retries,
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title="EvoClaw stress test (naive vs guarded)", show_header=True, header_style="bold")
    table.add_column("metric")
    table.add_column("naive", justify="right")
    table.add_column("guarded", justify="right")
    naive, guarded = comparison.naive.summary(), comparison.guarded.summary()
    for key in ("pass_rate", "first_half", "second_half", "degradation", "longest_streak"):
        table.add_row(key, str(naive[key]), str(guarded[key]))
    console.print(table)
    console.print(
        f"[bold]degradation gap:[/bold] {comparison.degradation_gap} "
        "[dim](naive − guarded; >0 means the countermeasures held)[/dim]"
    )


# Two rulers live with the area they measure rather than here; they join the group by reference.
bench_app.command("context-curve")(context_curve_cmd)
bench_app.command("rubric-grade")(rubric_grade)
