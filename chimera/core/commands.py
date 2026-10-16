"""Markdown prompt commands, discovered without granting their text any authority.

A command file is project data, including when it came from a cloned repository. Its only output is
an ordinary user message; this module deliberately has no path from a template to a process, tool
registry, model posture, or network request. Keeping discovery and interpolation here makes that
boundary testable without starting a CLI, TUI, or API server.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)(.*)\Z", re.DOTALL)
_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$", re.ASCII)
_PLACEHOLDER = re.compile(r"\$(ARGUMENTS|[1-9][0-9]*)")
_NAME_PART = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class PromptCommand:
    """A discovered command; ``error`` preserves broken files in listings for repair."""

    name: str
    description: str
    origin: str
    template: str
    model: str | None = None
    error: str | None = None


def _scalar(value: str) -> str:
    """Parse the deliberately small scalar subset used by command metadata."""
    value = value.strip()
    if not value:
        return ""
    if value.startswith('"'):
        parsed = json.loads(value)
        if not isinstance(parsed, str):
            raise ValueError("metadata values must be strings")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError("unterminated single-quoted metadata value")
        return value[1:-1].replace("''", "'")
    if any(char in value for char in "{}[]|>&*!%@`\\"):
        raise ValueError("metadata must be a plain string")
    return value


def _parse_file(path: Path, name: str, origin: str) -> PromptCommand:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return PromptCommand(
            name, "", origin, "", error=f"could not read command: {exc}"
        )
    match = _FRONTMATTER.match(text)
    description = ""
    model: str | None = None
    template = text
    if text.startswith("---"):
        if match is None:
            return PromptCommand(
                name, "", origin, "", error="invalid frontmatter: missing closing ---"
            )
        template = match.group(2)
        try:
            seen: set[str] = set()
            for line in match.group(1).splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                field = _FIELD.match(line)
                if field is None:
                    raise ValueError(f"invalid metadata line: {line.strip()!r}")
                key, raw = field.groups()
                if key not in {"description", "model"}:
                    raise ValueError(f"unsupported metadata field: {key}")
                if key in seen:
                    raise ValueError(f"duplicate metadata field: {key}")
                seen.add(key)
                value = _scalar(raw or "")
                if key == "description":
                    description = value
                else:
                    model = value or None
            if model is not None:
                from chimera.providers.catalog import CATALOG

                if model not in {entry.slug for entry in CATALOG}:
                    raise ValueError(f"unknown model slug: {model}")
        except (ValueError, json.JSONDecodeError) as exc:
            return PromptCommand(
                name, "", origin, template, error=f"invalid frontmatter: {exc}"
            )
    return PromptCommand(name, description, origin, template, model)


def discover_commands(workspace: Path, home: Path) -> list[PromptCommand]:
    """Read project and global Markdown commands; a same-name project file wins.

    Resolve each candidate before reading it. A symlink inside ``.chimera/commands`` must not turn
    a project command into an arbitrary filesystem reader, even though its contents are still only
    text and never executable.
    """
    roots = (
        (Path(workspace) / ".chimera" / "commands", "project"),
        (Path(home) / "commands", "global"),
    )
    found: dict[str, PromptCommand] = {}
    for root, origin in roots:
        try:
            files = sorted(root.rglob("*.md"))
        except OSError:
            continue
        for path in files:
            try:
                relative = path.relative_to(root)
                resolved_root = root.resolve()
                resolved_path = path.resolve()
                if not resolved_path.is_relative_to(resolved_root) or not path.is_file():
                    continue
            except (OSError, ValueError):
                continue
            parts = (*relative.parts[:-1], relative.stem)
            if not parts or any(not _NAME_PART.fullmatch(part) for part in parts):
                continue
            name = "/".join(parts)
            if name not in found or origin == "project":
                found[name] = _parse_file(path, name, origin)
    return [found[name] for name in sorted(found)]


def split_arguments(text: str) -> list[str]:
    """Split arguments with shell-like quote grouping, but never invoke a shell."""
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        # An incomplete quote is user input, not a reason to lose the whole prompt.
        return [text]


def expand_command(command: PromptCommand, arguments: str) -> str:
    """Expand a template into plain message text. No command content is interpreted or run."""
    if command.error is not None:
        raise ValueError(command.error)
    tokens = split_arguments(arguments)
    placeholders = [
        int(match.group(1))
        for match in _PLACEHOLDER.finditer(command.template)
        if match.group(1) != "ARGUMENTS"
    ]
    largest = max(placeholders, default=0)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "ARGUMENTS":
            return arguments
        position = int(key)
        if position > largest or position > len(tokens):
            return ""
        if position == largest:
            return " ".join(tokens[position - 1 :])
        return tokens[position - 1]

    if _PLACEHOLDER.search(command.template):
        return _PLACEHOLDER.sub(replace, command.template)
    return command.template + (f"\n\n{arguments}" if arguments else "")


def find_command(
    commands: list[PromptCommand], name: str, *, builtins: set[str] | None = None
) -> PromptCommand | None:
    """Return a custom command unless an embedded command owns the same slash name."""
    normalized = name.lstrip("/")
    if builtins is not None and normalized in {item.lstrip("/") for item in builtins}:
        return None
    return next((command for command in commands if command.name == normalized), None)


__all__ = [
    "PromptCommand",
    "discover_commands",
    "expand_command",
    "find_command",
    "split_arguments",
]


# End of module.
