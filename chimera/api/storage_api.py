"""``/api/storage`` and ``/api/diagnostics`` — the Storage and Diagnostics cards (study 29, P5.3).

Storage reads :func:`chimera.core.storage.measure` and offers exactly two actions, each refused
unless the request carries ``confirm: true``: collecting orphaned worktrees and rotating the
diagnostic traces. Nothing here deletes a session, a memory, the history or an approval — those are
measured, shown, and left alone.

Diagnostics is what a bug report needs: the backend's version, the paths the desktop fixed for it,
the last ``backend-crash.txt`` the desktop wrote, and a plain-text summary to copy. Every piece of
text that leaves through here is scrubbed of credentials first — a crash report is the backend's
stderr, and a provider error quotes the request it failed on.
"""

from __future__ import annotations

import platform
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, params

from chimera.api.schemas import (
    AppDiagnosticsOut,
    LogRotateOut,
    StorageConfirmIn,
    StorageOut,
    WorktreePruneOut,
)
from chimera.config import Settings
from chimera.core.redact import MASK, redact

#: What the desktop writes when the backend dies mid-session (`apps/desktop/src-tauri/src/main.rs`,
#: `Trouble::DiedMidSession`), in its data directory.
CRASH_FILE = "backend-crash.txt"

#: The tail kept from a crash report. The useful part of a traceback is its end, and the desktop
#: already keeps only the last lines of stderr; this bounds what a pathological file can put on
#: screen.
CRASH_MAX_CHARS = 20_000

#: The desktop launches the backend with ``CHIMERA_HOME=<data dir>/data`` (main.rs), and writes its
#: crash report in ``<data dir>``. A home with another name is the CLI's or a server's, whose parent
#: is somebody's project — not a place to go reading a file of this name from.
_DESKTOP_HOME_NAME = "data"

#: A name that marks what follows it as a credential, in any case and any spelling a crash carries:
#: ``OPENAI_API_KEY``, ``openai_api_key``, ``api-key``, ``x-api-key``, ``password``, ``bot_token``.
#: ``token`` is not followed by ``s`` or ``iz``, so ``max_tokens=4096`` and ``tokenizer: gpt2`` —
#: ordinary lines of a provider error — keep the numbers that diagnose it.
_SECRET_NAME = (
    r"[\w-]*?(?:api[_-]?key|apikey|secret|token(?!s|iz)|password|passwd|credential|private[_-]?key)"
    r"[\w-]*"
)

#: ``name=value``, ``name: value``, ``"name": "value"``, ``'name': 'value'`` — an environment dump,
#: a config file, a dict's repr (how the Anthropic client prints its headers) and JSON. `redact`
#: masks the values this process holds; this masks the ones it does not: a key rotated since the
#: crash, or one that belonged to an MCP server's config rather than this environment. A quoted value
#: is hidden to its closing quote, spaces included; an unquoted one to the next separator. A value
#: already masked is left as it is.
_ASSIGNMENT = re.compile(
    rf"(?P<keep>(?<![\w-])[\"']?{_SECRET_NAME}[\"']?[ \t]*[=:][ \t]*[\"']?)"
    rf"(?!{re.escape(MASK)})(?P<hide>(?<=[\"'])[^\"'\n]+|[^\s\"',;}}\]]+)",
    re.IGNORECASE,
)

#: Credentials recognised by their shape, which `redact` does not list: a Google API key, and a
#: Telegram bot token inside its API URL (`/bot<id>:<secret>/getMe`), where the URL IS the secret.
_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), MASK),
    (re.compile(r"\bbot\d{3,}:[A-Za-z0-9_-]{30,}"), f"bot{MASK}"),
)


def scrub(text: str) -> str:
    """Known credential formats out of text that is about to be shown or copied.

    Known formats, not every credential: a secret with no name beside it and no recognisable shape
    survives any list. The card says exactly that, and asks for a read before posting publicly.
    """
    text = _ASSIGNMENT.sub(lambda m: f"{m.group('keep')}{MASK}", redact(text))
    for pattern, replacement in _SHAPES:
        text = pattern.sub(replacement, text)
    return text


def crash_report(home: Path) -> dict[str, str] | None:
    """The desktop's last crash report, scrubbed and tail-capped; None when there is none to read."""
    home = Path(home).resolve()
    if home.name != _DESKTOP_HOME_NAME:
        return None
    path = home.parent / CRASH_FILE
    try:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8", errors="replace")
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
    except OSError:
        return None
    return {"path": str(path), "modified": modified, "text": scrub(raw[-CRASH_MAX_CHARS:])}


def diagnostics(settings: Settings, workspace: Path, *, storage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything the Diagnostics card shows, and the text its Copy button copies."""
    from chimera import __version__
    from chimera.api.config_api import doctor
    from chimera.core.storage import measure, summary_rows
    from chimera.core.worktree import worktree_parent

    home = Path(settings.home).resolve()
    crash = crash_report(home)
    health = doctor(settings)
    report = storage if storage is not None else measure(home, workspace)
    lines = [
        "Chimera diagnostics",
        f"backend version: {__version__}",
        f"python: {platform.python_version()}",
        f"platform: {platform.platform()}",
        f"home: {home}",
        f"workspace: {workspace}",
        f"worktree location: {worktree_parent(workspace)}",
        f"default model: {health['default_model']}",
        f"configured providers: {', '.join(health['configured_providers']) or 'none'}",
        f"memory backend: {health['memory_backend']}",
        f"sandbox: {health['sandbox'] or 'auto'}",
    ]
    for row in [health["spend"], *health["editor"], *health["external_agents"]]:
        lines.append(f"{row.get('label') or row.get('key')}: {'yes' if row.get('available') else 'no'}")
    lines.append("")
    lines.append("storage")
    lines.extend(f"  {label}: {value}" for label, value in summary_rows(report))
    if crash is not None:
        lines.extend(["", f"last crash report ({crash['modified']}):", crash["text"]])
    return {
        "backend_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "home": str(home),
        "workspace": str(workspace),
        "worktree_dir": str(worktree_parent(workspace)),
        "crash": crash,
        "report": scrub("\n".join(lines)),
    }


def _confirmed(body: StorageConfirmIn) -> None:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="this removes files; send confirm: true to do it")


def register_storage_api(
    app: FastAPI,
    guard: params.Depends,
    workspace: Path,
    settings: Settings,
    *,
    live_settings: Callable[[], Settings] | None = None,
) -> None:
    """Mount the storage and diagnostics routes.

    ``home`` is read from ``settings`` — the launch photograph — like every other route that reads
    it: the data directory does not move under a running backend. ``live_settings`` is for the
    doctor's view, which describes the configuration as it is now.
    """
    read_settings = live_settings or (lambda: settings)
    home = Path(settings.home)

    @app.get("/api/storage", dependencies=[guard], response_model=StorageOut)
    def storage_endpoint() -> dict[str, Any]:
        """What this install keeps on disk, by kind. A category that could not be counted is null."""
        from chimera.core.storage import measure

        return measure(home, workspace)

    @app.post(
        "/api/storage/worktrees/prune", dependencies=[guard], response_model=WorktreePruneOut
    )
    def prune_worktrees_endpoint(body: StorageConfirmIn) -> dict[str, int]:
        """Collect orphaned worktrees. A live run's worktree, or one whose maker cannot be
        identified, is never touched — see `chimera.core.worktree.classify_worktree_dir`."""
        _confirmed(body)
        from chimera.core.worktree import prune_worktree_dirs

        return prune_worktree_dirs()

    @app.post("/api/storage/logs/rotate", dependencies=[guard], response_model=LogRotateOut)
    def rotate_logs_endpoint(body: StorageConfirmIn) -> dict[str, int]:
        """Rotate the diagnostic traces now — the rename their writers make at the size cap."""
        _confirmed(body)
        from chimera.core.storage import rotate_logs

        return rotate_logs(home)

    @app.get("/api/diagnostics", dependencies=[guard], response_model=AppDiagnosticsOut)
    def diagnostics_endpoint() -> dict[str, Any]:
        current = read_settings()
        return diagnostics(current.model_copy(update={"home": home}), workspace)
