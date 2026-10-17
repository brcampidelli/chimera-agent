"""Installed plugins: what is on disk, where it came from, and whether the owner switched it on.

Layout — ``<CHIMERA_HOME>/plugins/<name>/``:

* ``tree/`` — the plugin's files exactly as fetched (:mod:`chimera.plugins.fetch`);
* ``plugin.record.json`` — the record: version, source kind and pinned ref, sha256 of the tree and of
  every file, licence, installed_at, status, origin marketplace, capability summary, component list.

Written by the server's own code (the CLI, later the app's routes), never by an agent tool: the data
folder is the owner's (``chimera.tools.workspace.refuse_own_files``). Like a skill bundle, the record
lives beside the files rather than in a central index, so the directory is the truth.

**The rules this module enforces (owner decisions, 17/10/2026):**

* every plugin installs ``pending`` — ``defaultEnabled`` is ignored; only the owner switches one on
  (:func:`enable`), and nothing reaches a prompt, the commands or the agents before that;
* hooks, monitors, ``bin/``, workflows and an exec ``settings.json`` are stored and listed as INERT
  suggestions (:func:`inert_suggestions`), never loaded or run;
* an MCP server is never added by installing. Enabling adds only the servers the owner names, each
  through the existing MCP add path, whose manifest pin holds it on any later change
  (:mod:`chimera.plugins.mcp`);
* ``integration.use`` ``plugin:<name>`` in the deployment policy refuses install and enable, and
  hides a denied plugin's components everywhere (:mod:`chimera.plugins.mapping`);
* an update that changes anything leaves the plugin ``held`` or ``pending`` with the diff stored
  (:mod:`chimera.plugins.changes`).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Collection
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chimera.core.filelock import atomic_write_text
from chimera.plugins import changes
from chimera.plugins.fetch import FetchError, SourceSpec, fetch, parse_source
from chimera.plugins.manifest import read_plugin
from chimera.plugins.model import Capability, PluginReport, Severity, SourceKind
from chimera.skills.bundles import _swap_into, is_bundle_name, recover_aside
from chimera.telemetry import get_logger

_log = get_logger("plugins.registry")

PLUGINS_DIR = "plugins"
TREE = "tree"
RECORD = "plugin.record.json"
#: ``pending`` nobody decided yet; ``active`` switched on by the owner; ``inactive`` switched off on
#: purpose; ``held`` an update changed something governed and waits for the owner, diff attached.
STATUSES = ("pending", "active", "inactive", "held")
#: Component kinds that are kept on disk for the owner to read and never loaded.
INERT_KINDS = frozenset({"hook", "monitor", "executable", "workflow", "lsp_server", "settings"})


class PluginError(RuntimeError):
    """An install or a switch refused. The message is written for a person."""


class PluginExists(PluginError):
    """A plugin by that name is installed; ``update`` or ``replace`` it instead."""


class PluginDenied(PluginError):
    """The owner's deployment policy denies ``plugin:<name>``."""


@dataclass
class InstalledPlugin:
    name: str
    version: str = ""
    description: str = ""
    #: ``local`` / ``github`` / ``archive``, the spec ``update`` re-reads, and what pins it (a commit
    #: SHA or the archive's sha256; empty for a local folder, which ``tree_sha256`` pins instead).
    source_kind: str = ""
    source: str = ""
    ref: str = ""
    tree_sha256: str = ""
    license: str = ""
    installed_at: str = ""
    updated_at: str = ""
    status: str = "pending"
    #: Written by :func:`enable` and nothing else: an ``active`` without it is read as pending.
    switched_on_at: str = ""
    marketplace: str = ""
    from_anthropic: bool = False
    capabilities: dict[str, int] = field(default_factory=dict)
    components: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    held_diff: dict[str, Any] = field(default_factory=dict)
    #: MCP server names (as configured in ``mcp.json``) this plugin added at the owner's request.
    mcp_added: list[str] = field(default_factory=list)
    #: Always ``tainted``: a plugin is somebody else's text and somebody else's code.
    provenance: str = "tainted"
    #: Read, never stored: whether the deployment policy denies it right now.
    denied: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("denied", None)
        return data

    @property
    def is_on(self) -> bool:
        return self.status == "active" and bool(self.switched_on_at) and not self.denied

    def by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [row for row in self.components if row.get("kind") == kind]


def plugins_root(home: Path) -> Path:
    return Path(home) / PLUGINS_DIR


def plugin_dir(home: Path, name: str) -> Path:
    """The directory of ``name``. The bundle rule decides what may become a path (``C:`` may not)."""
    if not is_bundle_name(name):
        raise PluginError(f"{name!r} is not a name a plugin can be installed under")
    return plugins_root(home) / name


def tree_of(home: Path, name: str) -> Path:
    return plugin_dir(home, name) / TREE


def _denied(name: str) -> bool:
    from chimera.deployment_policy import integration_denied

    return integration_denied(f"plugin:{name}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load(meta: Path) -> InstalledPlugin | None:
    try:
        raw = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.warning("unreadable plugin record %s: %s", meta.parent.name, type(exc).__name__)
        return None
    if not isinstance(raw, dict):
        return None
    known = {f.name for f in fields(InstalledPlugin)} - {"denied"}
    record = InstalledPlugin(**{k: v for k, v in raw.items() if k in known})
    if record.status == "active" and not record.switched_on_at:
        record.status = "pending"
    record.denied = _denied(record.name)
    return record


def _save(home: Path, record: InstalledPlugin) -> None:
    meta = plugin_dir(home, record.name) / RECORD
    atomic_write_text(meta, json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n")


def installed(home: Path) -> list[InstalledPlugin]:
    """Every installed plugin read from disk, denied ones included and marked (``denied``)."""
    root = plugins_root(home)
    if not root.is_dir():
        return []
    out: list[InstalledPlugin] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.endswith(".partial") or not is_bundle_name(child.name):
            continue
        meta = child / RECORD
        record = _load(meta) if meta.is_file() else None
        if record is not None and record.name == child.name:
            out.append(record)
    return out


def get(home: Path, name: str) -> InstalledPlugin | None:
    if not is_bundle_name(name):
        return None
    meta = plugins_root(home) / name / RECORD
    record = _load(meta) if meta.is_file() else None
    return record if record is not None and record.name == name else None


def active(home: Path, scope: Collection[str] | None = None) -> list[InstalledPlugin]:
    """Only the plugins the owner switched on and the policy allows, narrowed to ``scope`` if given."""
    return [p for p in installed(home) if p.is_on and (scope is None or p.name in scope)]


# --- install ---------------------------------------------------------------------------------------


def _errors(report: PluginReport) -> str:
    found = [p for p in report.problems if p.severity is Severity.ERROR]
    return "; ".join(f"{p.where}: {p.message}" for p in found[:3]) or "the plugin has no name"


def _record_for(report: PluginReport, spec: SourceSpec, tree: Path, files: list[str],
                marketplace: str) -> InstalledPlugin:
    assert report.manifest is not None
    digests = changes.file_digests(tree, files)
    caps = report.capabilities
    return InstalledPlugin(
        name=report.manifest.name,
        version=report.manifest.version or "",
        description=report.manifest.description or "",
        source_kind=spec.kind.value,
        source=spec.describe(),
        ref=spec.ref,
        tree_sha256=changes.tree_digest(digests),
        license=report.manifest.license or "",
        installed_at=_now(),
        status="pending",
        marketplace=marketplace,
        from_anthropic=spec.kind is SourceKind.GITHUB and spec.repo.lower().startswith("anthropics/"),
        capabilities={"text": caps.text, "network": caps.network, "process": caps.process, "exec": caps.exec},
        components=changes.component_rows(report.components),
        files=digests,
    )


def _stage(home: Path, spec: SourceSpec, fallback_name: str | None) -> tuple[Path, PluginReport, list[str]]:
    """Fetch into a fresh ``*.partial`` folder and read it. The caller owns removing the folder."""
    root = plugins_root(home)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=root, suffix=".partial"))
    try:
        files = fetch(spec, staging / TREE)
    except FetchError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise PluginError(str(exc)) from exc
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    report = read_plugin(staging / TREE, fallback_name=fallback_name or spec.default_name())
    return staging, report, files


def _check_installable(report: PluginReport) -> str:
    if not report.ok or report.manifest is None:
        raise PluginError(f"the plugin does not validate: {_errors(report)}")
    name = report.manifest.name
    if not is_bundle_name(name):
        raise PluginError(
            f"{name!r} is a valid Claude Code name but not one Chimera installs under "
            "(lowercase letters, digits, '-' and '_', 2-64 characters)"
        )
    if _denied(name):
        raise PluginDenied(f"plugin:{name} is denied by the deployment policy")
    return name


def install(spec: SourceSpec | str, home: Path, *, marketplace: str = "",
            fallback_name: str | None = None) -> InstalledPlugin:
    """Fetch, validate and record a plugin, ``pending``. Runs nothing and switches nothing on."""
    if isinstance(spec, str):
        try:
            spec = parse_source(spec)
        except FetchError as exc:
            raise PluginError(str(exc)) from exc
    staging, report, files = _stage(home, spec, fallback_name)
    try:
        name = _check_installable(report)
        target = plugin_dir(home, name)
        recover_aside(target)
        if target.exists():
            raise PluginExists(f"{name} is already installed — use update")
        record = _record_for(report, spec, staging / TREE, files, marketplace)
        atomic_write_text(staging / RECORD, json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n")
        _swap_into(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    record.denied = False
    return record


# --- the switch ------------------------------------------------------------------------------------


def _require(home: Path, name: str) -> InstalledPlugin:
    record = get(home, name)
    if record is None:
        raise PluginError(f"no installed plugin named {name!r}")
    return record


def enable(home: Path, name: str, *, add_mcp: Collection[str] = ()) -> InstalledPlugin:
    """Switch a plugin on. The OWNER's act: the CLI, later the app behind its token — never a bridge
    tier, a bot or a cron. ``add_mcp`` names the plugin's MCP servers the owner also wants added;
    none is added otherwise (:func:`chimera.plugins.mcp.add_servers`)."""
    record = _require(home, name)
    if record.denied:
        raise PluginDenied(f"plugin:{name} is denied by the deployment policy")
    added: list[str] = []
    if add_mcp:
        from chimera.plugins.mcp import add_servers

        added = add_servers(home, record, add_mcp)
    record.status = "active"
    record.switched_on_at = _now()
    record.held_diff = {}
    record.mcp_added = sorted(set(record.mcp_added) | set(added))
    _save(home, record)
    return record


def _drop_mcp(home: Path, record: InstalledPlugin) -> None:
    if record.mcp_added:
        from chimera.plugins.mcp import remove_servers

        remove_servers(home, record.mcp_added)
        record.mcp_added = []


def disable(home: Path, name: str) -> InstalledPlugin:
    """Switch a plugin off: its skills, commands and agents disappear, its MCP servers are removed."""
    record = _require(home, name)
    _drop_mcp(home, record)
    record.status = "inactive"
    record.switched_on_at = ""
    _save(home, record)
    return record


def remove(home: Path, name: str) -> bool:
    record = get(home, name)
    target = plugins_root(home) / name if is_bundle_name(name) else None
    if target is None or not target.is_dir():
        return False
    if record is not None:
        _drop_mcp(home, record)
    shutil.rmtree(target, ignore_errors=True)
    if record is not None:
        # A plugin the owner uploaded keeps its files in `plugin-uploads/<digest>` as its pinned
        # source; that folder goes with it, under the guards in `forget_upload`.
        from chimera.plugins.upload import forget_upload

        forget_upload(home, record, installed(home))
    return True


# --- update ----------------------------------------------------------------------------------------


@dataclass
class UpdateResult:
    name: str
    changed: bool
    status: str
    diff: dict[str, Any] = field(default_factory=dict)


def _compare(home: Path, record: InstalledPlugin,
             source: SourceSpec | str | None) -> tuple[Path, InstalledPlugin, dict[str, Any] | None]:
    """Fetch the source into a ``*.partial`` folder and compare it with what is installed.

    Returns the staging folder (the caller owns removing it), the fresh record, and the diff — None
    when the bytes are the same."""
    if record.denied:
        raise PluginDenied(f"plugin:{record.name} is denied by the deployment policy")
    spec_text = source if source is not None else record.source
    try:
        spec = parse_source(spec_text) if isinstance(spec_text, str) else spec_text
    except FetchError as exc:
        raise PluginError(str(exc)) from exc
    staging, report, files = _stage(home, spec, record.name)
    try:
        new_name = _check_installable(report)
        if new_name != record.name:
            raise PluginError(f"the source now names the plugin {new_name!r}, not {record.name!r}")
        fresh = _record_for(report, spec, staging / TREE, files, record.marketplace)
        if fresh.tree_sha256 == record.tree_sha256:
            return staging, fresh, None
        change = changes.diff(tree_of(home, record.name), record.files, record.components,
                              staging / TREE, fresh.files, fresh.components)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging, fresh, change


def check_update(home: Path, name: str, source: SourceSpec | str | None = None) -> UpdateResult:
    """What :func:`update` WOULD do, changing nothing: the source is read into a scratch folder,
    compared and the folder removed. ``status`` is the state the plugin would land in, so a screen
    can show the diff before the owner accepts it."""
    record = _require(home, name)
    staging, _fresh, change = _compare(home, record, source)
    shutil.rmtree(staging, ignore_errors=True)
    if change is None:
        return UpdateResult(name, False, record.status)
    return UpdateResult(name, True, "held" if change["governed"] else "pending", change)


def update(home: Path, name: str, source: SourceSpec | str | None = None) -> UpdateResult:
    """Re-read the source (or a new pinned ``source``). Unchanged bytes change nothing; any change
    leaves the plugin ``held`` (governed change) or ``pending`` (prose only), never on."""
    record = _require(home, name)
    staging, fresh, change = _compare(home, record, source)
    if change is None:
        shutil.rmtree(staging, ignore_errors=True)
        return UpdateResult(name, False, record.status)
    try:
        fresh.status = "held" if change["governed"] else "pending"
        fresh.held_diff = change
        fresh.installed_at = record.installed_at
        fresh.updated_at = _now()
        _drop_mcp(home, record)
        atomic_write_text(staging / RECORD, json.dumps(fresh.to_dict(), indent=2, ensure_ascii=False) + "\n")
        _swap_into(staging, plugin_dir(home, name))
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return UpdateResult(name, True, fresh.status, change)


# --- what is shown and never run -------------------------------------------------------------------


def inert_suggestions(record: InstalledPlugin) -> list[dict[str, Any]]:
    """Hooks, monitors, executables, workflows, LSP servers and exec settings: listed, never loaded.

    The owner may copy a hook by hand into their own ``chimera-hooks.json``, where the owner's rules
    apply (hooks only tighten: deny, ask or annotate). Nothing here writes that file.
    """
    out = []
    for row in record.components:
        kind = str(row.get("kind"))
        if kind in INERT_KINDS and (kind != "settings" or row.get("capability") == Capability.EXEC.value):
            out.append({**row, "state": "inert"})
    return out


__all__ = [
    "INERT_KINDS",
    "PLUGINS_DIR",
    "RECORD",
    "STATUSES",
    "TREE",
    "InstalledPlugin",
    "PluginDenied",
    "PluginError",
    "PluginExists",
    "UpdateResult",
    "active",
    "check_update",
    "disable",
    "enable",
    "get",
    "inert_suggestions",
    "install",
    "installed",
    "plugin_dir",
    "plugins_root",
    "remove",
    "tree_of",
    "update",
]
