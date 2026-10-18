"""Marketplaces the owner added: where ``chimera plugin install <plugin>@<marketplace>`` looks.

Stored in ``<CHIMERA_HOME>/plugins/marketplaces.json`` as what was read when the owner added it — the
marketplace's name, where it lives, and each entry's source as the classifier saw it — so listing
needs no network and installing resolves exactly the entry the owner was shown.

Two kinds, the same v1 rule as plugins: a **local** folder, or a GitHub repository pinned by a commit
SHA, of which only ``.claude-plugin/marketplace.json`` is fetched. An entry with a relative source in
a GitHub marketplace installs as that repository at that SHA, from the entry's folder.

No install counts are stored or shown: Chimera is local and has no central telemetry, and a number
that is not measured is not shown (ESTUDO-01 §6.4).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chimera.core.filelock import atomic_write_text
from chimera.plugins.fetch import (
    FetchError,
    SourceSpec,
    archive_spec,
    github_spec,
    local_spec,
    refusal_for,
)
from chimera.plugins.marketplace import MARKETPLACE, parse_marketplace, read_marketplace
from chimera.plugins.model import MarketplaceReport, Severity, SourceKind
from chimera.plugins.registry import PluginError, plugins_root

STORE = "marketplaces.json"


def _path(home: Path) -> Path:
    return plugins_root(home) / STORE


def load(home: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _entries(report: MarketplaceReport) -> list[dict[str, Any]]:
    out = []
    for entry in report.plugins:
        if not entry.name:
            continue
        out.append({
            "name": entry.name,
            "description": entry.description or "",
            "version": entry.version or "",
            "category": entry.category or "",
            "tags": list(entry.tags),
            "source": {k: (v.value if hasattr(v, "value") else v) for k, v in asdict(entry.source).items()},
            "installable": entry.installable,
            "refusal": refusal_for(entry.source) or ("" if entry.installable else "the entry has errors"),
        })
    return out


def _fetch_github_marketplace(repo: str, sha: str) -> MarketplaceReport:
    from chimera.skills import bundles

    github_spec(repo, sha)  # same validation as a plugin source
    try:
        body = bundles._get(f"https://raw.githubusercontent.com/{repo}/{sha}/{MARKETPLACE}", accept="*/*")
    except bundles.BundleError as exc:
        raise PluginError(f"could not read {MARKETPLACE} from {repo}@{sha[:12]}: {exc}") from exc
    try:
        data = json.loads(body.decode("utf-8-sig"))
    except ValueError as exc:
        raise PluginError(f"{MARKETPLACE} is not JSON") from exc
    return parse_marketplace(data, anthropic_origin=repo.lower().startswith("anthropics/"))


def add(home: Path, source: str) -> dict[str, Any]:
    """Read a marketplace from a local folder or ``github:owner/repo@<sha>`` and remember it."""
    text = source.strip()
    try:
        if text.lower().startswith("github:"):
            repo, _, sha = text[len("github:"):].partition("@")
            report = _fetch_github_marketplace(repo, sha)
            where: dict[str, Any] = {"kind": "github", "repo": repo, "sha": sha}
        else:
            folder = Path(local_spec(text).path)
            report = read_marketplace(folder)
            where = {"kind": "local", "path": str(folder)}
    except FetchError as exc:
        raise PluginError(str(exc)) from exc
    if not report.ok or report.name is None:
        errors = [p for p in report.problems if p.severity is Severity.ERROR]
        detail = "; ".join(f"{p.where}: {p.message}" for p in errors[:3])
        raise PluginError(f"the marketplace does not validate: {detail}")
    record = {
        "name": report.name,
        **where,
        "description": report.description or "",
        "owner": report.owner.name if report.owner else "",
        "added_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "plugins": _entries(report),
    }
    kept = [item for item in load(home) if item.get("name") != report.name]
    atomic_write_text(_path(home), json.dumps([*kept, record], indent=2, ensure_ascii=False) + "\n")
    return record


def remove(home: Path, name: str) -> bool:
    """Forget an added marketplace. Plugins installed from it stay installed, with their own pins."""
    found = load(home)
    kept = [item for item in found if item.get("name") != name]
    if len(kept) == len(found):
        return False
    atomic_write_text(_path(home), json.dumps(kept, indent=2, ensure_ascii=False) + "\n")
    return True


def resolve(home: Path, plugin: str, marketplace: str) -> SourceSpec:
    """The pinned source of ``plugin`` in the added marketplace ``marketplace``, or why it has none."""
    found = next((item for item in load(home) if item.get("name") == marketplace), None)
    if found is None:
        raise PluginError(f"no marketplace named {marketplace!r} — add it with `chimera plugin marketplace add`")
    entry = next((e for e in found.get("plugins", []) if e.get("name") == plugin), None)
    if entry is None:
        raise PluginError(f"{marketplace} lists no plugin named {plugin!r}")
    if not entry.get("installable"):
        raise PluginError(f"{plugin}@{marketplace} is not installable: {entry.get('refusal') or 'refused'}")
    source = entry.get("source") or {}
    kind = SourceKind(str(source.get("kind")))
    try:
        if kind is SourceKind.GITHUB:
            return github_spec(str(source["repo"]), str(source["sha"]))
        if kind is SourceKind.GIT_SUBDIR:
            # Never without its folder: a git-subdir record that lost ``path`` would widen into the
            # whole repository, which is not what the owner was shown.
            inner = str(source.get("path") or "")
            if not inner:
                raise PluginError(f"{plugin}@{marketplace}: a git-subdir source has no folder")
            return github_spec(str(source["repo"]), str(source["sha"]), inner)
        if kind is SourceKind.ARCHIVE:
            return archive_spec(str(source["url"]), str(source["sha256"]))
        if kind is SourceKind.LOCAL:
            inner = str(source.get("path") or "")
            if found.get("kind") == "github":
                return github_spec(str(found["repo"]), str(found["sha"]), inner)
            return local_spec(Path(str(found["path"])) / inner)
    except (FetchError, KeyError) as exc:
        raise PluginError(f"{plugin}@{marketplace}: {exc}") from exc
    raise PluginError(f"{plugin}@{marketplace}: {kind.value} sources are not installable in v1")


__all__ = ["STORE", "add", "load", "remove", "resolve"]
