"""A project's pack — ``.chimera/pack.json`` — which narrows the owner's skills, MCP servers and tools.

Study 29, P7.6. Installed skill bundles and configured MCP servers are per *home*, so they reach every
project at once: the Supabase server one product needs, and the skills written for it, ride along —
with their schemas and their prompt lines — into a conversation about something else. A pack lets a
folder say "here, only these":

.. code-block:: json

    {"skills": ["pdf-forms"], "mcp": ["github"], "tools_deny": ["browser"]}

**It only ever narrows, and that is the whole design.**

* ``skills`` keeps the switched-on bundles it names and drops the rest from the prompt. Naming a
  bundle that is not switched on does nothing — it is not installed, not activated, not read. The
  owner's switch is the ceiling; a file in a repository is not a second switch.
* ``mcp`` keeps the configured servers it names and denies every tool of the others. Naming a server
  that is not configured adds nothing: a pack has no command line to launch one with, and the field
  that would carry it is not read.
* ``tools_deny`` joins the union of denials that already exists (request, posture, deployment). It is
  a denylist, and nothing here builds an allowlist from a pack, so it cannot grant a tool by naming it.
* Every other key — ``tools_allow``, ``reach``, ``posture``, ``host_exec``, ``verify``, a server
  definition — is not read. It is reported as ignored, so the person looking at the card sees that
  the file asked for something and that it was refused, rather than finding out later.

The verifier that decides a coding turn's verdict is not a tool in the registry
(``chimera.core.verify.CommandVerifier`` runs beside it), and the pack has no field that reaches its
command, so ``tools_deny`` cannot weaken a receipt by hiding the check behind it. What a pack *can*
do is deny ``run_shell``, which stops the agent running tests itself: that is a real restriction,
and the card lists it rather than burying it.

**Two gates before anything applies.** ``CHIMERA_PROJECT_PACK`` is off by default — narrowing changes
what a run can do, and that has not been measured (the plan's bench is pre-registered, not run). And
a pack applies only after the owner accepted *these bytes* for *this folder*: the file can arrive in
a clone, and a stranger's repository deciding what the agent may use, even only by subtraction, is
a decision the owner sees first. Accepting records the file's SHA-256 *and what it asked for*; a
changed file is a new file, and it does not apply until it is accepted. Until then the version the
owner accepted keeps applying — the narrowing they agreed to, from their own record, not from the
file. That is the review's point: the agent can write in its workspace, so a file whose change lifted
the narrowing would let a run (or an instruction planted in what a run read) hand itself back every
tool the owner had taken away by editing one byte. Deleting the file, or breaking it, is a change
like any other. Only the owner's accept or revoke moves the record.

**Which runs it reaches.** The assembly that builds a run's tools in the app
(:func:`chimera.api.code_api.assemble_registry` — the Code screen, Runs, the orchestration and
lifecycle routes) applies the pack's servers and tools *and* stamps the skills narrowing on the
registry it returns, which is where the agent reads it. One decision, made once, for both halves:
the skills of a crew worker in a temporary worktree are narrowed by the folder whose acceptance
narrowed its tools. Scheduled jobs, the terminal and the bots do not read packs; neither half
applies there, and the card says so.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("core.project_pack")

#: Where a project keeps its pack, relative to the project folder.
PACK_PATH = Path(".chimera") / "pack.json"

#: The fields a pack may set. Everything else in the file is reported and not read.
KNOWN_KEYS = ("skills", "mcp", "tools_deny")

#: A pack is a few names. A file far larger than that is not a pack, and is not parsed.
MAX_PACK_BYTES = 64 * 1024
MAX_NAMES = 200
MAX_NAME_CHARS = 128

#: The owner's acceptances, beside the other per-home records.
_CONSENT_FILE = "project_packs.json"


class PackError(ValueError):
    """The pack could not be read. The message is written to be shown to a person."""


@dataclass(frozen=True)
class ProjectPack:
    """What one pack file asks for, already held to what a pack may ask."""

    #: ``None`` means the field is absent and the owner's skills pass untouched; a tuple, even an
    #: empty one, is the list of switched-on bundles to keep.
    skills: tuple[str, ...] | None
    #: Same shape, for configured MCP servers.
    mcp: tuple[str, ...] | None
    tools_deny: tuple[str, ...]
    #: Keys present in the file that a pack cannot set.
    ignored: tuple[str, ...]
    #: SHA-256 of the file's bytes — what an acceptance is recorded against.
    digest: str


@dataclass(frozen=True)
class PackRead:
    """The pack in one folder: absent, readable, or present and refused (``error``)."""

    present: bool
    pack: ProjectPack | None = None
    error: str = ""
    digest: str = ""


def read_pack(folder: Path | str) -> PackRead:
    """Read ``<folder>/.chimera/pack.json``. Never raises: a broken pack is a refused pack."""
    path = Path(folder) / PACK_PATH
    try:
        if not path.is_file():
            return PackRead(present=False)
        # Bounded BEFORE the bytes are loaded. `parse_pack` refuses anything over the limit, but
        # it was handed the whole file first: a clone shipping a multi-GB `pack.json` (or a symlink
        # to one) went into the sidecar's memory every time the Code screen opened the folder.
        # The size is checked and the read is capped, so a file that grows in between still costs
        # at most one byte past the limit.
        if path.stat().st_size > MAX_PACK_BYTES:
            return PackRead(present=True, error=_too_large())
        with path.open("rb") as handle:
            raw = handle.read(MAX_PACK_BYTES + 1)
    except OSError as exc:
        return PackRead(present=True, error=f"could not read {PACK_PATH.as_posix()}: {exc}")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        return PackRead(present=True, pack=parse_pack(raw), digest=digest)
    except PackError as exc:
        # The whole pack is refused rather than the readable half applied: a person accepting a pack
        # accepts the file they read, and half of it is a different file.
        return PackRead(present=True, error=str(exc), digest=digest)


def parse_pack(raw: bytes) -> ProjectPack:
    """Validate a pack's bytes. Raises :class:`PackError` with a sentence a person can act on."""
    if len(raw) > MAX_PACK_BYTES:
        raise PackError(_too_large())
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackError(f"the pack is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PackError("the pack must be a JSON object")
    ignored = tuple(sorted(str(k) for k in data if k not in KNOWN_KEYS))
    skills = _names(data, "skills")
    mcp = _names(data, "mcp")
    tools_deny = _names(data, "tools_deny")
    return ProjectPack(
        skills=skills,
        mcp=mcp,
        tools_deny=tools_deny or (),
        ignored=ignored,
        digest=hashlib.sha256(raw).hexdigest(),
    )


def _too_large() -> str:
    return f"the pack is larger than {MAX_PACK_BYTES // 1024}KB"


def _names(data: dict[str, Any], key: str) -> tuple[str, ...] | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, list):
        raise PackError(f"`{key}` must be a list of names")
    if len(value) > MAX_NAMES:
        raise PackError(f"`{key}` names more than {MAX_NAMES} entries")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PackError(f"`{key}` must hold only non-empty names")
        name = item.strip()
        if len(name) > MAX_NAME_CHARS or any(ord(ch) < 32 for ch in name):
            raise PackError(f"`{key}` holds a name that is not one: {name[:40]!r}")
        if name not in out:
            out.append(name)
    return tuple(out)


# ---------------------------------------------------------------------------------------------
# The owner's acceptances

#: One lock for every read-modify-write of the record. Two windows accepting at once are two
#: requests on one sidecar: without it the second write was built from a read taken before the
#: first landed, and one acceptance vanished — or, on Windows, the shared temporary file was held
#: open by the other writer and the rename failed.
_consent_lock = threading.Lock()


def _folder_key(folder: Path | str) -> str:
    """The folder as one canonical string, so two spellings of one directory share an acceptance."""
    try:
        return str(Path(folder).expanduser().resolve())
    except OSError:
        return str(Path(folder))


def project_folder(folder: Path | str) -> Path:
    """``folder`` held to what an acceptance can be about: a named, absolute, existing directory.

    Checked before anything is written. A blank path resolved to the sidecar's own working
    directory, so ``DELETE /api/code/pack?path=`` revoked whatever acceptance that folder had, and a
    blank accept could accept the pack of a folder nobody named.
    """
    text = str(folder).strip()
    if not text:
        raise PackError("name the project folder")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise PackError("the project folder must be an absolute path")
    if not path.is_dir():
        raise PackError("the project folder does not exist")
    return path


def _read_consents(home: Path) -> dict[str, dict[str, Any]]:
    path = Path(home) / _CONSENT_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    accepted = data.get("accepted") if isinstance(data, dict) else None
    if not isinstance(accepted, dict):
        return {}
    return {str(k): dict(v) for k, v in accepted.items() if isinstance(v, dict)}


def _write_consents(home: Path, accepted: dict[str, dict[str, Any]]) -> None:
    path = Path(home) / _CONSENT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"accepted": accepted}, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    # A name of its own per write: a fixed `.tmp` is one file two writers share.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def accepted_digest(home: Path, folder: Path | str) -> str:
    """The digest the owner accepted for this folder's pack, or "" when none was."""
    return str(_read_consents(home).get(_folder_key(folder), {}).get("digest", ""))


def held_pack(home: Path, folder: Path | str) -> ProjectPack | None:
    """The pack the owner accepted for ``folder``, from the owner's own record — not from the file.

    ``None`` when nothing was accepted, or when the record holds no content (an acceptance written
    before the content was recorded, on a development build): such a record cannot say what was
    agreed, and guessing it from the file is the very thing this exists not to do.
    """
    record = _read_consents(home).get(_folder_key(folder))
    stored = record.get("pack") if record else None
    if not isinstance(stored, dict):
        return None
    try:
        return ProjectPack(
            skills=_names(stored, "skills"),
            mcp=_names(stored, "mcp"),
            tools_deny=_names(stored, "tools_deny") or (),
            ignored=(),
            digest=str(record.get("digest", "")) if record else "",
        )
    except PackError:
        # The owner's own record, damaged by hand: nothing it says can be trusted to be what they
        # agreed, so it is read as no acceptance — which the card shows as "not applied".
        return None


def accept(home: Path, folder: Path | str, digest: str) -> PackRead:
    """Record that the owner accepted the pack in ``folder`` — the one whose bytes hash to ``digest``.

    The digest is what the person was SHOWN. If the file changed between the screen reading it and
    the click, the click is about a file that no longer exists, and it is refused rather than
    transferred to the new one. What the pack asked for is recorded beside the digest, so the
    narrowing agreed to outlives the file (see the module docstring).
    """
    place = project_folder(folder)
    current = read_pack(place)
    if current.pack is None:
        raise PackError(current.error or "there is no pack in this folder")
    if not digest or digest != current.digest:
        raise PackError("the pack changed since it was shown — read it again before accepting")
    pack = current.pack
    with _consent_lock:
        consents = _read_consents(home)
        consents[_folder_key(place)] = {
            "digest": current.digest,
            "accepted_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "pack": {
                **({"skills": list(pack.skills)} if pack.skills is not None else {}),
                **({"mcp": list(pack.mcp)} if pack.mcp is not None else {}),
                "tools_deny": list(pack.tools_deny),
            },
        }
        _write_consents(home, consents)
    return current


def revoke(home: Path, folder: Path | str) -> bool:
    """Stop applying this folder's pack. Returns False when nothing had been accepted."""
    place = project_folder(folder)
    with _consent_lock:
        consents = _read_consents(home)
        if consents.pop(_folder_key(place), None) is None:
            return False
        _write_consents(home, consents)
    return True


def applied_pack(settings: Any, folder: Path | str | None) -> ProjectPack | None:
    """The pack a run in ``folder`` is held to right now, or ``None`` — the owner's settings, whole.

    ``None`` unless the switch is on and the owner accepted a pack for exactly this folder. Then:
    the file itself when its bytes are the ones accepted, and otherwise — changed, broken, deleted —
    the version the owner accepted, from their record. A change to the file never LIFTS a
    narrowing; only the owner's revoke does. Never raises: a run must not fail because a pack could
    not be read.
    """
    if folder is None or not getattr(settings, "project_pack", False):
        return None
    try:
        home = Path(settings.home)
        agreed = accepted_digest(home, folder)
        if not agreed:
            return None
        found = read_pack(folder)
        if found.pack is not None and found.digest == agreed:
            return found.pack
        return held_pack(home, folder)
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        _log.debug("project pack skipped for %s: %s", folder, exc)
        return None


# ---------------------------------------------------------------------------------------------
# What a pack does


def bundle_filter(pack: ProjectPack | None) -> frozenset[str] | None:
    """The bundle names a prompt may carry under ``pack``; ``None`` when it does not narrow skills."""
    if pack is None or pack.skills is None:
        return None
    return frozenset(pack.skills)


def denied_tools(pack: ProjectPack | None) -> frozenset[str]:
    """The tool names ``pack`` takes out of a registry: its ``tools_deny``. Only names to REMOVE —
    the caller unions them into the denials it already has. Servers are narrowed by
    :func:`narrow_pool`, before their tools are ever listed."""
    if pack is None:
        return frozenset()
    return frozenset(pack.tools_deny)


def narrow_pool(pack: ProjectPack | None, pool: Any) -> Any:
    """``pool`` holding only the servers ``pack`` keeps — ``pool`` itself when it keeps them all.

    The servers a pack leaves out are never asked for their tools at all, rather than listed and
    then denied by name. Denying after the fact needed a SECOND ``list_tools`` round trip per hidden
    server per turn, and when that one failed transiently the server's tools — already registered
    by the first, or served later by the deferred proxy, which lists again — were not denied. The
    narrowing failed open on a network blip. A server that is not in the pool cannot be registered,
    deferred or called, whatever it would have answered.
    """
    if pack is None or pack.mcp is None or pool is None:
        return pool
    from chimera.integrations.connectors import ConnectorRegistry

    keep = set(pack.mcp)
    narrowed = ConnectorRegistry()
    for server in pool.names():
        if server in keep:
            narrowed.register(pool.get(server))
    return narrowed


@dataclass(frozen=True)
class PackEffect:
    """What a pack would do against the owner's current settings — what the card shows."""

    skills_kept: list[str] = field(default_factory=list)
    skills_hidden: list[str] = field(default_factory=list)
    #: Names the pack lists that are not switched on: clamped, never activated.
    skills_not_active: list[str] = field(default_factory=list)
    mcp_kept: list[str] = field(default_factory=list)
    mcp_hidden: list[str] = field(default_factory=list)
    #: Names the pack lists that are not configured: clamped, never launched.
    mcp_not_configured: list[str] = field(default_factory=list)
    tools_denied: list[str] = field(default_factory=list)


def effect(pack: ProjectPack, active_bundles: Iterable[str], servers: Collection[str]) -> PackEffect:
    """Hold ``pack`` against what the owner has switched on and configured."""
    active = sorted(set(active_bundles))
    configured = sorted(set(servers))
    out = PackEffect(tools_denied=sorted(pack.tools_deny))
    if pack.skills is None:
        out.skills_kept.extend(active)
    else:
        named = set(pack.skills)
        out.skills_kept.extend(b for b in active if b in named)
        out.skills_hidden.extend(b for b in active if b not in named)
        out.skills_not_active.extend(sorted(named - set(active)))
    if pack.mcp is None:
        out.mcp_kept.extend(configured)
    else:
        named = set(pack.mcp)
        out.mcp_kept.extend(s for s in configured if s in named)
        out.mcp_hidden.extend(s for s in configured if s not in named)
        out.mcp_not_configured.extend(sorted(named - set(configured)))
    return out
