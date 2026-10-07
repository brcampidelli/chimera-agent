"""The conversational surfaces: chat, assist and tui, and the turn helpers they share.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.markup import escape
from rich.panel import Panel

from chimera.cli.commands._shared import _cascade_backend, _session_profile, app, console
from chimera.cli.commands.memory import (
    _emit_skill_nudges,
    _learned_skill_labels,
    _maybe_autoconsolidate,
    _memory_extractor,
    _memory_manager,
    _recall_graph,
)
from chimera.cli.commands.solve import _run_solve_command
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.memory import MemoryManager
    from chimera.providers import SupportsComplete



def _resume_or_new(manager: Any, wanted: str | None, force_new: bool) -> tuple[str, bool]:
    """Which conversation this run continues, and whether it is a continuation.

    Default is to resume the newest thread, because the defect being fixed is an assistant that
    forgets: a right hand that starts from nothing every morning is the thing people complain
    about. `--new` and `/new` are how you say otherwise, and the caller prints which thread it
    opened either way — resuming without saying so is the same surprise as forgetting.

    An id that does not exist yet is honoured rather than rejected: `chimera chat -s standup` is a
    reasonable way to name a thread, and refusing it would be pedantry.
    """
    if wanted:
        return wanted, bool(manager.list() and any(m.id == wanted for m in manager.list()))
    if force_new:
        return manager.new(), False
    existing = manager.list()  # newest first
    if existing:
        return existing[0].id, True
    return manager.new(), False


def _sandbox_banner() -> None:
    """Say once, in one line, that commands run on this machine — instead of a WARNING block.

    Claimed BEFORE the tools are built, because ``default_registry`` builds the sandbox and the
    first call is what logs the four-line notice above the banner.
    """
    from chimera.sandbox import claim_unsandboxed_notice

    if claim_unsandboxed_notice():
        console.print(
            "[yellow]⚠ no OS sandbox — the agent's commands run on this machine.[/yellow]"
            "[dim] 'chimera doctor' explains; CHIMERA_SANDBOX=docker isolates them.[/dim]"
        )


def _run_turn(
    session: Any, message: str, documents: list[tuple[str, str]] | None = None
) -> tuple[Any, str]:
    """Run one REPL turn and print whatever went wrong. Returns ``(report | None, outcome)``.

    ``outcome`` is ``"ok"`` (``report`` is a :class:`~chimera.interface.session.TurnReport`),
    ``"continue"`` (a recoverable error, already printed) or ``"stop"`` (end the REPL).

    ``send_verbose`` rather than ``send``: the refusals, the token count and the price are on the
    report and were being thrown away by both REPLs.
    """
    from chimera.core.code_session import _accepts
    from chimera.interface import render
    from chimera.providers import MissingCredentialsError

    def say_notice(code: str, text: str, data: dict[str, Any]) -> None:
        console.print(render.notice_line(code, text))

    try:
        with console.status("[dim]thinking…[/dim]"):
            # Passed only when there are documents: a session written against the older signature
            # (a test double, a published `SupportsRun` surface) is never handed a keyword it lacks.
            # The same goes for `on_notice`.
            extra: dict[str, Any] = (
                {"on_notice": say_notice} if _accepts(session.send_verbose, "on_notice") else {}
            )
            if documents:
                return session.send_verbose(message, documents=documents, **extra), "ok"
            return session.send_verbose(message, **extra), "ok"
    except MissingCredentialsError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        return None, "stop"
    except KeyboardInterrupt:
        # Ctrl-C is not an `Exception`, so it escaped the handler below and Click aborted the
        # process: exit 130, no "bye", nothing persisted, no receipt. The turn was paid for either
        # way; what was lost was every record that it had happened.
        console.print("\n[dim]interrupted — the turn was dropped[/dim]")
        return None, "stop"
    except Exception as exc:  # noqa: BLE001 — keep the REPL alive on transient errors
        console.print(render.error_line(exc))
        return None, "continue"


def _replayed_provenance(session: Any) -> list[str]:
    """The provenance of every RESTORED turn that this prompt replayed.

    ``_replay`` renders ``turns[-max_history:]`` and fences the restored ones that are not known
    clean; this is the same window, read for the same reason one line further out. Read BEFORE the
    turn runs, because ``send_verbose`` appends the new exchange and would shift the window by one.
    """
    from chimera.interface.session import recent_turns

    return [turn.provenance for turn in recent_turns(session.turns, session.max_history) if turn.restored]


def _render_turn(
    report: Any,
    *,
    session_id: str,
    settings: Settings,
    hand: Any = None,
    restored: list[str] | None = None,
) -> None:
    """Print a finished turn — reply, refusals, governance, provenance, price — and file it.

    Order matters and is the fix: the reply is on screen before anything else can fail. The
    refusal lines are what stop "the command printed exactly: marker-42" from being the last word
    about a command that never ran.

    ``hand`` adds the other half of that sentence. A refusal already had a line; a GRANT had
    none — so a person who typed ``y`` to a governance prompt mid-turn had, once the reply
    scrolled, no record that they had allowed anything. An approval nobody can see afterwards is a
    record and not a decision.

    ``restored`` is what :func:`_replayed_provenance` read before the turn ran. The store has
    recorded a turn's provenance since the transcript learned to persist, and the fence around a
    restored turn is built from it on every prompt — and until now nothing showed it, so the
    person could not tell a thread that came off disk from one the model had just said.
    """
    from chimera.api.usage import record_turn
    from chimera.interface import render

    console.print(render.reply_line(report.answer))
    grounded = render.grounded_line(report)
    if grounded:
        # Right under the reply it qualifies: "verified", "the sources don't cover this", or that
        # the verifier could not run and the answer is unchecked.
        console.print(grounded)
    for line in render.refusal_lines(report):
        console.print(line)
    for line in render.todo_lines(report):
        console.print(line)
    cut = render.cut_short_line(report)
    if cut:
        console.print(cut)
    if hand is not None:
        line = render.governance_line(*hand.turn_verdicts(), attended=hand.attended)
        if line:
            console.print(line)
    provenance = render.provenance_line(report, restored=restored or [])
    if provenance:
        console.print(provenance)
    console.print(render.cost_line(report))
    record_turn(settings.home, session_id, report)


def _render_memory_note(report: Any, message: str, settings: Settings) -> None:
    """Say what happened to an explicit "remember that…" — including when the answer is "nothing".

    The model answers "Got it, I'll remember" whatever the setting says, so the surface has to be
    the one telling the truth: confirm the fact when one was written, and name the command that
    writes it when the setting is off.
    """
    if report.memory_saved:
        console.print(f"[dim]remembered:[/dim] [yellow]{escape(report.memory_saved)}[/yellow]")
        return
    if settings.remember_from_chat:
        return
    from chimera.memory.capture import parse_remember_request

    fact = parse_remember_request(message)
    if fact:
        console.print(
            "[yellow]not remembered[/yellow][dim] — chat does not write memory unless "
            "CHIMERA_CHAT_MEMORY=1. Store it now with: "
            f'chimera memory add "{escape(fact)}"[/dim]'
        )


def _emit_memory_nudges(
    session: Any, memory: MemoryManager | None, already: set[str], hint: str
) -> None:
    """Suggest storing a preference the conversation keeps implying (once each)."""
    if memory is None:
        return
    recent = [turn.user for turn in session.turns[-4:]]
    for fact in memory.nudges(recent):
        if fact not in already:
            already.add(fact)
            console.print(
                f"[dim]💡 remember this? [/dim][yellow]{escape(fact)}[/yellow]"
                f"[dim] → {escape(hint.format(fact=fact))}[/dim]"
            )


def _persist_turn(manager: Any, session_id: str) -> None:
    """Save the thread after a turn — and survive a save that cannot happen.

    This used to run BEFORE the answer was printed and outside any ``try``, so an unwritable home
    or a full disk threw away a reply that had already been paid for. A conversation you can read
    but not resume beats one that was correctly filed and never shown.
    """
    try:
        manager.persist(session_id)
    except Exception as exc:  # noqa: BLE001 — a thread that cannot be saved is not a dead REPL
        console.print(f"[yellow]not saved:[/yellow] [dim]{escape(str(exc))}[/dim]")


def _chat_commands() -> list[Any]:
    """``chat``'s commands, for BOTH ``/help`` and the unknown-command message.

    One table for the two, so they cannot disagree about what exists. ``/quit`` and ``/q`` are
    aliases of ``/exit`` and are matched before this table is consulted. Built lazily because the
    CLI pays for every module it imports at start, on every command.
    """
    from chimera.interface.render import SlashCommand, session_commands

    return [
        SlashCommand("/help", "", "this list"),
        SlashCommand("/new", "", "start a fresh thread (the current one stays saved)"),
        SlashCommand("/reset", "", "same as /new — the transcript is on disk now"),
        SlashCommand("/model", "<slug>", "switch model (no argument = back to default)"),
        *session_commands(),
        SlashCommand(
            "/solve", "<task>", "hand it to the verified loop: plan, verify, revert on failure"
        ),
        SlashCommand("/attach", "<file>", _ATTACH_HELP),
        SlashCommand("/exit", "", "quit (also /quit, /q)"),
    ]


_ATTACH_HELP = "attach a document to your next message; the answer is checked against it"


def _attach_document(argument: str, settings: Settings, pending: list[tuple[str, str]]) -> None:
    """Read a document for the next message, the way the desktop's paperclip reads one.

    Through the same extraction (`chimera/api/attachments.py`: plain text read directly, anything
    else through the converter, the text sanitized and fenced as data), so the terminal and the app
    hand the model the same bytes for the same file, and the grounded-answer check reads them.
    """
    from chimera.api.attachments import AUDIO_SUFFIXES, IMAGE_SUFFIXES, save

    raw = argument.strip().strip('"').strip("'")
    if not raw:
        console.print("[dim]usage: /attach <file> — the next message is answered from it[/dim]")
        return
    path = Path(raw).expanduser()
    if not path.is_file():
        console.print(f"[red]no such file:[/red] {escape(str(path))}")
        return
    if path.suffix.lower() in IMAGE_SUFFIXES | AUDIO_SUFFIXES:
        console.print("[yellow]only documents can be attached here; images and audio are the app's[/yellow]")
        return
    try:
        found = save(settings.home, path.name, path.read_bytes())
    except (OSError, ValueError) as exc:
        console.print(f"[red]could not attach:[/red] {escape(str(exc))}")
        return
    if not found.text:
        console.print(f"[yellow]{escape(found.note or 'nothing readable in that file')}[/yellow]")
        return
    pending.append((path.name, found.text))
    console.print(
        f"[dim]attached {escape(path.name)} ({len(found.text):,} chars) to your next message.[/dim]"
    )


def _grounded_answers_for(settings: Settings, gateway: Any) -> Any:
    """The grounded-answer check a terminal session builds when a turn carries a document."""

    def build() -> Any:
        from chimera.fusion.verified import build_grounded_answers

        return build_grounded_answers(settings, gateway)

    return build


def _assist_commands() -> list[Any]:
    """``assist``'s commands — a different set, which is why each surface owns its own table."""
    from chimera.interface.render import SlashCommand, session_commands

    return [
        SlashCommand("/help", "", "this list"),
        SlashCommand("/task", "<hard ask>", "full-power fusion route, one shot"),
        SlashCommand(
            "/solve", "<task>", "hand it to the verified loop: plan, verify, revert on failure"
        ),
        SlashCommand("/profile", "<kind>: <fact>", "remember a fact about you"),
        SlashCommand("/model", "<slug>", "switch model (no argument = back to default)"),
        *session_commands(),
        SlashCommand("/new", "", "clear the conversation context (same as /reset)"),
        SlashCommand("/reset", "", "clear the conversation context (nothing is deleted)"),
        SlashCommand("/attach", "<file>", _ATTACH_HELP),
        SlashCommand("/exit", "", "quit (also /quit, /q)"),
    ]


def _session_command(head: str, session: Any, *, home: Path, usage_id: str) -> bool:
    """``/undo``, ``/cost`` and ``/compact``, the same in ``chat`` and ``assist``. True if handled.

    One function for both REPLs, and the lines come from `chimera.interface.render`, which the
    full-screen app prints too: three surfaces, one meaning per command.
    """
    from chimera.interface import render

    if head == "/undo":
        for line in render.undo_lines(session.undo_last()):
            console.print(line)
    elif head == "/cost":
        from chimera.api.usage import session_spend

        console.print(render.session_cost_line(*session_spend(home, usage_id)))
    elif head == "/compact":
        console.print(render.compact_line(session.compact()))
    else:
        return False
    return True


def _handle_unknown_command(head: str, commands: list[Any]) -> bool:
    """Print "unknown command" for an unrecognised ``/word``. True when it was handled.

    An unrecognised slash used to be sent to the model as an ordinary message: ``/help`` cost 31 s
    and a capabilities essay, and ``/foo`` got a considered answer about "/foo". A path or a
    fraction (``/usr/local``, ``/2``) is not command-shaped and still reaches the model.
    """
    from chimera.interface import render

    if not render.is_command_like(head):
        return False
    for line in render.unknown_command_lines(head, commands):
        console.print(line)
    return True


def _print_help(commands: list[Any]) -> None:
    from chimera.interface import render

    for line in render.help_lines(commands):
        console.print(line)


def _switch_model(
    session: Any, agent: Any, slug: str | None, *, routed: Any = None, plain: Any = None
) -> None:
    """``/model`` for both REPLs — and what naming a model means when a router is picking them.

    Under the tier cascade the slug was **dropped in silence**: ``CascadeBackend._route`` calls the
    gateway with ``config.mid`` or ``config.weak`` and never with the ``model`` it was handed
    (`chimera/fusion/cascade.py:144-151, 169, 189`). A live check measured all eight routes landing
    on the ladder's mid model while ``--model deepseek-chat-v3.1`` was on the command line. That is
    a setting that accepts a value and ignores it — the defect ``recovery``'s validation was written
    against, one command over.

    It is HONOURED rather than refused, and honoured by swapping the BACKEND rather than by
    teaching the cascade to obey. The ladder is a way of *choosing* a model, so naming one says the
    choice is already made; and doing it here keeps the blast radius at the two REPLs, where a
    person is reading the line that says what happened. Changing ``CascadeBackend`` would also
    change ``solve --cascade --profile economy``, where the role models and the ladder are two
    deliberate mechanisms and which of them should win is a separate question nobody has asked.

    ``routed`` is the cascade backend when one is active, ``plain`` the single-model gateway
    underneath it. With ``routed`` None (no cascade) this is the old behaviour exactly.
    """
    if not session.set_model(slug):
        console.print("[red]can't switch model[/red]")
        return
    if routed is None:
        console.print(f"[dim]model → {escape(slug or 'default')}[/dim]")
        return
    if slug:
        agent.backend = plain
        console.print(
            f"[dim]model → {escape(slug)} — pinned, so the tier cascade is off "
            "until /model with no argument.[/dim]"
        )
    else:
        agent.backend = routed
        console.print("[dim]model → default — the tier cascade picks it per turn again.[/dim]")


def _pinned_notice(slug: str) -> None:
    """Say, once at start, what ``--model`` did to the routing — because it used to do nothing."""
    console.print(
        f"[dim]model pinned to {escape(slug)} — the tier cascade is off for this run "
        "(it is what chooses a model, so naming one replaces it).[/dim]"
    )


def _check_max_usd(max_usd: float | None) -> None:
    """Refuse a ceiling that is not one, before the first prompt is drawn.

    Zero is the value that fails in the dangerous direction, and it is the same argument
    ``code_api``'s ``gt=0`` makes about its own field: everything downstream reads a dollar cap for
    truthiness, so ``0`` says "spend nothing" and means "spend anything". A negative reaches
    ``SpendBudget`` and raises — from inside the REPL's first turn, after the greeting.
    """
    if max_usd is not None and max_usd <= 0:
        raise typer.BadParameter(
            f"--max-usd must be greater than zero; {max_usd} would read as 'no ceiling'."
        )


def _run_task_command(
    session: Any,
    argument: str,
    *,
    gateway: Any,
    settings: Settings,
    usage_session: str,
    budget: Any = None,
) -> None:
    """``/task`` in ``assist``: one forced fusion — now inside the conversation and on the receipt.

    Two things were wrong with it and both were about what happens AFTER the answer.

    It **answered outside the thread**: nothing was appended to the session, so the reply the person
    had just paid a three-model panel for was not in the next turn's context and the follow-up
    question was answered by a model that had never seen it. The exchange is recorded now, and
    recording it is honest in a way ``/solve``'s would not be — this text is the model's own words.

    And it **bypassed** ``send_verbose``, so it wrote no ``usage.jsonl`` row and printed no price:
    the single most expensive route in the terminal was the one route the Cost screen could not
    see. It goes through ``_render_turn`` now, like every other turn.

    What it still does NOT do is send the conversation to the panel — the ask goes alone. That is
    the command's whole shape ("full-power route, one shot"): feeding six turns of history to a
    panel plus a judge plus a synthesizer multiplies the cost of the thing that exists to be used
    sparingly, and the person who wants context asks normally.
    """
    from chimera.fusion.factory import fusion_engine
    from chimera.interface import render
    from chimera.interface.session import CLEAN, ChatTurn, TurnReport
    from chimera.orchestration.receipts import price_completion

    task_text = argument.strip()
    if not task_text:
        console.print("[dim]usage: /task <the hard ask>[/dim]")
        return
    if budget is not None:
        # `admit(None)`, not `blocked()`: a fused run picks its models and how many calls to make as
        # it goes, so its worst case cannot be priced. Off this IS `blocked()`; under a strict
        # ceiling (`CHIMERA_STRICT_SPEND_CAP`) it refuses with the sentence that says so, which is
        # what the setting promises for every fused run with a ceiling. Asking only `blocked()`
        # started the panel and charged it afterwards, past a ceiling the owner made strict.
        why = budget.admit(None)
        if why is not None:
            console.print(render.budget_spent_line(str(why)))
            return
    try:
        with console.status("[dim]full-power (fusion)…[/dim]"):
            fused = fusion_engine(gateway).complete([{"role": "user", "content": task_text}])
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted — the turn was dropped[/dim]")
        return
    except Exception as exc:  # noqa: BLE001 — keep the REPL alive
        console.print(render.error_line(exc))
        return
    if budget is not None:
        # By STAGES. A fused turn answers as `model="fusion"`, which no price table resolves, so
        # charging it by that name would bill the most expensive call in the product at zero.
        budget.record_result(fused)
    cost = price_completion(fused)
    report = TurnReport(
        answer=fused.content,
        model=fused.model,
        prompt_tokens=fused.prompt_tokens or 0,
        completion_tokens=fused.completion_tokens or 0,
        cache_read_tokens=fused.cache_read_tokens or 0,
        cache_write_tokens=fused.cache_write_tokens or 0,
        # None when any stage could not be priced: `cost_text` then says "unavailable" rather than
        # showing a floor as if it were the bill.
        usd=None if cost.unpriced is not None else cost.usd,
        route_meta=fused.route_meta,
        # No tool ran on this route, so nothing external could have entered it.
        provenance=CLEAN,
        stopped_reason="final",
    )
    _render_turn(report, session_id=usage_session, settings=settings)
    session.turns.append(
        ChatTurn(user=task_text, assistant=str(fused.content), provenance=CLEAN)
    )


@app.command()
def chat(
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    cascade: bool = typer.Option(
        False, "--cascade", help="Tiered routing: weak -> gate -> mid -> gate -> fusion (cheap by default)."
    ),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
    session_id: str | None = typer.Option(
        None, "--session", "-s", help="Resume a specific session id (see 'chimera sessions')."
    ),
    new: bool = typer.Option(False, "--new", help="Start a fresh session instead of resuming."),
    max_usd: float | None = typer.Option(
        None,
        "--max-usd",
        help="Stop once this conversation has spent this much (the whole thread, not one turn).",
    ),
    write_region: str | None = typer.Option(
        None,
        "--write-region",
        help="Comma-separated globs the file-writers may touch (e.g. 'src/**,*.py'). A write "
        "outside is refused — blocks an injected instruction from rewriting an unrelated file.",
    ),
) -> None:
    """Interactive multi-turn chat — your terminal right-hand. Requires a key.

    The whole conversation is saved after every turn, under ``<home>/sessions``, and picked up again
    on the next run. ``chimera sessions`` lists the threads and ``chimera chat -s <id>`` resumes
    one; ``GET /api/sessions`` serves the same files to any HTTP client.

    Coding conversations in the desktop app are a different store — ``<home>/code_sessions``, which
    keeps the model's own message list and its turn receipts rather than prose pairs — so a thread
    does not travel between the two.

    A resumed turn is labelled as restored in the next prompt, and one that ran while untrusted
    content was in the conversation comes back inside the data fence; a turn saved before that was
    recorded is treated the same way, because nothing measured it — and a dim line under each reply
    now says so on screen. Memory recall is scoped to ``--workspace``: that folder's facts, plus
    the ones stored with no project at all.

    ``--max-usd`` bounds the whole thread rather than one turn, and ``/solve`` hands the
    conversation's task to the same verified loop ``chimera solve`` runs, inside that same ceiling.
    """
    from chimera.api.sessions import SessionManager, SessionStore
    from chimera.cli.right_hand import build_right_hand
    from chimera.cli.spend import BudgetedTurns, session_budget
    from chimera.core import Agent, AgentConfig
    from chimera.core.agent import attended
    from chimera.core.instructions import load as load_identity
    from chimera.core.instructions import render as render_identity
    from chimera.core.jobs import finished_note
    from chimera.interface import ChatSession, render
    from chimera.memory.models import project_key
    from chimera.providers import LLMGateway

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    _check_max_usd(max_usd)

    # One of the project's two transcript stores, and the docstring above used to deny it: it
    # promised "the same store the desktop app reads, so a thread started here can be continued
    # there and the other way round". That was false the day it was written — the app deleted its
    # chat screens on 2026-08-07 and this command learned to save on 2026-08-09 — and it was
    # published three times over, because `docs/commands.md` is generated from it. The split itself
    # is right: see `chimera/core/code_session.py`, whose first paragraph is the argument, and
    # `tests/test_the_two_transcript_stores_say_what_they_are.py`, which fails if the sentence
    # comes back while no line of app code fetches /api/sessions.
    store = SessionStore(settings.home / "sessions")
    # Name the directory this command actually writes to. The line below used to say the thread
    # was "open in the app", which is the same false claim the docstrings carried: the desktop
    # reads <home>/code_sessions and never this one. A path a reader can go and look at cannot
    # drift the way a promise about another program can.
    store_label = str(settings.home / "sessions")
    if session_id is not None:
        # BEFORE the first turn. `chimera chat -s ../escape` was accepted here, ran a whole turn,
        # and raised on the save — after the answer had been paid for and while it was being
        # thrown away. The store already knew how to reject the id; nobody asked it in time.
        try:
            store.check_id(session_id)
        except ValueError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(code=1) from exc

    _sandbox_banner()  # before the registry builds the sandbox, which is what logs the long notice
    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    # The cascade, when there is one, is kept by name: `/model <slug>` swaps it out for the plain
    # gateway and `/model` with no argument swaps it back. Under the ladder the slug was dropped in
    # silence — see `_switch_model`.
    routed: SupportsComplete | None = None
    if cascade or settings.cascade:
        routed = backend = _cascade_backend(gateway, settings)
        if model:
            backend = gateway
            _pinned_notice(model)
    elif fuse:
        from chimera.fusion import RoutedBackend
        from chimera.fusion.factory import fusion_engine

        backend = RoutedBackend(gateway, fusion_engine(gateway))
    # The same stack the API path assembles, and until now the thing this surface had none of: a
    # write region, the deployment fence, the owner's reach floor, the trust kernel, a taint ledger
    # and an approver that can actually be answered. Measured before it was argued about — 7 of 7
    # attacks executed here that the governed registry blocked, and 0 of 12 external reads arrived
    # inside the `<<external-data>>` fence the system prompt below promises on every single turn
    # (`bench/right_hand_governance/RESULTS.md`, 2026-09-08).
    hand = build_right_hand(
        Path(workspace), settings=settings, surface="chat", write_region=write_region
    )
    agent = Agent(
        backend,
        hand.registry,
        # Same workspace, both arguments: the one that roots the tools also carries the
        # project's conventions. Splitting them is how `AGENTS.md` came to be read on
        # four surfaces out of twenty-seven.
        attended(AgentConfig(
            model=model,
            max_steps=max_steps,
            project_root=Path(workspace),
            # The owner's own words from `agent.json`. The desktop passes them and no terminal
            # surface did, so one configuration produced two agents that answered differently
            # depending on which window you opened.
            instructions=render_identity(load_identity(settings.home)),
            turn_context=True,
        )),
    )
    mem = None if no_memory else _memory_manager()

    # The conversation outlives the terminal.
    #
    # `chat` used to build a ChatSession in memory and drop it on exit: a right hand that forgets
    # yesterday. The store that fixes it already existed and was already tested — `SessionManager`
    # over `<home>/sessions` — but it was constructed in exactly one place, the desktop API. So the
    # two products shared a data directory and had no conversation in common: nothing started in the
    # terminal could be picked up on screen, or the reverse.
    #
    # This is that store, not a second one. A parallel CLI-only transcript would have been the
    # easier change and would have made the split permanent.
    # One meter for the thread, or None. `AgentConfig.max_usd` would have been one line and would
    # have built a FRESH budget inside every `Agent.run` — a cap on one answer, not on the evening.
    # See `chimera.cli.spend`.
    budget = session_budget(max_usd)
    turns: Any = agent if budget is None else BudgetedTurns(agent, budget)
    if budget is not None:
        console.print(f"[dim]spend ceiling: ${budget.max_usd:.4f} for this whole thread.[/dim]")
    manager = SessionManager(
        lambda: ChatSession(
            turns,
            memory=mem,
            graph=_recall_graph(mem),
            profile=_session_profile(mem),
            # The setting existed and no terminal surface passed it, so "remember that…" was
            # answered "Got it, I'll remember" and wrote nothing, with the flag on or off.
            remember_from_chat=settings.remember_from_chat,
            # A tainted fact recalled into this conversation arms its ledger like a fetched page
            # (study 30 S30-25): the [unverified] label alone narrows nothing.
            on_tainted_recall=hand.ledger.record_fetch,
            real_history=settings.chat_real_history,
            # Recall narrowed to the folder this conversation is open on, exactly as the coding
            # turn does it. `--workspace` decided which files the tools could touch and said
            # nothing about which project's memory arrived, so a note from one codebase turned up
            # as context in a chat about another.
            project=project_key(workspace),
            # The thread's id when the spend is written: `/new` rebinds `active` below.
            extractor=_memory_extractor(settings, mem, lambda: active),
            cite_facts=settings.memory_extract,
            # A message with `/attach`ed documents is checked against them (study 26); built on
            # such a turn only. The plain gateway: the escalation names its own model.
            grounded_answers=_grounded_answers_for(settings, gateway),
            # A shell command that outlived its timeout kept running as a job; the next turn hears
            # that it ended, as on the Code screen.
            turn_note=lambda: finished_note(settings.home, Path(workspace)),
            workspace=Path(workspace),
        ),
        store,
    )
    active, resumed = _resume_or_new(manager, session_id, new)
    session = manager.get(active)
    skill_names = _learned_skill_labels(settings)

    commands = _chat_commands()
    console.print(
        "[bold]Chimera chat[/bold] — your terminal right-hand. "
        "[cyan]/model <slug>[/cyan] to switch, [cyan]/new[/cyan] for a fresh thread, "
        "[cyan]/help[/cyan] for the rest, [cyan]/exit[/cyan] to quit."
    )
    # Say which thread this is, always. Resuming silently is the same surprise as forgetting.
    if resumed:
        console.print(f"[dim]resuming {active} — {len(session.turns)} turn(s). /new starts over.[/dim]")
    else:
        console.print(f"[dim]session {active} — saved as you go, under {store_label}.[/dim]")
    nudged: set[str] = set()  # preferences already suggested this session
    skill_nudged: set[str] = set()  # recurring tasks already suggested as skills
    pending_docs: list[tuple[str, str]] = []  # `/attach`ed for the next message only
    while True:
        try:
            message = console.input("[bold green]you ›[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings, active)
            break
        if not message:
            continue
        head, argument = render.split_command(message)
        if head in ("/exit", "/quit", "/q"):
            console.print("[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings, active)
            break
        if head == "/help":
            _print_help(commands)
            continue
        if _session_command(head, session, home=settings.home, usage_id=active):
            continue
        if head in ("/new", "/reset"):
            # `/reset` used to clear an in-memory transcript, which cost nothing. Now that the
            # transcript is on disk, clearing it in place would delete the conversation — a command
            # that says "clear context" must not be the one that loses work. Both start a new thread
            # and leave the old one where the app can still open it.
            active = manager.new()
            session = manager.get(active)
            console.print(f"[dim]new thread {active} — the previous one is saved.[/dim]")
            continue
        if head == "/model":
            _switch_model(session, agent, argument or None, routed=routed, plain=gateway)
            continue
        if head == "/attach":
            _attach_document(argument, settings, pending_docs)
            continue
        if head == "/solve":
            # Never automatic, and the one command here that can change files. `_run_solve_command`
            # says what it is about to do before it does it, and records the loop's own answer in
            # this thread so the next turn knows what happened.
            # Measured like a turn, so `/undo` takes back what the loop left as well.
            measuring = session.measure() if hasattr(session, "measure") else None
            _run_solve_command(
                session,
                argument,
                workspace=workspace,
                agent=agent,
                write_region=write_region,
                budget=budget,
                hand=hand,
            )
            if measuring is not None:
                session.end_measure(measuring)
            _persist_turn(manager, active)
            continue
        if _handle_unknown_command(head, commands):
            continue
        if budget is not None and budget.blocked():
            # Refused here rather than one layer down: the loop would refuse too, at zero cost, but
            # it would first record a turn whose "answer" is the budget error — and that text is
            # replayed into every later prompt as if the model had written it.
            console.print(render.budget_spent_line(str(budget.blocked())))
            continue
        # Before a single tool runs: the ledger is told whose words this turn is. Without it every
        # fetch reads `unknown`, and `CHIMERA_TAINT_AUTHORITY=authority` — the one setting that
        # spends the person's attention only on pages they did NOT ask for — cannot tell the two
        # apart. The desktop chat still cannot; see `chimera/cli/right_hand.py`.
        hand.begin_turn(message)
        # Read BEFORE the turn: `send_verbose` appends the new exchange, which would shift the
        # window this reads by one and drop the oldest restored turn out of the count.
        restored = _replayed_provenance(session)
        docs, pending_docs = pending_docs, []
        report, outcome = _run_turn(session, message, docs)
        if outcome == "stop":
            _persist_turn(manager, active)  # whatever the thread already had, before leaving
            console.print("[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings, active)
            break
        if outcome != "ok":
            continue
        _render_turn(
            report, session_id=active, settings=settings, hand=hand, restored=restored
        )
        _render_memory_note(report, message, settings)
        # After the turn, not at exit: Ctrl-C and a closed terminal are how a REPL usually ends,
        # and neither runs a shutdown hook. After the PRINT, not before it — see `_persist_turn`.
        _persist_turn(manager, active)
        _emit_memory_nudges(session, mem, nudged, 'memory add --persona "{fact}"')
        _emit_skill_nudges(session, skill_names, skill_nudged)


@app.command()
def assist(
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
    no_cascade: bool = typer.Option(
        False, "--no-cascade", help="Disable tiered routing (single default model instead)."
    ),
    max_usd: float | None = typer.Option(
        None,
        "--max-usd",
        help="Stop once this conversation has spent this much (the whole run, not one turn).",
    ),
    write_region: str | None = typer.Option(
        None,
        "--write-region",
        help="Comma-separated globs the file-writers may touch (e.g. 'src/**,*.py'). A write "
        "outside is refused — blocks an injected instruction from rewriting an unrelated file.",
    ),
) -> None:
    """Your daily-driver assistant: cheap by default, escalates when it must.

    Assist = chat with the second-brain defaults ON: the tier cascade routes
    chit-chat to cheap models and escalates hard asks; your persistent profile
    (chimera profile) is the stable preamble; memory, nudges and end-of-session
    consolidation are active. On exit it prints the session cost receipt —
    tier distribution + measured tokens — so 'cheap by default' is a number.

    ``--max-usd`` bounds the whole run rather than one turn. Naming a model — ``--model`` or
    ``/model`` — pins it and turns the ladder off for as long as it is pinned, because the ladder
    is what chooses a model; it used to accept the slug and ignore it. ``/solve`` hands a task to
    the verified loop, inside the same ceiling.
    """
    import time as _time
    from uuid import uuid4

    from chimera.cli.right_hand import build_right_hand
    from chimera.cli.spend import BudgetedTurns, session_budget
    from chimera.core import Agent, AgentConfig
    from chimera.core.agent import attended
    from chimera.core.instructions import load as load_identity
    from chimera.core.instructions import render as render_identity
    from chimera.core.jobs import finished_note
    from chimera.fusion.route_log import format_route_summary, load_routes, summarize_routes
    from chimera.interface import ChatSession, render
    from chimera.memory.models import project_key
    from chimera.providers import LLMGateway

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)

    _check_max_usd(max_usd)
    session_start = _time.time()
    routes_path = Path(settings.home) / "routes.jsonl"
    # `assist` keeps no durable thread, but its turns still cost money, and the Cost screen groups
    # by session: one id per run puts this run's spending together instead of scattering it.
    usage_session = uuid4().hex[:12]
    _sandbox_banner()  # before the registry builds the sandbox, which is what logs the long notice
    gateway = LLMGateway()
    routed: SupportsComplete | None = None if no_cascade else _cascade_backend(gateway, settings)
    backend: SupportsComplete = gateway if routed is None else routed
    # `--model` under the ladder was a value accepted and dropped — see `_switch_model`. Naming one
    # pins it, and the ladder steps aside for as long as it is named.
    if model and routed is not None:
        backend = gateway
        _pinned_notice(model)
    # The same assembly `chat` builds, from the same function, so the two right hands cannot drift
    # into having different protections — which is how one of them ended up with none.
    hand = build_right_hand(
        Path(workspace), settings=settings, surface="assist", write_region=write_region
    )
    agent = Agent(
        backend,
        hand.registry,
        # Same workspace, both arguments: the one that roots the tools also carries the
        # project's conventions. Splitting them is how `AGENTS.md` came to be read on
        # four surfaces out of twenty-seven.
        attended(AgentConfig(
            model=model,
            max_steps=max_steps,
            project_root=Path(workspace),
            instructions=render_identity(load_identity(settings.home)),
            turn_context=True,
        )),
    )
    # Second-brain defaults: memory + graph + profile preamble always on (unless opted out).
    mem = None if no_memory else _memory_manager()
    # One meter for the run, exactly as `chat` builds one — see `chimera.cli.spend` for why it is
    # not `AgentConfig.max_usd`.
    budget = session_budget(max_usd)
    turns: Any = agent if budget is None else BudgetedTurns(agent, budget)
    session = ChatSession(
        turns,
        memory=mem,
        graph=_recall_graph(mem),
        profile=_session_profile(mem),
        remember_from_chat=settings.remember_from_chat,
        # A tainted fact recalled into this conversation arms its ledger like a fetched page
        # (study 30 S30-25): the [unverified] label alone narrows nothing.
        on_tainted_recall=hand.ledger.record_fetch,
        real_history=settings.chat_real_history,
        # Same narrowing as `chat` and the coding turn: this folder's facts plus the ones that
        # belong everywhere. Both terminal surfaces take a `--workspace` and neither used it here.
        project=project_key(workspace),
        extractor=_memory_extractor(settings, mem, usage_session),
        cite_facts=settings.memory_extract,
        grounded_answers=_grounded_answers_for(settings, gateway),
        turn_note=lambda: finished_note(settings.home, Path(workspace)),
        workspace=Path(workspace),
    )
    skill_names = _learned_skill_labels(settings)

    def _session_receipt() -> None:
        records = [r for r in load_routes(routes_path) if r.ts >= session_start]
        if records:
            console.print(
                Panel.fit(format_route_summary(summarize_routes(records)), title="session receipt")
            )

    ladder = settings.tier_ladder()
    commands = _assist_commands()
    console.print(
        "[bold]Chimera assist[/bold] — your right-hand, cheap by default. "
        f"[dim]tiers: {ladder.weak.split('/')[-1]} → {ladder.mid.split('/')[-1]} → fusion "
        f"(entry: {ladder.entry})[/dim]\n"
        "[cyan]/task <hard ask>[/cyan] full-power route, [cyan]/profile <kind>: <fact>[/cyan] remember, "
        "[cyan]/reset[/cyan] clear, [cyan]/help[/cyan] the rest, [cyan]/exit[/cyan] quit."
    )
    if budget is not None:
        console.print(f"[dim]spend ceiling: ${budget.max_usd:.4f} for this whole run.[/dim]")
    nudged: set[str] = set()
    skill_nudged: set[str] = set()
    pending_docs: list[tuple[str, str]] = []  # `/attach`ed for the next message only
    while True:
        try:
            message = console.input("[bold green]you ›[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings, usage_session)
            _session_receipt()
            break
        if not message:
            continue
        head, argument = render.split_command(message)
        if head in ("/exit", "/quit", "/q"):
            console.print("[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings, usage_session)
            _session_receipt()
            break
        if head == "/help":
            _print_help(commands)
            continue
        if _session_command(head, session, home=settings.home, usage_id=usage_session):
            continue
        if head in ("/reset", "/new"):
            session.reset()
            console.print("[dim]context cleared[/dim]")
            continue
        if head == "/profile":
            # "/profile preference: answer in PT-BR" (kinds: preference|project|context|name)
            from chimera.interface.profile import load_profile, profile_path, save_profile

            kind, sep, value = argument.partition(":")
            if not sep or not value.strip():
                console.print("[dim]usage: /profile <preference|project|context|name>: <fact>[/dim]")
                continue
            path = profile_path(settings.home)
            stored = load_profile(path)
            if kind.strip().lower() == "name":
                stored.name, changed = value.strip(), True
            else:
                changed = stored.add(kind.strip(), value.strip())
            if changed:
                save_profile(path, stored)
                session.profile = _session_profile(mem)  # takes effect next turn
                console.print(
                    f"[green]stored[/green] {escape(kind.strip())}: {escape(value.strip())}"
                )
            else:
                console.print("[yellow]not stored (unknown kind or duplicate)[/yellow]")
            continue
        if head == "/task":
            # Full-power route for a hard ask: fusion-forced, one shot, no cascade climb.
            _run_task_command(
                session,
                argument,
                gateway=gateway,
                settings=settings,
                usage_session=usage_session,
                budget=budget,
            )
            continue
        if head == "/solve":
            # Measured like a turn, so `/undo` takes back what the loop left as well.
            measuring = session.measure() if hasattr(session, "measure") else None
            _run_solve_command(
                session,
                argument,
                workspace=workspace,
                agent=agent,
                write_region=write_region,
                budget=budget,
                hand=hand,
            )
            if measuring is not None:
                session.end_measure(measuring)
            continue
        if head == "/model":
            _switch_model(session, agent, argument or None, routed=routed, plain=gateway)
            continue
        if head == "/attach":
            _attach_document(argument, settings, pending_docs)
            continue
        if _handle_unknown_command(head, commands):
            continue
        if budget is not None and budget.blocked():
            console.print(render.budget_spent_line(str(budget.blocked())))
            continue
        hand.begin_turn(message)  # the ledger learns whose words this turn is; see `chat`
        restored = _replayed_provenance(session)
        docs, pending_docs = pending_docs, []
        report, outcome = _run_turn(session, message, docs)
        if outcome == "stop":
            console.print("[dim]bye[/dim]")
            _maybe_autoconsolidate(mem, settings, usage_session)
            _session_receipt()
            break
        if outcome != "ok":
            continue
        _render_turn(
            report, session_id=usage_session, settings=settings, hand=hand, restored=restored
        )
        _render_memory_note(report, message, settings)
        _emit_memory_nudges(session, mem, nudged, "/profile preference: {fact}")
        _emit_skill_nudges(session, skill_names, skill_nudged)


@app.command()
def tui(
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(6, "--max-steps", help="Max tool-calling steps per message."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Don't recall long-term memory."),
    session_id: str | None = typer.Option(
        None, "--session", "-s", help="Resume a specific session id (see 'chimera sessions')."
    ),
    new: bool = typer.Option(False, "--new", help="Start a fresh session instead of resuming."),
    stream: bool = typer.Option(
        True, "--stream/--no-stream", help="Live token streaming (single-model path only)."
    ),
    max_usd: float | None = typer.Option(
        None,
        "--max-usd",
        help="Stop once this session has spent this much (the whole session, not one turn). "
        "The activity panel shows what is left.",
    ),
    write_region: str | None = typer.Option(
        None,
        "--write-region",
        help="Comma-separated globs the file-writers may touch (e.g. 'src/**,*.py'). A write "
        "outside is refused — blocks an injected instruction from rewriting an unrelated file.",
    ),
) -> None:
    """Launch the full-screen TUI — your right-hand. Requires a key.

    Governed like ``chimera chat``: the taint ledger told your own message, the
    ``<<external-data>>`` fence around untrusted tool output, the trust kernel, the owner's reach
    floor and the connected MCP servers. What took longer to arrive here is the part that makes any
    of it usable — a question this surface can **draw**. Textual owns the terminal, so the
    stdin prompt every other surface uses was never seen: measured in a pty, a ``run_shell`` under
    the shipped ``CHIMERA_HOST_EXEC=ask`` blocked 123.8 s against a 120 s timeout and came back as
    ``✗ run_shell`` with no reason (`bench/right_hand_governance/RESULTS.md` Part 2). Both gates now
    open a modal instead; silence still refuses, and now says so while it is counting down.

    The conversation outlives the window. Every turn is saved under ``<home>/sessions`` — the same
    store ``chimera chat`` writes and ``chimera sessions`` lists, so a thread started in one can be
    picked up in the other — and the newest thread is resumed by default. ``--session`` opens a
    named one and ``--new`` starts fresh; on screen, ``/new`` (or ``Ctrl+R``, or ``/reset``) starts
    another and leaves the current one where it is. That last part is a change of meaning rather
    than of wording: ``/reset`` cleared an in-memory transcript back when nothing was on disk, and
    clearing a thread that is now a file in place would be the command that destroys it.

    Note that the scrollback is not redrawn on resume: a resumed turn is in the model's context and
    not on your screen, and the line under the banner says how many.
    """
    from chimera.api.sessions import SessionManager, SessionStore
    from chimera.cli.right_hand import build_right_hand
    from chimera.cli.spend import BudgetedTurns, session_budget
    from chimera.core import Agent, AgentConfig
    from chimera.core.agent import attended
    from chimera.core.instructions import load as load_identity
    from chimera.core.instructions import render as render_identity
    from chimera.core.jobs import finished_note
    from chimera.interface import ChatSession
    from chimera.memory.models import project_key
    from chimera.providers import LLMGateway
    from chimera.sandbox.confirm import declare_no_human_here

    try:
        from chimera.tui.app import ChimeraTUI
        from chimera.tui.confirm import ModalGate
    except ImportError:  # Textual is a base dep, but degrade gracefully if the install was slimmed.
        console.print(
            "[yellow]Textual isn't installed — falling back to 'chimera chat'. "
            "Install it with: pip install textual[/yellow]"
        )
        # EVERY argument, explicitly. Calling a Typer command as a plain function hands the
        # parameters you omit their `typer.OptionInfo` DEFAULT OBJECTS, not their defaults:
        # `bool(OptionInfo)` is True (so cascade and --new were both forced on) and
        # `str(OptionInfo)` became the session name, which died on the first save with
        # "Object of type OptionInfo is not JSON serializable". The documented fallback in
        # docs/usage.md could not survive one turn.
        return chat(
            model=model,
            max_steps=max_steps,
            workspace=workspace,
            fuse=fuse,
            cascade=False,
            no_memory=no_memory,
            # Forwarded now that this surface has threads of its own, and onto the SAME store:
            # `chimera tui -s standup` and `chimera chat -s standup` open one conversation, so a
            # fallback that minted a fresh thread would silently answer a different question than
            # the one the person asked for.
            session_id=session_id,
            new=new,
            # The TUI has its own `--max-usd` now, so the fallback forwards it rather than dropping
            # it: the flag was withheld while this surface had no panel line to show the ceiling on,
            # and falling back to a `chat` that ignored a ceiling the person typed would be the
            # worse half of that trade. It still cannot be *omitted* — an omitted parameter arrives
            # as an `OptionInfo` object and `session_budget` reads that as a truthy cap, which is
            # what `test_the_tui_fallback_passes_values_not_option_objects` exists to catch.
            max_usd=max_usd,
            # Forwarded, not dropped. This read `write_region=None` while `tui` had no flag of its
            # own, and that was the honest spelling of "no region was asked for" — but the day the
            # flag arrived it became a fence the person typed and this branch silently removed.
            # Falling back to a `chat` that writes anywhere is the worse half of that trade, and it
            # is invisible: the fallback prints "falling back to chimera chat" and nothing about a
            # narrowing it just dropped. It still cannot be *omitted* — an omitted parameter arrives
            # as an `OptionInfo` object and `.split(",")` would fail on it, which is the defect this
            # whole argument list exists to prevent.
            write_region=write_region,
        )

    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    _check_max_usd(max_usd)

    # The same store `chimera chat` writes, and deliberately not a second one. A TUI-only transcript
    # would have been the easier change and would have made permanent a split between two surfaces
    # that are the same conversation. (`<home>/code_sessions` — the app's *coding* thread — stays
    # separate for the reason `chimera/core/code_session.py` argues in its first paragraph.)
    store = SessionStore(settings.home / "sessions")
    if session_id is not None:
        # BEFORE the screen opens, for `chat`'s reason: an id that cannot address a file inside the
        # store was accepted there, ran a whole turn, and raised on the save — after the answer had
        # been paid for and while it was being thrown away. Here it would be worse, because the
        # exception would arrive with Textual holding the terminal.
        try:
            store.check_id(session_id)
        except ValueError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(code=1) from exc

    # From here on, this process must not put a question on stdin and expect an answer: Textual is
    # about to take the terminal into raw mode, and anything that writes a prompt there is writing
    # where nobody can look. `_human_can_answer()` used to say the opposite — stdin genuinely IS a
    # tty here — and that single wrong bit is what produced the 123.8 s block. The declaration is
    # what `chimera/api/app.py` does for the server, for exactly the same reason, and it is made
    # AFTER the fallback above has returned: the fallback runs `chimera chat`, which can prompt.
    #
    # Anything with a modal to draw on says so explicitly instead, by passing `ask=` below. So a
    # stray gate resolved by inference in this process refuses, and the two gates that matter ask.
    declare_no_human_here("tui")

    _sandbox_banner()  # before the registry builds the sandbox, which is what logs the long notice
    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse:
        from chimera.fusion import RoutedBackend
        from chimera.fusion.factory import fusion_engine

        backend = RoutedBackend(gateway, fusion_engine(gateway))
    # Built before the app that will draw its questions, because the tools that consult it are built
    # before the session that the app is constructed around. `ChimeraTUI.on_mount` binds it.
    gate = ModalGate()
    hand = build_right_hand(
        Path(workspace),
        settings=settings,
        surface="tui",
        ask=gate,
        write_region=write_region,
    )
    agent = Agent(
        backend,
        hand.registry,
        # Same workspace, both arguments: the one that roots the tools also carries the
        # project's conventions. Splitting them is how `AGENTS.md` came to be read on
        # four surfaces out of twenty-seven.
        attended(AgentConfig(
            model=model,
            max_steps=max_steps,
            project_root=Path(workspace),
            # The owner's own words from `agent.json`. `chat`, `assist` and the desktop app all
            # apply them; this surface did not, so one configuration produced two agents that
            # answered in different languages and different voices depending on which window you
            # opened. Nothing failed and nothing said so — the only symptom is a reply that reads
            # like a stranger's.
            instructions=render_identity(load_identity(settings.home)),
            turn_context=True,
        )),
    )
    mem = None if no_memory else _memory_manager()
    budget = session_budget(max_usd)
    turns: Any = agent if budget is None else BudgetedTurns(agent, budget)
    # The conversation outlives the window.
    #
    # This app built a `ChatSession` in memory and dropped it on exit — a right hand that forgets
    # the moment you close the terminal. The store that fixes it was already here, already tested
    # and already what `chat` uses; nothing needed writing, only wiring. The factory shape is
    # `SessionManager`'s, so a thread is hydrated from disk on first touch.
    manager = SessionManager(
        lambda: ChatSession(
            turns,
            memory=mem,
            graph=_recall_graph(mem),
            profile=_session_profile(mem),
            remember_from_chat=settings.remember_from_chat,
            # A tainted fact recalled into this conversation arms its ledger like a fetched page
            # (study 30 S30-25): the [unverified] label alone narrows nothing.
            on_tainted_recall=hand.ledger.record_fetch,
            real_history=settings.chat_real_history,
            # Recall narrowed to the folder this app was opened on, exactly as `chat` and `assist`
            # do it. This surface takes a `--workspace` too, and until now that argument decided
            # which files the tools could touch and said nothing about which project's memory
            # arrived — so a note a `solve` wrote in one codebase turned up as context in a
            # full-screen conversation about another. `project_key` and not `str(workspace)`:
            # writer and reader have to spell a folder the same way or the scoped read matches
            # nothing the scoped write produced, which is the defect underneath #401 and shows up
            # as memory that is simply never recalled.
            project=project_key(workspace),
            # Filed where the screen files its turns, which follows a `/reset`; read when the
            # spend is written, by which time `screen` exists.
            extractor=_memory_extractor(settings, mem, lambda: screen.session_id),
            cite_facts=settings.memory_extract,
            turn_note=lambda: finished_note(settings.home, Path(workspace)),
            workspace=Path(workspace),
        ),
        store,
    )
    active, resumed = _resume_or_new(manager, session_id, new)
    screen = ChimeraTUI(
        manager.get(active),
        model_label=model or settings.default_model,
        stream=stream,
        fuse=fuse,
        # The TUI shows a turn's price and wrote it nowhere: the app's Cost screen and every
        # self-measurement this project runs were blind to the terminal. One id per run, so a
        # session's turns group together.
        usage_home=settings.home,
        hand=hand,
        gate=gate,
        budget=budget,
        sessions=manager,
        session_id=active,
        resumed=resumed,
    )
    try:
        screen.run()
    finally:
        # Belt and braces with `on_unmount`. A crash between the last frame and the process exiting
        # would otherwise leave the gate claiming a screen that is gone, and a question asked in
        # that window would wait for a modal nothing can draw.
        gate.release()
