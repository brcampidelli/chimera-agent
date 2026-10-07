"""Shared CLI plumbing: the Typer ``app``, the console, and helpers every command area uses.

Moved verbatim out of ``chimera/cli/main.py`` (S30-70); ``chimera.cli.main`` re-exports every
name defined here.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, cast

import typer
from rich.console import Console

from chimera import __version__
from chimera.config import get_settings

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.core import AgentEvent
    from chimera.providers import SupportsComplete



def _force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 so model output never crashes a legacy console.

    Windows terminals default to cp1252, which raises UnicodeEncodeError on
    common model output (em dashes, non-breaking hyphens, emoji). Reconfiguring
    to UTF-8 with a safe error handler keeps the CLI robust everywhere.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):  # detached/odd streams
                reconfigure(encoding="utf-8", errors="backslashreplace")


_force_utf8_streams()


def owner_identity(home: Path) -> str:
    """The owner's rendered identity block for the install at ``home`` ("" when none is set).

    Every command that builds an agent a person talks to passes this, so the language, the name and
    the standing instructions set in Settings follow the owner onto every surface, the bots included.
    Imported lazily, like the rest of this module's dependencies, to keep `chimera --help` fast.
    """
    from chimera.core.instructions import for_home

    return for_home(home)

app = typer.Typer(
    name="chimera",
    help="Self-evolving AI agent with an LLM-Fusion reasoning core.",
    no_args_is_help=True,
    # On since S30-70: `--install-completion` / `--show-completion` for bash, zsh, fish and
    # PowerShell. With ~60 visible commands and their subcommands, tab completion is how a person
    # finds `chimera skills import` without reading the reference first.
    add_completion=True,
)
console = Console()


def _print_version(value: bool) -> None:
    """``chimera --version``: the release, and the commit when this install knows it.

    The SHA comes from the same helper the run receipts use (`chimera.build_info`), so the two can
    never disagree about which build this is. Plain ``typer.echo`` rather than the Rich console: a
    script reading the version must not get markup or colour codes.
    """
    if not value:
        return
    from chimera.build_info import chimera_git_sha

    sha = chimera_git_sha()
    typer.echo(f"chimera {__version__}" + (f" ({sha[:12]})" if sha else ""))
    raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        help="Print the version (and the git commit, when known) and exit.",
        callback=_print_version,
        is_eager=True,
    ),
) -> None:
    # No docstring on purpose: Typer would use it as the CLI's help, replacing `help=` above.
    del version


def _set_env_var(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file, replacing the line if present, appending otherwise.

    Held to the same rule as every other writer of the file: the value is encoded so every reader
    reads it back unchanged (`key_vault.encode_env_value`, which refuses what no spelling makes
    safe), and the file is split on newlines only.
    """
    from chimera.api.key_vault import encode_env_value, env_lines

    line_for = f"{key}={encode_env_value(key, value)}"
    lines = env_lines(path.read_text(encoding="utf-8")) if path.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[i] = line_for
            break
    else:
        lines.append(line_for)
    # Atomic write: a crash mid-write to .env must not truncate the user's secrets/config.
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def _resolve_cli_roles(profile: str | None, overrides: str | None, settings: Any) -> Any:
    """Parse ``--profile`` / ``--role-model`` into the SAME role plan the desktop endpoint resolves.

    One resolver for both surfaces, deliberately. ``bench/role_routing`` drives the CLI, and a bench
    that exercised a second implementation of the routing would measure something the app does not
    ship — which is the quiet way a benchmark stops being about the product.

    An unknown role name is an error rather than a silent no-op: a typo in ``--role-model edt=…``
    would otherwise produce a run that looks routed, reports itself as routed, and is not.
    """
    from chimera.api.roles import RoleModels
    from chimera.api.roles import resolve as resolve_roles

    fields = {f for f in RoleModels.model_fields if not f.startswith("fuse_")}
    parsed: dict[str, str] = {}
    for item in (overrides or "").split(","):
        if not item.strip():
            continue
        role, sep, slug = item.partition("=")
        role, slug = role.strip(), slug.strip()
        if not sep or not slug:
            raise typer.BadParameter(f"--role-model expects role=model, got {item!r}")
        if role not in fields:
            raise typer.BadParameter(
                f"unknown role {role!r}; expected one of {', '.join(sorted(fields))}. "
                "('verify' is not a role here — it runs a command and has no model to choose.)"
            )
        parsed[role] = slug
    if profile is not None and profile not in ("economy", "balanced", "max"):
        raise typer.BadParameter(f"unknown profile {profile!r}; expected economy, balanced or max")
    # The str-valued role fields only; the fuse_* flags come from the profile, never from here.
    override = RoleModels.model_validate(parsed) if parsed else None
    return resolve_roles(profile, settings, override)  # type: ignore[arg-type]


def _fused_if(backend: Any, fuse: bool, gateway: Any, settings: Any) -> Any:
    """Wrap a TOOL-FREE role's backend in the fusion engine when its profile asks for it.

    Only ever reached for planning and review. Both are turns with no tool schemas, which is the
    whole reason fusion can run at all: the router sends any turn carrying tools to a single model,
    so fusing the editor would be a switch that never fires and reports that it did.

    The panel comes from ``fusion_for_role`` — the user's own tier ladder — and NOT from a bare
    ``FusionEngine(gateway)``. The bare version was the bug: it fell through to the frontier default
    panel, so a profile chosen under a cheap cost mode silently billed Opus + GPT-5.5 + Gemini.
    """
    if not fuse:
        return backend
    from chimera.api.roles import fusion_for_role

    return fusion_for_role(gateway, settings)


def _apply_tool_allowlist(
    registry: Any,
    *,
    allow: str | None,
    deny: str | None,
    settings: Settings,
    audit: Any | None = None,
    later_denials: list[str] | None = None,
) -> Any:
    """Filter a registry by the per-session tool allowlist (CLI option over env).

    A ``--allow-tools``/``--deny-tools`` CLI value wins over the ``CHIMERA_TOOL_*``
    env lists; an empty allowlist means "no restriction". Returns the registry
    unchanged when nothing is restricted, so the common path stays a no-op.

    ``later_denials`` are names a guard the CALLER applies after this will remove — the app chat's
    `guard_chat_registry`. They are not removed here (the caller's guard does that, with its own
    audit); they are only handed to the deferral, which runs here and would otherwise put them
    behind ``tool_call`` before that guard can match them.
    """
    from chimera.governance import restrict_registry

    def _csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    allow_names = _csv(allow) if allow is not None else (settings.tool_allowlist or None)
    deny_names = _csv(deny) if deny is not None else list(settings.tool_denylist)
    if allow_names is not None or deny_names:
        registry = restrict_registry(registry, allow=allow_names, deny=deny_names, audit=audit)
    return _maybe_defer(registry, allow_names, [*deny_names, *(later_denials or [])], settings)


def _maybe_defer(
    registry: Any, allow_names: list[str] | None, deny_names: list[str], settings: Settings
) -> Any:
    """Put the non-core tools behind `tool_list` when the setting says so.

    Here, and not in `default_registry`, for two reasons that pull in the same direction.

    **Order.** Deferral takes the names out of the registry, so it has to run AFTER the restriction
    filter or the filter has nothing left to match — a denylist naming `scrape` would report success
    while the proxy still reached it. `default_registry` runs before, so wiring it there would build
    exactly that hole.

    **One place.** `default_registry` is called ten times in this file alone. Wiring a toggle into
    each is the "second code path that has to remember to run" that `mcp_defer` names, and this
    function is already the single point every CLI surface routes its tool governance through.

    **The whole fence, not only the lists this function was handed.** Every guard that removes tools
    by NAME after this point is blind to what this put behind ``tool_call``. The terminal's right
    hand applies the ``CHIMERA_REACH`` floor after it, and the app's chat its own guard: with the
    switch on and ``CHIMERA_REACH=read_only``, the floor removed ``run_shell`` and ``write_file``
    and ``tool_call(tool="execute_code")`` still ran. So the catalogue carries
    :func:`~chimera.api.posture.deployment_fence` — denylist plus reach floor, the same fence
    ``mcp_defer.mount`` hands the MCP proxy — on top of what the caller passed. On ``chimera run``
    and ``solve``, which do not apply the floor to the declared registry, this makes the deferred
    catalogue the stricter of the two shapes; that is the safe direction for a disagreement, and the
    floor not reaching those two surfaces at all is a gap of its own.
    """
    if not settings.defer_tools:
        return registry
    from chimera.api.posture import deployment_fence
    from chimera.tools.defer import defer_builtins

    fence_denied, _fence_allowed = deployment_fence(settings)
    deferido, _ = defer_builtins(
        registry,
        denied=frozenset(deny_names) | fence_denied,
        allowed=frozenset(allow_names) if allow_names else None,
    )
    return deferido


def _session_profile(mem: Any) -> str:
    """The chat/assist preamble: stable user profile first, volatile memory facts after.

    Byte-stable for the same profile (the cacheable prefix); memory-derived facts go
    in a separated volatile section so they never break the stable prefix.
    """
    from chimera.interface.profile import session_preamble

    return session_preamble(get_settings().home, mem)


def _cascade_backend(gateway: SupportsComplete, settings: Any) -> SupportsComplete:
    """Build the FrugalGPT cascade (weak -> gate -> mid -> gate -> fusion) over the tier ladder.

    Route decisions are appended to ``<home>/routes.jsonl`` (hash+tokens only, never prompt
    text) — the per-session cost receipt and the future router's training data.

    The top rung comes from ``fusion_for_role`` — the user's own ladder — and NOT from a bare
    ``FusionEngine(gateway)``. That distinction is not a style preference: `api/roles.py` carries
    a long comment explaining that the bare form was a *measured* bug, because an engine with no
    config falls through to ``FusionConfig.from_settings()`` and its frontier default panel. Role
    fusion was fixed; the cascade kept building the bare one, so the escape hatch for a user who
    picked cheap tiers ended at Opus + GPT-5.5 + Gemini, judged by Opus, with nothing in the run
    saying which models had answered.
    """
    from chimera.api.roles import fusion_for_role
    from chimera.fusion.cascade import CascadeBackend, CascadeConfig

    ladder = settings.tier_ladder()
    config = CascadeConfig(
        weak=ladder.weak,
        mid=ladder.mid,
        entry=ladder.entry,
        log_path=Path(settings.home) / "routes.jsonl",
    )
    return CascadeBackend(
        gateway, cast("SupportsComplete", fusion_for_role(gateway, settings)), config
    )


#: Exit codes for ``--json`` / ``--jsonl`` only. Without those flags every command keeps the codes it
#: always had (``solve`` exits 1 for any run that did not finish), because a script that reads 1 as
#: "did not finish" must not start reading 9 the day it upgrades. ``no_op`` is a success that changed
#: nothing on disk, so it is 0 like ``final``; the payload keeps the distinction.
_STOP_EXIT_CODES = {
    "final": 0, "no_op": 0, "max_steps": 2, "tool_loop": 3, "budget": 4, "spend": 5,
    "cancelled": 6, "context_stuck": 7, "handover": 8, "exhausted": 9,
    "paused": 10, "denied": 11, "unknown": 12,
}


def _task_from_stdin(task: str | None) -> str:
    """Return the task, reading stdin only for ``-`` or for an omitted task with stdin piped.

    A terminal on stdin is refused rather than read: ``chimera agent -`` typed at a prompt would
    otherwise sit waiting for an EOF nobody knows to send. Failures exit 1, not Click's usage code
    2, because 2 is ``max_steps`` in the headless table.
    """
    import sys

    piped = not sys.stdin.isatty()
    if task == "-" or (task is None and piped):
        if not piped:
            console.print("[red]'-' reads the task from stdin, and stdin is a terminal.[/red]")
            raise typer.Exit(code=1)
        value = sys.stdin.read()
        if not value.strip():
            console.print("[red]The task read from stdin is empty.[/red]")
            raise typer.Exit(code=1)
        return value.rstrip("\r\n")
    if task is None:
        console.print("[red]Provide a task, or pipe one on stdin ('-').[/red]")
        raise typer.Exit(code=1)
    return task


_HEADLESS_STDOUT: ContextVar[TextIO | None] = ContextVar("_HEADLESS_STDOUT", default=None)


def _headless(fn: Callable[..., Any]) -> Callable[..., Any]:
    """In ``--json``/``--jsonl`` mode, send every human line of the command to stderr.

    Every ``console.print`` in a command (banners, notices, the cost line) otherwise lands between
    the JSON lines a caller is parsing. The real stdout is kept for the JSON (see
    :func:`_machine_stdout`), and the redirect ends with the call, so it cannot leak into the next
    command a test runner or the ``/solve`` REPL path invokes in the same process.
    """
    import contextlib
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("json_output") is not True and kwargs.get("jsonl") is not True:
            return fn(*args, **kwargs)
        token = _HEADLESS_STDOUT.set(sys.stdout)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                return fn(*args, **kwargs)
        finally:
            _HEADLESS_STDOUT.reset(token)

    return wrapper


def _machine_stdout(enabled: bool) -> TextIO:
    """The stream the JSON goes to: the real stdout under :func:`_headless`, else ``sys.stdout``."""
    real = _HEADLESS_STDOUT.get() if enabled else None
    return real if real is not None else sys.stdout


def _headless_payload(
    answer: str, stopped_reason: str, *, model: str = "", usd: float | None = None,
    tokens: int = 0, steps: int = 0,
) -> dict[str, Any]:
    return {"answer": answer, "stopped_reason": stopped_reason,
            "receipt": {"model": model, "usd": usd, "tokens": tokens, "steps": steps}}


def _json_line(obj: dict[str, Any], stream: TextIO | None = None) -> None:
    import json
    import sys

    out = stream if stream is not None else sys.stdout
    # default=str: an event's data is whatever the loop put there, and one non-JSON value must not
    # turn a finished, paid-for run into a traceback.
    out.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    out.flush()


def _emit_headless(
    payload: dict[str, Any], *, json_output: bool, jsonl: bool, stream: TextIO | None = None
) -> None:
    if jsonl:
        from chimera.core.events import final

        event = final(payload["stopped_reason"] in ("final", "no_op"), payload["answer"])
        data = {**event.data, "stopped_reason": payload["stopped_reason"],
                "receipt": payload["receipt"]}
        _json_line({"kind": event.kind, "text": event.text, "data": data}, stream)
    elif json_output:
        _json_line(payload, stream)


def _exit_for(stopped_reason: str) -> int:
    return _STOP_EXIT_CODES.get(stopped_reason, 12)


def _stream_sink(event: AgentEvent) -> None:
    """Print one live progress event during ``solve --stream`` (dim, one line each)."""
    if event.kind == "final":
        return  # the final answer is printed by the command itself
    icon = {"status": "•", "attempt": "▸"}.get(event.kind, "•")
    if event.kind == "result":
        icon = "✓" if event.data.get("success") else "✗"
    console.print(f"[dim]{icon} {event.text}[/dim]")


# Module-level so the list-typed default isn't a call-in-default (ruff B008).
_IMAGE_OPTION = typer.Option(
    None, "--image", help="Attach an image (path or URL); repeatable. Needs a vision model."
)
_BATCH_TASKS_ARG = typer.Argument(..., help="Tasks to solve in parallel, each isolated.")
_CREW_WORKER_OPT = typer.Option(
    None, "--worker", "-W", help="A worker as 'name:instruction'; repeatable. Each edits in its own worktree."
)
