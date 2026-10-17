"""Read and validate one Claude Code plugin directory, without installing or running any of it.

The format is Claude Code's, taken as it is — https://code.claude.com/docs/en/plugins-reference,
read 2026-10-17 — so a plugin published for Claude Code reads here unchanged. ``plugin.json`` is
optional and only ``name`` is required in it; without one, the name is the folder's (or the
marketplace entry's, passed as ``fallback_name``).

Fields read (the "Fields" table of that page): ``$schema``, ``name``, ``displayName``, ``version``,
``description``, ``author{name,email,url}``, ``homepage``, ``repository``, ``license``,
``keywords``, ``metadata``, ``icon``, ``documentationUrl``, ``supportUrl``, ``privacyPolicyUrl``,
``termsOfServiceUrl``, ``defaultEnabled``, ``dependencies``, ``settings``, ``userConfig``, ``types``,
``channels`` and the component keys ``skills``, ``commands``, ``agents``, ``hooks``, ``mcpServers``,
``lspServers``, ``outputStyles``, ``workflows`` and ``experimental{themes,monitors,evals}``. An
unknown top-level key is a WARNING, as Claude Code strips it and loads the plugin; an unknown key in
a strict object (``userConfig`` option, ``channels`` entry, LSP config, monitor) is an ERROR, as
Claude Code refuses to load. Chimera-only fields live in ``metadata.chimera`` and nowhere else.

Everything read is untrusted data: nothing is imported, executed or fetched, links are never
followed, and the scan is bounded (`chimera.plugins.scan`).
"""

from __future__ import annotations

from pathlib import Path

from chimera.plugins import components, fields, names
from chimera.plugins.fields import error, warn
from chimera.plugins.model import Code, PluginManifest, PluginReport, Problem, Severity
from chimera.plugins.scan import TreeIndex, read_text, resolve, scan

MANIFEST = components.MANIFEST
_FIELDS = frozenset({
    "$schema", "name", "displayName", "version", "description", "author", "homepage", "repository",
    "license", "keywords", "metadata", "icon", "documentationUrl", "supportUrl", "privacyPolicyUrl",
    "termsOfServiceUrl", "defaultEnabled", "dependencies", "settings", "userConfig", "types",
    "channels", "skills", "commands", "agents", "hooks", "mcpServers", "lspServers", "outputStyles",
    "workflows", "experimental",
    # Still loaded by Claude Code at the top level, with a deprecation warning of their own.
    "themes", "monitors",
})
_EXPERIMENTAL = frozenset({"themes", "monitors", "evals"})
_CONTENT_MARKERS = (
    ".claude-plugin", "skills", "commands", "agents", "hooks", ".mcp.json", ".lsp.json",
    "output-styles", "workflows", "themes", "monitors", "bin", "settings.json", "SKILL.md",
)


def _name_problems(name: str, where: str, problems: list[Problem]) -> None:
    reason = names.plugin_name_error(name)
    if reason is not None:
        error(problems, Code.BAD_NAME, where, reason)
        return
    if names.reserved_plugin_name(name):
        error(problems, Code.RESERVED_NAME, where, f'Plugin name "{name}" is reserved: it passes as one of Anthropic\'s own')
    elif names.reads_as_anthropic(name):
        warn(problems, Code.NAME_READS_AS_ANTHROPIC, where, f'Plugin name "{name}" reads as one of Anthropic\'s own')
    if not names.is_kebab(name):
        warn(problems, Code.NOT_KEBAB_CASE, where, "use kebab-case")
    if len(name) > names.MAX_NAME_CHARS:
        warn(problems, Code.BAD_NAME, where, f"longer than {names.MAX_NAME_CHARS} characters; Claude Desktop drops it")


def _dependencies(data: dict[str, object], problems: list[Problem]) -> tuple[str, ...]:
    value = data.get("dependencies")
    if value is None:
        return ()
    if not isinstance(value, list):
        error(problems, Code.BAD_TYPE, f"{MANIFEST}#dependencies", "must be an array")
        return ()
    found: list[str] = []
    for item in value:
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            market = item.get("marketplace")
            found.append(f"{item['name']}@{market}" if isinstance(market, str) else str(item["name"]))
        else:
            error(problems, Code.BAD_TYPE, f"{MANIFEST}#dependencies", "each dependency is a name or {name, marketplace, version}")
    return tuple(found)


def _check_fields(data: dict[str, object], index: TreeIndex, problems: list[Problem]) -> None:
    for unknown in sorted(set(data) - _FIELDS):
        warn(problems, Code.UNKNOWN_FIELD, f"{MANIFEST}#{unknown}", f"Unknown field '{unknown}'. Claude Code ignores it at load time.")
    experimental = data.get("experimental")
    if experimental is not None and not isinstance(experimental, dict):
        error(problems, Code.BAD_TYPE, f"{MANIFEST}#experimental", "must be an object")
    elif isinstance(experimental, dict):
        for unknown in sorted(set(experimental) - _EXPERIMENTAL):
            warn(problems, Code.UNKNOWN_FIELD, f"{MANIFEST}#experimental.{unknown}", "unknown experimental key")
    homepage = data.get("homepage")
    if isinstance(homepage, str) and not fields.is_absolute_url(homepage):
        error(problems, Code.BAD_URL, f"{MANIFEST}#homepage", "must parse as a URL, or the plugin fails to load")
    for key in ("icon", "types"):
        if key in data:
            problems.extend(resolve(index, data[key], f"{MANIFEST}#{key}").problems)
    for key in ("documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl"):
        value = data.get(key)
        if isinstance(value, str) and not value.startswith("https://"):
            warn(problems, Code.BAD_URL, f"{MANIFEST}#{key}", "a directory listing link is an https:// URL")
    if "settings" in data and not isinstance(data["settings"], dict):
        error(problems, Code.BAD_TYPE, f"{MANIFEST}#settings", "must be an object")
    for key in ("version", "description", "author"):
        if key not in data:
            warn(problems, Code.MISSING_RECOMMENDED, f"{MANIFEST}#{key}", f"no '{key}'")


def _manifest(data: dict[str, object], index: TreeIndex, problems: list[Problem]) -> PluginManifest | None:
    raw_name = data.get("name")
    if raw_name is None or raw_name == "":
        error(problems, Code.MISSING_NAME, f"{MANIFEST}#name", "the manifest needs a 'name'")
        return None
    if not isinstance(raw_name, str):
        error(problems, Code.BAD_TYPE, f"{MANIFEST}#name", "must be a string")
        return None
    _name_problems(raw_name, f"{MANIFEST}#name", problems)
    _check_fields(data, index, problems)
    where = MANIFEST + "#"
    return PluginManifest(
        name=raw_name,
        name_from="manifest",
        display_name=fields.text(data, "displayName", where, problems),
        version=fields.text(data, "version", where, problems),
        description=fields.text(data, "description", where, problems),
        author=fields.author(data, "author", where, problems),
        homepage=fields.text(data, "homepage", where, problems),
        repository=fields.text(data, "repository", where, problems),
        license=fields.text(data, "license", where, problems),
        keywords=fields.strings(data, "keywords", where, problems),
        default_enabled=fields.boolean(data, "defaultEnabled", where, problems, True),
        dependencies=_dependencies(data, problems),
        user_config=fields.user_config(data.get("userConfig"), f"{MANIFEST}#userConfig", problems),
        chimera=fields.chimera_metadata(data, where, problems),
    )


def read_plugin(root: Path, *, fallback_name: str | None = None) -> PluginReport:
    """Read the plugin at ``root``. Never raises for what the directory contains; problems say what is wrong.

    ``fallback_name`` is the marketplace entry's name, used only when there is no ``plugin.json``
    (Claude Code's rule); otherwise the folder name is used.
    """
    root = Path(root)
    if not root.is_dir():
        problem = Problem(Code.PATH_NOT_FOUND, Severity.ERROR, ".", "not a directory")
        return PluginReport(None, (), (problem,))
    index = scan(root)
    problems = list(index.problems)
    manifest: PluginManifest | None
    data: dict[str, object] = {}
    if index.is_file(MANIFEST):
        text = read_text(index, MANIFEST, problems)
        parsed = None if text is None else fields.load_json(text, MANIFEST, problems)
        if parsed is not None and not isinstance(parsed, dict):
            error(problems, Code.BAD_TYPE, MANIFEST, "the manifest is a JSON object")
        data = parsed if isinstance(parsed, dict) else {}
        manifest = _manifest(data, index, problems) if isinstance(parsed, dict) else None
    elif MANIFEST in index.links or index.link_on(".claude-plugin") is not None:
        manifest = None  # the scan already reported the link; a manifest behind one is not read
    else:
        name = fallback_name or root.resolve().name
        _name_problems(name, ".", problems)
        manifest = PluginManifest(name=name, name_from="directory")
        if not any(index.is_file(marker) or index.is_dir(marker) for marker in _CONTENT_MARKERS):
            error(problems, Code.NOT_A_PLUGIN, ".", "no manifest and no component folder; this is not a plugin")
    context = components.Context(
        index=index,
        plugin=manifest.name if manifest else "",
        manifest=data,
        user_config_keys=frozenset(option.key for option in manifest.user_config) if manifest else frozenset(),
    )
    components.discover(context)
    problems.extend(context.problems)
    return PluginReport(manifest, tuple(context.components), tuple(problems))
