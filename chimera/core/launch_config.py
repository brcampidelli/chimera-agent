"""The project's dev-server recipe: ``.chimera/launch.json`` (or ``.claude/launch.json``).

The same file the Claude Code desktop app reads, so a project that already has one works here
unchanged. The folder's own file is the ONLY thing that can name a command to start through the
preview panel and the agent's ``preview`` tool: the tool takes a NAME, never a command.

Starting an entry is RUNNING A COMMAND, and the file is project content, so this module does not make
it safe by itself; what does is where it is used. The file tools may not write ``.chimera/`` or
``.claude/launch.json`` (`write_region.ALWAYS_DENIED`), but a shell command can, so the agent's start
goes through the very path ``run_shell`` takes (kernel, taint ledger, host-exec question, fences), is
refused when the file, ``package.json`` or a file the entry runs changed during the turn
(`executed_files`), and never runs on a tainted run. ``env`` cannot set what changes how a program
loads (:data:`FORBIDDEN_ENV`).

Format::

    {"version": "0.0.1", "autoVerify": true,
     "configurations": [{"name": "web", "runtimeExecutable": "npm", "runtimeArgs": ["run", "dev"],
                         "port": 3000, "cwd": "${workspaceFolder}/web", "env": {"NODE_ENV": "development"},
                         "autoPort": true}]}

* ``program`` + ``args`` is sugar for ``runtimeExecutable: "node"`` with ``runtimeArgs: [program, ...args]``
  (the two spellings cannot be mixed in one entry). No other debugger-style field is read.
* ``port`` defaults to 3000. The process is told its port through ``PORT`` and through a ``${PORT}``
  placeholder in ``runtimeArgs``; a server that ignores both is not detected as listening on a
  different port, so say the port it really uses.
* ``autoPort``: true picks a free port when ``port`` is busy; false refuses to start when it is busy
  (an OAuth callback or a CORS rule pins a port); absent behaves as true.
* ``url`` (no ``runtimeExecutable``) names a server that is already running. Only ``http``/``https`` on
  loopback (``localhost``, ``127.0.0.1``, ``[::1]``), the origin only, and its port is the entry's
  ``port``. Nothing is started for it.
* ``env`` is committed with the repository. A name or a value that looks like a credential is a
  WARNING, never an error: the file is still read, and the panel says so.

Parsing is strict: an unknown field, a wrong type, an escaping ``cwd`` or a duplicate name makes the
whole file invalid with a sentence per fault, and an invalid file starts nothing. Stdlib + pydantic
only; no I/O except :func:`load_launch_config`.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from chimera.core.redact import is_secret_env

#: Where the recipe lives, in the order it is looked for: ours first, Claude Code's as the fallback.
LAUNCH_FILES: tuple[str, ...] = (".chimera/launch.json", ".claude/launch.json")
#: A file bigger than this is not a recipe.
MAX_FILE_BYTES = 64 * 1024
MAX_CONFIGURATIONS = 20
DEFAULT_PORT = 3000

#: Names an entry may not set: they change which code a program loads or which program runs, so a
#: committed recipe would be a way to run something other than what it says. Refused, not stripped.
FORBIDDEN_ENV = frozenset({
    "PATH", "PATHEXT", "COMSPEC", "SHELL", "ENV", "BASH_ENV", "IFS", "CDPATH", "HOME", "USERPROFILE",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "NODE_OPTIONS", "NODE_PATH", "NODE_EXTRA_CA_CERTS",
    "NODE_TLS_REJECT_UNAUTHORIZED", "PYTHONSTARTUP", "PYTHONPATH", "PYTHONHOME", "PYTHONINSPECT",
    "PYTHONUSERBASE", "PYTHONWARNINGS", "RUBYOPT", "RUBYLIB", "PERL5OPT", "PERL5LIB", "JAVA_TOOL_OPTIONS",
    "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "CLASSPATH", "GIT_SSH_COMMAND", "GIT_EXEC_PATH",
    "GOFLAGS", "CARGO_BUILD_RUSTC_WRAPPER", "RUSTC_WRAPPER", "BROWSER", "EDITOR", "VISUAL", "PORT",
})
_FORBIDDEN_ENV_PREFIXES = ("DYLD_", "LD_", "NPM_CONFIG_", "YARN_", "BUN_", "PNPM_", "BASH_FUNC_")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: What a value may be made of: it reaches the command line of a shell, so nothing a shell reads.
_ENV_VALUE = re.compile(r"^[A-Za-z0-9_.,:/@+=%-]*$")

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_WORKSPACE_VAR = "${workspaceFolder}"


class ServerConfig(BaseModel):
    """One entry of ``configurations``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str
    runtimeExecutable: str | None = Field(default=None, max_length=300)  # the file's own spelling
    runtimeArgs: list[str] = Field(default_factory=list, max_length=64)
    program: str | None = Field(default=None, max_length=300)
    args: list[str] = Field(default_factory=list, max_length=64)
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    cwd: str | None = Field(default=None, max_length=400)
    env: dict[str, str] = Field(default_factory=dict, max_length=64)
    autoPort: bool | None = None
    url: str | None = Field(default=None, max_length=300)

    @field_validator("name")
    @classmethod
    def _name_is_plain(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("name must be 1-60 characters: letters, digits, space, dot, dash, underscore")
        return value

    @field_validator("runtimeArgs", "args")
    @classmethod
    def _args_are_short(cls, value: list[str]) -> list[str]:
        if any(len(item) > 500 or "\x00" in item for item in value):
            raise ValueError("an argument is too long or contains a NUL")
        return value

    @field_validator("env")
    @classmethod
    def _env_is_plain(cls, value: dict[str, str]) -> dict[str, str]:
        for key, text in value.items():
            upper = key.upper()
            if not _ENV_NAME.match(key):
                raise ValueError(f"env name {key!r} is not a plain variable name")
            if upper in FORBIDDEN_ENV or upper.startswith(_FORBIDDEN_ENV_PREFIXES):
                raise ValueError(f"env {key} changes what the program loads or runs and cannot be set here")
            if not _ENV_VALUE.match(text) or len(text) > 300:
                raise ValueError(f"env {key} holds characters a command line cannot carry safely")
        return value

    @field_validator("runtimeExecutable", "program")
    @classmethod
    def _no_shell_syntax(cls, value: str | None) -> str | None:
        if value is not None and (not value or re.search(r"[\x00\r\n&|;<>`$]", value)):
            raise ValueError("must be one executable or script path, with no shell syntax")
        return value

    @model_validator(mode="after")
    def _shape(self) -> ServerConfig:
        has_exe = self.runtimeExecutable is not None
        has_prog = self.program is not None
        if has_exe and has_prog:
            raise ValueError("runtimeExecutable and program cannot be combined")
        if (self.args and not has_prog) or (self.runtimeArgs and has_prog):
            raise ValueError("args goes with program, runtimeArgs with runtimeExecutable")
        if self.url is not None:
            if has_exe or has_prog:
                raise ValueError("url names a server already running: it cannot also carry a command")
            if self.runtimeArgs or self.args or self.env or self.cwd:
                raise ValueError("url names a server already running: command fields do not apply")
            _check_url(self.url, self.port)
        elif not (has_exe or has_prog):
            raise ValueError("needs runtimeExecutable (or program), or url for a server already running")
        return self

    @property
    def attach_only(self) -> bool:
        """A server somebody else started: there is nothing to start or stop here."""
        return self.url is not None

    def argv(self, port: int) -> list[str]:
        """The command line, with ``${PORT}`` replaced. Never run through a shell."""
        if self.program is not None:
            head, rest = "node", [self.program, *self.args]
        else:
            head, rest = str(self.runtimeExecutable), list(self.runtimeArgs)
        return [head, *(item.replace("${PORT}", str(port)) for item in rest)]

    def command_line(self, port: int) -> str:
        """The argv as one string, for the owner's question and the log header."""
        return " ".join(_quote(part) for part in self.argv(port))


def _quote(part: str) -> str:
    return f'"{part}"' if re.search(r"\s", part) else part


def _check_url(url: str, port: int) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url must be http or https")
    if parsed.username or parsed.password:
        raise ValueError("url must not carry a user name or password")
    host = (parsed.hostname or "").lower()
    try:
        loopback = host in _LOOPBACK_HOSTS or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError("url must be on localhost, 127.0.0.1 or [::1]: an outside server is not previewed")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.params:
        raise ValueError("url must be the origin only (no path, query or fragment)")
    if parsed.port is not None and parsed.port != port:
        raise ValueError(f"url port {parsed.port} does not match port {port}")


class LaunchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(default="0.0.1", max_length=20)
    autoVerify: bool = True
    configurations: list[ServerConfig] = Field(default_factory=list, max_length=MAX_CONFIGURATIONS)

    @model_validator(mode="after")
    def _unique_names(self) -> LaunchConfig:
        seen: set[str] = set()
        for entry in self.configurations:
            key = entry.name.lower()
            if key in seen:
                raise ValueError(f"duplicate configuration name {entry.name!r}")
            seen.add(key)
        return self

    def get(self, name: str) -> ServerConfig | None:
        return next((c for c in self.configurations if c.name == name), None)


@dataclass(frozen=True)
class LaunchLoad:
    """What reading the folder found. ``config`` is None when there is no file or it is invalid."""

    config: LaunchConfig | None = None
    source: Path | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        return self.source is not None


def secret_warnings(config: LaunchConfig) -> list[str]:
    """A line per ``env`` entry that looks like a credential. The file goes to the repository."""
    out: list[str] = []
    for entry in config.configurations:
        for key, value in entry.env.items():
            if is_secret_env(key, value):
                out.append(
                    f"{entry.name}: env {key} looks like a secret; launch.json is committed with the "
                    "project, so keep credentials in the environment or a .env file instead"
                )
    return out


def parse_launch_config(raw: str) -> tuple[LaunchConfig | None, list[str]]:
    """Parse the file's text. ``(config, [])`` when valid, ``(None, [one sentence per fault])`` when not."""
    try:
        data: Any = json.loads(raw)
    except ValueError as exc:
        return None, [f"not valid JSON: {exc}"]
    if not isinstance(data, dict):
        return None, ["the file must hold one JSON object"]
    try:
        return LaunchConfig.model_validate(data), []
    except ValidationError as exc:
        faults = []
        for item in exc.errors():
            where = ".".join(str(part) for part in item["loc"]) or "file"
            faults.append(f"{where}: {item['msg'].removeprefix('Value error, ')}")
        return None, faults


#: Files a dev server's tool reads at start: any `*.config.*`, the named bundler configs, `server.*` and `.env*`.
_CONFIG_GLOBS = (
    "*.config.*", "vite.config.*", "webpack.*", "next.config.*", "nuxt.config.*", "server.*", ".env*",
)
_PACKAGE_RUNNERS = frozenset({"npm", "npx", "yarn", "pnpm", "bun", "bunx", "deno", "npm.cmd", "yarn.cmd", "pnpm.cmd"})


def recipe_files(root: Path) -> list[Path]:
    """The recipe files themselves, present or not: a change to either is a change to what would run."""
    return [root / relative for relative in LAUNCH_FILES]


def executed_files(root: Path, entry: ServerConfig) -> list[Path]:
    """Files under ``root`` that running ``entry`` would execute or read as its scripts.

    The entry's own program and every argument that names a file in the project, and ``package.json`` for a
    package runner (its ``scripts`` are the command). Used to refuse a start when any of them changed during
    the turn: the agent wrote what is about to run.
    """
    base = root.resolve()
    work = resolve_cwd(root, entry.cwd) or base
    candidates = [entry.runtimeExecutable or entry.program or "", *entry.runtimeArgs, *entry.args]
    found: list[Path] = []
    for raw in candidates:
        if not raw or raw.startswith("-"):
            continue
        for anchor in (work, base):
            path = (anchor / raw.replace("${workspaceFolder}", str(base))).resolve()
            if path.is_file() and (path == base or path.is_relative_to(base)):
                found.append(path)
                break
    exe = Path(entry.runtimeExecutable or "").name.lower()
    if exe in _PACKAGE_RUNNERS:
        found += [work / "package.json", base / "package.json"]
    # What a dev server reads on the way up: its tool's config and its environment files, in the folder
    # it runs in and the project root. Not recursive, and files only.
    for folder in dict.fromkeys([work, base]):
        for pattern in _CONFIG_GLOBS:
            found += sorted(p.resolve() for p in folder.glob(pattern) if p.is_file())
    return list(dict.fromkeys(found))


def resolve_cwd(root: Path, cwd: str | None) -> Path | None:
    """The working directory under ``root``, or None when it names somewhere else.

    ``${workspaceFolder}`` is the only variable. A path outside the project is refused rather than
    clamped: a recipe that says ``..`` is asking for a folder the owner did not open.
    """
    base = root.resolve()
    if not cwd:
        return base
    text = cwd.replace(_WORKSPACE_VAR, str(base))
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    if resolved != base and not resolved.is_relative_to(base):
        return None
    return resolved


def load_launch_config(root: Path) -> LaunchLoad:
    """Read the recipe of the project at ``root``: ``.chimera/launch.json``, else ``.claude/launch.json``."""
    for relative in LAUNCH_FILES:
        path = root / relative
        try:
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                return LaunchLoad(source=path, errors=(f"{relative} is larger than {MAX_FILE_BYTES} bytes",))
            raw = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return LaunchLoad(source=path, errors=(f"{relative} could not be read: {exc.__class__.__name__}",))
        config, errors = parse_launch_config(raw)
        if config is None:
            return LaunchLoad(source=path, errors=tuple(errors))
        faults = [
            f"{entry.name}: cwd {entry.cwd!r} is outside the project"
            for entry in config.configurations
            if entry.cwd and resolve_cwd(root, entry.cwd) is None
        ]
        if faults:
            return LaunchLoad(source=path, errors=tuple(faults))
        return LaunchLoad(config=config, source=path, warnings=tuple(secret_warnings(config)))
    return LaunchLoad()
