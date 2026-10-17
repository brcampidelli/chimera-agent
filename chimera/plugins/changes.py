"""What changed between two versions of an installed plugin, and whether the change is governed.

An update re-reads the source. Whatever it finds, a plugin that changed is never left switched on:
the owner switched on the files they were shown, and these are other files. The only question is
which state it returns to:

* ``held`` — the change touches something that runs or decides: the manifest, hooks, MCP or LSP
  servers, monitors, ``bin/``, workflows, settings, any file that is not Markdown (a server's own
  code), or a ``tools``/``allowed-tools``/``model`` line in a Markdown frontmatter. Shown with the
  diff, like an MCP server whose manifest changed (``chimera.integrations.mcp_pins``);
* ``pending`` — only the prose of Markdown files changed. Still a new decision: the precedent is the
  skill bundle, whose update "lands pending like any install: new instructions from a stranger are a
  new decision, whatever the old ones were" (``chimera.skills.bundles.check_update``).

Both stop the plugin from reaching any prompt until the owner switches it on again.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from chimera.plugins.model import Capability, Component

#: Files whose change is governed whatever their extension says.
_GOVERNED_FILES = frozenset({
    ".claude-plugin/plugin.json", "hooks/hooks.json", ".mcp.json", ".lsp.json",
    "monitors/monitors.json", "settings.json",
})
_GOVERNED_DIRS = ("bin/", "hooks/", "monitors/", "workflows/")
_TEXT_SUFFIXES = (".md", ".markdown", ".txt")
_FRONT = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---", re.DOTALL)
_DECIDING_KEYS = re.compile(r"^\s*(tools|allowed-tools|allowed_tools|model|disallowedTools)\s*:", re.IGNORECASE)


def file_digests(root: Path, files: Iterable[str]) -> dict[str, str]:
    """``{relative path: sha256}`` of the installed files — the content a later update compares."""
    out: dict[str, str] = {}
    for rel in sorted(files):
        out[rel] = hashlib.sha256((root / Path(*rel.split("/"))).read_bytes()).hexdigest()
    return out


def tree_digest(files: Mapping[str, str]) -> str:
    """One digest for the whole tree: sha256 over sorted ``path\\0digest`` lines."""
    joined = "\n".join(f"{rel}\0{digest}" for rel, digest in sorted(files.items()))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def component_rows(components: Iterable[Component]) -> list[dict[str, Any]]:
    return [
        {"kind": c.kind.value, "name": c.name, "path": c.path, "capability": c.capability.value,
         "detail": list(c.detail)}
        for c in components
    ]


def _deciding_lines(root: Path, rel: str) -> list[str]:
    path = root / Path(*rel.split("/"))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    match = _FRONT.match(text)
    if match is None:
        return []
    return [line.strip() for line in match.group(1).splitlines() if _DECIDING_KEYS.match(line)]


def _governed_file(rel: str) -> bool:
    return rel in _GOVERNED_FILES or rel.startswith(_GOVERNED_DIRS) or not rel.lower().endswith(_TEXT_SUFFIXES)


def diff(old_root: Path, old_files: Mapping[str, str], old_components: list[dict[str, Any]],
         new_root: Path, new_files: Mapping[str, str], new_components: list[dict[str, Any]]) -> dict[str, Any]:
    """The change, as data a screen can show, with ``governed`` and the ``reasons`` it is governed."""
    added = sorted(set(new_files) - set(old_files))
    removed = sorted(set(old_files) - set(new_files))
    changed = sorted(rel for rel in set(old_files) & set(new_files) if old_files[rel] != new_files[rel])
    reasons: list[str] = []
    for rel in (*added, *removed, *changed):
        if _governed_file(rel):
            reasons.append(f"file {rel}")
    for rel in changed:
        if not _governed_file(rel) and _deciding_lines(old_root, rel) != _deciding_lines(new_root, rel):
            reasons.append(f"tools or model line in {rel}")

    def key(row: dict[str, Any]) -> tuple[str, str]:
        return str(row["kind"]), str(row["name"])

    old_by, new_by = {key(r): r for r in old_components}, {key(r): r for r in new_components}
    comp_added = [new_by[k] for k in sorted(set(new_by) - set(old_by))]
    comp_removed = [old_by[k] for k in sorted(set(old_by) - set(new_by))]
    comp_changed: list[dict[str, Any]] = [
        {"kind": k[0], "name": k[1], "before": old_by[k], "after": new_by[k]}
        for k in sorted(set(old_by) & set(new_by)) if old_by[k] != new_by[k]
    ]
    for row in (*comp_added, *comp_removed):
        if row["capability"] != Capability.TEXT.value:
            reasons.append(f"{row['kind']} {row['name']}")
    for row in comp_changed:
        if Capability.TEXT.value not in (row["before"]["capability"], row["after"]["capability"]) or \
                row["before"]["capability"] != row["after"]["capability"]:
            reasons.append(f"{row['kind']} {row['name']}")
    return {
        "files_added": added,
        "files_removed": removed,
        "files_changed": changed,
        "components_added": comp_added,
        "components_removed": comp_removed,
        "components_changed": comp_changed,
        "governed": bool(reasons),
        "reasons": sorted(set(reasons)),
    }


__all__ = ["component_rows", "diff", "file_digests", "tree_digest"]
