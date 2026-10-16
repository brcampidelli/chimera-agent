"""Owner-declared, read-only folders that a foreground agent may consult.

The owner lists folders outside the project in ``<CHIMERA_HOME>/references.json`` —
``{"alias": {"path": "...", "description": "..."}}`` — and the read tools of an ATTENDED surface
(``chat``, ``assist``, ``tui``, an attended ``solve``, the desktop's Code turn) accept
``@alias/path`` inside them. Three properties carry the design, and each is a test:

- **Read-only.** Only ``read_file``, ``list_dir``, ``grep`` and ``glob`` resolve an alias. The
  writers are bound too, but only so a known ``@alias`` is REFUSED by name instead of falling
  through to a workspace file that happens to share the spelling.
- **Project content, not external content.** A reference read is recorded exactly as a workspace
  read is. An earlier draft marked the four read tools ``untrusted_output`` wholesale; with one
  reference configured, every read of the agent's OWN project then counted as an external fetch in
  the taint ledger, and the governance of a person who only read their code changed without a word.
- **Opt-in per registry.** Nothing here is global: a registry that is never passed to
  :func:`bind_references` (bots, cron, ``scenarios``, the VPS) has no alias to resolve, and there a
  path spelled ``@x`` is the ordinary workspace file ``@x``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from chimera.core.own_files import contains, protected_reason, same_file, within
from chimera.tools.workspace import PathEscapesWorkspaceError, refuse_own_env_read

_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}\Z")

#: The terminal surfaces ``build_right_hand`` binds references on. An allowlist, not a denylist:
#: ``scenarios`` replays a script with nobody watching, and a surface added tomorrow should have to
#: be named here to receive the owner's folders rather than receive them by forgetting to be
#: excluded. The desktop's Code turn binds in ``code_api.build_agent`` (never for a share-link
#: guest), and ``solve`` binds only when somebody is at the terminal.
REFERENCE_SURFACES = frozenset({"chat", "assist", "tui"})

_READ_VERBS = frozenset({"read", "list", "search"})
_BOUND_TOOLS = frozenset(
    {"read_file", "list_dir", "grep", "glob", "write_file", "edit_file", "apply_patch", "edit_batch"}
)


def _user_home() -> Path | None:
    try:
        return Path.home()
    except (RuntimeError, OSError):  # no HOME/USERPROFILE: nothing to compare against
        return None


def _refusal(candidate: Path, home: Path) -> str | None:
    """Why ``candidate`` (resolved, existing) cannot be a reference root, or None.

    Compared by file identity (``same_file``/``within``), so a Windows 8.3 name, a UNC spelling of
    the same folder or a different case cannot slip the user's home or the data folder past a
    string comparison.
    """
    if not candidate.is_dir():
        return "is not a directory"
    if candidate == Path(candidate.anchor):
        return "cannot expose a filesystem root"
    user_home = _user_home()
    # The home itself, or anything holding it (``/home``, ``C:\\Users``): every key, token and
    # browser profile the person has would become readable under one alias.
    if user_home is not None and (same_file(candidate, user_home) or contains(candidate, user_home)):
        return "cannot expose the user's home folder"
    resolved_home = Path(home).expanduser().resolve()
    if (
        same_file(candidate, resolved_home)
        or protected_reason(candidate, resolved_home) is not None
        or contains(candidate, resolved_home)
    ):
        return "cannot expose Chimera's own data folder"
    return None


def _normalize(home: Path, alias: Any, item: Any) -> dict[str, str]:
    """One declaration, validated; raises ``ValueError`` with the reason it was refused."""
    if not isinstance(alias, str) or not _ALIAS.fullmatch(alias) or not isinstance(item, dict):
        raise ValueError("each reference needs a valid alias and an object")
    path, description = item.get("path"), item.get("description", "")
    if not isinstance(path, str) or not path.strip() or not isinstance(description, str):
        raise ValueError(f"reference @{alias} needs a path and optional text description")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"reference @{alias} must use an absolute local path")
    try:
        candidate = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"reference @{alias} does not exist") from exc
    why = _refusal(candidate, home)
    if why is not None:
        raise ValueError(f"reference @{alias} {why}")
    return {"path": str(candidate), "description": description.strip()}


def load_references(home: Path) -> dict[str, dict[str, str]]:
    """Load only well-formed local directory declarations from the owner's data folder.

    Validated again on every load, not only when saved: the file is the owner's, but it can be
    edited by hand, and a folder can be replaced by a link after the save. A bad entry is skipped,
    never repaired — the others stay usable.
    """
    file = Path(home) / "references.json"
    if not file.exists():
        return {}
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for alias, item in raw.items():
        try:
            result[alias] = _normalize(home, alias, item)
        except ValueError:
            continue
    return result


def validate_reference_payload(home: Path, values: Any) -> dict[str, dict[str, str]]:
    """Validate declarations without writing, so API errors cannot partially replace owner state."""
    if not isinstance(values, dict):
        raise ValueError("references must be an object keyed by alias")
    return {alias: _normalize(home, alias, item) for alias, item in values.items()}


def save_references(home: Path, values: Any) -> dict[str, dict[str, str]]:
    """Validate and atomically replace the owner-only declaration, never an agent-editable file."""
    normalized = validate_reference_payload(home, values)
    target = Path(home) / "references.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return normalized


def render_reference_guidance(home: Path) -> str:
    """Announce described references only; the path is informational, never a new permission."""
    entries = [
        f"- @{alias}: {item['path']} — {item['description']}"
        for alias, item in load_references(home).items()
        if item["description"]
    ]
    if not entries:
        return ""
    return "Owner-declared read-only references (use @alias/path to consult):\n" + "\n".join(entries)


def references_prompt(home: Path) -> str:
    """Prompt fragment for the foreground surfaces that can actually resolve references."""
    references = render_reference_guidance(home)
    if not references:
        return ""
    return (
        references
        + "\nOnly consult these folders with read_file, list_dir, grep, or glob; "
        + "they cannot be edited or used as a shell working directory."
    )


def foreground_instructions(home: Path) -> str:
    """Combine the owner's identity with reference directions on desktop and terminal agents."""
    from chimera.core.instructions import for_home

    identity = for_home(home)
    guidance = references_prompt(home)
    return "\n\n".join(part for part in (identity, guidance) if part)


def bind_references(registry: Any, home: Path) -> None:
    """Give only this explicitly foreground registry's file tools the declared roots."""
    roots = {alias: Path(item["path"]) for alias, item in load_references(home).items()}
    if not roots:
        return
    resolved_home = Path(home).expanduser().resolve()
    for registered in registry.tools():
        # The desktop binds after assembly, so a tool may already be inside the governance and
        # taint wrappers — which carry the SAME name as the tool they wrap. Bind the innermost,
        # executable tool: stopping at the first matching name bound the outer proxy, and the
        # advertised @alias then reached an unbound reader that answered "file not found".
        # Nothing about trust is set here — the reads keep the workspace's status (module docstring).
        tool: Any = registered
        seen: set[int] = {id(tool)}
        while (inner := getattr(tool, "inner", None)) is not None and id(inner) not in seen:
            seen.add(id(inner))
            tool = inner
        if getattr(tool, "name", "") in _BOUND_TOOLS:
            tool.reference_roots = roots
            tool.chimera_home = resolved_home


def _split(tool: Any, path: str) -> tuple[str, Path, str] | None:
    """``(alias, root, suffix)`` when ``path`` names a reference THIS tool has, else None.

    Only a bound tool with that alias reads ``@x`` as a reference; anywhere else ``@x`` is an
    ordinary file name, which the workspace may well contain and the writers must still reach.
    """
    if not path.startswith("@"):
        return None
    alias, separator, suffix = path[1:].partition("/")
    roots = getattr(tool, "reference_roots", None) or {}
    if alias not in roots:
        return None
    if not separator:
        # `@docs` alone: the root itself. A listing of the alias is the first thing an agent tries.
        suffix = "."
    return alias, Path(roots[alias]).resolve(strict=True), suffix


def _contain(tool: Any, alias: str, root: Path, suffix: str) -> Path:
    candidate = (root / suffix).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathEscapesWorkspaceError(f"path {suffix!r} escapes reference @{alias}")
    configured_home = getattr(tool, "chimera_home", None)
    if configured_home is not None:
        home = Path(configured_home)
        if within(candidate, home) or contains(candidate, home):
            raise PathEscapesWorkspaceError("reference cannot expose Chimera's own data folder")
    return candidate


def resolve_reference(tool: Any, path: str, *, verb: str) -> Path | None:
    """Resolve an ``@alias`` path within its configured root; never grant write/edit access."""
    split = _split(tool, path)
    if split is None:
        return None
    alias, root, suffix = split
    if verb not in _READ_VERBS:
        raise PathEscapesWorkspaceError(
            f"@{alias} is an owner-declared read-only reference; writes stay in the workspace"
        )
    candidate = _contain(tool, alias, root, suffix)
    # Reference roots are read-only, but that does not make Chimera's own files readable.
    # Apply the same `.env` owner-boundary as ordinary workspace reads.
    refuse_own_env_read(candidate, forced=bool(getattr(tool, "hide_own_env", False)))
    return candidate


def containment_root(tool: Any, path: str) -> Path:
    """The folder a listing or search of ``path`` must not leave: the alias root, or the workspace.

    Not the listed folder itself — a link from ``src/`` to ``docs/`` inside the same project stays
    visible; only one that leaves the project (or the reference) is dropped.
    """
    split = _split(tool, path)
    if split is not None:
        return split[1]
    return Path(tool.workspace).resolve()


def reference_glob(tool: Any, pattern: str) -> tuple[str, Path, str] | None:
    """Return ``(alias, root, inner_pattern)`` for a named glob, or None for a workspace glob.

    A glob is discovery, not permission: every returned path is resolved and checked against the
    same root again by the caller, because a symlink may point outside even when its spelling does
    not.
    """
    split = _split(tool, pattern)
    if split is None:
        return None
    alias, root, suffix = split
    _contain(tool, alias, root, suffix)
    return alias, root, suffix
