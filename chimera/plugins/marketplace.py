"""Read and validate a Claude Code ``marketplace.json`` and classify where each plugin would come from.

Format: https://code.claude.com/docs/en/plugins/marketplace-reference, read 2026-10-17. The file is
``<root>/.claude-plugin/marketplace.json`` with ``name``, ``owner{name}`` and ``plugins[]``
required; optional ``description``, ``version``, ``metadata.{description,version,pluginRoot}``,
``forceRemoveDeletedPlugins``, ``allowCrossMarketplaceDependenciesOn`` and ``renames``. Each entry
needs ``name`` and ``source`` and accepts every ``plugin.json`` field (except the directory listing
ones) plus ``category``, ``tags``, ``strict``, ``relevance``, ``headers`` and ``headersHelper``.
Each entry is validated on its own: one bad entry is reported and the rest still read, as in Claude
Code.

Sources, and what a v1 Chimera installer would do with them (ESTUDO-01 §6.2):

* relative path (``./x``, ``.``, or a bare name under ``metadata.pluginRoot``) — installable;
* ``github`` with a full 40-hex ``sha`` — installable; without one it is ``unpinned_source``, because
  the tree reviewed today is not the tree a branch names tomorrow;
* ``archive`` with ``https://`` and a ``sha256`` — installable; without the digest, ``unpinned_source``;
* ``url`` and ``git-subdir`` — described; unpinned ones are ``unpinned_source`` and pinned ones are
  ``unsupported_source`` (v1 fetches GitHub through its API only);
* ``npm`` and ``command`` — ``unsupported_source``: one is supply-chain surface with nothing to gain,
  the other runs a command on the host to install.

Nothing here fetches, clones or runs anything.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import re
import urllib.parse
from pathlib import Path

from chimera.plugins import fields, names
from chimera.plugins.fields import error, warn
from chimera.plugins.model import (
    Code,
    MarketplaceEntry,
    MarketplaceReport,
    PluginSource,
    Problem,
    Severity,
    SourceKind,
)
from chimera.plugins.scan import TreeIndex, read_text, resolve, scan

MARKETPLACE = ".claude-plugin/marketplace.json"
_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
_TOP = frozenset({
    "$schema", "name", "owner", "plugins", "description", "version", "metadata",
    "forceRemoveDeletedPlugins", "allowCrossMarketplaceDependenciesOn", "renames",
})
_ENTRY_OWN = frozenset({
    "name", "source", "category", "tags", "strict", "relevance", "headers", "headersHelper",
})
#: The plugin.json fields an entry also accepts (the directory listing fields are not among them).
_ENTRY_MANIFEST = frozenset({
    "$schema", "displayName", "version", "description", "author", "homepage", "repository", "license",
    "keywords", "metadata", "defaultEnabled", "dependencies", "settings", "userConfig", "types",
    "channels", "skills", "commands", "agents", "hooks", "mcpServers", "lspServers", "outputStyles",
    "workflows", "experimental", "themes", "monitors",
})


def _refused(kind: SourceKind, code: Code, problems: list[Problem], where: str, message: str, *,
             repo: str | None = None, url: str | None = None, ref: str | None = None,
             sha: str | None = None, package: str | None = None) -> PluginSource:
    """A recognised source a v1 installer will not fetch. A warning: the marketplace itself is fine."""
    problems.append(Problem(code, Severity.WARNING, where, message))
    return PluginSource(kind=kind, repo=repo, url=url, ref=ref, sha=sha, package=package, refusal=code)


def _blocked_host(host: str) -> bool:
    """Loopback, link-local and cloud-metadata hosts — refused for an archive URL, as Claude Code does."""
    lowered = host.lower().strip("[]")
    if lowered in ("localhost", "metadata.google.internal") or lowered.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return address.is_loopback or address.is_link_local or address.is_unspecified


def _local(raw: str, plugin_root: str | None, index: TreeIndex | None, where: str, problems: list[Problem]) -> PluginSource:
    if raw in (".", "./"):
        candidate = "./"
    elif raw.startswith("./") or "\\" in raw or ".." in raw.split("/"):
        candidate = raw
    elif "/" not in raw and plugin_root is not None:
        candidate = f"{plugin_root.rstrip('/')}/{raw}"
    else:
        error(problems, Code.PATH_NOT_RELATIVE, where, f'Invalid string: must start with "./": {raw!r}')
        return PluginSource(kind=SourceKind.LOCAL, refusal=Code.PATH_NOT_RELATIVE)
    if index is None:
        # Without the marketplace's files the path can only be checked for shape.
        result = resolve(TreeIndex(root=Path(".")), candidate, where, allow_dot=True)
        shape = [p for p in result.problems if p.code is not Code.PATH_NOT_FOUND]
        problems.extend(shape)
        rel = "/".join(part for part in candidate[2:].split("/") if part not in ("", "."))
        return PluginSource(kind=SourceKind.LOCAL, path=rel, refusal=shape[0].code if shape else None)
    result = resolve(index, candidate, where, allow_dot=True)
    problems.extend(result.problems)
    if result.rel is None:
        code = result.problems[0].code if result.problems else Code.PATH_NOT_FOUND
        return PluginSource(kind=SourceKind.LOCAL, refusal=code)
    if not index.is_dir(result.rel):
        error(problems, Code.PATH_NOT_FOUND, where, "a relative source names a directory")
        return PluginSource(kind=SourceKind.LOCAL, path=result.rel, refusal=Code.PATH_NOT_FOUND)
    return PluginSource(kind=SourceKind.LOCAL, path=result.rel)


def _github(spec: dict[str, object], where: str, problems: list[Problem]) -> PluginSource:
    repo, ref, sha = spec.get("repo"), spec.get("ref"), spec.get("sha")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or ".." in repo:
        error(problems, Code.BAD_SOURCE, where, "a github source needs 'repo' as owner/repo")
        return PluginSource(kind=SourceKind.GITHUB, refusal=Code.BAD_SOURCE)
    branch = ref if isinstance(ref, str) else None
    if sha is None:
        return _refused(SourceKind.GITHUB, Code.UNPINNED_SOURCE, problems, where,
                        "a github source without 'sha' is not pinned; v1 installs only a pinned commit",
                        repo=repo, ref=branch)
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        error(problems, Code.BAD_SHA, where, "'sha' is a full 40-character lowercase commit SHA")
        return PluginSource(kind=SourceKind.GITHUB, repo=repo, ref=branch, refusal=Code.BAD_SHA)
    return PluginSource(kind=SourceKind.GITHUB, repo=repo, ref=branch, sha=sha)


def _archive(spec: dict[str, object], where: str, problems: list[Problem]) -> PluginSource:
    url, digest = spec.get("url"), spec.get("sha256")
    if not isinstance(url, str):
        error(problems, Code.BAD_URL, where, "an archive needs 'url'")
        return PluginSource(kind=SourceKind.ARCHIVE, refusal=Code.BAD_URL)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or _blocked_host(parsed.hostname):
        error(problems, Code.BAD_URL, where, "an archive 'url' is https:// to a public host")
        return PluginSource(kind=SourceKind.ARCHIVE, refusal=Code.BAD_URL)
    if digest is None:
        return _refused(SourceKind.ARCHIVE, Code.UNPINNED_SOURCE, problems, where,
                        "an archive without 'sha256' is not pinned; v1 installs only a verified download", url=url)
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        error(problems, Code.BAD_SHA, where, "'sha256' is 64 hex characters")
        return PluginSource(kind=SourceKind.ARCHIVE, url=url, refusal=Code.BAD_SHA)
    return PluginSource(kind=SourceKind.ARCHIVE, url=url, sha256=digest.lower())


def _git(kind: SourceKind, spec: dict[str, object], where: str, problems: list[Problem]) -> PluginSource:
    url, ref, sha = spec.get("url"), spec.get("ref"), spec.get("sha")
    if not isinstance(url, str) or not url or (kind is SourceKind.GIT_SUBDIR and not isinstance(spec.get("path"), str)):
        error(problems, Code.BAD_SOURCE, where, f"a {kind.value} source needs 'url'" + (" and 'path'" if kind is SourceKind.GIT_SUBDIR else ""))
        return PluginSource(kind=kind, refusal=Code.BAD_SOURCE)
    branch = ref if isinstance(ref, str) else None
    if not (isinstance(sha, str) and _SHA.fullmatch(sha)):
        return _refused(kind, Code.UNPINNED_SOURCE, problems, where,
                        f"an unpinned {kind.value} source is not installable in v1", url=url, ref=branch)
    return _refused(kind, Code.UNSUPPORTED_SOURCE, problems, where,
                    f"a {kind.value} source is not supported in v1; use github with sha, or archive with sha256",
                    url=url, ref=branch, sha=sha)


def classify_source(raw: object, *, plugin_root: str | None = None, index: TreeIndex | None = None,
                    where: str = "source", problems: list[Problem] | None = None) -> PluginSource:
    """Classify one entry's ``source``. ``index`` is the marketplace tree, when its files are at hand."""
    found = problems if problems is not None else []
    if isinstance(raw, str):
        return _local(raw, plugin_root, index, where, found)
    if not isinstance(raw, dict):
        error(found, Code.BAD_SOURCE, where, "a source is a './path' string or an object with 'source'")
        return PluginSource(kind=SourceKind.UNKNOWN, refusal=Code.BAD_SOURCE)
    kind = raw.get("source")
    if kind == "github":
        return _github(raw, where, found)
    if kind == "archive":
        return _archive(raw, where, found)
    if kind in ("url", "git-subdir"):
        return _git(SourceKind(kind), raw, where, found)
    if kind == "npm":
        package = raw.get("package")
        return _refused(SourceKind.NPM, Code.UNSUPPORTED_SOURCE, found, where, "npm sources are not supported in v1",
                        package=package if isinstance(package, str) else None)
    if kind == "command":
        return _refused(SourceKind.COMMAND, Code.UNSUPPORTED_SOURCE, found, where,
                        "a command source runs a command on this machine to install; refused in v1")
    error(found, Code.BAD_SOURCE, where, f"unknown source type {kind!r}")
    return PluginSource(kind=SourceKind.UNKNOWN, refusal=Code.BAD_SOURCE)


def _entry(raw: object, position: int, plugin_root: str | None, index: TreeIndex | None) -> MarketplaceEntry:
    where = f"plugins[{position}]"
    problems: list[Problem] = []
    if not isinstance(raw, dict):
        return MarketplaceEntry(name="", source=PluginSource(kind=SourceKind.UNKNOWN, refusal=Code.BAD_TYPE),
                                problems=(Problem(Code.BAD_TYPE, Severity.ERROR, where, "an entry is an object"),))
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        error(problems, Code.MISSING_NAME, f"{where}.name", "an entry needs a 'name'")
        name = ""
    else:
        reason = names.plugin_name_error(name)
        if reason is not None:
            error(problems, Code.BAD_NAME, f"{where}.name", reason)
        elif names.reserved_plugin_name(name):
            error(problems, Code.RESERVED_NAME, f"{where}.name", f'Plugin name "{name}" is reserved: it passes as one of Anthropic\'s own')
    for unknown in sorted(set(raw) - _ENTRY_OWN - _ENTRY_MANIFEST):
        warn(problems, Code.UNKNOWN_FIELD, f"{where}.{unknown}", f"Unknown field '{unknown}'. Claude Code ignores it at load time.")
    if "source" not in raw:
        error(problems, Code.BAD_SOURCE, f"{where}.source", "an entry needs a 'source'")
        source = PluginSource(kind=SourceKind.UNKNOWN, refusal=Code.BAD_SOURCE)
    else:
        source = classify_source(raw["source"], plugin_root=plugin_root, index=index, where=f"{where}.source", problems=problems)
    fields.chimera_metadata(raw, where, problems)
    return MarketplaceEntry(
        name=name,
        source=source,
        problems=tuple(problems),
        display_name=fields.text(raw, "displayName", where, problems),
        description=fields.text(raw, "description", where, problems),
        version=fields.text(raw, "version", where, problems),
        category=fields.text(raw, "category", where, problems),
        tags=fields.strings(raw, "tags", where, problems),
        author=fields.author(raw, "author", where, problems),
    )


def _plugin_root(data: dict[str, object], problems: list[Problem]) -> str | None:
    metadata = data.get("metadata")
    value = metadata.get("pluginRoot") if isinstance(metadata, dict) else None
    if value is None:
        return None
    if not isinstance(value, str) or "\\" in value or value.startswith("/") or ".." in value.split("/") or ":" in value:
        error(problems, Code.PATH_ESCAPES_PLUGIN, "metadata.pluginRoot", "must be a relative path inside the marketplace")
        return None
    return value if value.startswith("./") else f"./{value}"


def parse_marketplace(data: object, *, index: TreeIndex | None = None, anthropic_origin: bool = False) -> MarketplaceReport:
    """Validate parsed ``marketplace.json`` content. ``index`` lets relative sources be checked on disk."""
    problems: list[Problem] = []
    if not isinstance(data, dict):
        error(problems, Code.BAD_TYPE, MARKETPLACE, "the marketplace file is a JSON object")
        return MarketplaceReport(None, None, None, None, (), tuple(problems))
    name = data.get("name")
    if not isinstance(name, str) or not name:
        error(problems, Code.MISSING_NAME, "name", "Marketplace must have a name")
        name = None
    else:
        reason = names.marketplace_name_error(name, anthropic_origin=anthropic_origin)
        if reason is not None:
            code = Code.RESERVED_NAME if "reserved" in reason or "impersonate" in reason else Code.BAD_NAME
            error(problems, code, "name", reason)
    owner = fields.author(data, "owner", "", problems) if "owner" in data else None
    if owner is None:
        error(problems, Code.MISSING_OWNER, "owner", "the marketplace needs 'owner' with a 'name'")
    for unknown in sorted(set(data) - _TOP):
        warn(problems, Code.UNKNOWN_FIELD, unknown, f"Unknown field '{unknown}'. Claude Code ignores it at load time.")
    raw_metadata = data.get("metadata")
    metadata: dict[str, object] = raw_metadata if isinstance(raw_metadata, dict) else {}
    description = fields.text(data, "description", "", problems) or fields.text(metadata, "description", "metadata", problems)
    if not description:
        warn(problems, Code.MISSING_DESCRIPTION, "description", "No marketplace description provided")
    plugin_root = _plugin_root(data, problems)
    raw_plugins = data.get("plugins")
    entries: list[MarketplaceEntry] = []
    if not isinstance(raw_plugins, list):
        error(problems, Code.MISSING_PLUGINS, "plugins", "the marketplace needs a 'plugins' array")
        raw_plugins = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_plugins):
        entry = _entry(raw, position, plugin_root, index)
        if entry.name and entry.name in seen:
            duplicate = Problem(Code.DUPLICATE_PLUGIN, Severity.ERROR, f"plugins[{position}].name",
                                f'Duplicate plugin name "{entry.name}" found in marketplace')
            entry = dataclasses.replace(entry, problems=(*entry.problems, duplicate))
        seen.add(entry.name)
        entries.append(entry)
    return MarketplaceReport(
        name=name,
        owner=owner,
        description=description,
        version=fields.text(data, "version", "", problems) or fields.text(metadata, "version", "metadata", problems),
        plugins=tuple(entries),
        problems=tuple(problems),
    )


def read_marketplace(root: Path, *, anthropic_origin: bool = False) -> MarketplaceReport:
    """Read ``<root>/.claude-plugin/marketplace.json``. ``root`` is the marketplace root, where relative sources start.

    ``anthropic_origin`` is the caller's statement that the files came from ``github.com/anthropics/``;
    only then may the marketplace use an official name. Never raises for what the files contain.
    """
    root = Path(root)
    if not root.is_dir():
        return MarketplaceReport(None, None, None, None, (), (Problem(Code.PATH_NOT_FOUND, Severity.ERROR, ".", "not a directory"),))
    index = scan(root)
    problems = list(index.problems)
    if not index.is_file(MARKETPLACE):
        problems.append(Problem(Code.MISSING_MARKETPLACE, Severity.ERROR, MARKETPLACE, "no .claude-plugin/marketplace.json (links are not followed)"))
        return MarketplaceReport(None, None, None, None, (), tuple(problems))
    text = read_text(index, MARKETPLACE, problems)
    data = None if text is None else fields.load_json(text, MARKETPLACE, problems)
    if data is None:
        return MarketplaceReport(None, None, None, None, (), tuple(problems))
    report = parse_marketplace(data, index=index, anthropic_origin=anthropic_origin)
    return MarketplaceReport(report.name, report.owner, report.description, report.version, report.plugins, (*problems, *report.problems))
