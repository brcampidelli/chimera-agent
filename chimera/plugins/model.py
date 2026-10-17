"""The typed, immutable result of reading a plugin or a marketplace.

Nothing here touches the disk. The reader (`chimera.plugins.manifest`, `chimera.plugins.marketplace`)
builds these values; a screen, an installer or a test only reads them. Every value is frozen and
every collection is a tuple or a read-only mapping, because the same report is shown to the owner
and later compared against what an installer is about to do — a report that could be edited between
the two would make the comparison meaningless.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class Severity(StrEnum):
    """An error means Claude Code itself would refuse or drop the thing; a warning means it loads."""

    ERROR = "error"
    WARNING = "warning"


class Code(StrEnum):
    """Stable problem codes. A screen localises by code, so a code is never renamed once shipped.

    Adding a code is free; renaming one breaks every translation keyed on it, the same rule as the
    desktop's i18n keys.
    """

    # The manifest and its fields.
    BAD_JSON = "bad_json"
    MISSING_NAME = "missing_name"
    BAD_NAME = "bad_name"
    RESERVED_NAME = "reserved_name"
    NAME_READS_AS_ANTHROPIC = "name_reads_as_anthropic"
    NOT_KEBAB_CASE = "not_kebab_case"
    MISSING_RECOMMENDED = "missing_recommended"
    BAD_TYPE = "bad_type"
    BAD_URL = "bad_url"
    INSECURE_URL = "insecure_url"
    UNKNOWN_FIELD = "unknown_field"
    UNKNOWN_FIELD_STRICT = "unknown_field_strict"
    DEPRECATED_LOCATION = "deprecated_location"
    BAD_CHIMERA_METADATA = "bad_chimera_metadata"
    # Paths.
    PATH_NOT_RELATIVE = "path_not_relative"
    PATH_ESCAPES_PLUGIN = "path_escapes_plugin"
    BACKSLASH_IN_PATH = "backslash_in_path"
    PATH_NOT_FOUND = "path_not_found"
    SYMLINK_OUT = "symlink_out"
    SYMLINK_IGNORED = "symlink_ignored"
    # Bounds.
    TOO_MANY_FILES = "too_many_files"
    TOO_DEEP = "too_deep"
    FILE_TOO_LARGE = "file_too_large"
    PLUGIN_TOO_LARGE = "plugin_too_large"
    # Components.
    BAD_COMPONENT = "bad_component"
    BAD_HOOKS = "bad_hooks"
    DEFAULT_FOLDER_IGNORED = "default_folder_ignored"
    UNDECLARED_USER_CONFIG = "undeclared_user_config"
    USER_CONFIG_IN_SHELL = "user_config_in_shell"
    REMOTE_MCP_BUNDLE = "remote_mcp_bundle"
    CLAUDE_MD_IGNORED = "claude_md_ignored"
    NOT_A_PLUGIN = "not_a_plugin"
    # Marketplaces and sources.
    MISSING_MARKETPLACE = "missing_marketplace"
    MISSING_OWNER = "missing_owner"
    MISSING_PLUGINS = "missing_plugins"
    MISSING_DESCRIPTION = "missing_description"
    DUPLICATE_PLUGIN = "duplicate_plugin"
    BAD_SOURCE = "bad_source"
    UNSUPPORTED_SOURCE = "unsupported_source"
    UNPINNED_SOURCE = "unpinned_source"
    BAD_SHA = "bad_sha"


@dataclass(frozen=True, slots=True)
class Problem:
    """One finding. ``where`` is a plugin-relative POSIX path or a field such as ``plugin.json#name``."""

    code: Code
    severity: Severity
    where: str
    message: str


class Capability(StrEnum):
    """What a component would DO if it were ever switched on — the line a plugin card shows.

    ``text`` only ever reaches the model as instructions. ``process`` starts a local process (an MCP
    stdio server, an LSP server, an MCP bundle). ``network`` connects to a remote MCP server and
    starts nothing locally. ``exec`` runs commands on its own, without the model asking: hooks,
    monitors, workflows, ``bin/`` and a status-line setting. The split follows Claude Code's own
    security page, which lists hooks, monitors and MCP/LSP servers as what runs outside its sandbox
    (https://code.claude.com/docs/en/plugins/security.md).
    """

    TEXT = "text"
    NETWORK = "network"
    PROCESS = "process"
    EXEC = "exec"


class ComponentKind(StrEnum):
    SKILL = "skill"
    COMMAND = "command"
    AGENT = "agent"
    OUTPUT_STYLE = "output_style"
    THEME = "theme"
    WORKFLOW = "workflow"
    HOOK = "hook"
    MCP_SERVER = "mcp_server"
    LSP_SERVER = "lsp_server"
    MONITOR = "monitor"
    EXECUTABLE = "executable"
    SETTINGS = "settings"


@dataclass(frozen=True, slots=True)
class Component:
    """One thing a plugin brings.

    ``path`` is where it was read, relative to the plugin root in POSIX form; an inline definition
    is written ``.claude-plugin/plugin.json#<field>.<key>``. ``detail`` carries short facts a card
    may show, such as an MCP server's transport and command or a hook's event handler types. It is
    untrusted text copied from the plugin, never something to run.
    """

    kind: ComponentKind
    name: str
    path: str
    capability: Capability
    detail: tuple[str, ...] = ()

    def qualified(self, plugin: str) -> str:
        """Claude Code's namespace: every component lives under its plugin, as ``plugin:name``."""
        return f"{plugin}:{self.name}"


@dataclass(frozen=True, slots=True)
class Author:
    name: str
    email: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class UserConfigOption:
    """A ``userConfig`` option. Only its shape is kept; a default value is not a secret but is not needed."""

    key: str
    type: str
    title: str
    sensitive: bool


def _empty() -> Mapping[str, object]:
    # A factory, not a shared default: Python 3.11's dataclass refuses an unhashable default
    # (mappingproxy is one) at class creation, which 3.12 no longer checks the same way.
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """The plugin's identity and metadata, from ``.claude-plugin/plugin.json`` or, without one, its folder.

    ``name_from`` says where the name came from: ``manifest``, or ``directory`` when the plugin has
    no manifest (Claude Code then takes the marketplace entry's name or the folder name).
    ``default_enabled`` is recorded as the plugin declares it; a Chimera installer ignores it for a
    third-party plugin, which always starts pending. ``chimera`` is the read-only content of
    ``metadata.chimera`` — the one place a Chimera-specific field may live, because Claude Code
    does not read ``metadata`` and the plugin stays valid in both worlds.
    """

    name: str
    name_from: str
    display_name: str | None = None
    version: str | None = None
    description: str | None = None
    author: Author | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: tuple[str, ...] = ()
    default_enabled: bool = True
    dependencies: tuple[str, ...] = ()
    user_config: tuple[UserConfigOption, ...] = ()
    chimera: Mapping[str, object] = field(default_factory=_empty)


@dataclass(frozen=True, slots=True)
class CapabilitySummary:
    """Counts per capability, for the card line ("só instruções", "sobe 1 servidor MCP", ...)."""

    text: int = 0
    network: int = 0
    process: int = 0
    exec: int = 0

    @property
    def instructions_only(self) -> bool:
        return self.network == 0 and self.process == 0 and self.exec == 0


def summarize(components: tuple[Component, ...]) -> CapabilitySummary:
    counts = {capability: 0 for capability in Capability}
    for component in components:
        counts[component.capability] += 1
    return CapabilitySummary(
        text=counts[Capability.TEXT],
        network=counts[Capability.NETWORK],
        process=counts[Capability.PROCESS],
        exec=counts[Capability.EXEC],
    )


def _has_error(problems: tuple[Problem, ...]) -> bool:
    return any(problem.severity is Severity.ERROR for problem in problems)


@dataclass(frozen=True, slots=True)
class PluginReport:
    """What reading one plugin directory produced. ``manifest`` is None only when no name could be established."""

    manifest: PluginManifest | None
    components: tuple[Component, ...]
    problems: tuple[Problem, ...]

    @property
    def ok(self) -> bool:
        """No errors: Claude Code would load it as read. Warnings do not count."""
        return self.manifest is not None and not _has_error(self.problems)

    @property
    def capabilities(self) -> CapabilitySummary:
        return summarize(self.components)

    def codes(self) -> frozenset[Code]:
        return frozenset(problem.code for problem in self.problems)


class SourceKind(StrEnum):
    LOCAL = "local"
    GITHUB = "github"
    GIT = "url"
    GIT_SUBDIR = "git-subdir"
    ARCHIVE = "archive"
    NPM = "npm"
    COMMAND = "command"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PluginSource:
    """Where a marketplace entry's plugin comes from, and whether a v1 Chimera installer could fetch it.

    Only three kinds are installable: ``local`` (a path inside the marketplace), ``github`` pinned
    to a full commit ``sha``, and ``archive`` pinned by ``sha256``. Everything else is described but
    refused, with the reason in ``refusal`` — an unpinned source because what was reviewed is not
    what would be fetched later, ``npm`` and ``command`` because they are supply-chain or host
    execution surface with no gain for v1 (ESTUDO-01 §6.2).
    """

    kind: SourceKind
    path: str | None = None
    repo: str | None = None
    url: str | None = None
    ref: str | None = None
    sha: str | None = None
    sha256: str | None = None
    package: str | None = None
    refusal: Code | None = None

    @property
    def installable(self) -> bool:
        return self.refusal is None

    @property
    def from_anthropic(self) -> bool:
        """Whether the source is a repository under ``github.com/anthropics/`` — the only "por Anthropic" a card may show."""
        return self.kind is SourceKind.GITHUB and (self.repo or "").lower().startswith("anthropics/")


@dataclass(frozen=True, slots=True)
class MarketplaceEntry:
    name: str
    source: PluginSource
    problems: tuple[Problem, ...]
    display_name: str | None = None
    description: str | None = None
    version: str | None = None
    category: str | None = None
    tags: tuple[str, ...] = ()
    author: Author | None = None

    @property
    def installable(self) -> bool:
        """A v1 installer could fetch it: the source is supported and pinned, and the entry has no error."""
        return self.source.installable and not _has_error(self.problems)


@dataclass(frozen=True, slots=True)
class MarketplaceReport:
    """What reading one ``marketplace.json`` produced. One bad entry never fails the whole file."""

    name: str | None
    owner: Author | None
    description: str | None
    version: str | None
    plugins: tuple[MarketplaceEntry, ...]
    problems: tuple[Problem, ...]

    @property
    def ok(self) -> bool:
        return self.name is not None and not _has_error(self.problems)
