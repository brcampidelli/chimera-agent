"""Find a plugin's components the way Claude Code does, and classify what each would do.

Source for every rule here: https://code.claude.com/docs/en/plugins-reference (sections "Standard
layout", "Component path forms" and "How each key combines with its default location"), read
2026-10-17. In short:

* default locations — ``skills/<name>/SKILL.md``, ``commands/*.md``, ``agents/*.md``,
  ``hooks/hooks.json``, ``.mcp.json``, ``.lsp.json``, ``output-styles/``, ``workflows/*.js``,
  ``themes/``, ``monitors/monitors.json``, ``bin/``, ``settings.json``; a plugin with a root
  ``SKILL.md``, no ``skills/`` and no ``skills`` key is one skill;
* ``commands``, ``agents``, ``outputStyles``, ``workflows``, ``experimental.themes`` and
  ``experimental.monitors`` REPLACE their default; ``skills`` ADDS to it; ``hooks``, ``mcpServers``
  and ``lspServers`` MERGE with the default file, a later server name replacing an earlier one.

Nothing found here is run, imported or fetched. A hook's command, an MCP server's command line and a
monitor's shell string are copied into ``Component.detail`` as untrusted text for the owner to read.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from chimera.plugins.fields import JsonObject, error, is_absolute_url, load_json, warn
from chimera.plugins.model import Capability, Code, Component, ComponentKind, Problem
from chimera.plugins.scan import MAX_FILE_BYTES, TreeIndex, read_text, resolve

MANIFEST = ".claude-plugin/plugin.json"
_USER_CONFIG_REF = re.compile(r"\$\{user_config\.([A-Za-z0-9_]*)\}")
_BUNDLE_SUFFIXES = (".mcpb", ".dxt")
#: https://code.claude.com/docs/en/plugins-reference#lspservers — a strict object.
_LSP_FIELDS = frozenset({
    "command", "extensionToLanguage", "args", "transport", "env", "initializationOptions", "settings",
    "workspaceFolder", "startupTimeout", "shutdownTimeout", "requestTimeout", "restartOnCrash",
    "maxRestarts", "diagnostics",
})
#: https://code.claude.com/docs/en/plugins-reference#monitors — a strict object.
_MONITOR_FIELDS = frozenset({"name", "command", "description", "when"})
_COMMAND_MAP_FIELDS = frozenset({"source", "content", "description", "argumentHint", "model", "allowedTools"})


@dataclass
class Context:
    index: TreeIndex
    plugin: str
    manifest: Mapping[str, object]
    user_config_keys: frozenset[str]
    problems: list[Problem] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    mcp_names: set[str] = field(default_factory=set)

    def add(self, kind: ComponentKind, name: str, path: str, capability: Capability, detail: tuple[str, ...] = ()) -> None:
        self.components.append(Component(kind, name, path, capability, detail))

    def add_file(self, kind: ComponentKind, name: str, rel: str, capability: Capability) -> None:
        size = self.index.files.get(rel, 0)
        if size > MAX_FILE_BYTES:
            error(self.problems, Code.FILE_TOO_LARGE, rel, f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
            return
        self.add(kind, name, rel, capability)


def _inline(field_name: str, key: str = "") -> str:
    return f"{MANIFEST}#{field_name}.{key}" if key else f"{MANIFEST}#{field_name}"


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else [value]


def _paths(ctx: Context, value: object, field_name: str, *, allow_dot: bool = False) -> list[str]:
    """Resolve a "path or array of paths" field. Bad entries become problems and are dropped."""
    resolved: list[str] = []
    for raw in _as_list(value):
        result = resolve(ctx.index, raw, _inline(field_name), allow_dot=allow_dot)
        ctx.problems.extend(result.problems)
        if result.rel is not None:
            resolved.append(result.rel)
    return resolved


def _stem(rel: str, base: str, suffix: str) -> str:
    """``agents/team/reviewer.md`` under ``agents`` → ``team:reviewer`` (subfolders join the name)."""
    inner = rel[len(base) + 1:] if base and rel.startswith(f"{base}/") else rel.rsplit("/", 1)[-1]
    trimmed = inner[: -len(suffix)] if inner.lower().endswith(suffix) else inner
    return trimmed.replace("/", ":")


def _files_with(ctx: Context, rels: list[str], suffix: str, field_name: str, *, dirs_ok: bool = True) -> Iterator[tuple[str, str]]:
    """Yield (name, rel) for each file with ``suffix`` named directly or found under a named directory."""
    for rel in rels:
        if ctx.index.is_file(rel):
            if not rel.lower().endswith(suffix):
                error(ctx.problems, Code.BAD_COMPONENT, _inline(field_name), f"{rel!r} is not a {suffix} file")
                continue
            yield _stem(rel, "", suffix), rel
        elif not dirs_ok:
            error(ctx.problems, Code.BAD_COMPONENT, _inline(field_name), f"{rel!r} is a directory; only {suffix} files are accepted")
        else:
            for found in ctx.index.below(rel):
                if found.lower().endswith(suffix):
                    yield _stem(found, rel, suffix), found


def _replacing(ctx: Context, key: str, default: str, value: object, suffix: str,
               kind: ComponentKind, capability: Capability, *, dirs_ok: bool = True) -> None:
    """A component whose manifest key replaces its default folder."""
    if value is None:
        rels = [default] if ctx.index.is_dir(default) else []
    else:
        rels = _paths(ctx, value, key)
        if ctx.index.is_dir(default) and not any(rel == default or rel.startswith(f"{default}/") for rel in rels):
            warn(ctx.problems, Code.DEFAULT_FOLDER_IGNORED, default,
                 f'Default {default}/ folder is ignored because the manifest sets "{key}"')
    for name, rel in _files_with(ctx, rels, suffix, key, dirs_ok=dirs_ok):
        ctx.add_file(kind, name, rel, capability)


def skills(ctx: Context) -> None:
    value = ctx.manifest.get("skills")
    dirs = ["skills"] if ctx.index.is_dir("skills") else []
    for rel in ([] if value is None else _paths(ctx, value, "skills", allow_dot=True)):
        if ctx.index.is_dir(rel):
            dirs.append(rel)
        else:
            error(ctx.problems, Code.BAD_COMPONENT, _inline("skills"), f"{rel!r} is not a directory")
    if not dirs and value is None and ctx.index.is_file("SKILL.md"):
        ctx.add_file(ComponentKind.SKILL, ctx.plugin, "SKILL.md", Capability.TEXT)
        return
    seen: set[str] = set()
    for directory in dict.fromkeys(dirs):
        own = f"{directory}/SKILL.md" if directory else "SKILL.md"
        if ctx.index.is_file(own):
            candidates = [(directory.rsplit("/", 1)[-1] or ctx.plugin, own)]
        else:
            candidates = [
                (child.rsplit("/", 1)[-1], f"{child}/SKILL.md")
                for child in ctx.index.children(directory)
                if ctx.index.is_file(f"{child}/SKILL.md")
            ]
        for name, rel in candidates:
            if rel not in seen:
                seen.add(rel)
                ctx.add_file(ComponentKind.SKILL, name, rel, Capability.TEXT)


def commands(ctx: Context) -> None:
    value = ctx.manifest.get("commands")
    if not isinstance(value, dict):
        _replacing(ctx, "commands", "commands", value, ".md", ComponentKind.COMMAND, Capability.TEXT)
        return
    named_inside_default = False
    for name, spec in value.items():
        at = _inline("commands", name)
        if not isinstance(spec, dict) or ("source" in spec) == ("content" in spec):
            error(ctx.problems, Code.BAD_COMPONENT, at, "each command sets exactly one of 'source' or 'content'")
            continue
        for unknown in sorted(set(spec) - _COMMAND_MAP_FIELDS):
            warn(ctx.problems, Code.UNKNOWN_FIELD, f"{at}.{unknown}", "unknown command field")
        if "content" in spec:
            if isinstance(spec["content"], str):
                ctx.add(ComponentKind.COMMAND, name, at, Capability.TEXT)
            else:
                error(ctx.problems, Code.BAD_TYPE, f"{at}.content", "must be a string")
            continue
        result = resolve(ctx.index, spec["source"], f"{at}.source")
        ctx.problems.extend(result.problems)
        if result.rel is not None:
            named_inside_default |= result.rel.startswith("commands/")
            if ctx.index.is_file(result.rel) and result.rel.lower().endswith(".md"):
                ctx.add_file(ComponentKind.COMMAND, name, result.rel, Capability.TEXT)
            else:
                error(ctx.problems, Code.BAD_COMPONENT, f"{at}.source", "must name a .md file")
    # Claude Code's own advice: naming a file inside the default folder keeps the warning away.
    if ctx.index.is_dir("commands") and not named_inside_default:
        warn(ctx.problems, Code.DEFAULT_FOLDER_IGNORED, "commands", 'Default commands/ folder is ignored because the manifest sets "commands"')


def simple_folders(ctx: Context) -> None:
    """Agents, output styles, workflows and themes: path-only keys that replace their default folder."""
    _replacing(ctx, "agents", "agents", ctx.manifest.get("agents"), ".md", ComponentKind.AGENT, Capability.TEXT, dirs_ok=ctx.manifest.get("agents") is None)
    _replacing(ctx, "outputStyles", "output-styles", ctx.manifest.get("outputStyles"), ".md", ComponentKind.OUTPUT_STYLE, Capability.TEXT)
    # A workflow is a .js program Claude Code's workflow engine runs: code, not instructions.
    _replacing(ctx, "workflows", "workflows", ctx.manifest.get("workflows"), ".js", ComponentKind.WORKFLOW, Capability.EXEC)
    experimental = ctx.manifest.get("experimental")
    themes = experimental.get("themes") if isinstance(experimental, dict) else None
    if themes is None and "themes" in ctx.manifest:
        themes = ctx.manifest["themes"]
        warn(ctx.problems, Code.DEPRECATED_LOCATION, _inline("themes"), "move 'themes' under 'experimental'")
    _replacing(ctx, "experimental.themes", "themes", themes, ".json", ComponentKind.THEME, Capability.TEXT)


def _json_file(ctx: Context, rel: str) -> object | None:
    text = read_text(ctx.index, rel, ctx.problems)
    return None if text is None else load_json(text, rel, ctx.problems)


def _hook_events(ctx: Context, events: object, where: str) -> None:
    if not isinstance(events, dict):
        error(ctx.problems, Code.BAD_HOOKS, where, "hooks must map event names to matcher arrays")
        return
    for event, matchers in events.items():
        if not isinstance(matchers, list):
            error(ctx.problems, Code.BAD_HOOKS, f"{where}.{event}", "an event maps to an array of matchers")
            continue
        kinds: set[str] = set()
        for matcher in matchers:
            handlers = matcher.get("hooks") if isinstance(matcher, dict) else None
            if not isinstance(handlers, list):
                error(ctx.problems, Code.BAD_HOOKS, f"{where}.{event}", "each matcher carries a 'hooks' array")
                continue
            kinds.update(str(handler.get("type", "?")) for handler in handlers if isinstance(handler, dict))
        # Every handler type counts as exec: a hook fires on its own at a lifecycle event, whatever
        # it then does (a shell command, an HTTP call, a prompt), without the model asking.
        ctx.add(ComponentKind.HOOK, str(event), where, Capability.EXEC, tuple(sorted(kinds)))


def hooks(ctx: Context) -> None:
    def from_file(rel: str) -> None:
        data = _json_file(ctx, rel)
        if data is None:
            return
        wrapped = data.get("hooks") if isinstance(data, dict) else None
        if wrapped is None:
            error(ctx.problems, Code.BAD_HOOKS, rel, 'a hooks file wraps its event map in a top-level "hooks" key')
            return
        _hook_events(ctx, wrapped, rel)

    if ctx.index.is_file("hooks/hooks.json"):
        from_file("hooks/hooks.json")
    value = ctx.manifest.get("hooks")
    for item in ([] if value is None else _as_list(value)):
        if isinstance(item, dict):
            _hook_events(ctx, item, _inline("hooks"))
            continue
        for rel in _paths(ctx, item, "hooks"):
            if rel.lower().endswith(".json") and ctx.index.is_file(rel):
                from_file(rel)
            else:
                error(ctx.problems, Code.BAD_COMPONENT, _inline("hooks"), f"{rel!r} is not a .json hooks file")


def _strings_in(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings_in(item)


def _loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return host.endswith(".localhost")


def _mcp_server(ctx: Context, name: str, spec: object, where: str, servers: dict[str, Component]) -> None:
    if not isinstance(spec, dict):
        error(ctx.problems, Code.BAD_COMPONENT, where, "an MCP server config is an object")
        return
    for ref in _USER_CONFIG_REF.findall(" ".join(_strings_in(spec))):
        if ref not in ctx.user_config_keys:
            error(ctx.problems, Code.UNDECLARED_USER_CONFIG, where, f"${{user_config.{ref}}} names an option the manifest does not declare")
    helper = spec.get("headersHelper")
    if isinstance(helper, str) and _USER_CONFIG_REF.search(helper):
        error(ctx.problems, Code.USER_CONFIG_IN_SHELL, where, "headersHelper runs through a shell and cannot reference ${user_config.*}")
    command, url = spec.get("command"), spec.get("url")
    if isinstance(command, str) and command:
        servers[name] = Component(ComponentKind.MCP_SERVER, name, where, Capability.PROCESS, ("stdio", command))
    elif isinstance(url, str) and url:
        if not is_absolute_url(url):
            error(ctx.problems, Code.BAD_URL, where, "a remote MCP 'url' must be an absolute URL")
            return
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ("http", "ws") and not _loopback(parsed.hostname or ""):
            warn(ctx.problems, Code.INSECURE_URL, where, f"{parsed.scheme}:// to a non-loopback host")
        transport = spec.get("type") if isinstance(spec.get("type"), str) else "http"
        servers[name] = Component(ComponentKind.MCP_SERVER, name, where, Capability.NETWORK, (str(transport), url))
    else:
        error(ctx.problems, Code.BAD_COMPONENT, where, "an MCP server needs a 'command' (stdio) or a 'url' (remote)")


def _mcp_map(ctx: Context, data: object, where: str, servers: dict[str, Component]) -> None:
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        data = data["mcpServers"]  # the `.mcp.json` shape used by projects; a bare map is accepted too
    if not isinstance(data, dict):
        error(ctx.problems, Code.BAD_COMPONENT, where, "an MCP config is an object of servers keyed by name")
        return
    for name, spec in data.items():
        _mcp_server(ctx, str(name), spec, where if where.endswith(".json") else f"{where}.{name}", servers)


def mcp_servers(ctx: Context) -> None:
    servers: dict[str, Component] = {}
    if ctx.index.is_file(".mcp.json"):
        data = _json_file(ctx, ".mcp.json")
        if data is not None:
            _mcp_map(ctx, data, ".mcp.json", servers)
    value = ctx.manifest.get("mcpServers")
    for item in ([] if value is None else _as_list(value)):
        if isinstance(item, dict):
            _mcp_map(ctx, item, _inline("mcpServers"), servers)
        elif isinstance(item, str) and item.lower().startswith("https://") and item.lower().endswith(_BUNDLE_SUFFIXES):
            # Claude Code downloads this at load time. The reader never fetches; it reports it.
            warn(ctx.problems, Code.REMOTE_MCP_BUNDLE, _inline("mcpServers"), f"an MCP bundle fetched from {item!r} at load time")
            name = item.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            servers[name] = Component(ComponentKind.MCP_SERVER, name, _inline("mcpServers"), Capability.PROCESS, ("bundle-url", item))
        else:
            for rel in _paths(ctx, item, "mcpServers"):
                lowered = rel.lower()
                if lowered.endswith(_BUNDLE_SUFFIXES) and ctx.index.is_file(rel):
                    name = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    servers[name] = Component(ComponentKind.MCP_SERVER, name, rel, Capability.PROCESS, ("bundle",))
                elif lowered.endswith(".json") and ctx.index.is_file(rel):
                    data = _json_file(ctx, rel)
                    if data is not None:
                        _mcp_map(ctx, data, rel, servers)
                else:
                    error(ctx.problems, Code.BAD_COMPONENT, _inline("mcpServers"), f"{rel!r} is not a .json, .mcpb or .dxt file")
    ctx.mcp_names.update(servers)
    ctx.components.extend(servers.values())


def _lsp_map(ctx: Context, data: object, where: str, servers: dict[str, Component]) -> None:
    if not isinstance(data, dict):
        error(ctx.problems, Code.BAD_COMPONENT, where, "an LSP config is an object of servers keyed by name")
        return
    for name, spec in data.items():
        at = f"{where}.{name}" if not where.endswith(".json") else where
        if not isinstance(spec, dict):
            error(ctx.problems, Code.BAD_COMPONENT, at, "an LSP server config is an object")
            continue
        for unknown in sorted(set(spec) - _LSP_FIELDS):
            error(ctx.problems, Code.UNKNOWN_FIELD_STRICT, f"{at}.{unknown}", "unknown key in a strict LSP server config")
        command, extensions = spec.get("command"), spec.get("extensionToLanguage")
        if not isinstance(command, str) or not command:
            error(ctx.problems, Code.BAD_COMPONENT, at, "an LSP server needs 'command'")
            continue
        if not isinstance(extensions, dict) or not extensions or not all(str(key).startswith(".") for key in extensions):
            error(ctx.problems, Code.BAD_COMPONENT, at, "'extensionToLanguage' maps at least one '.ext' to a language id")
            continue
        servers[str(name)] = Component(ComponentKind.LSP_SERVER, str(name), at, Capability.PROCESS, ("stdio", command))


def lsp_servers(ctx: Context) -> None:
    servers: dict[str, Component] = {}
    if ctx.index.is_file(".lsp.json"):
        data = _json_file(ctx, ".lsp.json")
        if data is not None:
            _lsp_map(ctx, data, ".lsp.json", servers)
    value = ctx.manifest.get("lspServers")
    for item in ([] if value is None else _as_list(value)):
        if isinstance(item, dict):
            _lsp_map(ctx, item, _inline("lspServers"), servers)
            continue
        for rel in _paths(ctx, item, "lspServers"):
            if rel.lower().endswith(".json") and ctx.index.is_file(rel):
                data = _json_file(ctx, rel)
                if data is not None:
                    _lsp_map(ctx, data, rel, servers)
            else:
                error(ctx.problems, Code.BAD_COMPONENT, _inline("lspServers"), f"{rel!r} is not a .json file")
    ctx.components.extend(servers.values())


def _monitor_list(ctx: Context, data: object, where: str) -> None:
    if not isinstance(data, list):
        error(ctx.problems, Code.BAD_COMPONENT, where, "monitors are an array")
        return
    for position, spec in enumerate(data):
        at = f"{where}[{position}]"
        if not isinstance(spec, dict):
            error(ctx.problems, Code.BAD_COMPONENT, at, "a monitor is an object")
            continue
        for unknown in sorted(set(spec) - _MONITOR_FIELDS):
            error(ctx.problems, Code.UNKNOWN_FIELD_STRICT, f"{at}.{unknown}", "unknown key in a strict monitor entry")
        name, command, description = spec.get("name"), spec.get("command"), spec.get("description")
        if not (isinstance(name, str) and name and isinstance(command, str) and command and isinstance(description, str)):
            error(ctx.problems, Code.BAD_COMPONENT, at, "a monitor needs 'name', 'command' and 'description'")
            continue
        if _USER_CONFIG_REF.search(command):
            error(ctx.problems, Code.USER_CONFIG_IN_SHELL, at, "a monitor command runs through a shell and cannot reference ${user_config.*}")
        ctx.add(ComponentKind.MONITOR, name, at, Capability.EXEC, (command,))


def monitors(ctx: Context) -> None:
    experimental = ctx.manifest.get("experimental")
    value = experimental.get("monitors") if isinstance(experimental, dict) else None
    if value is None and "monitors" in ctx.manifest:
        value = ctx.manifest["monitors"]
        warn(ctx.problems, Code.DEPRECATED_LOCATION, _inline("monitors"), "move 'monitors' under 'experimental'")
    if value is None:
        if ctx.index.is_file("monitors/monitors.json"):
            data = _json_file(ctx, "monitors/monitors.json")
            if data is not None:
                _monitor_list(ctx, data, "monitors/monitors.json")
        return
    if isinstance(value, list):
        _monitor_list(ctx, value, _inline("experimental.monitors"))
        return
    for rel in _paths(ctx, value, "experimental.monitors"):
        data = _json_file(ctx, rel) if ctx.index.is_file(rel) else None
        if data is not None:
            _monitor_list(ctx, data, rel)


def root_files(ctx: Context) -> None:
    """``bin/``, ``settings.json`` and the root ``CLAUDE.md`` Claude Code does not load."""
    for rel in ctx.index.below("bin") if ctx.index.is_dir("bin") else []:
        ctx.add(ComponentKind.EXECUTABLE, rel[len("bin/"):], rel, Capability.EXEC)
    if ctx.index.is_file("settings.json"):
        data = _json_file(ctx, "settings.json")
        if isinstance(data, dict):
            # Only `agent` and `subagentStatusLine` take effect; a status line is a command Claude
            # Code runs on its own, so its presence makes the settings file exec.
            runs = "subagentStatusLine" in data
            ctx.add(ComponentKind.SETTINGS, "settings", "settings.json", Capability.EXEC if runs else Capability.TEXT)
        elif data is not None:
            error(ctx.problems, Code.BAD_TYPE, "settings.json", "must be an object")
    if ctx.index.is_file("CLAUDE.md"):
        warn(ctx.problems, Code.CLAUDE_MD_IGNORED, "CLAUDE.md", "a CLAUDE.md at the plugin root is not loaded; put instructions in a skill")


def channels(ctx: Context) -> None:
    value: object = ctx.manifest.get("channels")
    if value is None:
        return
    if not isinstance(value, list):
        error(ctx.problems, Code.BAD_TYPE, _inline("channels"), "must be an array")
        return
    for position, spec in enumerate(value):
        at = f"{_inline('channels')}[{position}]"
        entry: JsonObject = spec if isinstance(spec, dict) else {}
        for unknown in sorted(set(entry) - {"server", "displayName", "userConfig"}):
            error(ctx.problems, Code.UNKNOWN_FIELD_STRICT, f"{at}.{unknown}", "unknown key in a strict channels entry")
        if entry.get("server") not in ctx.mcp_names:
            error(ctx.problems, Code.BAD_COMPONENT, at, "a channel binds to one of this plugin's MCP servers by 'server'")


def discover(ctx: Context) -> None:
    """Run every finder. Order is the inventory order a card shows: instructions first, then what runs."""
    skills(ctx)
    commands(ctx)
    simple_folders(ctx)
    mcp_servers(ctx)
    lsp_servers(ctx)
    hooks(ctx)
    monitors(ctx)
    root_files(ctx)
    channels(ctx)
