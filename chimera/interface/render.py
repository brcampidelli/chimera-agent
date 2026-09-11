"""What a terminal surface prints around a turn — written once, so it cannot drift.

``chat`` and ``assist`` grew as two copies of one REPL, and the copies had already diverged before
anything here was written. Every function below is a pure ``str``-in/``str``-out formatter so both
commands (and the TUI, for the parts it shares) call the same code and a test can check the text
without driving an event loop or a console.

Two rules the whole module exists to keep:

* **Nothing the model or a tool wrote is ever parsed as markup.** Rich reads ``[/]`` in a reply as a
  closing tag and raises ``MarkupError``, which killed the REPL *after* the turn had been paid for.
  Everything from outside goes through :func:`rich.markup.escape`.
* **A refusal is not a success and money is not a guess.** :func:`refusal_lines` prints the tool's
  own words when a call was declined or failed, and :func:`cost_text` says ``unavailable`` when the
  model's price is unknown rather than inventing a zero.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.markup import escape

if TYPE_CHECKING:
    from chimera.interface.session import TurnReport

#: Provider-internal identifiers that ride along inside an error body and have no business on a
#: person's screen. OpenRouter returns the account's ``user_id`` in the JSON it sends for a bad
#: model, LiteLLM re-raises that body as the exception text, and the REPL printed the lot.
_PROVIDER_IDS = re.compile(
    r'(?P<key>["\']?(?:user_id|userId|account_id|organization_id|org_id|request_id)["\']?\s*[:=]\s*)'
    r'(?P<value>"[^"]*"|\'[^\']*\'|[A-Za-z0-9_-]+)'
)

#: A slash command someone meant to type. ``/usr/local/bin`` and ``/2`` are deliberately outside it:
#: refusing to send those to the model would break a legitimate message in order to catch a typo.
_COMMAND_HEAD = re.compile(r"^/[A-Za-z][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class SlashCommand:
    """One REPL command, as shown by ``/help`` and by the unknown-command message.

    One table per surface, used for BOTH: a command that exists but is missing from the help, or
    listed in the help and not implemented, is the drift this replaces.
    """

    name: str  # "/model"
    argument: str = ""  # "<slug>", or "" when the command takes none
    help: str = ""


def scrub_provider_ids(text: str) -> str:
    """Redact provider-internal ids from text that is about to be shown to a person."""
    return _PROVIDER_IDS.sub(lambda m: m.group("key") + "<redacted>", text)


def reply_line(answer: str) -> str:
    """The model's reply, escaped so its own brackets cannot crash the console."""
    return f"[bold magenta]chimera ›[/bold magenta] {escape(answer)}"


def error_line(error: BaseException | str) -> str:
    """A failed turn, scrubbed of provider ids and escaped."""
    return f"[red]error: {escape(scrub_provider_ids(str(error)))}[/red]"


def refusal_lines(report: TurnReport) -> list[str]:
    """One line per tool call that a gate refused or that failed, for printing under the reply.

    The model is told about the refusal and routinely narrates around it — the measured case is
    ``run_shell`` returning "host execution declined … Not run." and the answer being *"The command
    printed exactly: marker-42"*. Nothing in the reply itself distinguishes that from a command
    that ran, so the surface has to say it.
    """
    return [
        f"[red]✗ {escape(call.name)} did not succeed:[/red] [dim]{escape(call.reason)}[/dim]"
        for call in report.declined
    ]


def governance_line(granted: int, refused: int, *, attended: bool) -> str:
    """What the governance layer decided this turn, or ``""`` when it decided nothing.

    The sibling of :func:`refusal_lines`, for the case that function cannot see. A refused call
    comes back as a refusal observation and lands in ``report.declined``; an APPROVED one comes back
    as an ordinary result and is indistinguishable, afterwards, from a call nothing ever questioned.
    So the person who typed ``y`` to a prompt mid-turn had no record of it once the reply scrolled.

    ``attended`` is on the refusal half rather than the grant half because it changes what the
    refusal means: "refused" and "refused because there was nobody to ask" call for different
    reactions, and this surface is the one place where the second should never happen.
    """
    if not granted and not refused:
        return ""
    parts = []
    if granted:
        parts.append(f"{granted} approved")
    if refused:
        parts.append(f"{refused} refused" + ("" if attended else " (nobody could be asked)"))
    return f"[dim]governance: {', '.join(parts)} this turn[/dim]"


#: What each ``AgentResult.stopped_reason`` means to the person reading the reply above it.
#:
#: ``final`` is absent on purpose — it is the only value that means the model was done, and a line
#: printed after every ordinary turn is a line nobody reads by the third one.
#: ``budget`` is deliberately vague about WHICH ceiling. ``Agent._step`` raises a plain
#: ``BudgetExceeded`` for the dollar cap as well as the token one, so this reason covers both — and
#: the ceiling's own sentence ("spend cap reached: $x of $y") is already the reply above this line,
#: which is where the specific answer honestly lives. Naming "money" here would be a guess that is
#: wrong half the time.
_CUT_SHORT = {
    "spend": "the spend ceiling was reached — the answer stops here",
    "budget": "a ceiling was reached — the answer stops here, and the reply above says which",
    "max_steps": "the tool loop hit --max-steps, so this is as far as it got",
    "tool_loop": "the same tool call kept repeating, so the loop was stopped",
    "context_stuck": "the prompt no longer fits the model's window — start a new thread",
    "cancelled": "the turn was cancelled",
}


def cut_short_line(report: TurnReport) -> str:
    """Why this reply is not a finished one, or ``""`` when it is.

    The sibling of :func:`refusal_lines` for the ceilings rather than the gates. A turn that used
    up its steps, its tokens or its money returns the work done so far as an ordinary answer, and
    nothing about that answer says it was cut off — so a truncated reply reads exactly like a
    complete one, which is the same failure mode as a refused command reading like a successful one.
    """
    reason = (report.stopped_reason or "").strip()
    if not reason or reason == "final":
        return ""
    note = _CUT_SHORT.get(reason)
    if note is None:
        # A reason this module has not been taught still gets said. Silence would be the safer-
        # looking option and is the wrong one: an unknown stop is exactly the case where the person
        # needs to know that something stopped.
        return f"[yellow]⚠ the turn stopped early:[/yellow] [dim]{escape(reason)}[/dim]"
    return f"[yellow]⚠ {note}[/yellow] [dim]({escape(reason)})[/dim]"


def approval_stats_line(stats: Mapping[str, Any]) -> str:
    """How the questions this deployment has asked actually ended — the answer rate and the wait.

    Printed by ``chimera approve`` because that is the command the answering person runs, and the
    two numbers are about them: with nobody reachable, a configured approver behaves exactly like
    none, and the block rate reads perfect either way. A deployment that has never asked gets the
    fact rather than a rate of zero over zero.
    """
    asked = int(stats.get("asked") or 0)
    if not asked:
        return "[dim]no question has been asked and resolved on this home yet[/dim]"
    answered = int(stats.get("answered") or 0)
    timeouts = int(stats.get("timeouts") or 0)
    rate = float(stats.get("answer_rate") or 0.0)
    p50, p90 = stats.get("p50_seconds"), stats.get("p90_seconds")
    speed = (
        f"; time to answer p50 {float(p50):.0f}s, p90 {float(p90):.0f}s"
        if p50 is not None and p90 is not None
        else ""
    )
    tone = "green" if rate >= 0.8 else "yellow"
    levels = stats.get("by_level") or {}
    per_level = ""
    if isinstance(levels, Mapping) and len(levels) > 1:
        # Only when there is more than one level to compare: the whole point of the breakdown is
        # a `block` row timing out behind a healthy overall rate.
        parts = []
        for level, row in levels.items():
            got, of = int(row.get("answered") or 0), int(row.get("asked") or 0)
            parts.append(f"{level} {got}/{of}")
        per_level = " Answered by level: " + ", ".join(parts) + "."
    return (
        f"[{tone}]{answered} of {asked} question(s) answered ({rate:.0%}); "
        f"{timeouts} timed out into a refusal{escape(speed)}.{escape(per_level)}[/{tone}]"
    )


def budget_spent_line(reason: str) -> str:
    """A turn that was not sent at all, because the conversation has no money left.

    ``reason`` is :meth:`~chimera.orchestration.budget.SpendBudget.blocked`'s own sentence, which
    names the amount and the cap. Refusing here rather than letting the loop refuse costs nothing
    and keeps the transcript honest: a turn recorded with the budget error as its "answer" would be
    replayed into every later prompt as if the model had said it.
    """
    return (
        f"[yellow]not sent — {escape(reason)}.[/yellow]"
        "[dim] Start a new run with a higher --max-usd, or /exit.[/dim]"
    )


def provenance_line(report: TurnReport, *, restored: list[str]) -> str:
    """Where this turn's context came from, in one dim line, or ``""`` when there is nothing to say.

    ``restored`` is the provenance label of each turn that came off disk and was replayed into this
    prompt. :class:`~chimera.interface.session.ChatTurn` has carried that label since the store
    learned to record it, ``_replay`` already fences a restored turn that is not known clean — and
    none of it reached the screen, so the one fact the fence exists to represent was invisible to
    the person it protects.

    Deliberately not inside the reply: the reply text is replayed into every later prompt, so a
    marker written into it would put words in the model's mouth for the rest of the conversation
    and be saved back that way, one layer deeper on each reopen.
    """
    from chimera.interface.session import CLEAN, TAINTED, UNKNOWN

    parts: list[str] = []
    if restored:
        unknown = sum(1 for label in restored if label == UNKNOWN)
        tainted = sum(1 for label in restored if label == TAINTED)
        clean = sum(1 for label in restored if label == CLEAN)
        detail = [
            text
            for text, count in (
                (f"{unknown} never measured", unknown),
                (f"{tainted} tainted", tainted),
                (f"{clean} recorded clean", clean),
            )
            if count
        ]
        fenced = "" if unknown + tainted == 0 else ", replayed inside the data fence"
        parts.append(
            f"context: {len(restored)} restored turn(s) — {', '.join(detail)}{fenced}"
        )
    if report.provenance == TAINTED:
        parts.append("this turn read external content — the rest of the thread is downstream of it")
    if not parts:
        return ""
    return f"[dim]{escape(' · '.join(parts))}[/dim]"


def cost_text(report: TurnReport) -> str:
    """The turn's price, or ``cost: unavailable`` — never a guessed zero.

    Shared with the TUI's activity panel so the two surfaces cannot disagree about what a turn cost.
    """
    if report.usd is None:
        return "cost: unavailable"
    # The price is off prompt+completion at list rate; cache read/write bill differently, so when
    # cache tokens are present flag that the shown cost excludes them (don't imply exact).
    has_cache = bool(report.cache_read_tokens or report.cache_write_tokens)
    return f"~ ${report.usd:.4f}" + (" (excl. cache)" if has_cache else "")


def cost_line(report: TurnReport) -> str:
    """The one-line token/cost receipt a REPL prints under a reply."""
    cache = (
        f" · cache r/w {report.cache_read_tokens}/{report.cache_write_tokens}"
        if (report.cache_read_tokens or report.cache_write_tokens)
        else ""
    )
    return (
        f"[dim]in {report.prompt_tokens} · out {report.completion_tokens}{cache} · "
        f"{cost_text(report)}[/dim]"
    )


def split_command(message: str) -> tuple[str, str]:
    """``"/model  gpt-4o"`` -> ``("/model", "gpt-4o")``; a plain message -> ``("", message)``.

    Split on a word boundary, which ``startswith`` is not: ``/taskforce`` used to run ``/task`` with
    the argument "force", and ``/newsletter`` used to start a new thread.
    """
    if not message.startswith("/"):
        return "", message
    parts = message.split(maxsplit=1)
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")


def is_command_like(head: str) -> bool:
    """Whether ``head`` is a slash command someone meant to type (not a path, not a fraction)."""
    return bool(_COMMAND_HEAD.match(head))


def _label(command: SlashCommand) -> str:
    return f"{command.name} {command.argument}".strip()


def help_lines(commands: list[SlashCommand]) -> list[str]:
    """The ``/help`` body: every command the surface actually implements, with its argument."""
    width = max((len(_label(c)) for c in commands), default=0)
    return [f"[cyan]{_label(c):<{width}}[/cyan]  [dim]{c.help}[/dim]" for c in commands]


def unknown_command_lines(head: str, commands: list[SlashCommand]) -> list[str]:
    """What to print for ``/foo``: that it is not a command, and what is.

    Never the model. An unrecognised slash used to be sent as an ordinary message — ``/help`` cost
    31 s and a capabilities essay, and ``/foo`` got a considered answer about "/foo".
    """
    known = " ".join(c.name for c in commands)
    return [
        f"[yellow]unknown command {escape(head)}[/yellow] "
        f"[dim](not sent to the model)[/dim]",
        f"[dim]commands: {known}[/dim]",
    ]
