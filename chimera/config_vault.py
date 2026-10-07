"""Provider keys in the operating system's own vault, instead of in a file or an environment.

Twelve credentials live in environment variables and, for most installs, in a `.env` beside the
project. That is the ordinary way to do this and it has one property nobody chose: the secret is
readable, in plain text, by anything running as that user — including the agent itself, and
including whatever the agent was asked to do with `cat`. This project has already paid for that
twice, with an OpenRouter key and a PassaPro token found in cleartext.

macOS, Windows and most Linux desktops ship a vault that solves exactly this, and `keyring` is the
one library that speaks to all three. It is an OPTIONAL extra: a container has no keychain, a server
has no session bus, and a tool that refuses to start without one would be worse than the file.

**The environment always wins.** This fills gaps; it never overrides. An install that works today
keeps working, unchanged, with no vault involved — and someone debugging with `OPENROUTER_API_KEY=…`
in front of a command gets the key they typed, which is the only behaviour that is not surprising.

**Read once, at startup, into the environment.** LiteLLM reads `os.environ` directly and so does
half of this codebase; a vault consulted lazily somewhere deeper would be a second source of truth
that disagrees with the first under conditions nobody could predict. One load, one place, before
`Settings` is built.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


#: The logger is fetched per call, not at import. `telemetry` reads settings, and settings loads
#: this module — importing it at module level makes a cycle whose symptom is an `ImportError` from
#: `get_settings`, i.e. the process failing to start.
def _log_() -> Any:
    from chimera.telemetry import get_logger

    return get_logger("config.vault")

#: The vault entry all of this lives under. One service name so `chimera secrets list` can find
#: what it wrote, and so an uninstall has one thing to clear.
SERVICE = "chimera-agent"

#: What may be stored. An allowlist, not "any variable": the vault is for CREDENTIALS, and letting
#: it carry `CHIMERA_DEFAULT_MODEL` would turn a security boundary into a second, invisible config
#: file that nobody thinks to look in when a setting is wrong.
STORABLE = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "ELEVENLABS_API_KEY",
    "TAVILY_API_KEY",
    "GITHUB_TOKEN",
    "SUPABASE_ACCESS_TOKEN",
    "STRIPE_API_KEY",
    "CHIMERA_SERVER_TOKEN",
    "CHIMERA_OPENROUTER_KEYS",
    # The rest of what the desktop's Settings screen masks as a credential (study 29, P7.7). The
    # screen offers to keep its keys in the vault, and a toggle that quietly left five of them in
    # the file would be a promise about "the keys" that holds for some of them. Same allowlist
    # reasoning as above: each is a secret the screen already treats as one, nothing else.
    "BRAVE_API_KEY",
    "SERPAPI_API_KEY",
    "STABILITY_API_KEY",
    "CHIMERA_DISCORD_BOT_TOKEN",
    "CHIMERA_TELEGRAM_BOT_TOKEN",
    "CHIMERA_OPENAI_KEYS",
    "CHIMERA_ANTHROPIC_KEYS",
    "CHIMERA_GEMINI_KEYS",
    "CHIMERA_DEEPSEEK_KEYS",
    # The GitHub webhook secrets sign the events that start code-writing jobs; the screen masks
    # them as a credential, so the vault may hold them like the rest.
    "CHIMERA_GITHUB_WEBHOOK_SECRETS",
)


def _keyring() -> Any | None:
    """The library, or None when it is not installed or has no working backend.

    Both failures are the same answer here: there is no vault on this machine. A container without
    a keychain and a desktop without `keyring` installed both need the file, and distinguishing them
    would only produce two error messages for one situation.
    """
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailBackend
    except ImportError:
        return None
    try:
        if isinstance(keyring.get_keyring(), FailBackend):
            return None
    except Exception:  # noqa: BLE001 — a backend that cannot even be queried is not a backend
        return None
    return keyring


def available() -> bool:
    """Whether this machine has a usable vault."""
    return _keyring() is not None


def store(name: str, value: str) -> bool:
    """Put one credential in the vault. False when there is no vault, or the name is not storable."""
    name = name.upper()
    if name not in STORABLE:
        return False
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(SERVICE, name, value)
    except Exception as exc:  # noqa: BLE001 — a locked keychain is a refusal, not a crash
        _log_().warning("could not write %s to the vault: %s", name, exc)
        return False
    return True


#: What Windows' Credential Manager keeps per entry: CRED_MAX_CREDENTIAL_BLOB_SIZE, 5 * 512 bytes.
#: `keyring`'s Windows backend writes the value as ONE UTF-16 blob, unsplit (keyring 25.7,
#: `backends/Windows.py`, `_set_password`), so ~1280 characters is the most a key can be there. A
#: rotation pool reaches it: ~17 OpenRouter keys, ~11 Anthropic ones. Past it `CredWrite` fails, and
#: the failure reads exactly like a locked vault unless something says otherwise first.
WINDOWS_BLOB_LIMIT = 5 * 512


def too_large(value: str, *, platform: str | None = None) -> bool:
    """Whether this machine's vault cannot hold ``value`` at all. Only Windows has a limit a key
    can reach; macOS' Keychain and the Secret Service take values far past any credential."""
    if (sys.platform if platform is None else platform) != "win32":
        return False
    return len(value.encode("utf-16-le")) > WINDOWS_BLOB_LIMIT


def forget(name: str) -> bool:
    """Remove one credential. False when it was not there."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.delete_password(SERVICE, name.upper())
    except Exception:  # noqa: BLE001 — "not found" is the common case and is not an error here
        return False
    return True


def stored() -> list[str]:
    """Which credentials this vault holds. NAMES only — never the values.

    A listing that printed secrets would put them in a terminal's scrollback, in a screenshot, and
    in whatever captured the session — undoing the entire point of storing them in a vault.
    """
    kr = _keyring()
    if kr is None:
        return []
    achados = []
    for name in STORABLE:
        try:
            if kr.get_password(SERVICE, name):
                achados.append(name)
        except Exception:  # noqa: BLE001 — an unreadable entry is one we cannot report on
            continue
    return achados


def load_into_environment(
    environ: dict[str, str] | None = None,
    *,
    names: Sequence[str] = STORABLE,
    env_file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] | None = None,
) -> list[str]:
    """Fill any storable credential the environment does not already have. Returns what was filled.

    The environment wins, always. Someone running `OPENROUTER_API_KEY=… chimera solve` is making a
    deliberate, visible choice for that one command, and a vault that overrode it would be a
    setting that cannot be overridden from the shell — the one place people expect to be able to.

    So does a value ``env_file`` assigns. Filling ``os.environ`` is not neutral: pydantic-settings
    ranks the environment ABOVE the dotenv file, so a vault copy written there for a name the file
    also sets would outrank the file — the vault overriding a key the owner typed into `.env`, and
    every later edit of that line ignored at each launch, with nothing on screen saying why. The
    Settings screen's way back reports such a key as "the file's value is in force"; this is what
    makes that true.

    ``names`` narrows what is read: the frozen desktop build reads only what its `.env` marks
    (:func:`startup_names`).
    """
    env = os.environ if environ is None else environ
    kr = _keyring()
    if kr is None:
        return []
    in_file = assigned(env_file)
    preenchidos = []
    for name in names:
        if name not in STORABLE or env.get(name) or name in in_file:
            continue
        try:
            value = kr.get_password(SERVICE, name)
        except Exception:  # noqa: BLE001 — a locked vault fills nothing and breaks nothing
            continue
        if value:
            env[name] = value
            preenchidos.append(name)
    if preenchidos:
        _log_().debug("loaded %d credential(s) from the OS vault", len(preenchidos))
    return preenchidos


def read(name: str) -> str | None:
    """One credential's value, for the code that has to move or verify it. Never for a client.

    The desktop's Settings screen moves keys between the vault and the `.env`, and a move that did
    not read the value back before deleting the original would be one locked keychain away from
    losing the key. Nothing that answers a request returns what this returns.
    """
    name = name.upper()
    if name not in STORABLE:
        return None
    kr = _keyring()
    if kr is None:
        return None
    try:
        value = kr.get_password(SERVICE, name)
    except Exception:  # noqa: BLE001 — an unreadable entry is an absent one
        return None
    return str(value) if value else None


def _mcp_vault_account(name: str) -> str:
    """Stable, non-secret account key for a per-server MCP OAuth credential."""
    import hashlib

    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]
    return f"mcp-oauth-{digest}"


def store_mcp_token(name: str, token: str) -> bool:
    """Store an MCP OAuth token in the OS vault, without ever exposing it in logs."""
    kr = _keyring()
    if kr is None or not token:
        return False
    try:
        kr.set_password(SERVICE, _mcp_vault_account(name), token)
    except Exception:  # noqa: BLE001 — credential values never enter an error message
        _log_().warning("could not write MCP OAuth credential to the vault")
        return False
    return True


def read_mcp_token(name: str) -> str | None:
    """Read an MCP OAuth token by configured server name; return no value to callers' logs."""
    kr = _keyring()
    if kr is None:
        return None
    try:
        value = kr.get_password(SERVICE, _mcp_vault_account(name))
    except Exception:  # noqa: BLE001 — an unreadable credential is absent
        return None
    return str(value) if value else None


#: The line the desktop leaves in `.env` where a key used to be, once the key is in the vault.
#:
#: A COMMENT, never `KEY=<placeholder>`. Every reader of the file — pydantic-settings, the
#: credential export in `get_settings`, python-dotenv itself — skips comments, while a placeholder
#: value would be read as the key: it would win over the vault (the environment and the file both
#: outrank it) and be sent to the provider as a credential. The marker exists for people, who open
#: the file and need to learn where the key went, and for one reader that needs it: the frozen
#: desktop build, which consults the vault only when the file says something lives there.
_MARKER = "# {name}: in the OS vault ({service})"
_MARKER_LINE = re.compile(r"^#\s*([A-Z][A-Z0-9_]*): in the OS vault \(([^)]*)\)\s*$")


def marker(name: str) -> str:
    """The `.env` line that says ``name`` lives in the vault."""
    return _MARKER.format(name=name.upper(), service=SERVICE)


def marker_name(line: str) -> str | None:
    """The credential a marker line names, or None when the line is not one of ours."""
    found = _MARKER_LINE.match(line.strip())
    if found is None or found.group(2) != SERVICE or found.group(1) not in STORABLE:
        return None
    return found.group(1)


def _env_paths(env_file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] | None) -> list[Path]:
    if not env_file:
        return []
    if isinstance(env_file, (str, os.PathLike)):
        return [Path(env_file)]
    return [Path(entry) for entry in env_file]


def marked(env_file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] | None) -> list[str]:
    """The credentials whose `.env` entry is a vault marker, in file order. Reads the file only."""
    found: list[str] = []
    for path in _env_paths(env_file):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            name = marker_name(line)
            if name is not None and name not in found:
                found.append(name)
    return found


def assigned(env_file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] | None) -> set[str]:
    """The storable credentials ``env_file`` gives a non-empty value, read the way dotenv reads them.

    Upper-cased: `Settings` is case-insensitive, so a lower-case line in the file is still the key
    in force. A missing or unreadable file assigns nothing.
    """
    paths = _env_paths(env_file)
    if not paths:
        return set()
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - a hard dependency of pydantic-settings
        return set()
    found: set[str] = set()
    for path in paths:
        try:
            values = dotenv_values(path) if path.is_file() else {}
        except OSError:
            continue
        found.update(name.upper() for name, value in values.items() if value)
    return found & set(STORABLE)


#: Never read from the vault at startup by the FROZEN build. The desktop shell (Rust) finds the
#: server token only in its environment or an uncommented `.env` line; a token the backend took from
#: the vault is one the shell cannot send, and every tray request becomes a 401 (see
#: `chimera.api.key_vault.SCREEN_STORABLE`). The screen never marks it, but a marker is a line
#: anyone can type, so the exclusion is stated here rather than trusted to the file.
_NEVER_FROZEN = frozenset({"CHIMERA_SERVER_TOKEN"})


def startup_names(
    env_file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] | None,
) -> tuple[str, ...]:
    """Which credentials this process reads from the vault at startup. Empty means: do not touch it.

    Every storable one, from a source or pip install — that is what `chimera secrets set` has
    promised since it existed, and nothing here may take it back.

    In the FROZEN desktop build, only the ones its `.env` marks. Before the Settings screen could
    put a key in the vault, that build had no `keyring` in it and never touched the OS vault.
    Bundling the library so the screen can offer the vault must not turn every launch, for everyone,
    into a keychain read — on Linux that read can open an unlock prompt nobody asked for. And it must
    not turn ONE marked key into all of them: the vault's service name is shared with any pip install
    on the same account, so a `chimera secrets set STRIPE_API_KEY` made there would otherwise start
    to apply inside the desktop the moment its owner moved one unrelated key, with nothing on screen
    saying so. The marker is the owner's own record of what lives there; it is the whole list.
    """
    if not getattr(sys, "frozen", False):
        return STORABLE
    return tuple(name for name in marked(env_file) if name not in _NEVER_FROZEN)


def consulted(env_file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] | None) -> bool:
    """Whether this process should read the vault at startup at all (:func:`startup_names`)."""
    return bool(startup_names(env_file))
