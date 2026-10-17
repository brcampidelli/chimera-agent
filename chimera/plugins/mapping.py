"""What an ACTIVE plugin contributes to the pieces Chimera already has — and nothing more.

Only a plugin the owner switched on (``registry.active``) and the policy allows contributes; a
pending, inactive, held or denied one contributes nothing, and switching it off removes everything
at once because nothing is copied: every function here reads the installed tree on each call.

* **skills** → skill bundles named ``<plugin>:<skill>``, ``origin="plugin"``, ``provenance="tainted"``
  (:func:`skill_bundles`). They appear wherever bundles appear (``chimera.skills.bundles.installed``,
  the prompt line, ``skill_view``), and the prompt line quotes the author's description instead of
  stating it. A ``!`command``` line in a SKILL.md is text: Chimera has no skill pre-processor, and
  ``skill_view`` returns the file as untrusted output.
* **commands** → prompt commands named ``<plugin>:<command>`` (:func:`prompt_commands`), whose
  expansion is fenced as external data. Their ``allowed-tools`` and ``model`` are ignored.
* **agents** → Markdown subagents named ``<plugin>:<agent>`` under the PROJECT-agent rules
  (:func:`markdown_agents`): no model choice, tools intersected with the session's, body fenced.

**Surfaces.** A component reaches attended surfaces (the desktop's Code and chat, ``chat``,
``assist``, the TUI) by default. Every unattended surface — the bots, the cron, ``chimera serve``,
GitHub-issue jobs, the board's lanes, the MCP/A2A/ACP servers — is assembled through
``chimera.governance.profile.governed_profile``, which narrows the scope to the plugins the owner
lists in ``CHIMERA_UNATTENDED_PLUGINS`` (empty by default: none), mirroring
``CHIMERA_BOT_MCP_SERVERS`` (:func:`scope_for_surface`).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path
from typing import Any

import yaml

from chimera.plugins.model import ComponentKind
from chimera.plugins.registry import InstalledPlugin, active, tree_of
from chimera.plugins.scan import MAX_FILE_BYTES
from chimera.telemetry import get_logger

_log = get_logger("plugins.mapping")

_FRONT = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)(.*)\Z", re.DOTALL)
_SLUG = re.compile(r"[^a-z0-9_-]+")
#: A plugin agent's step budget. Claude Code agents carry no step count; this is the cap's half
#: (`chimera.core.markdown_agents.MAX_AGENT_STEPS` is 32), a budget a delegated task fits in.
PLUGIN_AGENT_STEPS = 16


def scope_for_surface(settings: Any, surface: str) -> frozenset[str] | None:
    """``None`` (every active plugin) on an attended surface; the owner's list everywhere else."""
    from chimera.governance.surfaces import is_attended_surface

    if surface and is_attended_surface(surface):
        return None
    listed = str(getattr(settings, "unattended_plugins", "") or "")
    return frozenset(name.strip() for name in listed.split(",") if name.strip())


def apply_scope(registry: Any, scope: frozenset[str] | None) -> None:
    """Stamp ``scope`` on a fresh registry and on its ``skill_view``, before any wrapper copies it."""
    from chimera.skills.aliases import SkillView

    try:
        registry.plugin_scope = scope
    except AttributeError:
        return
    tools = getattr(registry, "tools", None)
    for tool in tools() if callable(tools) else ():
        if isinstance(tool, SkillView):
            tool.plugin_scope = scope


def _read(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _front(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONT.match(text)
    if match is None:
        return {}, text
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        loaded = None
    return (loaded if isinstance(loaded, dict) else {}), match.group(2)


def _file(record: InstalledPlugin, home: Path, rel: str) -> Path | None:
    if "#" in rel:
        return None
    tree = tree_of(home, record.name).resolve()
    target = (tree / Path(*rel.split("/"))).resolve()
    return target if target.is_relative_to(tree) else None


def _plugins(home: Path, scope: Collection[str] | None) -> list[InstalledPlugin]:
    try:
        return active(home, scope)
    except Exception as exc:  # noqa: BLE001 -- a broken plugins folder must not cost a surface
        _log.warning("plugins not read: %s", type(exc).__name__)
        return []


# --- skills ----------------------------------------------------------------------------------------


def skill_bundles(home: Path, scope: Collection[str] | None = None) -> list[Any]:
    """The active plugins' skills as :class:`~chimera.skills.bundles.InstalledBundle` records."""
    from chimera.deployment_policy import integration_denied
    from chimera.skills.bundles import InstalledBundle

    out = []
    for record in _plugins(home, scope):
        for row in record.by_kind(ComponentKind.SKILL.value):
            qualified = f"{record.name}:{row['name']}"
            if integration_denied(f"skill:{qualified}"):
                continue
            path = _file(record, home, str(row["path"]))
            front, _ = _front(_read(path) or "") if path else ({}, "")
            description = front.get("description")
            out.append(InstalledBundle(
                name=qualified,
                description=str(description) if isinstance(description, str) else "",
                source=f"plugin:{record.name}",
                path=str(row["path"]),
                ref=record.ref or record.tree_sha256,
                license=record.license,
                installed_at=record.installed_at,
                status="active",
                switched_on_at=record.switched_on_at,
                origin="plugin",
                provenance="tainted",
            ))
    return out


def skill_dir(home: Path, qualified: str, scope: Collection[str] | None = None) -> Path | None:
    """The folder of an active plugin skill ``<plugin>:<skill>``, or None — the only door ``skill_view``
    has into a plugin, so a pending, held, denied or out-of-scope plugin cannot be read by name."""
    plugin, sep, skill = qualified.partition(":")
    if not sep:
        return None
    for bundle in skill_bundles(home, scope):
        if bundle.name == qualified:
            record_tree = tree_of(home, plugin).resolve()
            folder = (record_tree / Path(*bundle.path.split("/"))).resolve().parent
            return folder if folder.is_relative_to(record_tree) else None
    del skill
    return None


# --- commands --------------------------------------------------------------------------------------


def _manifest_command(record: InstalledPlugin, home: Path, name: str) -> str | None:
    """An inline ``commands.<name>.content`` from the manifest."""
    import json

    path = _file(record, home, ".claude-plugin/plugin.json")
    text = _read(path) if path else None
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return None
    commands = data.get("commands") if isinstance(data, dict) else None
    spec = commands.get(name) if isinstance(commands, dict) else None
    content = spec.get("content") if isinstance(spec, dict) else None
    return content if isinstance(content, str) else None


def prompt_commands(home: Path, scope: Collection[str] | None = None) -> list[Any]:
    """The active plugins' commands as untrusted :class:`~chimera.core.commands.PromptCommand` values."""
    from chimera.core.commands import PromptCommand

    out = []
    for record in _plugins(home, scope):
        for row in record.by_kind(ComponentKind.COMMAND.value):
            rel = str(row["path"])
            text = _manifest_command(record, home, str(row["name"])) if "#" in rel else None
            if text is None:
                path = _file(record, home, rel)
                text = _read(path) if path else None
            name = f"{record.name}:{row['name']}"
            if text is None:
                out.append(PromptCommand(name, "", f"plugin:{record.name}", "", error="could not read command",
                                         untrusted=True))
                continue
            front, body = _front(text)
            description = front.get("description")
            out.append(PromptCommand(
                name=name,
                description=str(description) if isinstance(description, str) else "",
                origin=f"plugin:{record.name}",
                template=body,
                untrusted=True,
            ))
    return out


# --- agents ----------------------------------------------------------------------------------------


def _tools(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def markdown_agents(home: Path, scope: Collection[str] | None = None) -> tuple[list[Any], list[Any]]:
    """The active plugins' agents as project-restricted :class:`MarkdownAgent` values, plus issues."""
    from chimera.core.markdown_agents import PROJECT_MODEL_IGNORED, AgentIssue, MarkdownAgent
    from chimera.core.registry import MAX_INSTRUCTION_CHARS, AgentDef

    agents: list[Any] = []
    issues: list[Any] = []
    for record in _plugins(home, scope):
        for row in record.by_kind(ComponentKind.AGENT.value):
            qualified = f"{record.name}:{row['name']}"
            source = f"plugin:{record.name}/{row['path']}"
            path = _file(record, home, str(row["path"]))
            text = _read(path) if path else None
            front, body = _front(text or "")
            body = body.strip()
            if not body or len(body) > MAX_INSTRUCTION_CHARS:
                issues.append(AgentIssue(source, "plugin agent has an empty or oversized body", qualified))
                continue
            tools = _tools(front.get("tools"))
            if front.get("model") is not None:
                issues.append(AgentIssue(source, PROJECT_MODEL_IGNORED, qualified))
            if not tools:
                issues.append(AgentIssue(source, "declares no tools; it runs with none", qualified))
            slug = (_SLUG.sub("-", str(row["name"]).lower()).strip("-_") or "agent")[:40]
            description = front.get("description")
            agents.append(MarkdownAgent(
                definition=AgentDef(id=slug, name=qualified, instructions=body, model="", allowed_tools=tools),
                description=str(description).strip()[:500] if isinstance(description, str) else qualified,
                steps=PLUGIN_AGENT_STEPS,
                source=source,
                from_project=True,
                qualified_id=qualified,
            ))
    return agents, issues


__all__ = [
    "PLUGIN_AGENT_STEPS",
    "apply_scope",
    "markdown_agents",
    "prompt_commands",
    "scope_for_surface",
    "skill_bundles",
    "skill_dir",
]
