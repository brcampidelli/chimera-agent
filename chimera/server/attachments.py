"""Which files a chat turn wrote, and which of them may leave the machine as a bot attachment.

Study 29, P6.3. The Discord bot answered with text only, so "send me the report as a PDF" produced a
PDF on the VPS's disk and a sentence in the channel saying it existed. This module is the half of the
fix that decides WHAT may be attached; the adapter (``discord_adapter``) does the sending.

What leaves is narrow on purpose, because a file in a channel is the owner's data in a place other
people can read:

* **Only what this turn wrote**, read off the turn's own tool calls (``write_file``,
  ``create_document``, ``render_chart``, ``generate_image``) — never "whatever is in the folder". A
  turn cannot attach ``.env`` by naming it; it would have to write it, and the writers' own gate
  (``refuse_write``) never lets it.
* **Inside the workspace**, resolved strictly (``resolve_in_workspace``: no approved escape), outside
  ``.git``/``.chimera``/``.env``, and the resolved file must still be a regular file.
* **Deliverable types only** (:data:`ATTACHABLE_SUFFIXES`): documents, images, CSV, Markdown and
  plain text. Not HTML or
  SVG, which carry script, and not anything executable.
* **Bounded**: :data:`MAX_ATTACHMENT_BYTES` a file, :data:`MAX_ATTACHMENTS` a reply.

And it is OFF (``CHIMERA_DISCORD_ATTACH_FILES``), and refused even when on while the bot has no
allowlist (:func:`attach_refusal`): with no allowlist, anyone who can reach the bot could ask it to
write a file and then receive it — the owner's tools turned into a file-export service for strangers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.tools.workspace import PathEscapesWorkspaceError, resolve_in_workspace
from chimera.tools.write_region import ALWAYS_DENIED

if TYPE_CHECKING:
    from chimera.config import Settings

#: What may be attached. Deliverables a person opens, nothing that runs: HTML and SVG execute script
#: when opened. Markdown and plain text are in: "write a report" most often produces a .md, and the
#: reply carries the turn's ANSWER, not the contents of the file it wrote.
ATTACHABLE_SUFFIXES = frozenset(
    {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".csv", ".md", ".txt"}
)
#: Types a person could have asked for as the deliverable and that are refused for what they carry.
#: Leaving one of these behind is SAID ("page.html: type .html is not attached"), because "here is
#: the page" with no page reads as a broken bot. Any other type a turn writes — app.py, config.json —
#: is the work itself, not something to send, and naming each one on every reply was noise.
_REFUSED_DELIVERABLE_SUFFIXES = frozenset({".html", ".htm", ".svg", ".xhtml"})
#: Below Discord's smallest upload ceiling (10 MiB for an unboosted server), so a file this module
#: lets through is not refused by the platform after the turn has been paid for.
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
#: Discord takes ten a message; four is plenty for a turn's deliverables and bounds what one injected
#: instruction could push out at once.
MAX_ATTACHMENTS = 4

#: The writers whose output is a deliverable, with the argument naming the file and the default the
#: tool uses when the argument is absent (``None``: the tool requires it).
_WRITERS: dict[str, tuple[str, str | None]] = {
    "write_file": ("path", None),
    "create_document": ("path", None),
    "render_chart": ("out", "chart.{format}"),
    "generate_image": ("out", "generated_image.png"),
}


@dataclass(frozen=True)
class Attachments:
    """What a turn will attach, and a short reason for each file it wrote and will not."""

    files: tuple[Path, ...] = ()
    skipped: tuple[str, ...] = ()


def written_paths(activities: Iterable[Any]) -> list[str]:
    """The paths, as the model named them, that this turn's writers reported writing.

    ``activities`` are :class:`chimera.core.agent.ToolActivity` (name, arguments, ok). A call that
    failed or was refused (``ok`` false) wrote nothing and names nothing.
    """
    out: list[str] = []
    for act in activities:
        writer = _WRITERS.get(str(getattr(act, "name", "")))
        if writer is None or not getattr(act, "ok", False):
            continue
        key, default = writer
        args = getattr(act, "arguments", {}) or {}
        raw = args.get(key) if isinstance(args, dict) else None
        if not raw and default is not None:
            fmt = str(args.get("format") or "html") if isinstance(args, dict) else "html"
            raw = default.format(format=fmt.lower())
        if raw and str(raw) not in out:
            out.append(str(raw))
    return out


def check_attachment(path: Path, workspace: Path) -> str | None:
    """Why ``path`` may not be attached, or ``None`` when it may. Called twice: here when the turn's
    list is built, and again by the adapter just before sending, since the file can change between."""
    root = Path(workspace).resolve()
    try:
        resolved = resolve_in_workspace(root, str(path))
    except PathEscapesWorkspaceError:
        return "outside the workspace"
    rel = resolved.relative_to(root).as_posix() if resolved != root else ""
    if any(rel == d or rel.startswith(f"{d}/") for d in ALWAYS_DENIED):
        return "a protected folder"
    if resolved.suffix.lower() not in ATTACHABLE_SUFFIXES:
        return f"type {resolved.suffix or '(none)'} is not attached"
    if not resolved.is_file():
        return "not found"
    size = resolved.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        return f"{size} bytes, over the {MAX_ATTACHMENT_BYTES}-byte limit"
    return None


def turn_attachments(activities: Iterable[Any], workspace: Path) -> Attachments:
    """The files this turn wrote that may be attached, in the order written, and why the rest not."""
    root = Path(workspace).resolve()
    files: list[Path] = []
    skipped: list[str] = []
    for raw in written_paths(activities):
        name = Path(raw).name
        suffix = Path(raw).suffix.lower()
        if suffix not in ATTACHABLE_SUFFIXES and suffix not in _REFUSED_DELIVERABLE_SUFFIXES:
            continue  # code or data the turn worked on: not a deliverable, so nothing to explain
        reason = check_attachment(Path(raw), root)
        if reason is None and len(files) >= MAX_ATTACHMENTS:
            reason = f"over {MAX_ATTACHMENTS} files a reply"
        if reason is not None:
            skipped.append(f"{name}: {reason}")
            continue
        resolved = resolve_in_workspace(root, raw)
        if resolved not in files:
            files.append(resolved)
    return Attachments(tuple(files), tuple(skipped))


def attach_refusal(settings: Settings, platform: str) -> str | None:
    """Why ``platform``'s bot will not attach files although the owner asked, or ``None``.

    ``None`` too when the owner did not ask — there is nothing to refuse. Only Discord sends files.
    """
    from chimera.server.allowlist import ALLOWLIST_FIELDS, allowed_users_for

    if platform != "discord" or not settings.discord_attach_files:
        return None
    if allowed_users_for(settings, platform) is None:
        env = ALLOWLIST_FIELDS[platform][1]
        return (
            "CHIMERA_DISCORD_ATTACH_FILES is on but the discord bot is OPEN, so files are NOT "
            f"attached: anyone could ask it to write one and receive it. Set {env} first."
        )
    return None


def attach_enabled(settings: Settings, platform: str) -> bool:
    """Whether ``platform``'s bot attaches the files its turns write: asked for, and safe to give."""
    return (
        platform == "discord"
        and bool(settings.discord_attach_files)
        and attach_refusal(settings, platform) is None
    )
