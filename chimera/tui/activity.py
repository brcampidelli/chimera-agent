"""The TUI activity panel — shows, live, what the agent is doing this turn.

Every value here comes from a real signal the agent produced (tool calls it made, tokens the provider
reported, facts recall pulled). Nothing is fabricated: cost reads "unavailable" when the model's price
is unknown, the memory layer is omitted rather than guessed, and a plain Q&A turn shows an empty Tools
section — not an invented one. There is deliberately no verify/revert line: verify-or-revert runs in
the autonomous (solve/project) path, never in a chat turn, so promising it here would be dishonest.
"""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Label, Static

from chimera.core.agent import ToolActivity
from chimera.interface.session import TurnReport


class ActivityPanel(VerticalScroll):
    """Right-hand panel: turn status, tool calls, token/cost, memory recall."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._tools: list[str] = []

    def compose(self) -> ComposeResult:
        yield Label("activity", classes="act-title")
        yield Static("idle", id="act-status")
        yield Label("tools", classes="act-h")
        yield Static("[dim](none)[/dim]", id="act-tools", markup=True)
        yield Label("tokens", classes="act-h")
        yield Static("[dim]—[/dim]", id="act-tokens", markup=True)
        yield Label("memory", classes="act-h")
        yield Static("[dim]—[/dim]", id="act-memory", markup=True)

    # -- mutators (called on the UI thread from the app's message handlers) --

    def set_status(self, text: str, *, spinner: bool = False) -> None:
        mark = "[cyan]●[/cyan] " if spinner else ""
        self.query_one("#act-status", Static).update(f"{mark}{escape(text)}")

    def start_turn(self, status: str) -> None:
        """Reset the per-turn signals (tools + token/memory readouts) and show the busy status."""
        self._tools = []
        self.query_one("#act-tools", Static).update("[dim](none)[/dim]")
        self.query_one("#act-tokens", Static).update("[dim]—[/dim]")
        self.query_one("#act-memory", Static).update("[dim]—[/dim]")
        self.set_status(status, spinner=True)

    def add_tool(self, act: ToolActivity) -> None:
        icon = "[green]✓[/green]" if act.ok else "[red]✗[/red]"
        self._tools.append(f"{icon} {escape(act.name)}")
        self.query_one("#act-tools", Static).update("\n".join(self._tools))

    def set_tokens(self, report: TurnReport) -> None:
        cost = f"~ ${report.usd:.4f}" if report.usd is not None else "cost: unavailable"
        cache = ""
        if report.cache_read_tokens or report.cache_write_tokens:
            cache = f"\ncache r/w {report.cache_read_tokens}/{report.cache_write_tokens}"
        self.query_one("#act-tokens", Static).update(
            f"in {report.prompt_tokens} · out {report.completion_tokens}{cache}\n{cost}"
        )

    def set_memory(self, count: int, layer: str | None) -> None:
        if count <= 0:
            self.query_one("#act-memory", Static).update("[dim]no facts recalled[/dim]")
            return
        tag = f" ([italic]{escape(layer)}[/italic])" if layer else ""
        self.query_one("#act-memory", Static).update(f"{count} fact(s) recalled{tag}")
