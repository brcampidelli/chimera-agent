"""A plugin's MCP servers, added only when the owner names them, through the path every server takes.

Installing a plugin adds no server. Enabling it adds none either, unless the owner names servers
(``chimera plugin enable <name> --add-mcp <server>``): adding an MCP server is an owner decision
because it starts a process or opens a connection, often with credentials (ESTUDO-02, "Adicionar
servidor é decisão do dono"). Each named server is written with
:func:`chimera.integrations.mcp_config.add_server` — the same call ``chimera mcp add`` makes — so the
same rules follow it: the deployment policy (``mcp:<name>``), the bot allowlist
(``CHIMERA_BOT_MCP_SERVERS``), and the manifest pin (:mod:`chimera.integrations.mcp_pins`), which
pins the tool listing at its first mount and HOLDS the server on any later change until the owner
approves the diff.

The configured name is ``plugin-<plugin>-<server>``: visibly a plugin's, and never the same as a
server the owner configured by hand — a name already taken by one is refused, not replaced.

What v1 converts, and what it refuses with the reason (the owner can still configure any server by
hand with ``chimera mcp add``):

* ``${CLAUDE_PLUGIN_ROOT}`` becomes the installed tree's absolute path;
* ``${VAR}`` becomes Chimera's ``{env:VAR}`` (expanded only at connect time, never stored);
* ``${user_config.*}``, ``${VAR:-default}``, ``headers``/``headersHelper`` and ``.mcpb``/``.dxt``
  bundles are refused: v1 has no ``userConfig`` store, and a header helper is a shell command.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from pathlib import Path
from typing import Any

from chimera.plugins.model import ComponentKind
from chimera.plugins.registry import InstalledPlugin, PluginError, get, installed, tree_of
from chimera.plugins.scan import read_text, scan

_SERVER_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
_PLUGIN_ROOT = "${CLAUDE_PLUGIN_ROOT}"
_VAR = re.compile(r"\$\{([^}]*)\}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def configured_name(plugin: str, server: str) -> str:
    return f"plugin-{plugin}-{server}"


def _mcp_path(home: Path) -> Path:
    return Path(home) / "mcp.json"


def _raw_spec(tree: Path, where: str, server: str) -> dict[str, Any] | None:
    """Re-read one server's JSON from the installed tree, where the reader found it (bounded, no links)."""
    index = scan(tree)
    problems: list[Any] = []
    file_part, _, field = where.partition("#")
    text = read_text(index, file_part, problems)
    if text is None:
        return None
    try:
        data: Any = json.loads(text)
    except ValueError:
        return None
    if field:  # `.claude-plugin/plugin.json#mcpServers.<name>` — inline in the manifest
        data = data.get("mcpServers") if isinstance(data, dict) else None
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                nested = item.get("mcpServers")
                inner: dict[str, Any] = nested if isinstance(nested, dict) else item
                if isinstance(inner.get(server), dict):
                    return dict(inner[server])
        return None
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        data = data["mcpServers"]
    spec = data.get(server) if isinstance(data, dict) else None
    return dict(spec) if isinstance(spec, dict) else None


def _substitute(value: str, root: str) -> str:
    value = value.replace(_PLUGIN_ROOT, root)

    def replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner.startswith("user_config."):
            raise PluginError("it reads ${user_config.*}, and v1 has no userConfig store")
        if not _ENV_NAME.fullmatch(inner):
            raise PluginError(f"${{{inner}}} is not a plain environment reference")
        return "{env:" + inner + "}"

    return _VAR.sub(replace, value)


def to_config(record: InstalledPlugin, home: Path, server: str) -> Any:
    """The :class:`McpServerConfig` the owner would add for ``server``, or :class:`PluginError` saying why not."""
    from chimera.integrations.mcp_config import McpServerConfig

    row = next((r for r in record.by_kind(ComponentKind.MCP_SERVER.value) if r.get("name") == server), None)
    if row is None:
        raise PluginError(f"{record.name} has no MCP server named {server!r}")
    if not _SERVER_NAME.fullmatch(server):
        raise PluginError(f"the server name {server!r} is not a plain name")
    detail = list(row.get("detail") or [])
    if detail and detail[0] in ("bundle", "bundle-url"):
        raise PluginError(f"{server} is an MCP bundle (.mcpb/.dxt); v1 does not unpack bundles")
    tree = tree_of(home, record.name)
    spec = _raw_spec(tree, str(row.get("path", "")), server)
    if spec is None:
        raise PluginError(f"the configuration of {server} could not be read from the installed plugin")
    if "headers" in spec or "headersHelper" in spec:
        raise PluginError(f"{server} sends custom headers; configure it yourself with `chimera mcp add`")
    root = str(tree.resolve())
    name = configured_name(record.name, server)
    command = spec.get("command")
    if isinstance(command, str) and command:
        args = spec.get("args") or []
        env = spec.get("env") or {}
        if not isinstance(args, list) or not isinstance(env, dict):
            raise PluginError(f"{server}: 'args' is a list and 'env' an object")
        cwd = spec.get("cwd")
        return McpServerConfig(
            name=name,
            command=_substitute(command, root),
            args=[_substitute(str(arg), root) for arg in args],
            env={str(k): _substitute(str(v), root) for k, v in env.items()},
            cwd=_substitute(cwd, root) if isinstance(cwd, str) else None,
        )
    url = spec.get("url")
    if isinstance(url, str) and url.startswith("https://"):
        return McpServerConfig(name=name, url=_substitute(url, root))
    raise PluginError(f"{server}: v1 adds a stdio server or an https:// remote server")


def add_servers(home: Path, record: InstalledPlugin, servers: Collection[str]) -> list[str]:
    """Add the named servers through the MCP add path. All are converted before any is written, so a
    refused one leaves ``mcp.json`` untouched."""
    from chimera.integrations.mcp_config import _read_servers, add_server, server_denied

    path = _mcp_path(home)
    taken = {s.name for s in _read_servers(path)} - set(record.mcp_added)
    configs = []
    for server in dict.fromkeys(servers):
        cfg = to_config(record, home, server)
        if cfg.name in taken:
            raise PluginError(f"an MCP server named {cfg.name!r} is already configured; it is not replaced")
        if server_denied(cfg.name, path):
            raise PluginError(f"mcp:{cfg.name} is denied by the deployment policy")
        configs.append(cfg)
    for cfg in configs:
        add_server(path, cfg)  # forgets any old pin: the first mount pins, a later change is held
    return [cfg.name for cfg in configs]


def remove_servers(home: Path, names: Collection[str]) -> None:
    from chimera.integrations.mcp_config import remove_server

    for name in names:
        remove_server(_mcp_path(home), name)


def owner_of(home: Path, server_name: str) -> str | None:
    """The plugin that added the configured server ``server_name``, or None for an owner's own server."""
    if not server_name.startswith("plugin-"):
        return None
    for record in installed(home):
        if server_name in record.mcp_added:
            return record.name
    return None


def narrow_unattended(pool: Any, home: Path, scope: Collection[str] | None) -> Any:
    """``pool`` without the servers a plugin added, unless that plugin is in ``scope``.

    For the unattended surfaces (bots, a desktop conversation answered from a linked chat, a guest's
    turn, ``chimera serve``'s chat and ``/v1``): a plugin's server is an owner's server once added,
    and these surfaces already apply the owner's MCP rules (``CHIMERA_BOT_MCP_SERVERS``,
    ``CHIMERA_MCP_AUTOLOAD``); on top of those, a server a plugin brought needs the plugin itself in
    ``CHIMERA_UNATTENDED_PLUGINS``. ``scope`` None (an attended surface) leaves ``pool`` as it is.
    The narrowed pool holds the SAME connectors, so nothing is reconnected.
    """
    if pool is None or scope is None:
        return pool
    owners = {server: record.name for record in installed(home) for server in record.mcp_added}
    names = list(pool.names())
    keep = [name for name in names if owners.get(name, "") in scope or name not in owners]
    if len(keep) == len(names):
        return pool
    from chimera.integrations.connectors import ConnectorRegistry

    narrowed = ConnectorRegistry()
    for name in keep:
        narrowed.register(pool.get(name))
    return narrowed


def plugin_servers(home: Path, name: str) -> list[dict[str, Any]]:
    """The plugin's MCP servers as a card lists them: name, transport, and whether it was added."""
    record = get(home, name)
    if record is None:
        return []
    rows = []
    for row in record.by_kind(ComponentKind.MCP_SERVER.value):
        configured = configured_name(record.name, str(row.get("name")))
        rows.append({**row, "configured_as": configured, "added": configured in record.mcp_added})
    return rows


__all__ = [
    "add_servers", "configured_name", "narrow_unattended", "owner_of", "plugin_servers", "remove_servers",
    "to_config",
]
