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


def load_into_environment(environ: dict[str, str] | None = None) -> list[str]:
    """Fill any storable credential the environment does not already have. Returns what was filled.

    The environment wins, always. Someone running `OPENROUTER_API_KEY=… chimera solve` is making a
    deliberate, visible choice for that one command, and a vault that overrode it would be a
    setting that cannot be overridden from the shell — the one place people expect to be able to.
    """
    import os

    env = os.environ if environ is None else environ
    kr = _keyring()
    if kr is None:
        return []
    preenchidos = []
    for name in STORABLE:
        if env.get(name):
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
