"""The autonomous loop: solve, solve-batch, crew-isolated, explore, and the conversation hand-off.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.markup import escape
from rich.panel import Panel

from chimera.cli.commands._shared import (
    _BATCH_TASKS_ARG,
    _CREW_WORKER_OPT,
    _apply_tool_allowlist,
    _cascade_backend,
    _emit_headless,
    _exit_for,
    _fused_if,
    _headless,
    _headless_payload,
    _json_line,
    _machine_stdout,
    _resolve_cli_roles,
    _stream_sink,
    _task_from_stdin,
    app,
    console,
    owner_identity,
)
from chimera.cli.commands.learning import _curation_outcome, _load_playbook, _save_playbook
from chimera.cli.commands.memory import _memory_manager
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.core.autonomous import AutonomousResult
    from chimera.providers import SupportsComplete



#: The conversation a ``/solve`` was typed in, while that run is in flight — its governed stack
#: (:class:`~chimera.cli.right_hand.RightHand`), or None for a plain ``chimera solve``. A context
#: variable rather than a ``solve`` parameter: the approver and the ledger are live objects a
#: command line cannot carry. See ``_solve_from_conversation``.
_CONVERSATION_GOVERNANCE: ContextVar[Any] = ContextVar(
    "chimera_conversation_governance", default=None
)


def _solve_defaults() -> dict[str, Any]:
    """Every parameter ``chimera solve`` takes, with its REAL default value.

    Read off ``solve``'s own signature rather than typed out again. A Typer command called as a
    plain function hands the parameters you omit their ``typer.OptionInfo`` *default objects* — not
    their defaults — and ``bool(OptionInfo)`` is True, which is how the documented ``tui`` fallback
    died on its first turn (`#398`). Listing all fifty-two by hand would have re-created that trap
    the day somebody adds the fifty-third; unwrapping ``.default`` here cannot go stale.

    Read from ``_SOLVE_COMMAND`` and not from the module name ``solve``: the parameter list must
    come from the shipped command even when the name has been substituted, or a substitute would
    silently redefine what it is called with.
    """
    import inspect

    values: dict[str, Any] = {}
    for name, param in inspect.signature(_SOLVE_COMMAND).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue  # `*args`/`**kwargs` are not names anything can be passed under
        default = param.default
        values[name] = getattr(default, "default", default)
    return values


class SolveFailed(typer.Exit):
    """``chimera solve`` finished without success — exit 1, and the run it produced.

    A plain ``typer.Exit(code=1)`` is what the command has always raised and what the shell still
    sees; this subclass changes nothing about that and adds the one thing an in-process caller
    needs. ``/solve`` in a REPL has to put the loop's own answer into the conversation, and a
    failed run has an answer too — the alternative was the surface writing a sentence of its own
    into the ``assistant`` slot, which ``ChatTurn`` documents against precisely because that text
    is replayed into every later prompt as if the model had said it.
    """

    def __init__(self, result: Any) -> None:
        super().__init__(code=1)
        self.result = result


def _solve_from_conversation(
    task: str,
    *,
    workspace: str,
    model: str | None,
    write_region: str | None,
    budget: Any = None,
    hand: Any = None,
) -> Any:
    """Hand one task to the verified loop and come back. Returns the run, or None if it never ran.

    The Code screen has two buttons and `chimera/api/code_api.py:1215-1221` states the difference:
    Send edits your files and keeps whatever it wrote, "Run with verification" plans, verifies and
    can undo. The terminal had only the first, on every surface. This is the second.

    It calls ``solve`` — the command, with every one of its parameters — rather than assembling a
    second ``AutonomousAgent``. The construction is three hundred lines of closure binding fifty
    flags; a copy of it in this file would be a weaker product wearing the same name the day the
    two drift, and there is no factory to share because ``solve``'s worker, planner, manager,
    verifier, checkpointer and six learning seams are all built inside one closure over those
    flags. What is shared instead is the command itself.

    Never automatic: it runs only when a person types ``/solve``. It says what it is about to do
    before it does it, because unlike a chat turn this one edits files and can spend several
    attempts' worth of money.

    ``hand`` is the conversation's governed stack (:class:`~chimera.cli.right_hand.RightHand`), and
    the loop runs under it: the conversation's own taint ledger and approver, the owner's
    ``CHIMERA_REACH`` floor, and the trust kernel exactly when the conversation has one (its
    ``CHIMERA_GOVERNANCE`` mode, ``observe`` staying observe) — the same posture, not a stricter one. Without it this overrode only
    task, workspace, model, region and ceiling, so the loop ran on ``solve``'s defaults —
    ``guard=False``, ``taint=False``, no floor — and a governed conversation had an ungoverned exit
    one slash command away. The approver and the ledger travel through ``_CONVERSATION_GOVERNANCE``
    rather than as ``solve`` options: they are live objects a command line cannot carry, and a
    hidden option would still change the command's published signature.
    """
    from chimera.api.runs import total_usd

    if budget is not None:
        blocked = budget.blocked()
        if blocked:
            from chimera.interface import render

            console.print(render.budget_spent_line(blocked))
            return None
    args = _solve_defaults()
    args.update(
        task=task,
        workspace=workspace,
        model=model,
        write_region=write_region,
        # What is left of the conversation's ceiling, not a fresh one. `AutonomousAgent._run_budget`
        # reads `max_usd` off the worker's config to build ONE budget spanning every attempt, so
        # this bounds the whole nested run — and the spend is charged back below, or a `/solve`
        # typed twice would get the full allowance twice and the ceiling would not be one.
        max_usd=(budget.remaining if budget is not None else None),
    )
    if hand is not None:
        # The conversation's posture, not a stricter one: its taint ledger is always on, its kernel
        # only when CHIMERA_GOVERNANCE is observe/enforce. Forcing `guard=True` under `off` (the
        # default) put BLOCK/REVIEW in front of actions the conversation runs unasked — under a pipe
        # its deny approver turned them into refusals that exist only inside `/solve`.
        args.update(guard=getattr(hand, "governance_mode", "off") != "off", taint=True)
    attempts = args["max_attempts"]
    console.print(
        f"[yellow]→ handing this to the verified loop[/yellow] [dim](the same one "
        f"`chimera solve` runs): plan → edit → verify-or-revert in {escape(workspace)}, up to "
        f"{attempts} attempt(s). It CAN change files; a failed attempt is reverted."
        + (f" Ceiling: ${args['max_usd']:.4f}." if args["max_usd"] else "")
        + "[/dim]"
    )
    console.print(f"[dim]task: {escape(task)}[/dim]")
    result: Any = None
    token = _CONVERSATION_GOVERNANCE.set(hand)
    try:
        result = solve(**args)
    except SolveFailed as failed:
        result = failed.result
    except typer.Exit:
        # `solve` refuses before running for reasons of its own — no key, a bad flag combination.
        # It has already said which; the REPL stays open.
        return None
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted — the run was stopped[/dim]")
        return None
    finally:
        _CONVERSATION_GOVERNANCE.reset(token)
    if budget is not None and result is not None:
        # Priced from the attempts, which is the same number `solve` just printed. `total_usd`
        # answers None when a leg had no price, and `charge` treats that the way an unpriced call is
        # treated everywhere else: sticky, and the ceiling refuses the next turn rather than
        # pretending the run was free.
        budget.charge(total_usd(result.attempts), label="the /solve run")
    return result


def _run_solve_command(
    session: Any,
    argument: str,
    *,
    workspace: str,
    agent: Any,
    write_region: str | None,
    budget: Any = None,
    hand: Any = None,
) -> None:
    """``/solve`` in a REPL: pick the task, run the verified loop, put its answer in the thread.

    With no argument the task is the last thing the person asked — "the conversation's current
    task" — and the surface prints it back before running, because a command that spends several
    attempts' worth of money on a guess about what you meant must show the guess first.

    The run's own answer is recorded as an ordinary exchange, which is what makes ``/solve``
    different from ``/task`` before it was fixed: the next turn is aware of what the loop did.
    ``provenance`` is ``UNKNOWN`` and not ``CLEAN`` — the loop ran its own tools in its own
    process with its own ledger, and nothing in this session watched them, so "clean" here would
    be a guarantee derived from an instrument that could not have shown the opposite.

    The model comes off ``agent.config`` rather than from the command line, because ``/model``
    writes there: a person who pinned a model mid-conversation and then typed ``/solve`` meant the
    model they had just named, not the one they launched with.
    """
    from chimera.interface.session import UNKNOWN, ChatTurn

    task = argument.strip() or _last_user_message(session)
    if not task:
        console.print("[dim]usage: /solve <task> — or ask something first and type /solve[/dim]")
        return
    result = _solve_from_conversation(
        task,
        workspace=workspace,
        model=agent.config.model,
        write_region=write_region,
        budget=budget,
        hand=hand,
    )
    if result is None:
        return
    session.turns.append(
        ChatTurn(user=f"/solve {task}", assistant=str(result.answer), provenance=UNKNOWN)
    )


def _last_user_message(session: Any) -> str:
    """The last thing the person typed in this thread, or ``""`` when they have not yet."""
    for turn in reversed(session.turns):
        if turn.user.strip():
            return str(turn.user).strip()
    return ""


def _append_json_line(path: Path, row: dict[str, Any]) -> None:
    """One JSON object per line, best-effort. Used by `--tool-router` to record how much the
    router acted; a measurement that fails to persist must not fail the run it measured."""
    import json

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


@app.command()
@_headless
def solve(
    task: str = typer.Argument(None, help="The task to solve autonomously (omit with --approve/--deny)."),
    verify: str = typer.Option(None, "--verify", help="Verification command (exit 0 == success)."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_attempts: int = typer.Option(3, "--max-attempts", help="Max verify-or-revert attempts."),
    max_steps: int = typer.Option(8, "--max-steps", help="Max tool-calling steps per attempt."),
    max_usd: float = typer.Option(
        None,
        "--max-usd",
        help="Stop the whole run once this much has been spent (all attempts together).",
    ),
    context_budget: float = typer.Option(
        None,
        "--context-budget",
        help="Fraction of the model's window to spend on the prompt before compacting (e.g. 0.6).",
    ),
    no_plan: bool = typer.Option(False, "--no-plan", help="Skip the planning step."),
    no_manager: bool = typer.Option(False, "--no-manager", help="Skip Manager review."),
    rubric: bool = typer.Option(False, "--rubric", help="Manager reviews via the cascade rubric."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    cascade: bool = typer.Option(
        False, "--cascade", help="Tiered routing: weak -> gate -> mid -> gate -> fusion (cheap by default)."
    ),
    guard: bool = typer.Option(False, "--guard", help="Gate tool calls through the governance kernel."),
    allow_tools: str = typer.Option(
        None, "--allow-tools", help="Per-session allowlist: only these tools (comma-separated)."
    ),
    deny_tools: str = typer.Option(
        None, "--deny-tools", help="Per-session denylist: drop these tools (comma-separated)."
    ),
    taint: bool = typer.Option(
        False, "--taint", help="Track a capability ledger + review execution of tainted input."
    ),
    collect: bool = typer.Option(
        True, "--collect/--no-collect", help="Record trajectories for opt-in model evolution."
    ),
    no_remember: bool = typer.Option(
        False, "--no-remember", help="Don't auto-write a long-term memory fact on success."
    ),
    no_evolve_skills: bool = typer.Option(
        False, "--no-evolve-skills", help="Don't auto-propose a learned skill when a task recurs."
    ),
    isolate: bool = typer.Option(
        False, "--isolate", help="Run in an isolated git worktree; changes copied back only on success."
    ),
    explorer: bool = typer.Option(
        False, "--explorer", help="Give the agent an isolated Context Explorer for repo search (FastContext-style)."
    ),
    subagents: bool = typer.Option(
        False, "--subagents", help="Give the agent spawn_subagent to delegate subtasks to isolated subagents."
    ),
    repo_map: bool = typer.Option(
        False, "--repo-map", help="Prepend a structural map of the workspace (files + top-level symbols) to the agent's context."
    ),
    tool_router: str = typer.Option(
        None, "--tool-router",
        help="EXPERIMENT (study 20 B4): a cheap MODEL names the tool before each step and the executor is given only that tool. Reads a shallow context on purpose. Narrows only — an undecided router leaves the full list, and the run's receipt counts how often that happened.",
    ),
    tool_router_mode: str = typer.Option(
        "narrow", "--tool-router-mode",
        help="EXPERIMENT (study 22 B4b): 'narrow' gives the executor only the routed tool (B4, measured worse); 'hint' keeps every tool and only suggests one for the step, with no ANSWER.",
    ),
    escalate_on_tool_loop: str = typer.Option(
        None, "--escalate-on-tool-loop",
        help="EXPERIMENT (study 24 M6): when the tool-loop breaker trips, hand the rest of the run to this stronger MODEL instead of stopping. A second trip stops as before. Off by default.",
    ),
    snapshot_at_tool_loop: str = typer.Option(
        None, "--snapshot-at-tool-loop",
        help="EXPERIMENT (study 24 M6 fork): with --escalate-on-tool-loop, copy the workspace to this DIR at the trip, before escalating — what stopping would have left. Off by default.",
    ),
    progress_ledger: bool = typer.Option(
        False, "--progress-ledger", help="After a failed attempt, run a structured self-check that steers the retry (helps weak models)."
    ),
    checklist: bool = typer.Option(
        False, "--checklist", help="Extract the task's atomic requirements and grade each attempt's coverage (catches dropped constraints)."
    ),
    gen_tests: bool = typer.Option(
        False, "--gen-tests", help="With no --verify: generate executable pytest grounded in the task's requirements and use it as the gate (catches wrong code the coverage grade rubber-stamps). Measured on 78 labelled patches (bench/test_gate_two_sided): fails every wrong patch, and reverts 4 of 64 correct ones (6%) on a test of its own that is wrong — opt-in for that reason."
    ),
    profile: str = typer.Option(
        None, "--profile",
        help="Model-role profile: economy | balanced | max. Puts a different model on each role "
             "(explore/plan/edit/review) drawn from the tier ladder. Routing is NOT yet shown to "
             "improve outcomes — see bench/role_routing/PREREGISTRATION.md.",
    ),
    role_models: str = typer.Option(
        None, "--role-models",
        help="Per-role model overrides: 'edit=vendor/slug,plan=vendor/other'. Roles: explore, plan, "
             "edit, review. Merges over --profile; a role left unset keeps --model. `verify` is not "
             "a role here — it runs a command and has no model to choose.",
    ),
    write_region: str = typer.Option(
        None, "--write-region", help="Comma-separated globs the file-writers may touch (e.g. 'src/**,*.py'). A write outside is refused — blocks an injected instruction from rewriting an unrelated file."
    ),
    probe_log: bool = typer.Option(
        False, "--probe-log", help="Log (arm, proxy=manager-judgment, reward=verified) per attempt to <home>/probe.jsonl for PROBE best-arm selection (see `chimera probe-select --from-log`). Needs --verify + a manager."
    ),
    normalize_task: bool = typer.Option(
        False, "--normalize-task", help="Reshape a long, rambling bug-report task into a salient-facts-first form (location/repro/expected-vs-actual/fix-hint) before planning. No-op on non-bug or short tasks."
    ),
    playbook: bool = typer.Option(
        False, "--playbook", help="Inject the stored ACE strategy playbook into context, then curate it from this run's outcome (closed loop)."
    ),
    skill_cards: bool | None = typer.Option(
        None, "--skill-cards/--no-skill-cards", help="Read learned skill cards back into context (the learn->use loop). Default follows settings.skill_cards; this overrides it per run."
    ),
    agreement: int = typer.Option(
        1, "--agreement", help="With --fuse: sample K cheap answers per turn; escalate to fusion when they disagree (free confidence signal)."
    ),
    strong_verify: str = typer.Option(
        None, "--strong-verify", help="Model slug of a stronger, independent judge that grades hard-turn (retried) results before accepting them."
    ),
    replan: bool = typer.Option(
        False, "--replan", help="On a stall, rebuild the plan from accumulated failure causes (dual-ledger) instead of just nudging."
    ),
    diff_feedback: bool = typer.Option(
        False, "--diff-feedback", help="Show a failed attempt its own reverted diff, as a path not to retake."
    ),
    keep_workspace: bool = typer.Option(
        False, "--keep-workspace", help="On failure, leave the last attempt's edits on disk for an external grader (don't revert)."
    ),
    require_diff: bool = typer.Option(
        False, "--require-diff", help="Fail an attempt that changed no file — for code tasks, an explanation is not a fix."
    ),
    recovery: str = typer.Option(
        "generic", "--recovery", help="How a failed attempt's retry is briefed: generic (manager prose + verifier output) or targeted (a brief aimed at the classified failure)."
    ),
    stagnation_fuzzy: bool = typer.Option(
        False, "--stagnation-fuzzy", help="Match repeated-failure signatures approximately, not byte-identically."
    ),
    contract: str = typer.Option(
        None, "--contract", help="Machine-checkable success clauses, comma-separated: file_exists:PATH | file_contains:PATH:REGEX | answer_matches:REGEX."
    ),
    stream: bool = typer.Option(
        False, "--stream", help="Print live progress events (attempt/result/status) as the run proceeds."
    ),
    json_output: bool = typer.Option(False, "--json", help="Print one final JSON object."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Print JSON events as lines."),
    thread: str = typer.Option(
        None, "--thread", help="Checkpoint this run under a thread id; re-run with the same id to resume after a crash."
    ),
    pause_on_taint: bool = typer.Option(
        False, "--pause-on-taint", help="Pause for human approval before finalizing a run that consumed untrusted content (needs --thread)."
    ),
    approve: str = typer.Option(
        None, "--approve", help="HITL accept: finalize a paused run as-is, by thread id (no task needed)."
    ),
    deny: str = typer.Option(
        None, "--deny", help="HITL ignore: discard a paused run by thread id (no task needed)."
    ),
    respond: str = typer.Option(
        None, "--respond", help="HITL respond: resume a paused run by thread id with --feedback guidance."
    ),
    feedback_text: str = typer.Option(
        None, "--feedback", help="Guidance for --respond (fed back so the run tries again)."
    ),
    edit: str = typer.Option(
        None, "--edit", help="HITL edit: finalize a paused run with the corrected --answer, by thread id."
    ),
    answer_text: str = typer.Option(
        None, "--answer", help="The human-corrected answer for --edit."
    ),
) -> Any:
    """Tier-2: autonomously solve a task with plan + verify-or-revert. Requires a key.

    In the shell nothing about this has changed: a run that fails still exits 1. Inside the process
    it now hands its run back — returned on success, carried on the ``SolveFailed`` exit otherwise —
    so ``/solve`` in a REPL can put the loop's own answer into the conversation instead of writing
    a sentence of its own.
    """
    from chimera.core import (
        Agent,
        AgentConfig,
        AutonomousAgent,
        AutonomousConfig,
        CompletionContract,
        Manager,
        Planner,
        ProgressLedger,
        RequirementChecklist,
        RunCheckpointer,
        SpecTestGenerator,
        StrongVerifier,
        WorkspaceGuard,
    )
    from chimera.core.failure_class import RECOVERY_MODES
    from chimera.core.tool_router import ToolRouter as _ToolRouter
    from chimera.core.verify import CommandVerifier
    from chimera.evolution import build_evolution_context
    from chimera.fusion.probe_log import ProbeLog as _ProbeLog
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.tools import default_registry

    machine_out = _machine_stdout(json_output or jsonl)
    settings = get_settings()

    # Human-in-the-loop envelope (LangGraph {accept, edit, respond, ignore}) over the taint-pause.
    # 'ignore' (deny) drops the run without touching a model (no key needed).
    if sum(bool(x) for x in (deny, approve, respond, edit)) > 1:
        console.print("[red]Use only one of --approve / --respond / --edit / --deny.[/red]")
        raise typer.Exit(code=1)
    if edit and not answer_text:
        # An edit finalizes the reviewed answer as-is; without --answer it would commit an empty one.
        console.print("[red]--edit requires --answer with the revised text.[/red]")
        raise typer.Exit(code=1)
    if recovery not in RECOVERY_MODES:
        raise typer.BadParameter(f"unknown recovery {recovery!r}; expected generic or targeted")
    if deny:
        RunCheckpointer(settings.home / "runs.db").delete(deny)
        console.print(f"[yellow]Discarded[/yellow] paused run {deny!r}.")
        return
    if approve or respond or edit:
        cp = RunCheckpointer(settings.home / "runs.db")
        thread = approve or respond or edit
        if approve:
            ok, verb = cp.respond(thread, "accept"), "Approved"
        elif edit:
            ok, verb = cp.respond(thread, "edit", answer=answer_text), "Edited"
        else:
            ok, verb = cp.respond(thread, "respond", feedback=feedback_text), "Responding to"
        if not ok:
            console.print(f"[red]No paused run awaiting approval for thread {thread!r}.[/red]")
            raise typer.Exit(code=1)
        saved = cp.load(thread)
        task = str((saved or {}).get("task", ""))
        console.print(f"[green]{verb}[/green] {thread!r} — {'resuming' if respond else 'finalizing'}.")
    elif task == "-" or (task is None and not sys.stdin.isatty()):
        task = _task_from_stdin(task)
    elif not task:
        console.print("[red]Provide a task, or use --approve/--deny/--respond/--edit <thread>.[/red]")
        raise typer.Exit(code=1)

    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    workspace_path = Path(workspace)
    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    planner_backend: SupportsComplete = gateway
    escalate_backend: SupportsComplete | None = None
    # --cascade (or CHIMERA_CASCADE): tiered routing weak -> gate -> mid -> gate -> fusion.
    # Takes precedence over --fuse (the cascade already has fusion as its top rung).
    if cascade or settings.cascade:
        backend = _cascade_backend(gateway, settings)
        from chimera.fusion import RoutedBackend, RoutingPolicy
        from chimera.fusion.factory import fusion_engine

        escalate_backend = RoutedBackend(gateway, fusion_engine(gateway), RoutingPolicy(mode="always"))
    # --fuse (explicit) or CHIMERA_AUTO_FUSE (production default) both route the worker
    # through the cost-aware router, so deep/error-sensitive turns fuse and cheap/tool
    # turns stay single-model.
    elif fuse or settings.auto_fuse:
        from chimera.fusion import RoutedBackend, RoutingPolicy
        from chimera.fusion.factory import fusion_engine

        engine = fusion_engine(gateway)
        # --agreement K: sample K cheap answers per turn; disagreement escalates to fusion
        # (a free confidence signal). K=1 (default) keeps the a-priori routing unchanged.
        backend = RoutedBackend(gateway, engine, agreement_k=agreement)
        # Observed-difficulty escalation (issue #3): a fusion-forced backend for retrying a
        # task that already failed verification — "the review surface is where the difficulty
        # signal lives". The AutonomousAgent uses it only after an attempt fails.
        escalate_backend = RoutedBackend(gateway, engine, RoutingPolicy(mode="always"))
        # Planning is a deep, tool-free reasoning turn — exactly where fusion pays off —
        # so an explicit --fuse routes the plan through fusion directly. Auto-fuse keeps
        # planning single unless asked, to bound cost.
        if fuse:
            planner_backend = engine

    # Collective (cross-model) skill evolution is meaningful whenever the run's reasoning peak is
    # fusion over a multi-model panel — true for BOTH --fuse and --cascade (the cascade's top rung is
    # the same fusion panel). Share the gate instead of tying it to --fuse alone (P2-cascade), so a
    # cascade run keeps the most transferable proposal across the panel, not a single-model one.
    # Counted on the panel fusion actually convenes (the ladder unless one was named), the same one
    # `build_evolution_context` hands the collective evolver — not on the raw frontier default.
    from chimera.fusion.factory import fusion_config

    panel_evolution = (fuse or cascade or settings.cascade) and len(fusion_config(settings).panel) >= 2

    # ACE playbook (--playbook): load the stored playbook once so it is injected into the run
    # and curated back afterwards. Kept outside _run_solve so the worktree path doesn't shadow it.
    stored_playbook = _load_playbook() if playbook else None

    # Roles, resolved ONCE and before the registry is assembled — the explorer's model is part of
    # the routing, so computing this later would leave that one role silently unrouted. Resolved by
    # the same function the desktop endpoint uses: a bench that drives the CLI has to exercise the
    # routing the app ships, or it measures something nobody uses.
    roles = _resolve_cli_roles(profile, role_models, settings)
    # One meter per run, wrapping the gateway UNDERNEATH the non-worker backends — see
    # chimera.orchestration.metering. The worker is not metered: it prices itself via
    # AgentResult.usd, and routing it here too would double-count every step.
    from chimera.orchestration.metering import MeteredBackend

    meter = MeteredBackend(gateway, label="overhead")

    # Built OUTSIDE `_run_solve` on purpose: the worktree path calls that function again on retry,
    # and a ledger created inside it would be discarded along with the evidence of what the run was
    # not allowed to do — which is the one fact this whole mechanism exists to keep.
    from chimera.governance import ApprovalLedger, approver_for

    # A `/solve` typed in a governed conversation runs under that conversation's stack — its
    # ledger, its approver, its record of answers, its reach floor — not under defaults the person
    # never chose for it. None for a plain `chimera solve`, which keeps every default it had.
    inherited = _CONVERSATION_GOVERNANCE.get()
    approvals = inherited.approvals if inherited is not None else ApprovalLedger()
    # Whether any attempt of this solve consumed untrusted content, read after `_run_solve` by the
    # playbook curation below: the ledger is built inside it, per workspace attempt.
    run_tainted: list[bool] = []

    def _run_solve(ws: Path) -> AutonomousResult:
        from chimera.tools.write_region import WriteRegion

        region = WriteRegion(write_region.split(","), ws) if write_region else None
        registry = default_registry(ws, write_region=region)
        # The web research sub-agent (study 25, S12), when switched on. Before the allowlist, so
        # the lists reach it by name like a built-in; it draws its web tools from the FINAL
        # registry, resolved late, for the reason the subagent below gives.
        if settings.research_agent:
            from chimera.core.research import ResearchWebTool

            registry.register(
                ResearchWebTool(gateway, lambda: registry, model=roles.models.explore or model)
            )
        # Per-session grant first (issue #4): scope the native tools before the meta-tools
        # (explorer/subagents) are added, so subagents inherit the same allowlist.
        from chimera.governance import AuditLog

        allow_audit = AuditLog(settings.home / "audit.jsonl") if (guard or taint) else None
        registry = _apply_tool_allowlist(
            registry, allow=allow_tools, deny=deny_tools, settings=settings, audit=allow_audit
        )
        if explorer:
            from chimera.core import ExploreRepositoryTool

            # Cheap explorer model: a narrow localization task doesn't need the worker's model.
            # A narrow localisation question does not need the editor's model.
            registry.register(
                ExploreRepositoryTool(
                    gateway, ws, model=roles.models.explore or model, max_turns=max_steps,
                    contract=settings.explorer_contract,
                )
            )
        if subagents:
            from chimera.core import SubAgentTool

            # The subagent draws from the tools registered so far (minus spawn itself) — resolved
            # LATE, and that is the whole point. `registry` is rebound below by `govern_registry`
            # and `ledger_registry`; passing the object here handed the subagent the RAW tools,
            # so the one path that can spawn work escaped every guard its parent was under. The
            # lambda closes over the NAME, so it resolves to whatever the final wrapper is.
            registry.register(
                SubAgentTool(gateway, lambda: registry, model=model, max_turns=max_steps)
            )
        # Someone on the other side of the gate. Both layers have accepted an approver since they
        # were written and neither was ever given one, which measured out as 100% of dangerous-class
        # calls refused on any run that read something external — the gate was never too strict,
        # there was nothing behind it. `ask` degrades to `deny` without a terminal, and every
        # decision is recorded so a refused run can be told apart from an idle one.
        # `home=` is what lets `ask` reach somebody who is not at this keyboard. Without it the
        # posture degraded to `deny` on every unattended surface — the VPS, a container, cron — so
        # the three-state gate had two states there and the mandate ("confirm before billing, before
        # a destructive migration, before touching RLS") had nothing to confirm with. Silence still
        # refuses; the question just gets asked now.
        approve = approver_for(settings.approval_mode, approvals, home=settings.home,
                               audit=AuditLog(settings.home / "audit.jsonl"))
        if inherited is not None:
            # The person at the keyboard, through the channel the conversation already uses — the
            # REPL's prompt — rather than a question written to disk inside their own turn.
            approve = inherited.approve or approve
            # The owner's floor, as `build_right_hand` applies it to the conversation. After the
            # explorer and the subagent are registered, so neither can carry a floored name back in.
            from chimera.api.posture import deployment_posture
            from chimera.governance import restrict_registry

            floor = deployment_posture(settings).deny_tools
            if floor:
                registry = restrict_registry(registry, allow=None, deny=floor)
        if guard and inherited is not None:
            # The kernel the conversation runs under, assembled by the same call with the same
            # arguments `build_right_hand` uses: the deployment's mode (observe keeps its
            # allow-and-record approver instead of becoming enforce through the REPL's prompt), the
            # REVIEW band when it is on, and the conversation's ledger lineage so a precedent set
            # while the session was clean does not answer once it has read something untrusted.
            from chimera.governance.profile import govern_step

            registry = govern_step(
                registry,
                settings=settings,
                audit=AuditLog(settings.home / "audit.jsonl"),
                surface="solve",
                attended=True,
                audit_allows=False,
                lineage=inherited.ledger.lineage,
                taint=inherited.ledger.record_fetch,
            ).registry
        elif guard:
            from chimera.governance import TrustKernel, govern_registry
            from chimera.governance.profile import owner_hooks

            solve_audit = AuditLog(settings.home / "audit.jsonl")
            registry = govern_registry(
                registry, TrustKernel(audit=solve_audit), approve=approve
            )

            def hook_taint(source: str, text: str) -> object:
                # The ledger is built just below, outside the hooks; read at call time so what a
                # shell hook says still taints this run, as it does inside `govern_step`.
                return ledger.record_fetch(source, text) if ledger is not None else None

            # The owner's hooks: a solve outside a conversation never reached `govern_step`, so a
            # `pre_tool` deny the owner wrote did not stop it.
            registry = owner_hooks(
                registry, settings=settings, audit=solve_audit, approve=approve, taint=hook_taint
            )
        ledger = None
        if taint:
            # Outermost wrapper (issues #2/#5): sees the same calls the kernel does, records the
            # capability ledger, and escalates execution/self-mod on tainted input to review.
            from chimera.governance import TaintLedger, ledger_registry

            # The conversation's own ledger when there is one: it already knows what this session
            # read, and a page fetched three turns ago is no less untrusted because the person
            # typed `/solve` since.
            ledger = (
                inherited.ledger
                if inherited is not None
                else TaintLedger(
                    authority=settings.taint_authority,
                    egress_allow=settings.egress_allow.split(","),
                    exfil_host_path=settings.exfil_host_path,
                    shell_fetch_guard=settings.shell_fetch_guard,
                    rope_lite=settings.taint_rope_lite,
                )
            )
            # The user's own words, so a fetch of a page or a file the task names is recorded as
            # the user's request — the signal `CHIMERA_TAINT_AUTHORITY=authority` reads, and every
            # mode records.
            ledger.set_instruction(task, workspace=ws)
            # narrow_on_taint: once the run consumes untrusted content, dangerous tools
            # (shell/write/exec/email) require approval for the rest of the run (M9b).
            from chimera.governance.approval import nobody_is_at_a_terminal

            registry = ledger_registry(
                registry, ledger, audit=allow_audit, narrow_on_taint=True, approve=approve,
                rope_lite=settings.taint_rope_lite,
                # A send to an address nobody mentioned asks only a person at this terminal (study
                # 24, M2); a solve under cron or a pipe sends and records, rather than waiting on a
                # durable question the owner never asked for.
                ask_unseen_recipients=(
                    bool(inherited.attended)
                    if inherited is not None
                    else (settings.approval_mode or "ask").strip().lower() == "ask"
                    and not nobody_is_at_a_terminal()
                ),
            )
        # insist_on_action: solve is autonomous task completion, so a described-but-unexecuted plan
        # is pushed back to actually run — the fix for the worker narrating instead of acting.
        _worker_cfg = AgentConfig(
            # The EDITOR's model. Never fused: synthesising three patches produces one that applies
            # cleanly and means nothing.
            model=roles.models.edit or model,
            max_steps=max_steps,
            # Compaction, off unless asked — the library default stays `None` (see AgentConfig:
            # "off by default because compaction discards messages"). What changes is that a CLI
            # caller can now ask at all. Every terminal surface ran without it and had no way to
            # request it, while an overflow is TERMINAL: `failover.py` maps CONTEXT_OVERFLOW to
            # ABORT, so a long run died on the provider's error rather than shrinking its prompt.
            context_budget=context_budget,
            insist_on_action=True,
            # The workspace's own AGENTS.md, so `chimera solve` follows the conventions of the
            # repository it is solving in — the same instructions every other agent tool reads.
            project_root=ws,
            instructions=owner_identity(get_settings().home),
            turn_context=True,
            # One JSONL line per worker run: per-step tokens, cache hit rate, the tools called, and
            # the drift assessment. This is the only place the step log is persisted, so without it
            # every measurement the loop takes dies with the process.
            trace_path=settings.home / "traces.jsonl",
            # The dollar ceiling for the RUN, not the turn. `AutonomousAgent._run_budget` reads this
            # off the worker's config to build one `SpendBudget` spanning every attempt — without it
            # that method returns None and no ceiling is enforced for the run. All of it existed and
            # none of it could be asked for: `solve` had twenty-nine flags and not one about money.
            # (The `stopped_reason="spend"` path is no longer terminal-unreachable: `chat` and
            # `assist` carry a per-conversation budget, and the agent loop names the dollar ceiling
            # `spend` rather than borrowing the token ceiling's label.)
            max_usd=max_usd,
            # --tool-router: the System One experiment. Built here rather than inside the loop so
            # the router's spend goes through the SAME gateway (and the same spend ceiling) as the
            # step it precedes — pricing one arm's calls and not the other's would make the cost
            # comparison the experiment is about meaningless.
            tool_router=(
                _ToolRouter(backend, tool_router, mode=tool_router_mode) if tool_router else None
            ),
            escalate_on_tool_loop=escalate_on_tool_loop or None,
            snapshot_on_tool_loop=Path(snapshot_at_tool_loop) if snapshot_at_tool_loop else None,
        )
        worker = Agent(backend, registry, _worker_cfg)
        escalate_worker = (
            Agent(escalate_backend, registry, _worker_cfg)
            if escalate_backend is not None
            else None
        )
        from chimera.api.roles import review_model_for
        from chimera.evolution import StagnationDetector

        # The six learning seams (experience, trajectories, memory, auto_evolver, cards, playbook)
        # assembled once by the shared factory (M19-A0), so the flywheel is a property of the agent
        # stack, not this one command. `memory`/`playbook` are injected — their construction pulls
        # CLI-local helpers. Behaviour-preserving: same seams, same conditions as the old inline block.
        evo = build_evolution_context(
            settings,
            gateway,
            model,
            home=settings.home,
            collect=collect,
            evolve_skills=not no_evolve_skills,
            panel_evolution=panel_evolution,
            audit=allow_audit,
            memory=None if no_remember else _memory_manager(),
            playbook=stored_playbook,
            # --skill-cards/--no-skill-cards: per-run override of settings.skill_cards, so the
            # learn->use loop can be turned on for an experiment (learning-lift) without flipping
            # the global default. None keeps the settings default (today: off).
            skill_cards=skill_cards,
        )

        auto = AutonomousAgent(
            worker,
            escalate_worker=escalate_worker,
            # Pivot the retry when attempts keep failing the same way (short budget → window 2).
            # --stagnation-fuzzy loosens the match: byte-identical signatures are strict enough to
            # miss two attempts failing from the same cause with different assertion text, so the
            # pivot never fires and the loop refines a dead end (bench/retry_lift, I2).
            stagnation=StagnationDetector(window=2, signature_similarity=0.8 if stagnation_fuzzy else 1.0),
            # Structured per-attempt self-check (Magentic-One): turns "it failed" into a concrete
            # next_focus for the retry — the lift for weak models. Opt-in via --progress-ledger.
            progress_ledger=ProgressLedger(gateway, model) if progress_ledger else None,
            # Requirement checklist (--checklist): extract atomic requirements + grade coverage
            # per attempt, so a weak model can't silently drop a "must include / must not" clause.
            # --gen-tests also needs the extracted requirements, so it turns extraction on too.
            checklist=RequirementChecklist(gateway, model) if (checklist or gen_tests) else None,
            # Spec-grounded test generation (--gen-tests): with no --verify command, generate
            # executable pytest grounded in the extracted requirements and use it as the gate,
            # replacing the weak LLM coverage grade that rubber-stamps wrong code.
            spec_test_generator=SpecTestGenerator(gateway, model) if gen_tests else None,
            workspace=ws,
            # Independent strong verification (--strong-verify MODEL): a stronger judge grades
            # hard-turn (retried) results before they're accepted. Uses the same gateway, other model.
            strong_verifier=StrongVerifier(gateway, strong_verify) if strong_verify else None,
            # Dual-ledger re-plan (--replan): on a stall, rebuild the plan from accumulated
            # failure causes rather than just nudging. Needs the planner (so not with --no-plan).
            replan_on_stall=replan,
            # HITL (--pause-on-taint): pause for approval before finalizing a tainted run.
            pause_on_taint=pause_on_taint,
            # Repo-map (--repo-map): front-load a structural map of the workspace into context.
            repo_map=repo_map,
            # Declared, machine-checkable success clauses (--contract): an AND gate on top of
            # verify-or-revert that catches the model claiming a result the artifacts don't show.
            contract=(
                CompletionContract.from_specs([c.strip() for c in contract.split(",")], ws)
                if contract
                else None
            ),
            # Provenance gate: artifacts born from a tainted run are marked/held pending.
            taint=ledger,
            planner=None if no_plan else Planner(
                _fused_if(planner_backend, roles.models.fuse_plan, meter, settings), roles.models.plan or model
            ),
            # review_model_for refuses to let the reviewer be the model that wrote the patch —
            # generate-and-verify collapses when it grades its own work and agrees with itself.
            manager=None if no_manager else Manager(
                _fused_if(meter, roles.models.fuse_review, meter, settings),
                review_model_for(roles) or model,
                use_rubric=rubric,
            ),
            # `--verify` was typed in the same breath as the run: authorised by construction.
            verifier=CommandVerifier(verify, ws, source="user") if verify else None,
            # PROBE (M18-5): record (arm, cheap manager proxy, verified reward) per attempt.
            probe_log=_ProbeLog(settings.home / "probe.jsonl") if probe_log else None,
            guard=WorkspaceGuard(ws),
            # Retry conditioning (--diff-feedback): feed the failed attempt's own reverted diff back
            # so the retry is told what it wrote, not just that it failed (bench/retry_lift, I1).
            diff_feedback=diff_feedback,
            # --keep-workspace: leave the agent's final edits on disk when an external grader (e.g.
            # SWE-bench's own tests) decides pass/fail, instead of Chimera's verify-or-revert.
            keep_workspace=keep_workspace,
            # --require-diff: promote the diff-gate from observer to gate, so an attempt that edited
            # nothing fails and is retried instead of being approved on the strength of its prose.
            require_diff=require_diff,
            # --recovery: brief the retry on the CLASS of the failure (failing assertion, undone
            # diff, forced tool call) instead of the generic prose + verifier output. The class is
            # recorded on the receipt either way (arXiv 2606.01416; bench/PLAN-study16, item 1+7).
            recovery=recovery,
            # The six learning seams (experience, trajectories, memory, auto_evolver, cards, playbook)
            # from the shared factory above (M19-A0).
            **evo.apply_to(),
            spine_workspace=ws,
            on_event=(
                (lambda event: _json_line(
                    {"kind": event.kind, "text": event.text, "data": event.data}, machine_out
                ))
                if jsonl
                else (_stream_sink if stream else None)
            ),
            # Durable execution (--thread): checkpoint the loop to SQLite so a crash can resume.
            checkpointer=RunCheckpointer(settings.home / "runs.db") if thread else None,
            # Run receipt: persist how this run proved its work (verify-or-revert per attempt) to an
            # append-only log the desktop "Runs" screen reads read-only. Best-effort — never fails a run.
            run_log=settings.home / "runs.jsonl",
            run_profile=profile,
            meter=meter,
            config=AutonomousConfig(
                max_attempts=max_attempts,
                use_planner=not no_plan,
                use_manager=not no_manager,
                normalize_task=normalize_task,
            ),
        )
        outcome = auto.run(task, thread_id=thread)
        # Read from the agent, not the ledger: without `--taint` there is no ledger, and a tainted
        # recall must still store the curated bullets tainted (S30-25).
        run_tainted.append(auto.run_tainted())
        if _worker_cfg.tool_router is not None:
            # How much the intervention ACTED, beside what it cost (§2r). Written by the surface
            # that built the router, because that is the object that knows when the run ended.
            _append_json_line(
                settings.home / "tool_router.jsonl",
                {"task": task[:200], "model": tool_router, **_worker_cfg.tool_router.stats.as_dict()},
            )
        if ledger is not None:
            ledger.dump(settings.home / "ledger.jsonl")
            summary = ledger.capability_summary()
            console.print(
                f"[dim]capability ledger: {summary['events']} events, "
                f"{len(summary['tainted_writes'])} tainted write(s), "
                f"{len(summary['escalations'])} taint escalation(s) "
                f"-> {settings.home / 'ledger.jsonl'}[/dim]"
            )
        return outcome

    try:
        if isolate:
            from chimera.core.worktree import run_in_worktree

            result = run_in_worktree(workspace_path, _run_solve, succeeded=lambda r: r.success)
        else:
            result = _run_solve(workspace_path)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    from chimera.api.runs import cost_per_accepted_change

    machine: tuple[dict[str, Any], int] | None = None
    if json_output or jsonl:
        ending = str(getattr(result, "ending", "unknown") or "unknown")
        # The loop's own word for a finished run is "success"; the headless vocabulary's is "final".
        reason = "final" if ending == "success" else ending
        code = _exit_for(reason)
        if code == 0 and not result.success:
            reason, code = "unknown", _exit_for("unknown")  # never 0 for a run that did not finish
        tokens = sum(
            int(getattr(item, "prompt_tokens", 0) or 0) + int(getattr(item, "completion_tokens", 0) or 0)
            for item in result.attempts
        )
        model_name = next(
            (str(item.model) for item in reversed(result.attempts) if getattr(item, "model", "")),
            model or "",
        )
        machine = (
            _headless_payload(
                str(result.answer), reason, model=model_name,
                usd=cost_per_accepted_change(result.attempts).usd, tokens=tokens,
                steps=len(result.attempts),
            ),
            code,
        )

    console.print(escape(str(result.answer)))
    status = "[green]success[/green]" if result.success else "[red]failed[/red]"
    if getattr(result, "ending", "") == "handover":
        # Not a failure, and not done either: the page needs the person (study 25, S11). The exit
        # code below stays 1 all the same, because a script reading 0 as "the task is done" must
        # not read it here.
        status = "[yellow]handed over to you[/yellow]"
    # `ending` says WHICH of the endings this was; `success` alone cannot. A run that used up its
    # attempts, one the person cancelled, one cut off at the dollar ceiling and one that succeeded
    # while changing nothing on disk all printed the same two words before this.
    console.print(
        f"[dim]{status} after {len(result.attempts)} attempt(s) — "
        f"ended: {getattr(result, 'ending', 'unknown')}[/dim]"
    )
    # Money over delivered work: the ratio that says whether the retries paid for themselves. Every
    # term of it — per-attempt cost, verified, reverted, diff_productive — has been on the receipt
    # for releases, and no surface ever divided one by the other. The two empty cases print apart on
    # purpose: "bought nothing" and "cannot price it" are opposite verdicts, not one blank.
    _cost = cost_per_accepted_change(result.attempts)
    if _cost.per_change is not None and _cost.usd is not None:
        console.print(
            f"[dim]${_cost.usd:.4f} over {_cost.accepted} accepted change(s) — "
            f"${_cost.per_change:.4f} each[/dim]"
        )
    elif _cost.usd is None:
        console.print(
            f"[dim]cost unknown (a leg had no price) · "
            f"{_cost.accepted} accepted change(s)[/dim]"
        )
    else:
        console.print(f"[dim]${_cost.usd:.4f} and nothing was accepted[/dim]")

    # The loud half, and the reason the approver exists at all. A refused call comes back as an
    # ordinary observation string, so the agent reads it like any tool result and carries on — the
    # run ends in prose and the receipt says success. That is how a guardian reports green having
    # guarded nothing. Saying it here costs one line and removes the whole failure mode.
    if approvals.blocked:
        console.print(f"[yellow]governance: {approvals.summary()}[/yellow]")
        if result.success:
            console.print(
                "[yellow]…and this run was reported successful anyway — check that the work it "
                "was asked to do actually happened.[/yellow]"
            )

    # Close the ACE loop: reflect on this run's outcome (success or failure) and curate the
    # playbook with incremental deltas, so the next run starts from the improved guidance.
    if stored_playbook is not None:
        from chimera.evolution import BackendDeltaProposer, PlaybookCurator

        # Level-2 P3: seed curation from the run's error evidence (failing verifier output + the
        # fixing diff), not just verdict+final-answer — so the curator distils process pitfalls.
        outcome_text = _curation_outcome(result, from_errors=settings.playbook_curate_from_errors)
        applied = PlaybookCurator(BackendDeltaProposer(gateway, model)).curate(
            stored_playbook, task, outcome_text,
            # A run that consumed untrusted content (a fetch, or since S30-25 a tainted recall)
            # adds bullets that every later run reads: they are stored tainted and labelled.
            tainted=any(run_tainted),
        )
        _save_playbook(stored_playbook)
        console.print(
            f"[dim]playbook curated: {applied} delta(s) -> {len(stored_playbook.active())} active bullets[/dim]"
        )

    if machine is not None:
        # Last, so the playbook curation and the governance warning above still happen (on stderr):
        # --json changes where the words go, not what the run does.
        _emit_headless(machine[0], json_output=json_output, jsonl=jsonl, stream=machine_out)
        if machine[1]:
            raise typer.Exit(code=machine[1])
        return

    if not result.success:
        # Still exit 1, still a `typer.Exit`: the shell sees exactly what it saw before. The
        # subclass carries the run so a caller inside this process — `/solve` in a REPL — can put
        # the loop's own answer into the conversation instead of writing one for it.
        raise SolveFailed(result)
    return result


#: The shipped ``solve``, held by reference so :func:`_solve_defaults` reads ITS parameters.
#:
#: By reference and not by name. Something that stands in for ``solve`` — a test recorder, a future
#: wrapper — must not also get to redefine the list of parameters it is called with; that would
#: make the anti-``OptionInfo`` guard agree with whatever is in front of it.
_SOLVE_COMMAND: Any = solve


def _report_collusion(ledgers: dict[str, Any]) -> bool:
    """Run the aggregate cross-agent monitor over per-worker events; print findings. Returns True if
    any collusion was flagged.

    A per-worker monitor is blind to a split flow under fan-out (one worker fetches untrusted, a
    different worker sinks it — the fetch and the sink live in separate ledgers). The shared taint view
    now arms narrowing *live* for that flow; this monitor is the aggregate backstop (and the only place
    the fan-out-volume pattern is visible). No-op without ledgers.
    """
    if not ledgers:
        return False
    from chimera.governance import AggregateMonitor

    findings = AggregateMonitor().assess({name: led.events for name, led in ledgers.items()})
    if not findings:
        console.print("[dim]cross-agent monitor: no collusion signals across workers[/dim]")
        return False
    console.print("[yellow]⚠ cross-agent monitor flagged (review):[/yellow]")
    for finding in findings:
        agents = f" — agents: {', '.join(finding.agents)}" if finding.agents else ""
        console.print(f"  [yellow]- {finding.kind}[/yellow]: {finding.detail}{agents}")
    return True


def _report_batch_outcomes(
    units: list[Any], results: list[Any], worker_approvals: dict[str, Any]
) -> None:
    """One line per task, and the loud half beneath it.

    The loud half is the reason the approver exists at all. A refused call comes back as an ordinary
    observation string, so the worker reads it like any tool result and carries on: the task ends in
    prose and its result can still be ``ok``. Single-task `solve` has said this since its approver
    was wired; the batch printed a bare ``ok`` for a task that was not allowed to do its work, which
    is the one line this command must not print.

    Read PER TASK, never aggregated. These tasks are independent, so "task3 was not allowed" is the
    sentence that is true; a run-wide flag would drag a clean task down with its neighbour.
    """
    refused = [name for name, _ in units if getattr(worker_approvals.get(name), "blocked", False)]
    for (name, _), result in zip(units, results, strict=True):
        status = "[green]ok[/green]" if result.ok else f"[red]failed[/red] ({result.error or 'unsolved'})"
        if name in refused:
            status = f"[yellow]not allowed[/yellow] ({status})"
        console.print(f"[bold]{name}[/bold]: {status}")
        book = worker_approvals.get(name)
        if book is not None and book.blocked:
            console.print(f"  [yellow]governance: {book.summary()}[/yellow]")
    if refused:
        console.print(
            f"[yellow]{len(refused)} of {len(units)} task(s) had actions refused for review — "
            "check that the work they were asked to do actually happened.[/yellow]"
        )


@app.command(name="solve-batch")
def solve_batch(
    tasks: list[str] = _BATCH_TASKS_ARG,
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root (a git repo, to isolate)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per task."),
    context_budget: float = typer.Option(
        None,
        "--context-budget",
        help="Fraction of the model's window to spend on the prompt before compacting (e.g. 0.6).",
    ),
    max_attempts: int = typer.Option(2, "--max-attempts", help="Max verify-or-revert attempts per task."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max concurrent isolated workers."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    taint: bool = typer.Option(False, "--taint", help="Arm each worker's adaptive allowlist (dangerous-when-tainted tools require approval). The cross-agent collusion monitor runs regardless — it's always on for fan-out."),
) -> None:
    """Solve several tasks concurrently, each in its own git worktree (Tier-3 isolation).

    Every task runs against an isolated checkout, so parallel edits never collide. On
    merge-back, a file two tasks both changed is reported as a conflict and left for you
    to resolve rather than silently overwritten. Needs a git repo to isolate.

    A worker whose actions were refused for review is reported as **not allowed** rather than
    ``ok``, and the refusals are listed under it. Whether anyone can be asked follows
    ``CHIMERA_APPROVAL_MODE``: ``allow`` and ``deny`` answer immediately, ``ask`` prompts if this
    process has a terminal and otherwise writes the question down for ``chimera approve`` and waits
    ``CHIMERA_APPROVAL_WAIT`` seconds for it — per refused call, per worker. Set
    ``CHIMERA_APPROVAL_WEBHOOK`` so the question reaches somebody, or ``CHIMERA_APPROVAL_MODE=deny``
    for a batch that should never wait.
    """
    from chimera.core import (
        Agent,
        AgentConfig,
        AutonomousAgent,
        AutonomousConfig,
        Manager,
        Planner,
        WorkspaceGuard,
    )
    from chimera.governance import ApprovalLedger, TaintLedger, approver_for, ledger_registry
    from chimera.governance.approval import deliverer_for
    from chimera.governance.audit import AuditLog
    from chimera.orchestration import run_isolated
    from chimera.providers import LLMGateway, MissingCredentialsError

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse or settings.auto_fuse:
        from chimera.fusion import RoutedBackend
        from chimera.fusion.factory import fusion_engine

        backend = RoutedBackend(gateway, fusion_engine(gateway))

    # Per-worker capability ledgers, so the aggregate cross-agent monitor can see the whole fan-out
    # (a split exfiltration — one worker fetches untrusted, another sinks it — lives BETWEEN workers,
    # invisible to any single-worker monitor). ALWAYS ON for fan-out: recording is pure observability
    # and changes no behaviour; the monitor only escalates a review note at the end. --taint additionally
    # arms each worker's adaptive allowlist (dangerous-when-tainted tools require approval).
    ledgers: dict[str, TaintLedger] = {}
    # NO shared taint view here: solve-batch runs INDEPENDENT tasks in SEPARATE workspaces. Worker B
    # cannot see worker A's fetched content, so arming B's narrowing because A fetched (even benign)
    # content would block B's legitimate work for zero security benefit. Each worker's ledger is its
    # own; the AggregateMonitor still runs post-hoc for observability + the non-zero exit below. (The
    # live cross-worker gate is for crew-isolated, where workers collaborate on ONE task + workspace.)
    #
    # One approval ledger PER WORKER, and per-worker for the same reason the taint view is: these
    # tasks are independent, so "task3 was not allowed to do its work" is the sentence that is true,
    # and a shared ledger could only say "somebody wasn't". `crew-isolated` shares one because its
    # workers share a task.
    worker_approvals: dict[str, ApprovalLedger] = {}
    from chimera.governance.profile import owner_hooks

    # One audit for every worker's `hook` receipts: the file the Security screen reads.
    hooks_audit = AuditLog(settings.home / "audit.jsonl")

    def make_runner(name: str, one_task: str) -> Callable[[Path], AutonomousResult]:
        def run(ws: Path) -> AutonomousResult:
            from chimera.tools import default_registry

            ledger = TaintLedger(
                authority=settings.taint_authority,
                egress_allow=settings.egress_allow.split(","),
                exfil_host_path=settings.exfil_host_path,
                shell_fetch_guard=settings.shell_fetch_guard,
                rope_lite=settings.taint_rope_lite,
            )
            ledger.set_instruction(one_task, workspace=ws)
            ledgers[name] = ledger
            # An approver, because the comment above promises one: `--taint` "arms each worker's
            # adaptive allowlist (dangerous-when-tainted tools require approval)". It did not — this
            # was the last `ledger_registry` call site in the package passing none, and
            # `LedgeredTool` reads a missing approver as *refuse*. So "requires approval" was
            # "always refused", and an owner who set `CHIMERA_APPROVAL_MODE=allow` had that setting
            # ignored on this surface alone.
            #
            # Measured offline on the injection corpus, one instrument: with no approver a tainted
            # worker has **5 of 8** legitimate rows refused; with one and somebody answering, 0 of 8.
            # Attacks stay 7 of 7 blocked either way — the approver buys back the false refusals, not
            # the defence. `crew_isolated` already had this (`approver_for(..., home=…)` below); this
            # is the same line, per worker rather than shared, because these tasks are independent.
            approvals = ApprovalLedger()
            worker_approvals[name] = approvals
            worker_approve = approver_for(
                settings.approval_mode,
                approvals,
                home=settings.home,
                audit=AuditLog(settings.home / "audit.jsonl"),
                # Where the question is SENT. `home` alone makes it durable — written to disk,
                # answerable by `chimera approve` — but a durable question nobody is told about
                # is a 900 s wait ending in the same refusal, N workers deep. `deliverer_for`
                # returns None when this deployment has configured no webhook, in which case
                # that is exactly what happens; see the note in the command's docstring.
                deliver=deliverer_for(settings),
                # And how long it waits for the answer. The durable default is fifteen minutes
                # PER QUESTION, which is a reasonable pause for one `solve` and an afternoon for
                # four workers asking a dozen times each. `CHIMERA_APPROVAL_WAIT` is the number
                # this deployment already chose for the same question on the API path.
                wait_seconds=settings.approval_wait,
            )
            # The owner's hooks, inside the ledger like every other assembly that has one, asking
            # this worker's approver. This command builds its protection without `govern_step`, so
            # an owner's `pre_tool` deny on `git push` did not reach a batch worker at all — the
            # push ran, with no `hook` receipt, while the threat model listed no exception for it.
            registry = ledger_registry(
                owner_hooks(
                    default_registry(ws), settings=settings, audit=hooks_audit,
                    approve=worker_approve, taint=ledger.record_fetch,
                ),
                ledger,
                narrow_on_taint=taint,
                approve=worker_approve,
            )
            worker = Agent(
                backend,
                registry,
                # `ws` is this task's isolated worktree — a checkout of the repo, so its AGENTS.md is
                # the same one. Single-task `solve` has always passed it; the batch did not, which is
                # the same shape as a batch being quietly weaker than one run done alone.
                AgentConfig(
                    model=model,
                    max_steps=max_steps,
                    context_budget=context_budget,
                    project_root=ws,
                    instructions=owner_identity(get_settings().home),
                    turn_context=True,
                ),
            )
            auto = AutonomousAgent(
                worker,
                planner=Planner(gateway, model),
                manager=Manager(gateway, model),
                guard=WorkspaceGuard(ws),
                spine_workspace=ws,
                config=AutonomousConfig(max_attempts=max_attempts),
            )
            return auto.run(one_task)

        return run

    units = [(f"task{i + 1}", make_runner(f"task{i + 1}", task)) for i, task in enumerate(tasks)]
    try:
        batch = run_isolated(
            Path(workspace), units, succeeded=lambda r: r.success, max_workers=max_workers
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    _report_batch_outcomes(units, batch.results, worker_approvals)
    console.print(
        f"[dim]merged {batch.merged} file(s) across {len(units)} task(s)[/dim]"
    )
    if batch.conflicts:
        console.print(f"[yellow]conflicts (not merged):[/yellow] {', '.join(batch.conflicts)}")
    colluded = _report_collusion(ledgers)
    # Under --taint the aggregate finding is a real consequence, not just a note: the run exits
    # non-zero so a caller/CI treats the fan-out as needing review before its merged output is trusted.
    if colluded and taint:
        console.print("[red]cross-agent collusion under --taint — exiting non-zero for review.[/red]")
        raise typer.Exit(code=2)
    if not batch.ok:
        raise typer.Exit(code=1)


@app.command(name="crew-isolated")
def crew_isolated(
    task: str = typer.Argument(..., help="The task every worker attempts (or divides, with --merge-all)."),
    worker: list[str] = _CREW_WORKER_OPT,
    workspace: str = typer.Option(".", "--workspace", "-w", help="Repository root (a git repo, to isolate)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    verify: str = typer.Option(None, "--verify", help="Per-worker gate: shell command run in each worktree (exit 0 to merge)."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per worker."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max concurrent isolated workers."),
    synthesize: bool = typer.Option(False, "--synthesize", help="A supervisor folds the merged results into one unified report."),
    merge_all: bool = typer.Option(
        False, "--merge-all",
        help="Workers do DISJOINT parts: land every approved worker's files instead of one "
        "worker's whole tree (files two of them changed land from neither).",
    ),
    fuse: bool = typer.Option(False, "--fuse", help="Route worker turns through fusion."),
    taint: bool = typer.Option(False, "--taint", help="Arm each worker's adaptive allowlist (dangerous-when-tainted tools require approval). The cross-agent collusion monitor runs regardless — it's always on for fan-out."),
) -> None:
    """Tier-3: tool-using workers attempt ONE task, each in its own git worktree, verify-gated.

    Define workers with repeated --worker 'name:instruction'. Each runs a real agent loop
    (search/read/edit) against an isolated checkout; a worker whose check fails is rejected (its
    edits discarded). Of the workers that pass, ONE lands whole — a check that ran beats one that
    could not, then the smallest diff, then the order given — and --verify runs again on the
    merged workspace. With --merge-all, every approved worker's files land instead, and files two
    of them changed are flagged as conflicts. Needs a git repo to isolate.
    """
    from chimera.governance import SharedTaint, TaintLedger, ledger_registry
    from chimera.orchestration import IsolatedCrew, IsolatedWorker, Role, RoleAgent
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.tools import default_registry

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    if not worker:
        console.print("[red]give at least one --worker 'name:instruction'[/red]")
        raise typer.Exit(code=1)

    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse or settings.auto_fuse:
        from chimera.fusion import RoutedBackend
        from chimera.fusion.factory import fusion_engine

        backend = RoutedBackend(gateway, fusion_engine(gateway))

    # Per-worker capability ledgers for the aggregate cross-agent monitor — always on for fan-out
    # (pure observability; --taint additionally arms each worker's adaptive allowlist). ONE shared
    # taint view here (unlike solve-batch): crew workers COLLABORATE on a single task and merge into a
    # shared workspace, so untrusted content one worker fetched can flow to another — a fetch in any
    # worker arms the narrowing in all of them live, the cross-agent gate. (Batch tasks are
    # independent, so sharing there would only false-block; that's why it's crew-only.)
    ledgers: dict[str, Any] = {}
    shared_taint = SharedTaint()
    # The approval half of the same argument the shared taint makes two comments up. These workers
    # collaborate on ONE task, so a decision about that task is one decision — and they run in
    # parallel, so without sharing they asked N times at the same moment onto one terminal, where
    # two prompts interleave into a question nobody can answer correctly.
    #
    # Neither the CLI nor the API ever passed an approver here at all, which meant a REVIEW verdict
    # inside a crew worker was refused by nobody having been asked. `approve=` below is the first
    # time this path has one.
    from chimera.governance import approver_for
    from chimera.governance.audit import AuditLog
    from chimera.governance.shared_approval import SharedApprovals

    aprovacoes = SharedApprovals(
        approver_for(settings.approval_mode, home=settings.home,
                     audit=AuditLog(settings.home / "audit.jsonl"))
    )
    from chimera.governance.profile import owner_hooks

    hooks_audit = AuditLog(settings.home / "audit.jsonl")

    def make_factory(wname: str, prompt: str) -> Callable[[Path], Any]:
        def factory(ws: Path) -> Any:
            ledger = TaintLedger(
                shared=shared_taint,
                authority=settings.taint_authority,
                egress_allow=settings.egress_allow.split(","),
                exfil_host_path=settings.exfil_host_path,
                shell_fetch_guard=settings.shell_fetch_guard,
                rope_lite=settings.taint_rope_lite,
            )
            # Both halves are the person's own words: the shared task and this worker's brief.
            ledger.set_instruction(f"{task}\n{prompt}", workspace=ws)
            ledgers[wname] = ledger
            shared_approve = aprovacoes.approver()
            # The owner's hooks, inside the ledger and asking the crew's shared approver — the same
            # gap `solve-batch` had: built without `govern_step`, so the owner's hooks never ran here.
            return ledger_registry(
                owner_hooks(
                    default_registry(ws), settings=settings, audit=hooks_audit,
                    approve=shared_approve, taint=ledger.record_fetch,
                ),
                ledger,
                approve=shared_approve, narrow_on_taint=taint,
            )

        return factory

    workers = []
    for i, spec in enumerate(worker):
        name, sep, instruction = spec.partition(":")
        name = (name.strip() if sep else "") or f"worker{i + 1}"
        prompt = (instruction.strip() if sep else spec.strip()) or "Do your part of the task."
        workers.append(
            IsolatedWorker(Role(name, prompt), make_factory(name, prompt), max_steps=max_steps)
        )

    supervisor = None
    if synthesize:
        supervisor = RoleAgent(
            Role("supervisor", "You coordinate a team and write a single, unified final report "
                 "from the merged worker outputs. Be concise and note any conflicts or rejects."),
            backend,
        )
    crew = IsolatedCrew(backend, workers, supervisor=supervisor, max_workers=max_workers)
    try:
        result = crew.run(task, Path(workspace), verify=verify, merge="union" if merge_all else "select")
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    for msg in result.transcript:
        if merge_all:
            console.print(f"[green]✓ {msg.sender}[/green] merged")
        elif msg.sender == result.selected:
            console.print(f"[green]✓ {msg.sender}[/green] selected and merged")
        else:
            # Passed too. Said, because a second passing solution is information — and its diff is
            # gone with its worktree unless it is printed here.
            console.print(f"[cyan]✓ {msg.sender}[/cyan] passed, not selected")
            if result.diffs.get(msg.sender):
                console.print(result.diffs[msg.sender], markup=False, highlight=False)
    for name in result.rejected:
        console.print(f"[yellow]✗ {name}[/yellow] rejected (failed --verify)")
    for name, err in result.failures.items():
        console.print(f"[red]✗ {name}[/red] crashed: {err}")
    if result.conflicts:
        console.print(f"[yellow]conflicts (not merged):[/yellow] {', '.join(result.conflicts)}")
    landed_from = len(result.transcript) if merge_all else (1 if result.selected else 0)
    console.print(f"[dim]merged {result.merged} file(s) from {landed_from} worker(s)[/dim]")
    if result.reverify == "failed":
        console.print("[red]the merged workspace FAILS --verify:[/red]")
        console.print(result.reverify_output[-2000:], markup=False, highlight=False)
    elif result.reverify:
        console.print(f"[dim]--verify on the merged workspace: {result.reverify}[/dim]")
    if result.summary:
        console.print(Panel(escape(str(result.summary)), title="unified report", border_style="cyan"))
    colluded = _report_collusion(ledgers)
    if colluded and taint:
        console.print("[red]cross-agent collusion under --taint — exiting non-zero for review.[/red]")
        raise typer.Exit(code=2)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def explore(
    query: str = typer.Argument(..., help="What to locate in the repository."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Repository root to explore."),
    model: str = typer.Option(None, "--model", "-m", help="Model for the explorer (a cheap one is fine)."),
    max_turns: int = typer.Option(8, "--max-turns", help="Max exploration turns."),
    thoroughness: str = typer.Option(
        "medium", "--thoroughness",
        help="quick, medium or thorough: halves, keeps or doubles --max-turns. "
        "Read only when CHIMERA_EXPLORER_CONTRACT is on.",
    ),
) -> None:
    """Locate relevant code via the isolated Context Explorer subagent (FastContext-style).

    Returns only a compact file:line evidence block — the exploration turns never touch your
    context. A cheap model is usually the right call here; localization is a narrow task.
    With CHIMERA_EXPLORER_CONTRACT on, it returns findings with a location each, a gaps section,
    and a check of every cited location against the workspace.
    """
    from chimera.core import ContextExplorer
    from chimera.providers import LLMGateway, MissingCredentialsError

    if not get_settings().can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    from uuid import uuid4

    from chimera.api.usage import record_spend
    from chimera.core.agent import partial_spend

    explorer = ContextExplorer(LLMGateway(), Path(workspace), model=model, max_turns=max_turns)
    # One row in the usage log per exploration, under its own id: it is a run of its own, and the
    # Cost screen could not see it. Written on the way out whatever happened, like a failed turn's.
    usage_id = f"explore:{uuid4().hex[:12]}"
    try:
        result = explorer.explore(query, thoroughness.strip().lower())
    except Exception as exc:
        spent = partial_spend(exc)
        if spent is not None and (spent.prompt_tokens or spent.completion_tokens):
            record_spend(
                get_settings().home, session_id=usage_id, model=spent.model,
                prompt_tokens=spent.prompt_tokens, completion_tokens=spent.completion_tokens,
                usd=spent.usd,
            )
        if isinstance(exc, MissingCredentialsError):
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        raise
    record_spend(
        get_settings().home, session_id=usage_id, model=result.model,
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        usd=result.usd, tools=result.tool_calls,
    )
    # "$0.0000" and "cost unknown" are different answers, and a missing line was neither.
    cost = "cost unknown (a call had no price)" if result.usd is None else f"${result.usd:.4f}"
    if result.check is not None:
        # Plain text: the report and the receipt both carry square brackets rich would eat.
        console.print(result.as_context(), markup=False, highlight=False)
        console.print(
            f"[dim]{result.turns} turn(s), {result.tool_calls} tool call(s) · {cost}[/dim]"
        )
        return
    if not result.evidence:
        console.print(f"[dim]no relevant locations found · {cost}[/dim]")
        return
    for ev in result.evidence:
        loc = f"[cyan]{ev.path}[/cyan]" + (f":[yellow]{ev.lines}[/yellow]" if ev.lines else "")
        console.print(f"  {loc}" + (f" [dim]— {ev.note}[/dim]" if ev.note else ""))
    console.print(
        f"[dim]{len(result.evidence)} location(s) in {result.turns} turn(s), "
        f"{result.tool_calls} tool call(s) · {cost}[/dim]"
    )
