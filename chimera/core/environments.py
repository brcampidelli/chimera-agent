"""Where a Code conversation's commands run: saved environments, and the rule that fixes the choice.

An *environment* is a place the agent's shell commands execute. ``local`` is what has always
happened (the install's configured sandbox, on this machine). ``wsl`` runs them in a Linux distro on
the same Windows host (:mod:`chimera.sandbox.wsl`). The next kind, ``ssh``, is meant to be one more
entry in :data:`_KINDS`: a name, a way to validate its parameters, and a way to build its sandbox.
Nothing else in this file or in the turn path knows which kinds exist.

The list lives on the server, in the home folder beside the project registry, because the choice is a
fact about the installation and not about a window. The owner makes it on the Code screen before the
first message of a conversation and it is **locked after the first turn**, as the worktree is: a
conversation whose commands ran in one place and then in another has a transcript that describes two
machines. A conversation with no recorded environment is ``local``; that is every conversation made
before this existed, and it reads exactly as it did.

Two properties this file exists to keep:

* **Only the owner's screen chooses.** A paired device, the desktop bridge, a guest's share link, a
  linked chat and a background work are refused a choice (:func:`decide`), and a conversation that
  already has an environment keeps running in it whoever sends the next turn. Where commands run is
  reach, and reach is the owner's.
* **An environment never loosens anything.** It only decides which sandbox object a turn's shell and
  code tools get. The host-exec gate, the folder grant, Manual / Accept edits and the owner's
  ``deny`` are read from the same places they always were, and :func:`sandbox_for` refuses an
  environment that would run commands outside an isolation the install configured.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.config import Settings
    from chimera.sandbox.base import Sandbox

LOCAL_ID = "local"
FILE_NAME = "environments.json"

MAX_ENVIRONMENTS = 50
MAX_NAME = 80
MAX_PROJECT_KEY = 4096


class EnvironmentRefused(Exception):
    """A request the environment rules refuse, with the HTTP status the caller answers it with."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Environment:
    """One place to run commands: an id, a name the owner reads, a kind, and the kind's parameters."""

    id: str
    name: str
    kind: str
    params: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind, "params": dict(self.params)}

    def record(self) -> dict[str, str]:
        """What a conversation keeps of its environment: enough to show and to find it again.

        No parameters: the saved entry is the authority for those, so removing an environment stops
        a conversation from running in it instead of leaving a copy that still can.
        """
        return {"id": self.id, "name": self.name, "kind": self.kind}


LOCAL = Environment(LOCAL_ID, "Local", "local", {})


# -- kinds --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Kind:
    #: Cleans and checks the parameters the owner typed; raises ``ValueError`` with a sentence.
    validate: Callable[[Mapping[str, Any]], dict[str, str]]
    #: Whether this machine can use the kind at all (WSL: Windows).
    available: Callable[[], bool]
    #: The sandbox a turn in ``workspace`` gets, or None for "the install's own" (local).
    build: Callable[[Environment, Path], Sandbox | None]


def _validate_local(params: Mapping[str, Any]) -> dict[str, str]:
    if params:
        raise ValueError("the local environment takes no parameters")
    return {}


def _validate_wsl(params: Mapping[str, Any]) -> dict[str, str]:
    from chimera.sandbox.wsl import valid_distro

    extra = set(params) - {"distro"}
    if extra:
        raise ValueError(f"unknown parameter for a WSL environment: {sorted(extra)[0]}")
    distro = params.get("distro", "")
    if distro is None:
        distro = ""
    if not isinstance(distro, str):
        raise ValueError("distro must be text")
    distro = distro.strip()
    if distro and not valid_distro(distro):
        raise ValueError("distro is not a valid distro name")
    return {"distro": distro} if distro else {}


def _wsl_available() -> bool:
    from chimera.sandbox.wsl import wsl_supported

    return wsl_supported()


def _build_wsl(env: Environment, workspace: Path) -> Sandbox | None:
    from chimera.sandbox.wsl import WslSandbox

    return WslSandbox(workspace, env.params.get("distro", ""))


#: The kinds this build knows. ``ssh`` joins here, next to ``wsl``, with its own validator (a host
#: alias from ``~/.ssh/config``, never a key or a password) and its own sandbox.
_KINDS: dict[str, _Kind] = {
    "local": _Kind(_validate_local, lambda: True, lambda _env, _ws: None),
    "wsl": _Kind(_validate_wsl, _wsl_available, _build_wsl),
}

#: The kinds an owner may CREATE (``local`` is built in).
CREATABLE_KINDS: tuple[str, ...] = tuple(k for k in _KINDS if k != "local")


def kind_available(kind: str) -> bool:
    spec = _KINDS.get(kind)
    return spec is not None and spec.available()


def sandbox_for(env: Environment, workspace: Path, settings: Settings) -> Sandbox | None:
    """The sandbox ``env`` gives a turn in ``workspace``; None means the install's own (``get_sandbox``).

    Refuses (:class:`EnvironmentRefused`) rather than degrading: a kind this machine cannot run, and
    an environment that is not isolated on an install that configured isolation. Choosing WSL while
    ``CHIMERA_SANDBOX=docker`` would run on the host the commands the owner asked to keep in a
    container, which is the one way an environment could loosen something the local one would not.
    """
    spec = _KINDS.get(env.kind)
    if spec is None:
        raise EnvironmentRefused(409, f"this build cannot run environments of kind {env.kind!r}")
    if not spec.available():
        raise EnvironmentRefused(409, f"the {env.kind} environment is not available on this machine")
    built = spec.build(env, workspace)
    if built is not None and _install_isolates(settings):
        raise EnvironmentRefused(
            409,
            "this install runs commands in an isolated sandbox (CHIMERA_SANDBOX); "
            f"the {env.kind} environment is not isolated and would loosen that",
        )
    return built


def _install_isolates(settings: Settings) -> bool:
    """Whether the install's own sandbox is one the host-exec gate treats as isolation."""
    if (settings.sandbox or "auto").lower() == "docker":
        return True
    from chimera.sandbox import get_sandbox
    from chimera.sandbox.confirm import sandbox_is_isolated

    return sandbox_is_isolated(get_sandbox(settings))


# -- the saved list -----------------------------------------------------------------------------

_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = path.resolve(strict=False)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def project_key(path: str) -> str:
    """The key a project's default is stored under: the resolved folder, case-folded on Windows."""
    text = (path or "").strip()
    if not text or len(text) > MAX_PROJECT_KEY:
        raise ValueError("project is required")
    try:
        resolved = str(Path(text).expanduser().resolve(strict=False))
    except (OSError, ValueError, RuntimeError) as exc:
        raise ValueError("project is not a usable path") from exc
    return os.path.normcase(resolved)


class EnvironmentRegistry:
    """The saved environments, and each project's optional default, as a file under the home folder.

    Re-read on every call, like the project registry, so a hand-edited file is honoured and there is
    no cache to disagree with. A file that does not parse is set aside, never overwritten.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = _lock_for(self.path)

    # reading -----------------------------------------------------------------------------

    def entries(self) -> list[Environment]:
        with self._lock:
            return self._read()[0]

    def get(self, env_id: str) -> Environment | None:
        """The environment with this id; ``local`` is always there."""
        if env_id == LOCAL_ID:
            return LOCAL
        return next((e for e in self.entries() if e.id == env_id), None)

    def default_for(self, project: str) -> str:
        """The environment id a new conversation in ``project`` starts with, or "" (none set)."""
        try:
            key = project_key(project)
        except ValueError:
            return ""
        with self._lock:
            return self._read()[1].get(key, "")

    def defaults(self) -> dict[str, str]:
        with self._lock:
            return dict(self._read()[1])

    # writing -----------------------------------------------------------------------------

    def add(self, name: str, kind: str, params: Mapping[str, Any] | None = None) -> Environment:
        clean = (name or "").strip()
        if not clean:
            raise ValueError("name is required")
        if len(clean) > MAX_NAME:
            raise ValueError(f"name is longer than {MAX_NAME} characters")
        if kind not in CREATABLE_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(CREATABLE_KINDS)}")
        spec = _KINDS[kind]
        if not spec.available():
            raise ValueError(f"the {kind} environment is not available on this machine")
        cleaned = spec.validate(params or {})
        with self._lock:
            rows, defaults = self._read()
            if len(rows) >= MAX_ENVIRONMENTS:
                raise ValueError(f"at most {MAX_ENVIRONMENTS} environments")
            if clean.casefold() in {r.name.casefold() for r in rows} | {LOCAL.name.casefold()}:
                raise ValueError("an environment with this name already exists")
            env = Environment(uuid.uuid4().hex[:12], clean, kind, cleaned)
            self._write([*rows, env], defaults)
            return env

    def remove(self, env_id: str) -> bool:
        """Forget an environment and every project default that named it. False if it was not there."""
        if env_id == LOCAL_ID:
            raise ValueError("the local environment cannot be removed")
        with self._lock:
            rows, defaults = self._read()
            kept = [r for r in rows if r.id != env_id]
            if len(kept) == len(rows):
                return False
            self._write(kept, {k: v for k, v in defaults.items() if v != env_id})
            return True

    def set_default(self, project: str, env_id: str) -> None:
        """Make ``env_id`` the default of ``project``; ``""`` clears it."""
        key = project_key(project)
        with self._lock:
            rows, defaults = self._read()
            if env_id and env_id != LOCAL_ID and env_id not in {r.id for r in rows}:
                raise ValueError("no such environment")
            if env_id and env_id != LOCAL_ID:
                defaults[key] = env_id
            else:
                defaults.pop(key, None)
            self._write(rows, defaults)

    # file --------------------------------------------------------------------------------

    def _read(self) -> tuple[list[Environment], dict[str, str]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return [], {}
        except (OSError, ValueError):
            self._set_aside()
            return [], {}
        if not isinstance(raw, dict):
            self._set_aside()
            return [], {}
        rows: list[Environment] = []
        seen: set[str] = set()
        listed = raw.get("environments")
        for item in listed if isinstance(listed, list) else []:
            env = self._row(item)
            if env is not None and env.id not in seen and env.id != LOCAL_ID:
                seen.add(env.id)
                rows.append(env)
        ids = {r.id for r in rows}
        stored = raw.get("project_defaults")
        defaults = {
            k: v
            for k, v in (stored.items() if isinstance(stored, dict) else [])
            if isinstance(k, str) and isinstance(v, str) and v in ids
        }
        return rows, defaults

    @staticmethod
    def _row(item: object) -> Environment | None:
        """A stored row, re-validated: a hand-edited file does not get to name a distro the form refuses."""
        if not isinstance(item, dict):
            return None
        env_id, name, kind, params = item.get("id"), item.get("name"), item.get("kind"), item.get("params")
        if not (isinstance(env_id, str) and isinstance(name, str) and isinstance(kind, str)):
            return None
        if not env_id.strip() or not name.strip() or kind not in CREATABLE_KINDS:
            return None
        try:
            cleaned = _KINDS[kind].validate(params if isinstance(params, dict) else {})
        except ValueError:
            return None
        return Environment(env_id, name.strip(), kind, cleaned)

    def _write(self, rows: list[Environment], defaults: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"environments": [r.to_json() for r in rows], "project_defaults": defaults}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def _set_aside(self) -> None:
        aside = self.path.with_suffix(self.path.suffix + ".damaged")
        with contextlib.suppress(OSError):
            self.path.replace(aside)


def registry_for(home: Path | str) -> EnvironmentRegistry:
    return EnvironmentRegistry(Path(home) / FILE_NAME)


# -- the decision of one turn -------------------------------------------------------------------


@dataclass(frozen=True)
class TurnEnvironment:
    """What a turn runs in, and whether this turn is the one that fixes it."""

    env: Environment
    #: True on the conversation's first turn: the caller records ``env.record()`` on it.
    fix_now: bool


def decide(
    registry: EnvironmentRegistry,
    *,
    requested: str | None,
    stored: Mapping[str, Any] | None,
    locked: bool,
    may_choose: bool,
    project: str = "",
) -> TurnEnvironment:
    """The environment of one turn, or :class:`EnvironmentRefused`.

    ``locked`` is "this conversation already has a turn (or a recorded environment)". Then the
    environment is the recorded one (``local`` when none), the request may repeat it but not change
    it, and the saved entry is looked up again: a removed environment ends the conversation's
    commands instead of falling back to the local machine, which would be a different place than the
    transcript describes. On a first turn only the owner's screen may name one (``may_choose``);
    every other first turn is local, and a project's default applies only to the owner's own.
    """
    if locked:
        recorded = str((stored or {}).get("id") or LOCAL_ID)
        if requested is not None and requested != recorded:
            raise EnvironmentRefused(
                409,
                "the environment is fixed once the conversation has started (a first message that failed "
                "before it was saved fixes it too); open a new conversation to use another",
            )
        found = registry.get(recorded)
        if found is None:
            raise EnvironmentRefused(
                409, "the environment this conversation runs in was removed; open a new conversation"
            )
        return TurnEnvironment(found, fix_now=False)
    if requested is not None and not may_choose:
        raise EnvironmentRefused(403, "the environment is chosen by the owner on the app's own screen")
    wanted = requested
    if wanted is None and may_choose and project:
        wanted = registry.default_for(project) or None
    found = registry.get(wanted or LOCAL_ID)
    if found is None:
        raise EnvironmentRefused(422, "no such environment")
    return TurnEnvironment(found, fix_now=True)
