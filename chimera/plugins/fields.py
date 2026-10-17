"""Typed reads of untrusted JSON fields, shared by the plugin and marketplace readers.

Every helper returns a typed value or None and appends a Problem; none raises for bad input. A type
mismatch is an error because Claude Code's own validator fails on one ("Validation failed: the
manifest has a type mismatch", https://code.claude.com/docs/en/plugins-reference#validate-the-manifest).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from types import MappingProxyType

from chimera.plugins.model import Author, Code, Problem, Severity, UserConfigOption

JsonObject = dict[str, object]


def _at(where: str, key: str) -> str:
    """``plugin.json#`` + ``name`` → ``plugin.json#name``; ``plugins[0]`` + ``name`` → ``plugins[0].name``."""
    if not where or where.endswith("#"):
        return f"{where}{key}"
    return f"{where}.{key}"


def load_json(text: str, where: str, problems: list[Problem]) -> object | None:
    try:
        parsed: object = json.loads(text)
    except (ValueError, RecursionError) as exc:
        # RecursionError: a 2 MiB file of nested brackets is valid-looking input that would otherwise
        # escape as an exception from a reader that promises not to raise.
        problems.append(Problem(Code.BAD_JSON, Severity.ERROR, where, f"not valid JSON: {str(exc)[:120]}"))
        return None
    return parsed


def error(problems: list[Problem], code: Code, where: str, message: str) -> None:
    problems.append(Problem(code, Severity.ERROR, where, message))


def warn(problems: list[Problem], code: Code, where: str, message: str) -> None:
    problems.append(Problem(code, Severity.WARNING, where, message))


def text(data: Mapping[str, object], key: str, where: str, problems: list[Problem]) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        error(problems, Code.BAD_TYPE, _at(where, key), "must be a string")
        return None
    return value


def boolean(data: Mapping[str, object], key: str, where: str, problems: list[Problem], default: bool) -> bool:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        error(problems, Code.BAD_TYPE, _at(where, key), "must be true or false")
        return default
    return value


def strings(data: Mapping[str, object], key: str, where: str, problems: list[Problem]) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        error(problems, Code.BAD_TYPE, _at(where, key), "must be an array of strings")
        return ()
    return tuple(item for item in value if isinstance(item, str))


def author(data: Mapping[str, object], key: str, where: str, problems: list[Problem]) -> Author | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        error(problems, Code.BAD_TYPE, _at(where, key), "must be an object with 'name'")
        return None
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        error(problems, Code.BAD_TYPE, _at(where, key) + ".name", "an author needs a non-empty 'name'")
        return None
    email = value.get("email")
    url = value.get("url")
    return Author(
        name=name,
        email=email if isinstance(email, str) else None,
        url=url if isinstance(url, str) else None,
    )


def is_absolute_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return bool(parsed.scheme) and bool(parsed.netloc)


def freeze(value: object) -> object:
    """A deep read-only copy of parsed JSON: objects become mappings that refuse writes, arrays tuples."""
    if isinstance(value, dict):
        return MappingProxyType({str(key): freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def chimera_metadata(data: Mapping[str, object], where: str, problems: list[Problem]) -> Mapping[str, object]:
    """``metadata.chimera``: the only home for Chimera-specific fields. Claude Code never reads ``metadata``."""
    metadata = data.get("metadata")
    if metadata is None:
        return MappingProxyType({})
    if not isinstance(metadata, dict):
        warn(problems, Code.BAD_TYPE, _at(where, "metadata"), "'metadata' must be an object; ignored")
        return MappingProxyType({})
    chimera = metadata.get("chimera")
    if chimera is None:
        return MappingProxyType({})
    if not isinstance(chimera, dict):
        warn(problems, Code.BAD_CHIMERA_METADATA, _at(where, "metadata.chimera"), "must be an object; ignored")
        return MappingProxyType({})
    frozen = freeze(chimera)
    return frozen if isinstance(frozen, Mapping) else MappingProxyType({})


#: https://code.claude.com/docs/en/plugins-reference#user-configuration
_USER_CONFIG_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_USER_CONFIG_TYPES = frozenset({"string", "number", "boolean", "directory", "file"})
_USER_CONFIG_FIELDS = frozenset({
    "type", "title", "description", "required", "default", "options", "multiple", "sensitive", "min", "max",
})


def user_config(value: object, where: str, problems: list[Problem]) -> tuple[UserConfigOption, ...]:
    """Read ``userConfig``. Each option is a strict object: an unknown key is an error, as in Claude Code."""
    if value is None:
        return ()
    if not isinstance(value, dict):
        error(problems, Code.BAD_TYPE, where, "must be an object of options")
        return ()
    options: list[UserConfigOption] = []
    for key, spec in value.items():
        at = _at(where, key)
        if not _USER_CONFIG_KEY.fullmatch(key):
            error(problems, Code.BAD_COMPONENT, at, "an option key uses letters, digits and '_', not starting with a digit")
            continue
        if not isinstance(spec, dict):
            error(problems, Code.BAD_TYPE, at, "must be an object")
            continue
        for unknown in sorted(set(spec) - _USER_CONFIG_FIELDS):
            error(problems, Code.UNKNOWN_FIELD_STRICT, f"{at}.{unknown}", "unknown key in a strict userConfig option")
        kind, title, description = spec.get("type"), spec.get("title"), spec.get("description")
        if not isinstance(kind, str) or kind not in _USER_CONFIG_TYPES or not isinstance(title, str) or not isinstance(description, str):
            error(problems, Code.BAD_COMPONENT, at, "needs 'type' (string|number|boolean|directory|file), 'title' and 'description'")
            continue
        options.append(UserConfigOption(key=key, type=kind, title=title, sensitive=spec.get("sensitive") is True))
    return tuple(options)
