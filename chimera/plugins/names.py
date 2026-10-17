"""Name rules for plugins and marketplaces, copied from Claude Code rather than invented.

Sources, read 2026-10-17:

* plugin ``name`` — https://code.claude.com/docs/en/plugins-reference#name : non-empty, no spaces,
  ``@``, ``:``, path separators, control or bidirectional-formatting characters; kebab-case is the
  convention (a non-kebab name is a ``claude plugin validate`` warning, not an error). The names that
  pass as Anthropic's own are errors, and a whole-word ``claude``/``anthropic`` is a warning.
* plugin id part — https://code.claude.com/docs/en/plugins/marketplace-reference#plugin-entries :
  "letters, digits, ``.``, ``_``, and ``-``, starting with a letter or digit"; Claude Code cannot
  install any other name, so for a marketplace entry it is an error.
* marketplace ``name`` and its reserved list — same page, "Reserved names".

Why it matters here and not only in Claude Code: a name becomes a namespace (``plugin:skill``) and,
in a later step, a directory. ``chimera.skills.bundles`` learned on Windows that ``skills / "C:"`` is
the skills directory itself; the rule below never lets ``:``, a separator or ``..`` through.
"""

from __future__ import annotations

import re
import unicodedata

from chimera.skills.bundles import is_bundle_name

#: The plugin-id shape Claude Code installs. Also refuses ``.``/``..`` because they cannot start
#: with a letter or digit; a ``..`` inside a name is refused separately.
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_KEBAB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SEPARATORS = re.compile(r"[-_.\s]+")

#: Claude Desktop's limit; Claude Code accepts longer, so past it is a warning, not an error.
MAX_NAME_CHARS = 128

_RESERVED_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")
_RESERVED_EXACT = frozenset({"claude", "anthropic", "anthropics", "claude-code", "claude-mods"})
_BRAND_WORDS = frozenset({"claude", "anthropic", "anthropics"})

_RESERVED_MARKETPLACES = frozenset({
    "claude-code-marketplace", "claude-code-plugins", "claude-plugins-official",
    "anthropic-marketplace", "anthropic-plugins", "agent-skills", "anthropic-agent-skills",
    "life-sciences", "knowledge-work-plugins", "claude-for-legal", "claude-for-financial-services",
    "financial-services-plugins", "first-party-plugins", "claude-tag-plugins", "claude-community",
    "claude-plugins-community", "healthcare", "anthropic-plugin-directory", "claude-plugin-directory",
})
#: Reserved whatever the origin: names Claude Code uses itself, package managers, and the members
#: every JavaScript object has (a marketplace named ``constructor`` would collide with them there).
_ALWAYS_RESERVED_MARKETPLACES = frozenset({
    "inline", "builtin", "skills-dir", "synced", "claude-plugin-test",
    "npm", "pip", "uv", "cargo", "github", "gh",
    "constructor", "hasownproperty", "isprototypeof", "propertyisenumerable", "tolocalestring",
    "tostring", "valueof",
})


def _has_control(name: str) -> bool:
    # Cc is control, Cf includes the bidirectional overrides (U+202E and friends) that make a name
    # read differently from what it is.
    return any(unicodedata.category(char) in ("Cc", "Cf") for char in name)


def _words(name: str) -> list[str]:
    return [word for word in _SEPARATORS.split(name.casefold()) if word]


def _normalized(name: str) -> str:
    """Claude Code's comparison: case ignored, any run of separators read as one hyphen."""
    return "-".join(_words(name))


def plugin_name_error(name: str) -> str | None:
    """Why Claude Code would refuse ``name`` as a plugin name, or None."""
    if not name:
        return "the name is empty"
    if _has_control(name):
        return "the name contains a control or bidirectional-formatting character"
    if any(char in name for char in " \t@:/\\"):
        return "the name contains a space, '@', ':' or a path separator"
    if not _ID.fullmatch(name) or ".." in name:
        return "the name may use only letters, digits, '.', '_' and '-', starting with a letter or digit"
    return None


def reserved_plugin_name(name: str) -> bool:
    """Whether the name passes as one of Anthropic's own — a ``claude plugin validate`` error."""
    normalized = _normalized(name)
    words = set(normalized.split("-"))
    return (
        normalized in _RESERVED_EXACT
        or normalized.startswith(_RESERVED_PREFIXES)
        or ("official" in words and bool(words & {"claude", "anthropic"}))
    )


def reads_as_anthropic(name: str) -> bool:
    """A whole-word brand anywhere else, such as ``mcp-for-claude`` — a warning only."""
    return bool(set(_words(name)) & _BRAND_WORDS)


def is_kebab(name: str) -> bool:
    return bool(_KEBAB.fullmatch(name))


def is_installable_name(name: str) -> bool:
    """Whether the name can become an installed directory under Chimera's existing bundle rule.

    Stricter than Claude Code (lowercase, no dot, 2-64 chars). Exposed so the install step, not this
    reader, decides what to do with a valid Claude Code name that is not a safe Chimera directory.
    """
    return is_bundle_name(name)


def marketplace_name_error(name: str, *, anthropic_origin: bool = False) -> str | None:
    """Why Claude Code would refuse ``name`` as a marketplace name, or None.

    ``anthropic_origin`` is True only when the caller fetched the file from ``github.com/anthropics/``;
    the official names are reserved for that origin and nothing else.
    """
    if not name:
        return "the marketplace has no name"
    if _has_control(name):
        return "the name contains a control or bidirectional-formatting character"
    if not name.isascii():
        return "a non-ASCII marketplace name impersonates an official marketplace"
    if not _ID.fullmatch(name) or ".." in name:
        return "the name may use only letters, digits, '.', '_' and '-', starting with a letter or digit"
    folded = name.casefold()
    normalized = _normalized(name.rstrip("."))
    if folded in _ALWAYS_RESERVED_MARKETPLACES or folded.startswith("claudeai-"):
        return "the name is reserved by Claude Code"
    if anthropic_origin:
        return None
    words = set(normalized.split("-"))
    if normalized in _RESERVED_MARKETPLACES:
        return "the name is reserved for an official Anthropic marketplace"
    if words & {"claude", "anthropic"} and ("official" in words or "plugins" in words):
        # Covers the documented examples (`official-claude-plugins`, `claude-plugins-v2`) without
        # claiming to reproduce Claude Code's full impersonation heuristic, which is not published.
        return "the name impersonates an official Anthropic marketplace"
    return None
