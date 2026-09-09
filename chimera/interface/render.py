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
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
