"""The sections of a catalogue screen, computed on this machine with the reason for each item (plan M4).

They replace "most installed", which Chimera cannot know. Every section rests on a fact somebody can
check, and a section with nothing in it is absent — never shown empty, never padded:

* ``for_you`` — matched against what is already on this machine: the MCP servers configured, the
  skills installed, the dependencies named by the registered projects' manifests. Each item says
  which one, in words. With no such signal the section does not exist. Nothing leaves the machine.
* ``no_setup`` — installable, no secret or account, nothing that executes, by the catalogue's record.
* ``library`` — written for the Chimera library rather than copied from an upstream author.
* ``new`` — first added within :data:`WINDOW_DAYS` of the **pinned commit's** date.
* ``updated`` — changed again at least a day after it was added, within :data:`WINDOW_DAYS` of the
  pinned commit's date.

"New" and "Updated" are measured from the pin, not from today: the same Chimera shows the same
sections on every machine on every day, and an old install does not quietly empty them.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chimera.catalogs.model import (
    CatalogItemOut,
    CatalogOut,
    CatalogSectionOut,
    CatalogSuggestionOut,
    SectionId,
)
from chimera.telemetry import get_logger

log = get_logger(__name__)

WINDOW_DAYS = 30
#: An edit on the day an item was added is part of adding it, not an update.
_MIN_UPDATE_GAP = timedelta(days=1)
#: Projects whose manifests are read per request, and the size past which a manifest is skipped.
_MAX_PROJECTS = 50
_MAX_MANIFEST_BYTES = 256 * 1024
_MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt")

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
#: A package called ``stripe-node`` or ``supabase-js`` is the ``stripe`` / ``supabase`` SDK.
_SDK_SUFFIXES = ("-js", "-node", "-python", "-py", "-sdk", "-client", "-api")


@dataclass(frozen=True)
class Signal:
    """Something already on this machine that makes an item relevant, and how to say so."""

    token: str
    reason: str
    #: The catalogue kind this signal IS (an MCP server is a connector already configured), so the
    #: item it names is not suggested as though it were missing.
    covers_kind: str | None = None


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _day(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")


def package_tokens(name: str) -> set[str]:
    """The names a dependency is known by: ``@supabase/supabase-js`` -> supabase, supabase-js."""
    text = name.strip().lower()
    out: set[str] = set()
    if text.startswith("@") and "/" in text:
        scope, _, text = text[1:].partition("/")
        out.add(scope)
    for part in (text, *(text[: -len(s)] for s in _SDK_SUFFIXES if text.endswith(s))):
        if part:
            out.add(part)
    return {t for t in out if _TOKEN_SPLIT.sub("", t)}


def _bounded_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            return None
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def _manifest_deps(folder: Path) -> list[tuple[str, str]]:
    """(dependency, manifest file) pairs from a project's top-level manifests. Never raises."""
    found: list[tuple[str, str]] = []
    text = _bounded_text(folder / "package.json")
    if text is not None:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                section = data.get(key)
                if isinstance(section, dict):
                    found.extend((str(dep), "package.json") for dep in section)
    text = _bounded_text(folder / "pyproject.toml")
    if text is not None:
        try:
            project = tomllib.loads(text).get("project", {})
        except tomllib.TOMLDecodeError:
            project = {}
        deps = project.get("dependencies", []) if isinstance(project, dict) else []
        for spec in deps if isinstance(deps, list) else []:
            match = _REQ_NAME.match(str(spec))
            if match:
                found.append((match.group(1), "pyproject.toml"))
    text = _bounded_text(folder / "requirements.txt")
    if text is not None:
        for line in text.splitlines():
            if line.strip().startswith(("#", "-")):
                continue
            match = _REQ_NAME.match(line)
            if match:
                found.append((match.group(1), "requirements.txt"))
    return found


def project_signals(projects: Iterable[tuple[str, Path]]) -> list[Signal]:
    signals: list[Signal] = []
    for label, folder in list(projects)[:_MAX_PROJECTS]:
        for dep, manifest in _manifest_deps(folder):
            for token in package_tokens(dep):
                signals.append(Signal(token, f"your project “{label}” uses {dep} ({manifest})"))
    return signals


def local_signals(home: Path) -> list[Signal]:
    """What this machine already has: configured MCP servers, installed skills, project manifests.

    Each source is read on its own and a failure in one is logged and skipped — "For you" losing a
    signal is a smaller loss than the catalogue failing to load.
    """
    home = Path(home)
    signals: list[Signal] = []
    try:
        from chimera.integrations.mcp_config import load_servers

        for server in load_servers(home / "mcp.json"):
            for token in package_tokens(server.name):
                signals.append(Signal(token, f"the MCP server “{server.name}” is configured here",
                                      covers_kind="connectors"))
    except Exception as exc:  # noqa: BLE001
        log.info("for-you: MCP servers not read: %s", exc)
    try:
        from chimera.skills.bundles import installed

        for bundle in installed(home):
            signals.append(Signal(bundle.name.lower(), f"the skill “{bundle.name}” is installed here",
                                  covers_kind="skills"))
    except Exception as exc:  # noqa: BLE001
        log.info("for-you: installed skills not read: %s", exc)
    try:
        from chimera.core.code_projects import CodeProjectRegistry

        rows = CodeProjectRegistry(home / "code_projects.json").entries()
        projects = [(row.alias or Path(row.path).name, Path(row.path)) for row in rows if not row.hidden]
        signals.extend(project_signals(projects))
    except Exception as exc:  # noqa: BLE001
        log.info("for-you: registered projects not read: %s", exc)
    return signals


def _for_you(items: list[CatalogItemOut], signals: list[Signal]) -> list[CatalogSuggestionOut]:
    by_token: dict[str, list[Signal]] = {}
    for signal in signals:
        by_token.setdefault(signal.token, []).append(signal)
    out: list[CatalogSuggestionOut] = []
    for item in items:
        if not item.installable:
            continue
        matches = by_token.get(item.id.lower(), [])
        if any(s.covers_kind == item.kind for s in matches):
            continue  # it is already here; suggesting it would read as "you are missing this"
        if matches:
            reasons = list(dict.fromkeys(s.reason for s in matches))
            extra = f" (and {len(reasons) - 1} more)" if len(reasons) > 1 else ""
            out.append(CatalogSuggestionOut(id=item.id, reason=f"Because {reasons[0]}{extra}."))
    return out


def _windowed(items: list[CatalogItemOut], pin_at: datetime | None,
              ) -> tuple[list[CatalogSuggestionOut], list[CatalogSuggestionOut]]:
    if pin_at is None:
        return [], []
    window = timedelta(days=WINDOW_DAYS)
    new: list[CatalogSuggestionOut] = []
    updated: list[CatalogSuggestionOut] = []
    for item in items:
        added, changed = _parse(item.added_at), _parse(item.updated_at)
        if added is not None and timedelta(0) <= pin_at - added <= window:
            new.append(CatalogSuggestionOut(
                id=item.id, reason=f"Added on {_day(added)}, within {WINDOW_DAYS} days of this "
                                   f"catalogue's pinned commit ({_day(pin_at)})."))
        if (added is not None and changed is not None and changed - added >= _MIN_UPDATE_GAP
                and timedelta(0) <= pin_at - changed <= window):
            updated.append(CatalogSuggestionOut(
                id=item.id, reason=f"Changed on {_day(changed)} (added {_day(added)}), within "
                                   f"{WINDOW_DAYS} days of the pinned commit ({_day(pin_at)})."))
    return new, updated


def compute_sections(catalog: CatalogOut, signals: list[Signal]) -> list[CatalogSectionOut]:
    """The non-empty sections for ``catalog``, in the order the screen shows them."""
    items = catalog.items
    pin_at = _parse(catalog.committed_at)
    new, updated = _windowed(items, pin_at)
    candidates: list[tuple[SectionId, str, str, list[CatalogSuggestionOut]]] = [
        ("for_you", "For you",
         "Matched against what is already on this machine; nothing was sent anywhere.",
         _for_you(items, signals)),
        ("no_setup", "Works without setup",
         "Installable with no account or secret, and nothing in it executes, by the catalogue's record.",
         [CatalogSuggestionOut(id=i.id, reason="No account, no secret, and nothing that executes.")
          for i in items if i.installable and i.setup_free is True]),
        ("library", "From the Chimera library",
         "Written for the Chimera library itself, not copied from an upstream author.",
         [CatalogSuggestionOut(id=i.id, reason="Written for the Chimera library.")
          for i in items if i.own]),
        ("new", "New",
         f"First added within {WINDOW_DAYS} days of the commit this Chimera pins.", new),
        ("updated", "Updated",
         f"Changed again within {WINDOW_DAYS} days of the commit this Chimera pins.", updated),
    ]
    return [CatalogSectionOut(id=sid, title=title, reason=reason, items=rows)
            for sid, title, reason, rows in candidates if rows]


__all__ = [
    "WINDOW_DAYS",
    "Signal",
    "compute_sections",
    "local_signals",
    "package_tokens",
    "project_signals",
]
