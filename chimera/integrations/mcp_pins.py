"""What each MCP server said its tools were, the last time the owner accepted it.

Study 30, S30-24. A server's tool descriptions and parameter schemas are text it writes and the model
reads as part of the tool list — outside the ``<<external-data>>`` fence that covers what a tool
RETURNS. Before this, the only fingerprint of a server was of its launch command
(``mcp_api._fingerprint``), and it only decided whether a remembered Test was still shown. So a server
that rewrote a description between two sessions was mounted on the second exactly as on the first,
and nobody saw the new text. Description drift is common in the wild (19,099 public servers,
arXiv 2608.00997) and poisoned descriptions alone reach 36.5% attack success (MCPTox, 2508.14925).

**The rule.** The first mount of a server pins its manifest (trust on first use: the owner just
configured it). A later mount whose manifest differs is HELD — the server is not mounted — and the new
manifest is recorded beside the pin so the owner can be shown the difference and approve it
(``chimera mcp approve``, or the MCP screen). A plain defect fix in the sense the study used: nothing
here changes what an UNCHANGED server does.

**What the manifest is.** Per tool: name, description and the whole input schema. The schema is in
because parameter descriptions live there, and they are read by the model the same way.

**Stored beside ``mcp.json``, not inside it**, for the reason ``mcp_tests.json`` gives: the VPS and
the CLI read ``mcp.json`` too. Unlike the remembered Test, this file DOES keep third-party text — a
diff cannot be shown without the old side of it — and it is local, like ``mcp.json`` itself.

**Approval is of the change the owner was SHOWN.** ``check_manifest`` rewrites the pending side
whenever a mount sees a different listing, and a mount can happen in another process at any moment
(the VPS bot, a CLI run, the app). So the diff carries the digest of the listing it shows, and
:func:`approve_change` takes that digest back and refuses (:class:`StaleApproval`) if the pending
listing is no longer the one with that digest. Without it, a server could show the owner one text
and have a second, never-shown text approved by the same click.

**One writer at a time, across processes.** Every read-modify-write takes a thread lock AND the
OS file lock beside the pin file (:mod:`chimera.core.filelock`): the app server, ``chimera mcp
approve`` and a VPS run are different processes, and a thread lock is invisible between them.

**An unreadable pin file is no pins.** The next mount pins again. Whoever can write this file can
write ``mcp.json`` and point the server anywhere, so refusing to mount on a corrupt file would buy
nothing against an attacker and would break every install whose disk hiccupped once.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.filelock import atomic_write_text, exclusively, locked
from chimera.integrations.mcp_cues import selection_cues
from chimera.telemetry import get_logger

_log = get_logger("integrations.mcp_pins")

PINS_FILE = "mcp_pins.json"

#: Every read-modify-write of the pin file, within this process. The pool is built under its own
#: lock, but the app's Approve route and a Test can reach this file at the same moment. Other
#: processes (the CLI, the VPS bot) are kept out by the OS file lock taken inside this one.
PINS_LOCK = threading.Lock()


class StaleApproval(ValueError):
    """The held listing changed after the owner was shown it; approving now would approve unseen text."""


def pins_path_for(mcp_path: Path) -> Path:
    """Where the pins of the store at ``mcp_path`` live."""
    return Path(mcp_path).parent / PINS_FILE


def _field(spec: Any, name: str, default: Any) -> Any:
    # A spec is an MCPToolSpec from a live session; some test doubles hand back plain dicts. Both
    # describe the same three facts, and the pin must not depend on which container carried them.
    if isinstance(spec, dict):
        return spec.get(name, default)
    return getattr(spec, name, default)


def manifest_of(specs: Iterable[Any]) -> list[dict[str, Any]]:
    """The canonical manifest of a tool listing: sorted by name, every field the model reads."""
    out = [
        {
            "name": str(_field(s, "name", "")),
            "description": str(_field(s, "description", "") or ""),
            "input_schema": _field(s, "input_schema", None) or {},
        }
        for s in specs
    ]
    return sorted(out, key=lambda t: t["name"])


def manifest_digest(manifest: list[dict[str, Any]], artifact: dict[str, Any] | None = None) -> str:
    """The digest of what is pinned: the listing and, when there is one, the artifact identity.

    Without an ``artifact`` the payload is the bare listing, byte for byte what it always was, so a
    pin written before P1-12 (or for a URL server, which has no local artifact) keeps its digest.
    """
    body: Any = {"manifest": manifest, "artifact": artifact} if artifact else manifest
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: The name the diff gives the launch line when it is what changed (the MCP screen and
#: ``chimera mcp approve`` print ``tool`` and ``change`` as they come).
COMMAND_CHANGE_TOOL = "(launch command)"
COMMAND_CHANGE = "command changed"


#: ``change`` of the entry shown when a pin made before the identity existed meets one for the first time.
FIRST_IDENTITY_CHANGE = "artifact identity to record"


def artifact_change(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any] | None:
    """The diff entry for a changed (or first-recorded) artifact identity, None when it did not move.

    Shaped like a tool entry so the existing approval flow (CLI and screen) shows it without a new
    route: the launch lines go in the description slots, and the NAMES of what moved lead the new one
    (an env variable, a file, the package) — never a value. A pin from before P1-12 has no identity
    (``old`` is None): that is held ONCE too, for the owner to register what the server runs for the
    first time, not adopted in silence (a server replaced before the first boot after the upgrade
    would otherwise be recorded as the approved one).
    """
    if new is None:
        return None
    from chimera.integrations.mcp_artifact import changed_parts, describe

    if old is None:
        return {
            "tool": COMMAND_CHANGE_TOOL,
            "change": FIRST_IDENTITY_CHANGE,
            "description_changed": True,
            "schema_changed": False,
            "old_description": "(no identity was recorded when this server was approved)",
            "new_description": describe(new),
            "old_schema": "",
            "new_schema": "",
            "duplicate": False,
            "cues": [],
        }
    if _canon(old) == _canon(new):
        return None
    moved = "; ".join(changed_parts(old, new))
    return {
        "tool": COMMAND_CHANGE_TOOL,
        "change": COMMAND_CHANGE,
        "description_changed": True,
        "schema_changed": False,
        "old_description": describe(old),
        "new_description": (f"[changed: {moved}] " if moved else "") + describe(new),
        "old_schema": "",
        "new_schema": "",
        "duplicate": False,
        "cues": [],
    }


def _canon(value: Any) -> str:
    # The digest's own serialisation. Comparing with == let two listings with different digests
    # diff as equal ({"max": 1} == {"max": 1.0}, True == 1): held, with no line of diff to show.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _by_name(manifest: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for tool in manifest:
        grouped.setdefault(str(tool["name"]), []).append(tool)
    return grouped


def manifest_diff(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool by tool, what changed between ``old`` and ``new`` — the thing the owner approves.

    Keyed by (name, position among the tools of that name), not by name alone. A server may list
    two tools with one name, and a dict by name kept only the last of each: a rewrite of the FIRST
    — the one that gets mounted, since the registry skips the colliding second — diffed as nothing,
    and the owner was shown a held server with an empty diff and an Approve button. Every tool whose
    name appears more than once in ``new`` is flagged ``duplicate``.

    Two manifests with different digests always give at least one change: entries are compared by
    the digest's own serialisation, and equal entries at every key make equal sorted lists.
    """
    before, after = _by_name(old), _by_name(new)
    changes: list[dict[str, Any]] = []
    for name in sorted(set(before) | set(after)):
        olds, news = before.get(name, []), after.get(name, [])
        duplicate = len(news) > 1
        for i in range(max(len(olds), len(news))):
            a = olds[i] if i < len(olds) else None
            b = news[i] if i < len(news) else None
            if a is None and b is not None:
                changes.append(_change(name, "added", None, b, duplicate))
            elif b is None and a is not None:
                changes.append(_change(name, "removed", a, None, duplicate))
            elif a is not None and b is not None and _canon(a) != _canon(b):
                changes.append(_change(name, "changed", a, b, duplicate))
    return changes


def _schema_text(schema: Any) -> str:
    # Shown to the owner whole, not summarised: every string in it reaches the model, and a summary
    # is a decision about which of them the owner does not need to read.
    if not schema:
        return ""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def _schema_strings(schema: Any) -> list[str]:
    """Every string in a schema: parameter descriptions, titles, enum values, defaults."""
    if isinstance(schema, str):
        return [schema]
    if isinstance(schema, dict):
        return [s for v in schema.values() for s in _schema_strings(v)]
    if isinstance(schema, list):
        return [s for v in schema for s in _schema_strings(v)]
    return []


def tool_cues(description: str, input_schema: Any) -> list[str]:
    """The selection cues of a tool, read over ALL the text the model would see of it.

    The description and every string in the input schema. Parameter descriptions are the channel
    MCPTox poisons, so a check that read the description alone left exactly that channel
    unannotated. The held diff and the Test screen both call this, so a server is annotated the
    same way at first sight as when it changes.
    """
    return selection_cues("\n".join([description, *_schema_strings(input_schema)]))


def manifest_cues(manifest: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Cue codes per tool of ``manifest``; tools with none are left out. Annotation, never a gate."""
    out: dict[str, list[str]] = {}
    for tool in manifest:
        cues = tool_cues(str(tool.get("description", "")), tool.get("input_schema"))
        if cues:
            out[str(tool["name"])] = cues
    return out


def _change(
    name: str,
    kind: str,
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
    duplicate: bool = False,
) -> dict[str, Any]:
    old_desc = "" if a is None else str(a.get("description", ""))
    new_desc = "" if b is None else str(b.get("description", ""))
    old_schema = None if a is None else a.get("input_schema")
    new_schema = None if b is None else b.get("input_schema")
    return {
        "tool": name,
        "change": kind,
        "description_changed": kind != "changed" or old_desc != new_desc,
        "schema_changed": kind != "changed" or _canon(old_schema) != _canon(new_schema),
        "old_description": old_desc,
        "new_description": new_desc,
        "old_schema": _schema_text(old_schema),
        "new_schema": _schema_text(new_schema),
        "duplicate": duplicate,
        # Over the NEW text: the old one was already approved.
        "cues": tool_cues(new_desc, new_schema),
    }


def _load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(path: Path, pins: dict[str, dict[str, Any]]) -> None:
    # Its own temporary file per writer, for the reason save_test_records gives; the shared helper
    # also retries the rename Windows refuses while another process holds the file open.
    text = json.dumps(pins, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"
    atomic_write_text(Path(path), text)


def _manifest_in(record: Any) -> list[dict[str, Any]] | None:
    if not isinstance(record, dict):
        return None
    manifest = record.get("manifest")
    if not isinstance(manifest, list) or not all(
        isinstance(t, dict) and "name" in t for t in manifest
    ):
        return None
    return manifest


@dataclass(frozen=True)
class PinCheck:
    """The verdict on one mount. ``held`` is the only one that stops it."""

    status: str  # "pinned" (first sight), "unchanged" or "held"
    changes: list[dict[str, Any]] = field(default_factory=list)
    #: tool name -> cue codes, for a server seen for the FIRST time. First sight mounts on trust, so
    #: this is the one moment a description hostile from day one can still be noticed (P0-16).
    cues: dict[str, list[str]] = field(default_factory=dict)

    @property
    def held(self) -> bool:
        return self.status == "held"


def _artifact_in(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    artifact = record.get("artifact")
    return artifact if isinstance(artifact, dict) and artifact else None


def _changes_between(
    old_manifest: list[dict[str, Any]],
    old_artifact: dict[str, Any] | None,
    new_manifest: list[dict[str, Any]],
    new_artifact: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """The listing diff plus, first, the launch-line change when the artifact identity moved."""
    changes = manifest_diff(old_manifest, new_manifest)
    moved = artifact_change(old_artifact, new_artifact)
    return [moved, *changes] if moved else changes


def check_artifact(mcp_path: Path, name: str, artifact: dict[str, Any] | None) -> PinCheck | None:
    """The verdict on the artifact ALONE, before the server is started; None when nothing is held.

    The listing needs a running server, and running the server is exactly what a changed artifact must
    not get to do. So the mount compares the identity first: a server with a pin whose identity moved
    (or that never had one) is held here, its pending record carrying the pinned listing and the new
    identity, and nothing the artifact contains has executed. First sight (no pin) is not held: the
    owner just configured it, and :func:`check_manifest` pins the listing with this identity.
    """
    if not artifact:
        return None
    path = pins_path_for(mcp_path)
    with PINS_LOCK, locked(path):
        pins = _load(path)
        record = pins.get(name)
        pinned = _manifest_in(record)
        if pinned is None or not isinstance(record, dict):
            return None
        pinned_artifact = _artifact_in(record)
        if pinned_artifact is not None and _canon(pinned_artifact) == _canon(artifact):
            return None
        digest = manifest_digest(pinned, artifact)
        pending = record.get("pending")
        if not (isinstance(pending, dict) and pending.get("digest") == digest):
            record["pending"] = {
                "digest": digest, "manifest": pinned, "artifact": artifact, "seen_at": time.time(),
            }
            _try_save(path, pins, name)
        return PinCheck("held", _changes_between(pinned, pinned_artifact, pinned, artifact))


def check_manifest(
    mcp_path: Path, name: str, specs: Iterable[Any], artifact: dict[str, Any] | None = None
) -> PinCheck:
    """Compare what ``name`` lists AND runs now with what was approved, pinning on first sight.

    ``artifact`` is the identity of what the server executes
    (:func:`chimera.integrations.mcp_artifact.artifact_identity`); ``None`` for a remote server or a
    caller that has none, in which case only the listing is compared and the pinned identity, if any,
    is left alone. A changed identity with an identical listing is held exactly like a changed
    listing, with ``command changed`` in the diff (P1-12, audit a06-04).

    A pin written before the identity existed has none to compare against, and is NOT adopted in
    silence: the first mount that brings an identity is held once, with "artifact identity to record"
    in the diff, for the owner to approve what the server runs for the first time.

    Never raises for a disk that cannot be written: on first sight the server is mounted (there is
    nothing to compare it with either way) and on a change it is held regardless — the hold is
    decided by the comparison, not by whether the record of it could be saved.
    """
    manifest = manifest_of(specs)
    path = pins_path_for(mcp_path)
    # `locked` degrades to running unlocked: the hold is decided by the comparison, and a lock that
    # cannot be taken must not turn a mount into a crash.
    with PINS_LOCK, locked(path):
        pins = _load(path)
        record = pins.get(name)
        pinned = _manifest_in(record)
        if pinned is None:
            digest = manifest_digest(manifest, artifact)
            fresh: dict[str, Any] = {"digest": digest, "manifest": manifest, "pinned_at": time.time()}
            if artifact:
                fresh["artifact"] = artifact
            pins[name] = fresh
            _try_save(path, pins, name)
            return PinCheck("pinned", cues=manifest_cues(manifest))
        assert isinstance(record, dict)  # _manifest_in returned a list only for a dict record
        pinned_artifact = _artifact_in(record)
        # No identity offered (a caller with none): compare the listing under the one already pinned.
        current_artifact = artifact if artifact else pinned_artifact
        digest = manifest_digest(manifest, current_artifact)
        if manifest_digest(pinned, pinned_artifact) == digest:
            if "pending" in record:
                # The server went back to what was approved: nothing is waiting for a decision.
                del record["pending"]
                _try_save(path, pins, name)
            return PinCheck("unchanged")
        changes = _changes_between(pinned, pinned_artifact, manifest, current_artifact)
        pending = record.get("pending")
        if not (isinstance(pending, dict) and pending.get("digest") == digest):
            held: dict[str, Any] = {"digest": digest, "manifest": manifest, "seen_at": time.time()}
            if current_artifact:
                held["artifact"] = current_artifact
            record["pending"] = held
            _try_save(path, pins, name)
        return PinCheck("held", changes)


def _try_save(path: Path, pins: dict[str, dict[str, Any]], name: str) -> None:
    try:
        _save(path, pins)
    except OSError as exc:
        _log.warning("could not record the MCP manifest of %r: %s", name, type(exc).__name__)


def held_change(mcp_path: Path, name: str) -> dict[str, Any] | None:
    """The change waiting for the owner, as ``{changes, seen_at, digest}``, or None. Never connects.

    ``digest`` names the listing these changes were computed from. Hand it back to
    :func:`approve_change`, so that what gets approved is what was shown.
    """
    with PINS_LOCK:
        record = _load(pins_path_for(mcp_path)).get(name)
    pinned = _manifest_in(record)
    if pinned is None or not isinstance(record, dict):
        return None
    pending = _manifest_in(record.get("pending"))
    if pending is None:
        return None
    seen_at = record["pending"].get("seen_at")
    pending_record = record["pending"]
    return {
        "changes": _changes_between(
            pinned, _artifact_in(record), pending, _artifact_in(pending_record)
        ),
        "seen_at": float(seen_at) if isinstance(seen_at, (int, float)) else 0.0,
        # Computed from the manifest rather than read from the record's field: this is the value
        # approve_change compares against, so both sides must derive it the same way.
        "digest": manifest_digest(pending, _artifact_in(pending_record)),
    }


def pinned_unverifiable(mcp_path: Path, name: str) -> list[str]:
    """Why what ``name`` runs could not be fully verified when it was pinned; empty when it could.

    Read from the pin file (no connect, no hashing): the MCP screen lists servers often, and the
    reasons were established at the mount that recorded them.
    """
    from chimera.integrations.mcp_artifact import unverifiable_reasons

    with PINS_LOCK:
        record = _load(pins_path_for(mcp_path)).get(name)
    return unverifiable_reasons(_artifact_in(record))


def approve_change(mcp_path: Path, name: str, expected_digest: str) -> bool:
    """Make the held manifest of ``name`` the approved one. False when nothing was held.

    ``expected_digest`` is the ``digest`` of the :func:`held_change` the owner was shown. When the
    held listing is no longer that one — a mount elsewhere replaced it in between — this raises
    :class:`StaleApproval` and approves nothing. It takes the cross-process lock for real
    (``exclusively``, which raises rather than writing unlocked): an approval lost to a concurrent
    write is worse than asking the owner to try again.
    """
    path = pins_path_for(mcp_path)
    with PINS_LOCK, exclusively(path):
        pins = _load(path)
        record = pins.get(name)
        if not isinstance(record, dict):
            return False
        pending = _manifest_in(record.get("pending"))
        if pending is None:
            return False
        pending_artifact = _artifact_in(record["pending"])
        digest = manifest_digest(pending, pending_artifact)
        if digest != expected_digest:
            raise StaleApproval(f"the held tools of {name!r} changed after they were shown")
        approved: dict[str, Any] = {
            "digest": digest,
            "manifest": pending,
            "pinned_at": time.time(),
        }
        if pending_artifact:
            approved["artifact"] = pending_artifact
        pins[name] = approved
        _save(path, pins)
    return True


def forget_pin(mcp_path: Path, name: str) -> None:
    """Drop the pin of ``name``: the store calls this when the owner adds or removes the server.

    An approval was about the server configured then. Re-adding by name is the owner configuring a
    server again, and its first mount is first sight again.
    """
    path = pins_path_for(mcp_path)
    with PINS_LOCK, locked(path):
        pins = _load(path)
        if name not in pins:
            return
        del pins[name]
        try:
            _save(path, pins)
        except OSError as exc:
            _log.warning("could not forget the MCP pin of %r: %s", name, type(exc).__name__)


class PinnedSession:
    """A session whose tool listing is the one that was checked, not whatever the server says next.

    ``MCPConnector.tools`` lists the tools again every time it is asked. Without this, a server
    could answer the check with the approved text and the next listing with something else, and the
    model would read the listing nobody compared to anything. Calls go through to the server.
    """

    def __init__(self, session: Any, specs: list[Any]) -> None:
        self._session = session
        self._specs = list(specs)

    def list_tools(self) -> list[Any]:
        return list(self._specs)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result: str = self._session.call_tool(name, arguments)
        return result

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if callable(close):
            close()
