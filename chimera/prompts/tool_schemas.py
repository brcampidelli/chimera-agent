"""The tool schema each surface sends the model, assembled the way the surface assembles it.

A prompt that changes shows up as text in `tests/prompt_snapshots/`. The tool schema is the larger
half of what is re-sent on every step (the CLI's 18,136 characters against its ~9.8 thousand with
deferral, finding a03-01) and until this module nothing showed a change to it: a description grown
by a few lines, or a parameter added, moved no snapshot and no number.

**Each surface is built with the functions production calls**, not with a registry put together in
the test. A hand-made registry passes green and protects nothing, because no surface sends it:

- ``code``: :func:`chimera.api.code_api.assemble_registry`, the Code turn's own assembly, which
  also applies deferral;
- ``design``: the same function on a turn that offers the design tool (`DesignOffer`);
- ``cli``: ``default_registry`` and then ``_apply_tool_allowlist``, as ``chimera run`` does it
  (``chat``, ``assist`` and ``solve`` route through the same deferral);
- ``cron``: ``governed_profile(default_registry(...))``, as the job runner does it;
- ``bot``: ``governed_profile(default_registry(...), voice=[SendMessageTool])``, as the messaging
  manager does it.

The conditions are fixed so the text is the same on every machine: default :class:`Settings`, an
empty temporary home and workspace, no environment variable of this project and no credential, and
the optional packages answered by :func:`_optional_modules` instead of by what is installed.

A surface that cannot be assembled that way is **not measured**, and says why in
:data:`NOT_MEASURED`; the table prints the reason. It is not approximated with a registry that
resembles it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

#: Surfaces this module assembles, in the order the table lists them.
SURFACES: tuple[str, ...] = ("code", "design", "cli", "cron", "bot")

#: Surfaces that carry tool deferral in production. Read from the call sites (`code_api.py`
#: ``settings.defer_tools`` block, `cli/commands/_shared.py::_maybe_defer`); `governed_profile`,
#: which the cron and the bot use, never calls `defer_builtins`.
DEFERRAL_APPLIES: frozenset[str] = frozenset({"code", "design", "cli"})

#: What each surface is, in a phrase, for the generated table.
LABELS: dict[str, str] = {
    "code": "Code turn with a screen (desktop, with question and task cards)",
    "design": "Code turn that offers the design tool",
    "cli": "`chimera run` (chat, assist and solve share its deferral)",
    "cron": "scheduled job",
    "bot": "messaging bot (Discord, Telegram)",
}

#: Surfaces with a tool schema that this module does not measure, and why. Each reason was read in
#: the source: a surface is listed here when its registry is built inside a closure that no caller
#: can reach, so measuring it would mean copying the assembly, and a copy is the registry nobody uses.
NOT_MEASURED: dict[str, str] = {
    "server chat": (
        "built inside `_chat_session`, a closure of the `serve` command "
        "(`chimera/cli/commands/serve.py`), which also mounts MCP connectors, OpenAPI connectors "
        "and a live pool; there is no callable assembler to run hermetically"
    ),
}

#: Environment variables a process needs to run at all. Everything else is dropped while a surface
#: is assembled, so a maintainer's `CHIMERA_*` or provider key cannot change the text.
_KEEP_ENV = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "OS", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONPATH", "PYTHONHASHSEED",
    "VIRTUAL_ENV", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "LD_LIBRARY_PATH",
})


def _optional_modules(real: Callable[..., Any]) -> Callable[..., Any]:
    """A ``find_spec`` that answers for the packages that gate a tool, and defers for the rest.

    ``default_registry`` registers the browser when ``playwright`` is importable (a core dependency),
    the image tools when ``diffusers`` is, and speech-to-text when ``faster_whisper`` is. Whether the
    last two are installed is a fact about a machine, and a schema that follows it is a snapshot that
    fails on one and passes on another. Pinned to a plain install: the browser present, the two extras
    absent.
    """

    def find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "playwright":
            return SimpleNamespace(name="playwright")
        if name in {"diffusers", "faster_whisper"}:
            return None
        return real(name, *args, **kwargs)

    return find_spec


@contextmanager
def hermetic(*, defer_tools: bool) -> Iterator[tuple[Path, Path]]:
    """Default settings, an empty home and workspace, and nothing of the machine reaching the text.

    Yields ``(home, workspace)``. ``defer_tools`` is the only knob: it is set through the same
    environment variable an owner would use, so every reader of ``get_settings()`` sees it.
    """
    from chimera.config import Settings, get_settings

    saved_env = dict(os.environ)
    saved_config = dict(Settings.model_config)
    saved_find_spec = importlib.util.find_spec
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        home, workspace, user = root / "home", root / "workspace", root / "user"
        for folder in (home, workspace, user):
            folder.mkdir()
        try:
            kept = {k: v for k, v in saved_env.items() if k in _KEEP_ENV}
            os.environ.clear()
            os.environ.update(kept)
            os.environ.update({
                "CHIMERA_HOME": str(home),
                # The user's own folders: the global `.env` and Claude Code's settings are read from
                # them, and both are the maintainer's, not the text's.
                "HOME": str(user),
                "USERPROFILE": str(user),
                "CLAUDE_CONFIG_DIR": str(root / "claude-config"),
                "PYTHON_DOTENV_DISABLED": "1",
                "CHIMERA_DEFER_TOOLS": "true" if defer_tools else "false",
            })
            cast(Any, Settings).model_config = {**saved_config, "env_file": None}
            importlib.util.find_spec = _optional_modules(saved_find_spec)  # type: ignore[assignment]
            get_settings.cache_clear()
            yield home, workspace
        finally:
            _release(home)
            importlib.util.find_spec = saved_find_spec  # type: ignore[assignment]
            cast(Any, Settings).model_config = saved_config
            os.environ.clear()
            os.environ.update(saved_env)
            get_settings.cache_clear()


def _release(home: Path) -> None:
    """Close what assembling opened under ``home``, so the folder can be deleted (Windows keeps it)."""
    from chimera.memory import history

    with history._INDEXES_LOCK:
        index = history._INDEXES.pop(str(home.resolve()), None)
    if index is not None:
        index.close()


class _NoBackend:
    """Stands in for a model. Assembling a registry never calls one."""

    def complete(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - never reached
        raise AssertionError("assembling a tool registry must not call the model")


def _assemble_code(workspace: Path, *, design: bool) -> Any:
    """The attended desktop turn, with the sinks that turn binds.

    ``approval_sink``, ``task_tray`` and ``chart_sink`` are what ``code_api`` creates for a turn that
    has a screen. Without them ``assemble_registry`` builds the headless registry, which lacks
    ``ask_user``, ``suggest_task`` and ``dismiss_task`` and wraps governance differently: a surface
    measured that way is one no desktop turn sends.
    """
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import get_settings
    from chimera.governance.approval import ApprovalAnnouncer
    from chimera.tools.chart import ChartAnnouncer
    from chimera.tools.design import DesignOffer
    from chimera.tools.task_suggestion import TaskSuggestionTray

    registry, _ledger = assemble_registry(
        CodeSeams(),
        workspace,
        get_settings(),
        _NoBackend(),
        steps=8,
        surface="api:turn",
        approval_sink=ApprovalAnnouncer(),
        chart_sink=ChartAnnouncer(),
        task_tray=TaskSuggestionTray(store=None),
        design=DesignOffer() if design else None,
    )
    return registry


def _assemble_cli(workspace: Path) -> Any:
    from chimera.cli.commands._shared import _apply_tool_allowlist
    from chimera.config import get_settings
    from chimera.tools import default_registry

    return _apply_tool_allowlist(
        default_registry(workspace), allow=None, deny=None, settings=get_settings()
    )


def _assemble_cron(workspace: Path) -> Any:
    from chimera.config import get_settings
    from chimera.governance.profile import governed_profile
    from chimera.tools import default_registry

    settings = get_settings()
    registry, _approvals = governed_profile(
        default_registry(workspace),
        settings=settings,
        home=settings.home,
        surface="cron:snapshot",
        instruction="summarise the day",
        workspace=workspace,
    )
    return registry


def _assemble_bot(workspace: Path) -> Any:
    from chimera.config import get_settings
    from chimera.governance.profile import governed_profile
    from chimera.integrations import SenderRegistry, SendMessageTool
    from chimera.tools import default_registry

    settings = get_settings()
    registry, _approvals = governed_profile(
        default_registry(workspace),
        settings=settings,
        home=settings.home,
        surface="app-messaging:snapshot",
        voice=[SendMessageTool(SenderRegistry())],
    )
    return registry


def assemble(surface: str, *, defer_tools: bool) -> Any:
    """The registry ``surface`` sends the model, built hermetically. Raises ``KeyError`` if unknown."""
    builders: dict[str, Callable[[Path], Any]] = {
        "code": lambda ws: _assemble_code(ws, design=False),
        "design": lambda ws: _assemble_code(ws, design=True),
        "cli": _assemble_cli,
        "cron": _assemble_cron,
        "bot": _assemble_bot,
    }
    with hermetic(defer_tools=defer_tools) as (_home, workspace):
        return _frozen(builders[surface](workspace))


@dataclass(frozen=True)
class Assembled:
    """What a surface declared: the schema text and the numbers that do not need the registry."""

    schema: str
    #: Characters of the schema as sent, which is the compact JSON, not the indented snapshot text.
    chars: int
    tools: int
    declared: int
    deferred_by_describe: int
    declared_chars_by_describe: int
    deferred_chars_by_describe: int


def schema_text(schemas: list[dict[str, Any]]) -> str:
    """The exact text a snapshot keeps: sorted keys, so no dict order can move it."""
    return json.dumps(schemas, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _frozen(registry: Any) -> Assembled:
    """Read everything needed from the registry while the hermetic settings are still in force."""
    from chimera.tools.defer import describe_saving

    schemas = registry.to_openai_schema()
    saving = describe_saving(registry)
    return Assembled(
        schema=schema_text(schemas),
        chars=len(json.dumps(schemas, ensure_ascii=False)),
        tools=len(schemas),
        declared=saving["tools"],
        deferred_by_describe=saving["deferred"],
        declared_chars_by_describe=saving["declared_chars"],
        deferred_chars_by_describe=saving["deferred_chars"],
    )


@dataclass(frozen=True)
class SurfaceRow:
    """One line of the by-surface table: the default configuration beside ``defer_tools=False``."""

    surface: str
    deferral_applies: bool
    tools_default: int
    tools_undeferred: int
    chars_default: int
    chars_undeferred: int
    #: ``describe_saving`` on the undeferred registry: what deferral would take out, and its price.
    deferrable: int
    describe_declared_chars: int
    describe_deferred_chars: int

    @property
    def tokens_default(self) -> int:
        return estimate_tokens(self.chars_default)

    @property
    def tokens_undeferred(self) -> int:
        return estimate_tokens(self.chars_undeferred)


def estimate_tokens(chars: int) -> int:
    """Characters divided by four. An ESTIMATE, not a tokenizer: the ratio is what the table is for."""
    return round(chars / 4)


def measure_surface(surface: str) -> tuple[SurfaceRow, Assembled, Assembled]:
    """Assemble ``surface`` twice (production default, then ``defer_tools=False``) and compare."""
    default = assemble(surface, defer_tools=True)
    undeferred = assemble(surface, defer_tools=False)
    row = SurfaceRow(
        surface=surface,
        deferral_applies=surface in DEFERRAL_APPLIES,
        tools_default=default.tools,
        tools_undeferred=undeferred.tools,
        chars_default=default.chars,
        chars_undeferred=undeferred.chars,
        deferrable=undeferred.deferred_by_describe,
        describe_declared_chars=undeferred.declared_chars_by_describe,
        describe_deferred_chars=undeferred.deferred_chars_by_describe,
    )
    return row, default, undeferred


def schema_snapshots() -> dict[str, str]:
    """``schema.<surface>`` for every measured surface: what the model is sent by default.

    A surface that carries deferral also gets ``schema.<surface>.full``, the same registry with
    ``defer_tools=False``. The default text leaves out the tools behind ``tool_describe``, so a
    description edited on one of them would move no snapshot, though the model reads it all the same.
    """
    out: dict[str, str] = {}
    for surface in SURFACES:
        default = assemble(surface, defer_tools=True)
        out[f"schema.{surface}"] = default.schema
        if surface in DEFERRAL_APPLIES:
            out[f"schema.{surface}.full"] = assemble(surface, defer_tools=False).schema
    return out


def surface_rows() -> list[SurfaceRow]:
    return [measure_surface(surface)[0] for surface in SURFACES]


def format_row(row: SurfaceRow) -> str:
    """One line for the lint: the same words in the terminal and in the JSON ``detail``."""
    return (
        f"{row.tools_default} tools, {row.chars_default} chars (~{row.tokens_default} tokens est.) "
        f"by default; defer_tools=False: {row.tools_undeferred} tools, {row.chars_undeferred} chars "
        f"(~{row.tokens_undeferred} tokens est.)"
        + ("" if row.deferral_applies else "; deferral is not applied on this surface")
    )


def lint_rows() -> list[dict[str, Any]]:
    """The section the prompt lint prints: one dict per surface, measured or not, with the numbers."""
    out: list[dict[str, Any]] = []
    for row in surface_rows():
        out.append({
            "surface": row.surface,
            "measured": True,
            "deferral_applies": row.deferral_applies,
            "tools": row.tools_default,
            "tools_defer_off": row.tools_undeferred,
            "chars": row.chars_default,
            "chars_defer_off": row.chars_undeferred,
            "tokens_estimate": row.tokens_default,
            "tokens_estimate_defer_off": row.tokens_undeferred,
            "tokens_are": "estimate: chars / 4",
            "detail": format_row(row),
        })
    for surface, reason in NOT_MEASURED.items():
        out.append({"surface": surface, "measured": False, "detail": f"not measured: {reason}"})
    return out


TABLE_PATH = Path(__file__).resolve().parents[2] / "docs" / "tool-schema-by-surface.md"


def render_table() -> str:
    """``docs/tool-schema-by-surface.md``, generated. Nothing in it depends on the day or the machine."""
    rows, assembled = [], {}
    for surface in SURFACES:
        row, _default, undeferred = measure_surface(surface)
        rows.append(row)
        assembled[surface] = undeferred
    lines = [
        "# Tool schema by surface",
        "",
        "<!-- Generated by `python scripts/tool_schema_table.py`. Do not edit by hand: "
        "`tests/test_the_tool_schema_by_surface.py` fails when this file is stale. -->",
        "",
        "The tool schema is re-sent on every step of every turn. This is what each surface sends, "
        "assembled by the functions the surface itself calls (see `chimera/prompts/tool_schemas.py`), "
        "under default settings, with no credential, no MCP server and an empty workspace. "
        "`tests/prompt_snapshots/schema.<surface>.txt` keeps the exact text, so a change to a tool "
        "description or parameter shows up as a diff.",
        "",
        "**Tokens here are characters divided by four. That is an estimate, not a tokenizer.** The "
        "characters are the compact JSON of the schema as sent.",
        "",
        "## What each surface sends",
        "",
        "| Surface | Deferral applied | Tools (default) | Chars (default) | Tokens est. (default) "
        "| Tools (`defer_tools=False`) | Chars (`defer_tools=False`) | Tokens est. (`defer_tools=False`) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {LABELS[row.surface]} | {'yes' if row.deferral_applies else 'no'} | {row.tools_default} "
            f"| {row.chars_default:,} | ~{row.tokens_default:,} | {row.tools_undeferred} "
            f"| {row.chars_undeferred:,} | ~{row.tokens_undeferred:,} |"
        )
    lines += [
        "",
        "## What deferral takes out, by `describe_saving`",
        "",
        "`chimera.tools.defer.describe_saving` run on each surface's registry with `defer_tools=False`: "
        "what deferral would move behind `tool_list` / `tool_describe` / `tool_call`, in schema "
        "characters (per-tool JSON, so a few characters under the table above). On the surfaces where "
        "deferral is not applied the figure is what it would save, not what is saved.",
        "",
        "| Surface | Declared | Core, never deferred | Deferred | Chars before | Chars after "
        "(core + 3 proxies) | Saving |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        a = assembled[row.surface]
        saving = (a.declared_chars_by_describe - a.deferred_chars_by_describe) / a.declared_chars_by_describe
        lines.append(
            f"| {LABELS[row.surface]} | {a.declared} | {a.declared - a.deferred_by_describe} "
            f"| {a.deferred_by_describe} | {a.declared_chars_by_describe:,} "
            f"| {a.deferred_chars_by_describe:,} | {saving:.1%} |"
        )
    lines += ["", "## Not measured", ""]
    for surface, reason in NOT_MEASURED.items():
        lines.append(f"- **{surface}**: {reason}.")
    lines += [
        "",
        "## Conditions",
        "",
        "- Default `Settings`, an empty temporary home and workspace, no `CHIMERA_*` variable and no "
        "credential in the environment.",
        "- Optional packages are pinned, not read from the machine: `playwright` present (a core "
        "dependency, so the browser is registered), `diffusers` and `faster_whisper` absent.",
        "- Tools gated by a key (search, e-mail, image, speech) are absent, because no key is set.",
        "- The Code turn's posture removes the execution tools that the default reach does not grant, "
        "which is why its schema is smaller than the CLI's.",
        "",
    ]
    return "\n".join(lines)
