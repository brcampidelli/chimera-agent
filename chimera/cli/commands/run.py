"""Single-shot commands: guard, run, deliver, agent, and the ``sessions`` group.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from chimera.cli.commands._shared import (
    _IMAGE_OPTION,
    _apply_tool_allowlist,
    _emit_headless,
    _exit_for,
    _headless,
    _headless_payload,
    _machine_stdout,
    _task_from_stdin,
    app,
    console,
    owner_identity,
)
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.providers import SupportsComplete



@app.command()
def guard(action: str = typer.Argument(..., help="The action/command to evaluate.")) -> None:
    """Show the governance verdict (allow/warn/review/block) for an action."""
    from chimera.governance import TrustKernel
    from chimera.governance.band import band_enabled, build_band

    # The band when the deployment turned it on — the one place to see the number an action gets
    # before any surface enforces it: `CHIMERA_GOVERNANCE=observe CHIMERA_GOVERNANCE_BAND=on chimera
    # guard "docker system prune -af"`.
    settings = get_settings()
    kernel = TrustKernel(band=build_band(settings) if band_enabled(settings) else None)
    verdict = kernel.evaluate(action)
    colors = {"allow": "green", "warn": "yellow", "review": "yellow", "block": "red"}
    color = colors[verdict.decision.value]
    detail = f" (rule: {verdict.rule})" if verdict.rule else ""
    number = f" p={verdict.confidence:.2f}" if verdict.confidence is not None else ""
    console.print(f"[{color}]{verdict.decision.value.upper()}[/{color}]{number} {verdict.reason}{detail}")


@app.command()
@_headless
def run(
    prompt: str = typer.Argument(None, help="The prompt to send (or '-' to read stdin)."),
    json_output: bool = typer.Option(False, "--json", help="Print one final JSON object."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Print JSON events as lines."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    system: str = typer.Option(None, "--system", "-s", help="Optional system prompt."),
    image: list[str] | None = _IMAGE_OPTION,
) -> None:
    """Run a single-shot Tier-1 completion (no fusion). Requires a provider key."""
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.providers.gateway import Message, MessageLike

    out = _machine_stdout(json_output or jsonl)
    prompt = _task_from_stdin(prompt)
    try:
        gateway = LLMGateway()
        if image:
            messages: list[MessageLike] = []
            if system:
                messages.append(Message(role="system", content=system))
            messages.append(Message(role="user", content=prompt, images=image))
            answer = gateway.complete(messages, model=model).content
        else:
            answer = gateway.quick(prompt, model=model, system=system)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    # Escaped, as on every command that prints a model's text: `console` parses markup, and a
    # reply containing `[/]` raised MarkupError after the call had been paid for.
    text = str(answer)
    if json_output or jsonl:
        _emit_headless(_headless_payload(text, "final", model=model or "", steps=1),
                       json_output=json_output, jsonl=jsonl, stream=out)
    else:
        console.print(escape(text))


@app.command()
def deliver(
    request: str = typer.Argument(..., help="What to produce (a report, plan, spec, README...)."),
    out: str = typer.Option(None, "--out", "-o", help="Write the deliverable to this file."),
    fmt: str = typer.Option(
        "md", "--format", "-f", help="md | txt | html | docx | xlsx | pdf (the last three need --out)"
    ),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    fuse: bool = typer.Option(False, "--fuse", help="Use the fusion engine for higher quality."),
) -> None:
    """Deliverable Mode: produce a polished, self-contained artifact. Requires a key."""
    from chimera.deliver import BINARY_FORMATS, FORMATS, produce_deliverable
    from chimera.providers import LLMGateway, MissingCredentialsError

    fmt = fmt.lower()
    if fmt not in FORMATS:
        console.print(f"[red]Unknown format {fmt!r}: use {' | '.join(FORMATS)}.[/red]")
        raise typer.Exit(code=2)
    # Checked before the model is called: a Word file cannot be printed to a terminal, and finding
    # that out after paying for the answer would be the expensive way to learn it.
    if fmt in BINARY_FORMATS and not out:
        console.print(f"[red]--format {fmt} writes a file: give it a path with --out.[/red]")
        raise typer.Exit(code=2)
    settings = get_settings()
    if not settings.can_answer():
        console.print("[red]No provider key configured, and the default model is not a local one. Run 'chimera doctor'.[/red]")
        raise typer.Exit(code=1)
    gateway = LLMGateway()
    backend: SupportsComplete = gateway
    if fuse:
        from chimera.fusion.factory import fusion_engine

        backend = fusion_engine(gateway)
    try:
        document = produce_deliverable(backend, request, fmt=fmt, model=model)
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if fmt in BINARY_FORMATS:
        _write_binary_deliverable(document, fmt, Path(out))
    elif out:
        Path(out).write_text(document, encoding="utf-8")
        console.print(f"[green]wrote[/green] {out} [dim]({len(document)} chars)[/dim]")
    else:
        console.print(escape(str(document)))


def _unused_markdown_path(out: Path) -> Path:
    """Where the kept Markdown goes: ``out`` as .md, or a numbered name beside it if that exists.

    The owner named ``plano.xlsx``, never ``plano.md`` — an existing ``plano.md`` is theirs, and the
    fallback used to overwrite it without a word.
    """
    candidate = out.with_suffix(".md")
    n = 1
    while candidate.exists():
        candidate = out.with_name(f"{out.stem}-{n}.md")
        n += 1
    return candidate


def _write_binary_deliverable(markdown: str, fmt: str, out: Path) -> None:
    """Render the model's Markdown to ``fmt`` and write it — or say why not, and keep the Markdown.

    A refusal here comes after the model was paid for, so the text is saved beside the requested path
    rather than thrown away: the owner keeps what they bought and can see why it did not convert.
    """
    from chimera.deliver import render_deliverable

    try:
        data, note = render_deliverable(markdown, fmt)
    except ImportError:
        reason = f"writing {fmt} needs the 'documents-out' extra: pip install 'chimera-agent[documents-out]'"
    except ValueError as exc:
        reason = f"the answer has nothing a {fmt} can hold ({exc})"
    else:
        out.write_bytes(data)
        console.print(f"[green]wrote[/green] {out} [dim]({len(data)} bytes{note})[/dim]")
        return
    fallback = _unused_markdown_path(out)
    fallback.write_text(markdown, encoding="utf-8")
    console.print(f"[red]{reason}[/red]")
    console.print(f"[dim]the Markdown was kept in {fallback}[/dim]")
    raise typer.Exit(code=1)


@app.command()
@_headless
def agent(
    task: str = typer.Argument(None, help="The task for the agent to accomplish (or '-' to read stdin)."),
    json_output: bool = typer.Option(False, "--json", help="Print one final JSON object."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Print JSON events as lines."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model slug."),
    max_steps: int = typer.Option(8, "--max-steps", help="Max tool-calling steps."),
    workspace: str = typer.Option(".", "--workspace", "-w", help="Workspace root for tools."),
    fuse: bool = typer.Option(False, "--fuse", help="Route deep-reasoning turns through fusion."),
    guard: bool = typer.Option(False, "--guard", help="Gate tool calls through the governance kernel."),
    allow_tools: str = typer.Option(
        None, "--allow-tools", help="Per-session allowlist: only these tools (comma-separated)."
    ),
    deny_tools: str = typer.Option(
        None, "--deny-tools", help="Per-session denylist: drop these tools (comma-separated)."
    ),
) -> None:
    """Run the ReAct agent loop with native tools. Requires a provider key."""
    from chimera.core import Agent, AgentConfig
    from chimera.core.agent import attended
    from chimera.providers import LLMGateway, MissingCredentialsError
    from chimera.tools import default_registry

    out = _machine_stdout(json_output or jsonl)
    task = _task_from_stdin(task)
    try:
        gateway = LLMGateway()
        backend: SupportsComplete = gateway
        if fuse:
            from chimera.fusion import RoutedBackend
            from chimera.fusion.factory import fusion_engine

            backend = RoutedBackend(gateway, fusion_engine(gateway))
        registry = default_registry(Path(workspace))
        registry = _apply_tool_allowlist(
            registry, allow=allow_tools, deny=deny_tools, settings=get_settings()
        )
        if guard:
            from chimera.governance import AuditLog, TrustKernel, govern_registry
            from chimera.governance.profile import owner_hooks

            run_audit = AuditLog(get_settings().home / "audit.jsonl")
            kernel = TrustKernel(audit=run_audit)
            registry = govern_registry(registry, kernel)
            # The owner's hooks, which `govern_step` installs and this direct kernel used to skip:
            # a guarded run carries them like every other guarded surface.
            registry = owner_hooks(registry, settings=get_settings(), audit=run_audit)
        runner = Agent(
            backend, registry,
            attended(AgentConfig(
                model=model, max_steps=max_steps, project_root=Path(workspace),
                instructions=owner_identity(get_settings().home),
                turn_context=True,
            )),
        )
        # Printed as they come, as `chat` does: the run was given its warnings and nobody heard them.
        from chimera.interface import render

        result = runner.run(
            task, on_notice=lambda code, text, _data: console.print(render.notice_line(code, text))
        )
    except MissingCredentialsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    stopped = str(result.stopped_reason or "final")
    if json_output or jsonl:
        _emit_headless(_headless_payload(
            str(result.answer), stopped, model=str(result.model or ""), usd=result.usd,
            tokens=int(result.prompt_tokens + result.completion_tokens), steps=int(result.steps),
        ), json_output=json_output, jsonl=jsonl, stream=out)
        code = _exit_for(stopped)
        if code:
            raise typer.Exit(code=code)
    else:
        console.print(escape(str(result.answer)))
        console.print(
            f"[dim]({result.stopped_reason}, {result.steps} steps, "
            f"{result.tool_calls_made} tool calls)[/dim]"
        )


# `chimera sessions` — the saved terminal threads, and (S30-66) the app's running coding turns.
# `sessions` stays importable from here: its docstring is the store's published description.
from chimera.cli.sessions_cmd import sessions as sessions  # noqa: E402
from chimera.cli.sessions_cmd import sessions_app  # noqa: E402

app.add_typer(sessions_app, name="sessions")
