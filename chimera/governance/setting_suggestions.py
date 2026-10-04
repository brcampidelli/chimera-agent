"""A settings change somebody else suggested, waiting for the owner's yes.

The desktop bridge may not WRITE the settings that pick which model answers or whether the app runs
scheduled jobs (`bridge_routes.SUGGESTABLE_SETTINGS`, the owner's decision of 2026-10-04), but a
client is often right about which model suits a task. So instead of a flat refusal, a bridge
``settings.edit`` naming only those keys leaves a card for the owner here, with exactly what would
change: the key, the value now, the value proposed, and who proposed it.

It is the approval queue the app already has, not a second one: the card is a question file in
``<home>/approvals/`` (`pending.py`), so `GET /api/approvals`, the status bar's chip, the Governance
screen and ``chimera approve`` list it like any other, and its end is one line of the same
``history.jsonl``. Two things differ, and both are the point.

**Nothing waits on it, and an answer FILE does not apply it.** Every other question has a thread
parked in `pending.ask_durably`, polling for ``<id>.answer.json``; whatever writes that file
answers. A suggestion has no waiter: :func:`resolve` applies it, and only the owner's own answering
route calls :func:`resolve` (`POST /api/approvals/{id}` without the bridge's scope mark, see
`chimera/api/app.py`). `pending.answer` and `pending.answer_with_code` refuse a suggestion outright,
so the chat bot's one-time codes, ``chimera approve --yes`` and a file dropped into the directory
all resolve nothing. Whoever can make the app's own handler believe it is the owner's screen can
still approve — which is to say the owner, or code running as the owner on this machine. The
bridge itself is not that code any more: since 2026-10-04 a run it starts reaches no further than
the owner's own posture at any tier (`bridge_routes.wider_than`), so it cannot widen a turn to a
shell and approvals ``never`` to edit ``.env`` itself, and the agent's write tools refuse
Chimera's own ``.env`` and data folder in any case (`chimera/core/own_files.py`). What stays open
is what the owner's posture already allows a run to do, and any local process that reads the
app's page: the desktop app starts without ``CHIMERA_SERVER_TOKEN`` and ``GET /`` hands the token
to any loopback client, so "the owner's surface" is, today, any process of the owner's account
that asks — pre-existing, and outside this module.

**It is checked again when it is applied.** Approval can come a day later. The value is re-run
through every check a save goes through (:func:`chimera.api.config_api.check_updates`), and the
setting must still hold the value the card SHOWED: if the owner, or anything else, changed it in
between, the card is ``stale`` and nothing is written — the yes was given to a change from a value
that no longer exists, and applying it would overwrite a decision the person made after reading the
card. A card past :data:`TTL_SECONDS` is ``expired``: silence refuses here as everywhere else.

**And the yes is to the card the owner SAW.** The card lives in a file, and a file can change
between the moment the screen drew it and the click. The listing hands the screen a
:func:`digest` of exactly what it shows; the screen sends it back with the yes, and a card whose
file no longer hashes to it is ``changed`` and not applied (review of 2026-10-04: through a
Windows device path the bridge could rewrite a suggestion's file after the owner had read it).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from chimera.governance.pending import SETTINGS_SUGGESTION, _dir, _record, sweep
from chimera.telemetry import get_logger

_log = get_logger("governance.setting_suggestions")

#: How long a suggestion waits for the owner. A day, the same horizon `pending.sweep` clears
#: questions at: a suggestion is not holding a worker thread, so it can wait for the owner to come
#: back to the app, and a yes given after that is a yes to a suggestion nobody remembers making.
TTL_SECONDS = 24 * 3600.0

#: How a suggestion ends. Only ``applied`` wrote anything.
Outcome = Literal[
    "applied", "refused", "stale", "changed", "invalid", "expired", "no_such_request"
]

#: One resolution at a time in this process: two clicks on the same card (two windows) must not
#: apply it twice, and a resolution must not race the expiry sweep.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Change:
    """One setting the card would change."""

    key: str
    current: str
    """The value the card SHOWED as current — what the owner's yes is a yes to changing."""
    proposed: str


@dataclass(frozen=True)
class Suggestion:
    """A card, as read back from its file."""

    id: str
    changes: tuple[Change, ...]
    suggested_by: str
    """The surface that suggested it — ``desktop_bridge``."""
    client_hint: str
    """Which bridge token suggested it: its last four characters, the hint Settings already shows."""
    asked_at: float
    expires_at: float


def _ask_path(home: Path, request_id: str) -> Path:
    return _dir(home) / f"{request_id}.ask.json"


def _describe(changes: list[Change]) -> str:
    """The action line every surface already prints: one ``KEY: 'now' -> 'proposed'`` per change."""
    return "settings.edit\n" + "\n".join(
        f"{c.key}: {c.current!r} -> {c.proposed!r}" for c in changes
    )


def suggest(
    home: Path,
    changes: list[Change],
    *,
    suggested_by: str,
    client_hint: str = "",
    now: float | None = None,
) -> str:
    """Write the card and return its id. Nothing about any setting changes here.

    Raises ``OSError`` when the card cannot be written — the caller answers that as a failure, never
    as a suggestion made.
    """
    if not changes:
        raise ValueError("a suggestion needs at least one change")
    asked_at = time.time() if now is None else now
    request_id = uuid.uuid4().hex[:12]
    directory = _dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    sweep(home)
    who = f"the desktop bridge (token {client_hint})" if client_hint else "the desktop bridge"
    payload = {
        "id": request_id,
        "kind": SETTINGS_SUGGESTION,
        "action": _describe(changes),
        "reason": (
            f"Suggested by {who}. Nothing is written until you approve it in the app; "
            "it expires in 24 hours."
        ),
        "asked_at": asked_at,
        "decision": "review",
        "suggestion": {
            "changes": [
                {"key": c.key, "current": c.current, "proposed": c.proposed} for c in changes
            ],
            "suggested_by": suggested_by,
            "client_hint": client_hint,
            "expires_at": asked_at + TTL_SECONDS,
        },
    }
    path = _ask_path(home, request_id)
    # Written whole and then renamed into place: a listing that ran mid-write would otherwise read
    # half a card, and a half card is one whose "current" column may be missing.
    partial = path.with_name(f"{request_id}.partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(partial, path)
    return request_id


def _parse(data: Any) -> Suggestion | None:
    if not isinstance(data, dict) or data.get("kind") != SETTINGS_SUGGESTION:
        return None
    body = data.get("suggestion")
    if not isinstance(body, dict):
        return None
    changes: list[Change] = []
    for row in body.get("changes") or []:
        if not isinstance(row, dict):
            return None
        changes.append(
            Change(
                key=str(row.get("key") or ""),
                current=str(row.get("current") or ""),
                proposed=str(row.get("proposed") or ""),
            )
        )
    if not changes or any(not c.key for c in changes):
        return None
    try:
        asked_at = float(data.get("asked_at") or 0.0)
        expires_at = float(body.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        return None
    return Suggestion(
        id=str(data.get("id") or ""),
        changes=tuple(changes),
        suggested_by=str(body.get("suggested_by") or ""),
        client_hint=str(body.get("client_hint") or ""),
        asked_at=asked_at,
        expires_at=expires_at,
    )


def read(home: Path, request_id: str) -> Suggestion | None:
    """The suggestion waiting under ``request_id``; None when there is none (or it is another kind)."""
    if not request_id or any(ch in request_id for ch in "/\\.") or len(request_id) > 64:
        return None
    try:
        data = json.loads(_ask_path(home, request_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _parse(data)


def digest(s: Suggestion) -> str:
    """A hash of everything the card shows and the yes applies: the id, every key with its value now
    and the value proposed, who suggested it, and when it expires."""
    shown = {
        "id": s.id,
        "changes": [[c.key, c.current, c.proposed] for c in s.changes],
        "suggested_by": s.suggested_by,
        "client_hint": s.client_hint,
        "expires_at": s.expires_at,
    }
    return hashlib.sha256(
        json.dumps(shown, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def is_suggestion(home: Path, request_id: str) -> bool:
    """Whether ``request_id`` names a waiting settings suggestion."""
    return read(home, request_id) is not None


def _end(home: Path, s: Suggestion, outcome: str, answered_at: float | None, via: str) -> None:
    _record(
        _dir(home),
        s.id,
        _describe(list(s.changes)),
        f"settings suggestion from {s.suggested_by or 'unknown'}",
        s.asked_at,
        outcome,
        answered_at,
        facts={"surface": s.suggested_by or "desktop_bridge", "tool": "settings.edit"},
        answered_via=via,
    )
    with contextlib.suppress(OSError):
        _ask_path(home, s.id).unlink(missing_ok=True)
    # An answer file somebody wrote by hand means nothing for a suggestion; it goes with the card so
    # it cannot sit there looking like a decision.
    with contextlib.suppress(OSError):
        (_dir(home) / f"{s.id}.answer.json").unlink(missing_ok=True)


def expire_due(home: Path, *, now: float | None = None) -> list[str]:
    """Retire every suggestion past its deadline, as a timeout. Returns their ids."""
    agora = time.time() if now is None else now
    directory = _dir(home)
    if not directory.exists():
        return []
    gone: list[str] = []
    with _LOCK:
        for path in sorted(directory.glob("*.ask.json")):
            try:
                s = _parse(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            if s is not None and agora >= s.expires_at:
                _end(home, s, "timeout", None, "")
                gone.append(s.id)
    return gone


def resolve(
    home: Path,
    request_id: str,
    approved: bool,
    *,
    via: str,
    current_of: Callable[[str], str],
    check: Callable[[dict[str, str]], None],
    apply: Callable[[dict[str, str]], object],
    allowed: Callable[[str], bool],
    shown: str | None = None,
    now: float | None = None,
) -> tuple[Outcome, str]:
    """End a suggestion with the owner's answer. Returns ``(outcome, detail)``.

    Only the OWNER's answering surface may call this — the function cannot tell who is calling, and
    that is why it is never reached from `pending.answer`, which every other answering path goes
    through. ``current_of`` reads a setting's value now, in the same form the card stored it;
    ``check`` raises ``ValueError`` for an update a save would refuse; ``apply`` writes it;
    ``allowed`` says whether a key may still be suggested at all (the classification may have moved
    since the card was written, and a key that became the owner's flat refusal is not applied off a
    card).

    ``shown`` is the :func:`digest` the owner's screen was given with the card. A yes without it, or
    with one the card no longer hashes to, is ``changed`` and writes nothing.

    A refusal writes nothing. A yes writes the proposed values only if the card has not expired,
    every key is still suggestable, every key still holds the value the card showed, and the update
    passes every check a save would make — otherwise it writes nothing and says which of those it was.
    """
    agora = time.time() if now is None else now
    with _LOCK:
        s = read(home, request_id)
        if s is None:
            return "no_such_request", ""
        if agora >= s.expires_at:
            _end(home, s, "timeout", None, via)
            return "expired", ""
        if not approved:
            _end(home, s, "refused", agora, via)
            return "refused", ""
        if shown is None or shown != digest(s):
            # Left waiting, not ended: the owner can look again at what the card says NOW.
            _log.warning("suggestion %s not applied: it changed after it was shown", s.id)
            return "changed", ""
        updates = {c.key: c.proposed for c in s.changes}
        not_allowed = sorted(k for k in updates if not allowed(k))
        if not_allowed:
            _end(home, s, "approved", agora, via)
            _log.warning("suggestion %s not applied: no longer suggestable: %s", s.id, not_allowed)
            return "invalid", f"no longer a setting that can be suggested: {', '.join(not_allowed)}"
        moved = sorted(c.key for c in s.changes if current_of(c.key) != c.current)
        if moved:
            _end(home, s, "approved", agora, via)
            _log.warning("suggestion %s not applied: changed since it was suggested: %s", s.id, moved)
            return "stale", ", ".join(moved)
        try:
            check(updates)
            apply(updates)
        except ValueError as exc:
            _end(home, s, "approved", agora, via)
            _log.warning("suggestion %s not applied: %s", s.id, exc)
            return "invalid", str(exc)
        _end(home, s, "approved", agora, via)
        return "applied", ", ".join(sorted(updates))
