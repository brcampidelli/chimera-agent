"""Owner-authored deployment policy: ordered denies that later rules may only undo locally.

``<CHIMERA_HOME>/policy.json`` (or the file ``CHIMERA_POLICY_FILE`` names, for a container whose
home is a mounted volume) holds ``{"rules": [{"action", "resource", "effect"}, ...]}``. Rules are
read in order and the LAST one that matches decides; with no matching rule the answer is "not
denied", exactly what an install without the file has always done.

**The golden rule: an ``allow`` here never grants anything.** It only undoes an earlier ``deny`` of
this same file. It cannot reopen the per-surface reach floor (:mod:`chimera.api.posture`), a kernel
BLOCK, or ``CHIMERA_TOOL_DENYLIST`` — every caller applies this policy *in addition to* those, and
nothing here is consulted as a reason to permit. The policy answers one question, ``denied(...)``;
there is deliberately no ``allowed(...)`` a caller could mistake for a grant.

Three actions:

- ``provider.use`` — resource is the canonical provider id (:func:`provider_of`). Matched without
  regard to case, because provider ids are lowercase everywhere they are spelled and ``OpenRouter``
  in a hand-written file means the same thing. ``openrouter/anthropic/x`` is the provider
  ``openrouter``: the policy speaks about who receives the request, not whose model answers.
- ``integration.use`` — ``mcp:<server name>``, ``skill:<skill name>`` or ``plugin:<plugin name>``.
  Case-SENSITIVE, like the names themselves (two MCP servers may differ only in case). A denied
  plugin cannot be installed or switched on, and its skills, commands, agents and the MCP servers it
  added disappear (`chimera.plugins`); a plugin's skill is also ``skill:<plugin>:<skill>``.
- ``tool.use`` — the tool's registered name, case-sensitive for the same reason.

An invalid file is never applied in part (a half-read file could drop exactly the deny that was
malformed). At startup it stops ``chimera serve``/``chat``/``tui``; in a process that already loaded
a valid version, a file that turns invalid keeps the LAST VALID policy — a typo while editing must
never make the install permissive for the seconds or hours until somebody notices.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

_LOG = logging.getLogger("chimera.policy")
_CACHE_LOCK = threading.RLock()
#: path -> (the exact text that produced it, the policy). Keyed by text rather than mtime: a save
#: inside the file system's timestamp resolution would otherwise serve the previous version.
_CACHE: dict[Path, tuple[str, DeploymentPolicy]] = {}
Action = Literal["provider.use", "integration.use", "tool.use"]
Effect = Literal["deny", "allow"]

#: Prefixes that name the same runtime under another route. ``ollama_chat/`` is LiteLLM's chat
#: endpoint for Ollama; a deny of ``ollama`` that missed it would leave the default local slug open.
_PROVIDER_ALIASES = {"ollama_chat": "ollama"}


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Action
    resource: str
    effect: Effect


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rules: list[PolicyRule]


class PolicyError(ValueError):
    """The owner policy exists but cannot be interpreted safely."""


@dataclass(frozen=True)
class DeploymentPolicy:
    path: Path
    rules: tuple[PolicyRule, ...]

    def denied(self, action: Action, resource: str) -> bool:
        """Apply the last matching rule; no match is not denied, as absence of policy always was."""
        if action == "provider.use":
            resource = provider_name(resource)
        decision = False
        for rule in self.rules:
            if rule.action != action:
                continue
            pattern = rule.resource.lower() if action == "provider.use" else rule.resource
            if fnmatch.fnmatchcase(resource, pattern):
                decision = rule.effect == "deny"
        return decision

    def denies_provider_of(self, slug: str) -> bool:
        """Whether the provider a model ``slug`` is sent to is denied (see :func:`provider_of`)."""
        return self.denied("provider.use", provider_of(slug))

    def effective(self) -> list[dict[str, str]]:
        """The validated owner-authored rules, in order, without inventing implicit grants."""
        return [rule.model_dump() for rule in self.rules]


def policy_path(settings: object) -> Path:
    """The policy file for ``settings``: ``CHIMERA_POLICY_FILE`` when set, else ``<home>/policy.json``.

    Read from the ``Settings`` object only. Reading ``os.environ`` here as well made a test's
    ``Settings(_env_file=None, CHIMERA_POLICY_FILE=...)`` lose to whatever the shell exported, and
    gave the policy two sources that could disagree.
    """
    configured = getattr(settings, "policy_file", None)
    if configured:
        return Path(configured).expanduser()
    return _home(settings) / "policy.json"


def _home(settings: object) -> Path:
    # Duck-typed settings (the tier resolver's protocol, test doubles) may carry no home; the
    # process's own is then the only one there is.
    home = getattr(settings, "home", None)
    if home is None:
        from chimera.config import get_settings

        home = get_settings().home
    return Path(home).expanduser()


def provider_name(name: str) -> str:
    """A provider NAME (``OpenRouter``, ``ollama_chat``) as the canonical lowercase policy id."""
    lowered = name.strip().lower()
    return _PROVIDER_ALIASES.get(lowered, lowered)


def provider_of(slug: str) -> str:
    """The provider a model ``slug`` is SENT to — the one place a slug becomes a policy id.

    A slug with a prefix is decided by its first segment, which is what the gateway itself routes on
    (``openrouter/anthropic/claude-x`` goes to OpenRouter; ``ollama_chat/llama3`` to Ollama). A bare
    slug (``gpt-4o``) is routed by LiteLLM's own inference, so the same inference names it here;
    without it a deny of ``openai`` would have let ``gpt-4o`` through.
    """
    text = slug.strip()
    head, separator, _ = text.partition("/")
    if separator:
        if head.lower() == "endpoint":
            endpoint = text.split("/", 2)[1] if len(text.split("/", 2)) == 3 else ""
            return f"endpoint:{endpoint}"
        return provider_name(head)
    try:
        import litellm  # lazy: heavy, and only bare slugs need it

        _model, provider, _key, _base = litellm.get_llm_provider(text)
        if provider:
            return provider_name(str(provider))
    except Exception:  # noqa: BLE001 -- an unknown bare slug is spoken of by its own name
        pass
    return provider_name(text)


def _read(path: Path) -> DeploymentPolicy:
    text = path.read_text(encoding="utf-8")
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == text:
        return cached[1]
    try:
        document = PolicyDocument.model_validate_json(text)
    except (ValueError, ValidationError) as exc:
        raise PolicyError(f"Invalid deployment policy {path}: {exc}") from exc
    policy = DeploymentPolicy(path, tuple(document.rules))
    if cached is None:
        _LOG.info("deployment policy loaded from %s (%d rules)", path, len(policy.rules))
    else:
        _LOG.warning("deployment policy at %s changed (%d rules)", path, len(policy.rules))
    _CACHE[path] = (text, policy)
    return policy


def load_policy(settings: object) -> DeploymentPolicy:
    """Read and validate the complete file; partial application would hide malformed denies.

    Raises :class:`PolicyError` only when the file is invalid AND this process never saw a valid
    version of it. After one valid load, an invalid file keeps that version (logged as an error),
    and a removed file is logged and then means "no rules" — removing the file is how an owner turns
    the policy off, so it is honoured, but never silently.
    """
    path = policy_path(settings).resolve()
    with _CACHE_LOCK:
        if not path.exists():
            if _CACHE.pop(path, None) is not None:
                _LOG.warning("deployment policy removed at %s; no policy rules apply now", path)
            return DeploymentPolicy(path, ())
        try:
            return _read(path)
        except (OSError, UnicodeError, PolicyError) as exc:
            error = exc if isinstance(exc, PolicyError) else PolicyError(
                f"Invalid deployment policy {path}: {exc}"
            )
            previous = _CACHE.get(path)
            if previous is not None:
                _LOG.error("%s — keeping the last valid policy (%d rules)", error, len(previous[1].rules))
                return previous[1]
            _LOG.error("%s", error)
            raise error from exc


def policy_error(settings: object) -> str | None:
    """Why the policy file cannot be used, or None. For startup checks and status screens.

    Validates the file as it is on disk NOW, ignoring the last-valid fallback: a status screen that
    reported "fine" because an old version is still in force would hide the broken file from the one
    person who can fix it.
    """
    path = policy_path(settings).resolve()
    if not path.exists():
        return None
    try:
        PolicyDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, ValidationError) as exc:
        return f"Invalid deployment policy {path}: {exc}"
    return None


def is_policy_file(target: Path, settings: object) -> bool:
    """Whether ``target`` is the owner's policy file (the configured one or ``<home>/policy.json``).

    By identity and by the name Windows would open: ``policy.json.``, ``POLICY.JSON`` and
    ``policy.json::$DATA`` are the same file, and a write that would CREATE it has no identity yet,
    so the folder and the opened name decide (`chimera.core.own_files`).
    """
    from chimera.core.own_files import normal_name, same_file

    candidates = {policy_path(settings), _home(settings) / "policy.json"}
    target = Path(target).expanduser()
    for protected in candidates:
        if same_file(target, protected):
            return True
        if normal_name(target.name).lower() == protected.name.lower() and same_file(
            target.parent, protected.parent
        ):
            return True
    return False


def is_denied(settings: object, action: Action, resource: str) -> bool:
    return load_policy(settings).denied(action, resource)


def tool_denied(name: str) -> bool:
    """Whether the running install's policy denies the tool ``name`` — fail CLOSED.

    For the tool boundary (:meth:`chimera.tools.registry.ToolRegistry.run`, the deferral proxies):
    an unreadable policy that this process never saw valid answers "denied" for every tool, because
    the tool the broken file was meant to deny cannot be told apart from the others. ``chimera
    serve``/``chat``/``tui`` refuse to start in that state; this is what still holds in a process
    that started anyway (the desktop app, which starts so it can show the error).
    """
    return _denied_failing_closed("tool.use", name)


def integration_denied(resource: str) -> bool:
    """Whether the running install's policy denies ``mcp:``, ``skill:`` or ``plugin:<name>`` — fail CLOSED,
    for the reason :func:`tool_denied` gives."""
    return _denied_failing_closed("integration.use", resource)


def _denied_failing_closed(action: Action, resource: str) -> bool:
    from chimera.config import get_settings

    try:
        return load_policy(get_settings()).denied(action, resource)
    except PolicyError:
        return True
