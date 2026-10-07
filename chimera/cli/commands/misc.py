"""Scenario, crew, lifecycle, workflow, drift, meta and find.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.markup import escape
from rich.table import Table

from chimera.cli.commands._shared import app, console
from chimera.cli.commands.memory import _recall_graph
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.eval.scenarios import ScenarioOutcome, SessionBuilder, SessionRequest, SuiteReport
    from chimera.providers import SupportsComplete



class _SpendCapReached(RuntimeError):
    """Raised from the per-scenario callback when the registered cost ceiling is passed."""


def _right_hand_builder(
    model: str | None, max_steps: int, *, system_prompt: str | None = None
) -> SessionBuilder:
    """Build :class:`ChatSession`s the way ``chimera chat`` builds one.

    Same agent over the same gateway, the same governed registry from
    :func:`chimera.cli.right_hand.build_right_hand`, the same memory manager, recall graph and
    profile preamble. The scenario suite exists to measure *that* object; a session assembled any
    other way would measure a right hand nobody ships — which is exactly what this did between
    2026-09-08 and the day `chat` was governed, when it kept calling the bare
    ``_apply_tool_allowlist(default_registry(...))`` that `chat` no longer makes.

    The one deliberate difference is isolation, and it is not cosmetic: workspace, home, memory and
    profile all come from the request, so one scenario cannot read another's fixture, a fact one
    scenario remembers cannot enter another's recall, and the number does not depend on whose
    laptop it ran on — the developer's own profile and memories would otherwise be pasted into
    every prompt and the series would compare two different rulers (§2aa).
    """
    from chimera.cli.right_hand import build_right_hand
    from chimera.core import Agent, AgentConfig
    from chimera.evolution.wiring import build_memory_manager
    from chimera.interface import ChatSession
    from chimera.interface.profile import load_profile, profile_path, render_profile
    from chimera.providers import LLMGateway

    gateway = LLMGateway()

    def build(request: SessionRequest) -> ChatSession:
        settings = get_settings().model_copy(update={"home": request.home})
        config = AgentConfig(
            model=model, max_steps=max_steps, project_root=request.workspace
        )
        if system_prompt is not None:
            config.system_prompt = system_prompt
        agent = Agent(
            gateway,
            build_right_hand(
                request.workspace, settings=settings, surface="scenarios"
            ).registry,
            config,
        )
        mem = build_memory_manager(settings)
        return ChatSession(
            agent,
            memory=mem,
            graph=_recall_graph(mem),
            profile=render_profile(load_profile(profile_path(settings.home)), mem.profile()),
            remember_from_chat=request.remember_from_chat,
        )

    return build


@app.command()
def scenarios(
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    k: int = typer.Option(3, "--k", help="Runs per scenario — one samples, two alert, three decide."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per turn."),
    max_usd: float = typer.Option(3.0, "--max-usd", help="Hard spend ceiling; the run stops at it."),
    seed: int = typer.Option(1, "--seed", help="Base seed; run i uses seed+i, so the generated values differ per run."),
    series: str = typer.Option(None, "--series", help="Where to append the JSONL row (default <home>/scenarios.jsonl)."),
) -> None:
    """Run the daily right-hand scenario suite through a real chat session (live). Requires a key.

    Each scenario is a script of turns driven through the same ``ChatSession`` ``chimera chat``
    builds — tools, memory, transcript — in its own workspace and its own home. The checks are
    functional, not substring: equality against a value generated *this run* and absent from the
    prompt, a fact read back out of the ``MemoryStore``, a fresh session's recall count, the
    transcript found in the next turn's assembled prompt, the absence of a fabricated figure.

    Twenty-six rows in two blocks. **Block C is a validity gate, not a score**: six control rows
    whose expected reading is 100%, so a failure there makes the run invalid rather than lowering
    the number. **Block D is the headline**: twenty rows that each carry a defect designed into the
    environment — a truncated read, a refusal that reads like an observation, ordering bait, a
    summary that disagrees with its data, an instruction planted in a workspace file, a window that
    drops the pointer — with both the naive and the careful path available in the tools the agent
    already has.

    Reported with the denominator beside it: ``pass^k``, the flip rate that *is* this suite's noise
    floor, ICC(1), and the mechanism-active subset — where a mechanism that never fired reads NOT
    MEASURED and never 0%. One row per invocation is appended to the series.
    Pre-registered in ``bench/scenarios/PREREGISTRATION-v3.md``.
    """
    import tempfile
    from datetime import datetime

    from chimera.eval.replicated import seeds_verdict
    from chimera.eval.scenarios import (
        CONTROL,
        DISCRIMINATING,
        SUITE_VERSION,
        append_series,
        block_arm,
        daily_scenarios,
        mechanism_arm,
        repo_sha,
        run_suite,
        series_record,
        suite_arm,
    )

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    suite = daily_scenarios()
    builder = _right_hand_builder(model, max_steps)
    reports: list[SuiteReport] = []
    spent = 0.0

    def watch(outcome: ScenarioOutcome) -> None:
        nonlocal spent
        spent += outcome.usd or 0.0
        mark = "[green]PASS[/green]" if outcome.passed else "[red]FAIL[/red]"
        if outcome.mechanism_active is None:
            mech = ""
        elif outcome.mechanism_active:
            mech = " [dim](mechanism fired)[/dim]"
        else:
            mech = " [yellow](mechanism did NOT fire)[/yellow]"
        console.print(
            f"  {mark} {outcome.id}{mech} [dim]{outcome.tokens} tok · ${spent:.4f} so far[/dim]"
        )
        if outcome.error:
            console.print(f"    [yellow]{escape(outcome.error)}[/yellow]")
        if spent > max_usd:
            raise _SpendCapReached(f"spend cap reached: ${spent:.4f} > ${max_usd:.2f}")

    with tempfile.TemporaryDirectory(prefix="chimera-scenarios-") as tmp:
        for index in range(k):
            console.print(f"[bold]run {index + 1}/{k}[/bold] [dim]seed {seed + index}[/dim]")
            try:
                reports.append(
                    run_suite(
                        builder,
                        suite,
                        root=Path(tmp) / f"run{index}",
                        seed=seed + index,
                        on_result=watch,
                    )
                )
            except _SpendCapReached as exc:
                # The partial run is discarded rather than padded: pass^k over a run that did not
                # cover every scenario would compare unlike rows.
                console.print(f"[yellow]{exc} — this run is discarded.[/yellow]")
                break

    if not reports:
        console.print("[red]No complete run — nothing to report.[/red]")
        raise typer.Exit(code=1)
    if all(o.error for o in reports[0].outcomes):
        console.print(
            "[red]Every scenario raised. That is an apparatus failure, not a measurement — "
            "no row is written.[/red]"
        )
        console.print(f"[dim]{escape(reports[0].outcomes[0].error)}[/dim]")
        raise typer.Exit(code=1)

    arm = suite_arm(reports)
    mech_arm = mechanism_arm(reports)
    table = Table(
        title=f"right-hand suite v{SUITE_VERSION} — {len(reports)} run(s) of {arm.n} scenarios",
        show_header=True,
    )
    table.add_column("scenario")
    table.add_column("block")
    table.add_column("runs")
    table.add_column(f"pass^{arm.k}")
    table.add_column("mechanism")
    table.add_column("asserts", overflow="fold")
    for position, scenario in enumerate(suite):
        row = [report.outcomes[position] for report in reports]
        marks = " ".join("[green]o[/green]" if o.passed else "[red]x[/red]" for o in row)
        if row[0].mechanism_active is None:
            cell = "[dim]none declared[/dim]"
        else:
            fired = sum(1 for o in row if o.mechanism_active)
            cell = f"{fired}/{len(row)}" if fired else "[yellow]never fired[/yellow]"
        block = "C" if scenario.block == CONTROL else f"D:{scenario.family}"
        table.add_row(
            scenario.id,
            block,
            marks,
            "[green]yes[/green]" if all(o.passed for o in row) else "no",
            cell,
            scenario.asserts,
        )
    console.print(table)

    # The families whose environment never produced its defect. Criterion R4: two or more of these
    # and the run is uninformative about the environment, whatever the headline says.
    silent = sorted(
        {
            suite[i].family
            for i in range(len(suite))
            if suite[i].block == DISCRIMINATING
            and suite[i].family != "split"
            and not any(report.outcomes[i].mechanism_active for report in reports)
        }
    )
    if silent:
        console.print(
            f"[yellow]families reading NOT MEASURED: {', '.join(silent)} — the defect was never "
            f"presented, so those rows are no evidence either way[/yellow]"
        )

    icc = "n/a" if arm.icc is None else f"{arm.icc:+.2f}"
    console.print(
        f"[bold]pass@1 {arm.pass_at_1:.1%}[/bold]   pass^{arm.k} {arm.pass_pow_k:.1%}   "
        f"flip {arm.flip_rate:.1%} [dim](the noise floor)[/dim]   ICC(1) {icc}"
        + (f" [dim]({arm.icc_reason})[/dim]" if arm.icc is None else "")
    )
    console.print(f"[dim]k={arm.k}: {seeds_verdict(arm.k)}[/dim]")
    if mech_arm is None:
        console.print("[dim]mechanism-active: no scenario declares one[/dim]")
    elif not mech_arm.active_trials:
        console.print(
            "[yellow]mechanism-active: 0 trials — NOT MEASURED (nothing ever fired; the suite is "
            "measuring the model through a session-shaped hole)[/yellow]"
        )
    else:
        assert mech_arm.active_pass_rate is not None
        console.print(
            f"mechanism-active: {mech_arm.active_pass_rate:.1%} over {mech_arm.active_trials} "
            f"active trials of {mech_arm.n * mech_arm.k}"
        )
    control, headline = block_arm(reports, CONTROL), block_arm(reports, DISCRIMINATING)
    if control is not None:
        # Criterion R2, and it is read BEFORE the headline on purpose (§2aa): one arm reproduces a
        # published number or there is no comparison to make, only two numbers.
        verdict = (
            "[green]clean — the run is valid[/green]"
            if control.pass_at_1 == 1.0
            else "[red]a control row FAILED — this run is INVALID, not a low score (R2)[/red]"
        )
        console.print(f"Block C (control, not scored): {control.pass_at_1:.1%} — {verdict}")
    if headline is not None:
        if headline.pass_at_1 >= 0.85:
            band = "[red]at the CEILING — the exact failure of the four suites before this[/red]"
        elif headline.pass_at_1 <= 0.20:
            band = "[red]at the FLOOR — as uninformative as a ceiling[/red]"
        elif 0.30 <= headline.pass_at_1 <= 0.65:
            band = "[green]inside the registered 30-65% band[/green]"
        else:
            band = "[yellow]outside the registered 30-65% band[/yellow]"
        # The band is the weak part of the registration and says so: whoever picks the trap-to-twin
        # ratio picks the mean. The per-row rules R1-R5 are what carries it, and the readable unit
        # is the row — 20 rows x k=3 is Wilson +/-11 pp, so a headline move under ~12 pp is noise.
        console.print(f"[bold]Block D (headline) pass@1 {headline.pass_at_1:.1%}[/bold] — {band}")

    observed = next(
        (o.model for report in reports for o in report.outcomes if o.model), model or "unknown"
    )
    record = series_record(
        reports,
        model=observed,
        # The repository root: chimera/cli/commands/misc.py -> parents[3].
        sha=repo_sha(Path(__file__).resolve().parents[3]),
        date=datetime.now(UTC).isoformat(timespec="seconds"),
        arm=arm,
    )
    path = Path(series) if series else settings.home / "scenarios.jsonl"
    append_series(path, record)
    total = record["usd"]
    console.print(
        f"[dim]cost ${total if total is not None else 'unpriced'} · "
        f"{record['prompt_tokens']}+{record['completion_tokens']} tok · "
        f"{record['seconds']}s · series → {path}[/dim]"
    )


@app.command()
def crew(
    task: str = typer.Argument(..., help="The task for the crew."),
    mode: str = typer.Option("sequential", "--mode", help="sequential | supervisor"),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    fuse: bool = typer.Option(False, "--fuse", help="Use the fusion engine as the backend."),
) -> None:
    """Run a multi-agent crew on a task (Tier 3). Requires a provider key."""
    from chimera.orchestration import Role, RoleAgent, SupervisorCrew, demo_crew
    from chimera.providers import LLMGateway

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse or settings.auto_fuse:  # explicit --fuse or the CHIMERA_AUTO_FUSE default
        from chimera.fusion import RoutedBackend
        from chimera.fusion.factory import fusion_engine

        backend = RoutedBackend(gateway, fusion_engine(gateway))

    if mode == "supervisor":
        supervisor = RoleAgent(
            Role("supervisor", "You coordinate a team and synthesize the single best final answer."),
            backend,
        )
        workers = [
            RoleAgent(Role("analyst", "You analyze the task and surface the key facts and trade-offs."), backend),
            RoleAgent(Role("engineer", "You propose a concrete, practical implementation."), backend),
            RoleAgent(Role("skeptic", "You find flaws, risks and missing cases in the approach."), backend),
        ]
        result = SupervisorCrew(supervisor, workers).run(task)
    else:
        result = demo_crew(backend).run(task)

    console.print(escape(str(result.answer)))
    console.print(f"[dim]({mode} crew, {len(result.transcript)} agent messages)[/dim]")


@app.command()
def lifecycle(
    task: str = typer.Argument(..., help="The feature/task to take through the SDLC."),
    verify: str = typer.Option(None, "--verify", help="Test command for the test stage (exit 0)."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_attempts: int = typer.Option(2, "--max-attempts", help="Build/test verify-or-revert budget."),
) -> None:
    """SDLC crew: plan -> build -> test -> review with verify-or-revert. Requires a key."""
    from chimera.orchestration import lifecycle_crew
    from chimera.providers import LLMGateway, MissingCredentialsError

    if not get_settings().can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    crew = lifecycle_crew(
        LLMGateway(),
        workspace=Path(workspace),
        verify=verify,
        model=model,
        max_build_attempts=max_attempts,
    )
    try:
        result = crew.run(task)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    for stage in result.stages:
        mark = "[green]✓[/green]" if stage.passed else "[red]✗[/red]"
        console.print(f"{mark} [bold]{stage.name}[/bold]")
        console.print(f"  {stage.output.strip()[:500]}")
    status = "[green]success[/green]" if result.success else "[red]failed[/red]"
    console.print(f"\nlifecycle: {status}")
    if not result.success:
        raise typer.Exit(code=1)  # a gate must fail the process, not just print red (CI/scripts)


@app.command()
def workflow(
    file: str = typer.Argument(..., help="Workflow YAML file (declarative loop)."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
) -> None:
    """Run a declarative workflow — a designed loop — from a YAML file. Requires a key."""
    from chimera.workflow import load_workflow, run_workflow
    from chimera.workflow.executors import build_executors

    if not get_settings().can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    flow = load_workflow(file)
    result = run_workflow(flow, build_executors(workspace=Path(workspace), model=model))
    console.print(f"[bold]{result.name}[/bold]")
    for run in result.runs:
        if run.skipped:
            console.print(f"  [dim]– {run.name} (skipped)[/dim]")
            continue
        mark = "[green]✓[/green]" if run.success else "[red]✗[/red]"
        extra = f" ×{run.attempts}" if run.attempts > 1 else ""
        console.print(f"  {mark} {run.name} [{run.uses}]{extra}")
    status = "[green]success[/green]" if result.success else "[red]failed[/red]"
    console.print(f"workflow: {status}")
    if not result.success:
        raise typer.Exit(code=1)  # advertised as a production entrypoint — a failed run must exit non-zero


@app.command()
def drift(
    spec: str = typer.Argument(..., help="Spec YAML file."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
    only: str = typer.Option(None, "--only", help="Check only this requirement id (project cards)."),
) -> None:
    """Drift gate: check the workspace against a spec (Spec Growth). Exit 1 on drift."""
    from chimera.governance import check_drift, load_spec
    from chimera.governance.drift import Spec

    spec_obj = load_spec(spec)
    if only is not None:
        # A per-requirement gate: the project orchestrator's cards verify with `--only <id>` so
        # each card's success maps to exactly one requirement of the spec.
        matched = [r for r in spec_obj.requirements if r.id == only]
        if not matched:
            console.print(f"[red]no requirement {only!r} in spec {spec_obj.name!r}[/red]")
            raise typer.Exit(code=2)
        spec_obj = Spec(name=spec_obj.name, requirements=matched)
    report = check_drift(spec_obj, Path(workspace))
    console.print(f"[bold]{report.name}[/bold]")
    for result in report.results:
        mark = "[green]✓[/green]" if result.satisfied else "[red]✗[/red]"
        detail = f" — {result.detail}" if result.detail else ""
        console.print(f"  {mark} {result.id}{detail}")
    if report.aligned:
        console.print("[green]aligned[/green] — spec and code are in sync")
    else:
        console.print("[red]drift[/red] — spec and code are out of sync")
        raise typer.Exit(code=1)


@app.command()
def meta(
    task: str = typer.Argument(..., help="The task to design a specialized agent for."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
) -> None:
    """Meta-agent: design a specialized agent blueprint for a task. Requires a key."""
    from chimera.ecosystem import MetaAgent
    from chimera.providers import LLMGateway
    from chimera.tools import default_registry

    if not get_settings().can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    allowed = default_registry().names()
    blueprint = MetaAgent(LLMGateway(), allowed_tools=allowed, model=model).design(task)
    if blueprint is None:
        console.print("[red]the meta-agent could not produce a valid blueprint[/red]")
        raise typer.Exit(code=1)

    table = Table(title="Agent blueprint", show_header=False, title_style="bold")
    table.add_row("Name", blueprint.name)
    table.add_row("Tools", ", ".join(blueprint.tools) or "[dim]none[/dim]")
    table.add_row("Role prompt", blueprint.role_prompt)
    console.print(table)


@app.command("find")
def find_command(
    query: str = typer.Argument(..., help="What you are looking for, in words."),
    path: str = typer.Option(".", "--path", help="Repository to search."),
    k: int = typer.Option(8, "--k", help="How many results."),
    reindex: bool = typer.Option(False, "--reindex", help="Rebuild the index before searching."),
    semantic: bool = typer.Option(
        False, "--semantic", help="Fuse keyword with embeddings. Costs money to index."
    ),
) -> None:
    """Search a repository by what code DOES, not by the string it contains.

    `chimera/rag/` has been in the tree since 0.44.0 — symbol-level chunking over Python's AST, one
    SQLite file with an FTS5 index, RRF fusion — measured, documented, and reachable from nothing.
    A library with no entrance is a library nobody has. This is the entrance.

    Keyword by default; `--semantic` fuses it with embeddings, and the fusion is what was measured
    and adopted in `bench/rag/RESULTS.md`: hybrid 0.5050 against keyword 0.4425 on this repository,
    +6.25 pp paired over 400 probes, McNemar p = 1.7e-04.

    **`--semantic` means HYBRID, never vectors alone**, and that is the measurement rather than a
    preference: the vector arm on its own scored **0.4100 — worse than keyword**. Every point of the
    win comes from fusing two rankings that are wrong about different things. A flag that gave you
    the vector arm would be a flag that made your search worse.

    The recall figure is printed with every search because it is per-corpus and per-embedder: the
    same harness measures 0.4750 on this repository as it stood three weeks ago, and there is no
    conversion from one embedding model's vector space to another's.
    """
    from chimera.rag import ChunkStore, default_index_path, walk
    from chimera.rag.hybrid import reciprocal_rank_fusion

    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        console.print(f"[red]{root} is not a directory[/red]")
        raise typer.Exit(code=1)

    settings = get_settings()
    embed = None
    if semantic:
        from chimera.evolution.wiring import semantic_embed

        embed = semantic_embed(settings, force=True)
        if embed is None:
            console.print("[red]--semantic needs an embedder; none could be built[/red]")
            raise typer.Exit(code=1)

    index = default_index_path(settings.home, root)
    store = ChunkStore(index)
    try:
        if reindex or store.stats()["chunks"] == 0:
            with console.status("indexing..."):
                n = store.replace_all(walk(root))
            console.print(f"[dim]indexed {n} chunks from {root}[/dim]")
        keyword = store.search_keyword(query, k=k)
        hits = keyword
        if embed is not None:
            pending = store.stats()["chunks"] - store.stats().get("embedded", 0)
            # Said BEFORE the money is spent, not in a footnote afterwards. Embedding a corpus is
            # the one part of this command with a bill, and a user who did not expect one has no way
            # to un-spend it.
            if pending > 0:
                console.print(
                    f"[dim]embedding {pending} chunks with {settings.embed_model} — "
                    f"this costs money, and only the first time or after --reindex[/dim]"
                )
            with console.status("embedding..."):
                # `embedder=` arms the guard that zeroes every vector when the model or its width
                # changes. Without it a mixed-dimension index reports healthy and returns nothing.
                store.embed_missing(embed, embedder=settings.embed_model)
            vector = store.search_vector(embed([query])[0], k=k)
            hits = reciprocal_rank_fusion([keyword, vector], limit=k)
        stats = store.stats()
    finally:
        store.close()

    def calibration() -> None:
        """The measured recall, printed on every search — including one that found nothing.

        It used to sit only after the results table, so the `return` below skipped it: a search that
        matched nothing showed no number at all. That is the moment the number is worth most, because
        an empty screen is where a reader has to decide between "my query was wrong" and "this
        retriever misses half of what it is asked for", and only one of those is true here.
        """
        if semantic:
            console.print(
                f"[dim]hybrid (keyword + {settings.embed_model}). Measured on this repository, "
                "3459 chunks: recall@10 of 0.505 against 0.443 for keyword alone — +6.25 pp paired "
                "over 400 probes, p = 1.7e-04. The vector arm ALONE scores 0.410, below keyword: "
                "the gain is the fusion, not the embeddings.[/dim]"
            )
        else:
            console.print(
                "[dim]keyword only. Measured on this repository, 3459 chunks: recall@10 of 0.443 — "
                "more than half of what you look for is NOT in the top ten. --semantic measures "
                "0.505, and costs an embedding pass over the corpus.[/dim]"
            )

    if not hits:
        # Named, because "no results" from a stale index and "no results" from a repository that
        # really does not contain this are different problems with the same empty screen.
        console.print(
            f"[dim]nothing matched in {stats['chunks']} chunks from {stats['files']} files. "
            f"If the code moved since the index was built, try --reindex.[/dim]"
        )
        calibration()
        return

    table = Table(title=repr(query), show_header=True, title_style="bold")
    table.add_column("where", style="cyan", no_wrap=True)
    table.add_column("what")
    table.add_column("score", justify="right")
    for hit in hits:
        chunk = hit.chunk
        table.add_row(
            f"{chunk.path}:{chunk.start_line}",
            chunk.symbol or f"[dim]{chunk.kind}[/dim]",
            f"{hit.score:.3f}",
        )
    console.print(table)
    # The number, every time. Measured on this repository, pre-registered before any embedder
    # existed so it could not be chosen after seeing what looked good.
    calibration()
