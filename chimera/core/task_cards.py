"""Proposed task cards: where they are kept, who may start them, and what a started one carries.

A card is written by the agent in one turn (``suggest_task``) and started later by the owner's click.
The server keeps the card, not the client: the start route takes a card id and reads the title,
prompt and taint from here, so nothing the browser sends can change what runs. A card is started at
most once; the claim is atomic, and a failed start gives the claim back.

Taint travels with the card, and the server alone decides it. A card is ``tainted`` when the turn that
proposed it had read external content, at the moment of the proposal or by the end of that turn, or
when its conversation had read some in an earlier turn. A conversation started from a tainted card
begins with the taint seeded and never with the bypass switch, even when the project has one saved,
and so does every later turn of it. "Do it here" sends the card into the conversation it came from:
a tainted card marks that conversation the same way, so the prompt never runs under a clean ledger.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("core.task_cards")

#: The most cards one conversation keeps. The oldest unused ones go first, and when every one is used
#: the oldest used ones, so a conversation that started fifty cards can still propose a fifty-first.
MAX_CARDS_PER_SESSION = 50

#: Files in the app's home. Both are named in the shell fence (`queue_fence._GOVERNANCE_FILES`): the
#: agent's shell may neither read the key nor rewrite the cards.
CARDS_FILE_NAME = "task_cards.json"
KEY_FILE_NAME = "task_cards.key"
#: A third file, written before the first card: "this store has existed". It holds nothing, and it is
#: why deleting the other two cannot reset the taint they recorded: a store that was seen and has no
#: file or no key fails closed, as a bad signature does. It is fenced from the shell like the pair.
SEEN_FILE_NAME = "task_cards.seen"

#: The most cards the file keeps in all. Past it the oldest USED cards go first, then the oldest.
MAX_CARDS_TOTAL = 500

#: Why a store that failed its check refuses to work, said to the owner who has to clear it.
BROKEN_REASON = (
    "the task card store failed its integrity check (the file was edited, or its key is gone); "
    f"delete {CARDS_FILE_NAME}, {KEY_FILE_NAME} and {SEEN_FILE_NAME} from the app's home to start over"
)


@dataclass(frozen=True)
class CardOrigin:
    """What a turn that starts a card carries: the card's own taint, decided by the server."""

    tainted: bool


@dataclass
class ProposedCard:
    id: str
    session_id: str
    title: str
    tldr: str
    prompt: str
    tainted: bool
    used: bool = False
    created_at: float = 0.0


def _canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _empty() -> dict[str, Any]:
    return {"cards": {}, "conversation_tainted": [], "card_tainted": []}


class TaskCardStore:
    """The cards of every conversation, kept in memory and copied to one signed file in the owner's home.

    The same discipline as the permission switches (`bypass_switches.py`): the file is READ ONCE, when
    the store opens, and memory is the authority after that, so a file the agent rewrites during the
    session changes nothing. For the restart in between, the file carries an HMAC over its whole body,
    keyed by a secret the shell is fenced from. A file that fails the check, or whose key is gone while
    the file is not (or the reverse), opens the store ``broken``, and a broken store fails CLOSED: no
    card can be started or proposed, every existing conversation counts as tainted, and nothing is
    rewritten, so the damage is not quietly healed into a clean state. Only the owner clears it, by
    deleting the two files.

    Three things are recorded besides the cards: the taint of each card (decided when it was proposed
    and again when its turn ended, since a card proposed before a web read in the same turn is tainted
    too); the conversations that read external content in some turn (their later cards inherit it);
    and the conversations begun from, or continued with, a tainted card (they keep the taint seeded
    and never get the bypass). A write goes through a lock and a temporary file, so a crash leaves
    the previous file whole.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._key_path = self.path.with_name(KEY_FILE_NAME)
        self._seen_path = self.path.with_name(SEEN_FILE_NAME)
        self._lock = threading.RLock()
        self._state = _empty()
        self.broken = False
        self._load()

    # ---- the signed file ---------------------------------------------------------------------

    def _key(self, *, create: bool) -> bytes | None:
        try:
            text = self._key_path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if len(text) < 32:
            if not create:
                return None
            text = secrets.token_hex(32)
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._key_path.with_name(self._key_path.name + f".{uuid.uuid4().hex}.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self._key_path)
        return hmac.new(text.encode("utf-8"), b"chimera task cards v1", hashlib.sha256).digest()

    def _load(self) -> None:
        file_exists, key_exists = self.path.exists(), self._key_path.exists()
        if not file_exists and not key_exists:
            if self._seen_path.exists():
                self._break("the store existed and both its files are gone")
            return  # a first start: nothing was ever written
        if not file_exists or not key_exists:
            self._break("one of the two files is missing")
            return
        if not self._seen_path.exists():
            self._break("the marker that the store existed is gone")
            return
        key = self._key(create=False)
        try:
            wrapper = json.loads(self.path.read_text(encoding="utf-8"))
            body, mac = wrapper["body"], str(wrapper["mac"])
            if key is None or not isinstance(body, dict):
                raise ValueError("unreadable")
            expected = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(mac, expected):
                raise ValueError("signature mismatch")
        except (OSError, ValueError, KeyError, TypeError):
            self._break("the signature does not match")
            return
        state = _empty()
        cards = body.get("cards")
        if isinstance(cards, dict):
            state["cards"] = {k: v for k, v in cards.items() if isinstance(v, dict)}
        for name in ("conversation_tainted", "card_tainted"):
            ids = body.get(name)
            state[name] = [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []
        self._state = state

    def _break(self, why: str) -> None:
        _log.error("task cards: %s; the store is closed until the owner clears it", why)
        self.broken = True

    def _write(self, state: dict[str, Any]) -> None:
        self._seen_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._seen_path.exists():
            self._seen_path.write_text("1", encoding="utf-8")  # before anything it protects exists
        key = self._key(create=True)
        if key is None:  # pragma: no cover - create=True always returns a key
            raise OSError("no key")
        mac = hmac.new(key, _canonical(state), hashlib.sha256).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps({"body": state, "mac": mac}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _save_best_effort(self) -> None:
        """Copy memory to the file after a change that already holds in memory (the safe direction)."""
        try:
            self._write(self._state)
        except OSError as exc:
            _log.warning("task cards: could not save (%s); memory still holds the change", exc)

    # ---- cards -------------------------------------------------------------------------------

    def propose(self, *, session_id: str, title: str, tldr: str, prompt: str, tainted: bool) -> str:
        """Keep a new card for ``session_id`` and return its id. A broken store refuses (OSError)."""
        card_id = uuid.uuid4().hex[:12]
        with self._lock:
            if self.broken:
                raise OSError(BROKEN_REASON)
            state: dict[str, Any] = json.loads(json.dumps(self._state))
            inherited = session_id in state["conversation_tainted"] or session_id in state["card_tainted"]
            mine = [c for c in state["cards"].values() if c.get("session_id") == session_id]
            if len(mine) >= MAX_CARDS_PER_SESSION:
                pool = [c for c in mine if not c.get("used")] or mine
                oldest = min(pool, key=lambda c: float(c.get("created_at", 0.0)))
                state["cards"].pop(oldest["id"], None)
            card = ProposedCard(
                id=card_id, session_id=session_id, title=title, tldr=tldr, prompt=prompt,
                tainted=bool(tainted or inherited), created_at=time.time(),
            )
            state["cards"][card_id] = asdict(card)
            while len(state["cards"]) > MAX_CARDS_TOTAL:
                everyone = list(state["cards"].values())
                pool = [c for c in everyone if c.get("used")] or everyone
                state["cards"].pop(min(pool, key=lambda c: float(c.get("created_at", 0.0)))["id"], None)
            self._write(state)  # a card is kept only if it could be saved
            self._state = state
        return card_id

    def get(self, session_id: str, card_id: str) -> ProposedCard | None:
        """The card, only when it was proposed in ``session_id``. Another conversation's is None."""
        with self._lock:
            raw = self._state["cards"].get(card_id)
            if self.broken or not isinstance(raw, dict) or raw.get("session_id") != session_id:
                return None
            return ProposedCard(**raw)

    def claim(self, session_id: str, card_id: str) -> str:
        """Mark the card used. ``"ok"``, ``"missing"`` (no such card here) or ``"used"``."""
        with self._lock:
            raw = self._state["cards"].get(card_id)
            if self.broken or not isinstance(raw, dict) or raw.get("session_id") != session_id:
                return "missing"
            if raw.get("used"):
                return "used"
            raw["used"] = True
            self._save_best_effort()
            return "ok"

    def release(self, session_id: str, card_id: str) -> None:
        """Give a claim back, when the start did not happen."""
        with self._lock:
            raw = self._state["cards"].get(card_id)
            if isinstance(raw, dict) and raw.get("session_id") == session_id and raw.get("used"):
                raw["used"] = False
                self._save_best_effort()

    def discard(self, session_id: str, card_id: str) -> None:
        """Remove a card its turn withdrew."""
        with self._lock:
            raw = self._state["cards"].get(card_id)
            if isinstance(raw, dict) and raw.get("session_id") == session_id:
                self._state["cards"].pop(card_id, None)
                self._save_best_effort()

    def forget_session(self, session_id: str) -> None:
        """The conversation was deleted: its cards and its taint records go with it."""
        with self._lock:
            if self.broken:
                return
            state = self._state
            state["cards"] = {k: v for k, v in state["cards"].items() if v.get("session_id") != session_id}
            for name in ("conversation_tainted", "card_tainted"):
                state[name] = [i for i in state[name] if i != session_id]
            self._save_best_effort()

    # ---- taint -------------------------------------------------------------------------------

    def settle_turn(self, session_id: str, card_ids: list[str], *, tainted: bool) -> None:
        """At the end of a turn: if it read external content, its cards are tainted, and so is the
        conversation for every card it proposes later. The check at ``propose`` saw only what had been
        read by then; a card proposed before a web read in the same turn is settled here."""
        if not tainted or self.broken:
            return
        with self._lock:
            for card_id in card_ids:
                raw = self._state["cards"].get(card_id)
                if isinstance(raw, dict) and raw.get("session_id") == session_id:
                    raw["tainted"] = True
            if session_id not in self._state["conversation_tainted"]:
                self._state["conversation_tainted"].append(session_id)
            self._save_best_effort()

    def mark_started(self, session_id: str, *, tainted: bool) -> None:
        """Record a conversation begun from (or continued with) a card; a tainted one stays tainted
        for its later turns, seeded and without the bypass."""
        if not tainted:
            return
        with self._lock:
            if session_id not in self._state["card_tainted"]:
                self._state["card_tainted"].append(session_id)
                self._save_best_effort()

    def is_tainted_session(self, session_id: str | None) -> bool:
        """Whether this conversation was begun from, or continued with, a tainted card. A broken store
        says yes for every conversation that already exists."""
        if not session_id:
            return False
        with self._lock:
            return self.broken or session_id in self._state["card_tainted"]


_BY_PATH: dict[str, TaskCardStore] = {}
_REGISTRY_LOCK = threading.RLock()


def open_store(path: Path) -> TaskCardStore:
    """The store of one home, read once and then shared by every reader in this process."""
    key = str(Path(path))
    with _REGISTRY_LOCK:
        found = _BY_PATH.get(key)
        if found is None:
            found = TaskCardStore(Path(path))
            _BY_PATH[key] = found
        return found


def forget_conversation(path: Path, session_id: str) -> None:
    """A deleted conversation takes its cards with it, without creating a store for a home that has none."""
    with _REGISTRY_LOCK:
        if str(Path(path)) not in _BY_PATH and not Path(path).exists():
            return
    open_store(Path(path)).forget_session(session_id)


def forget_all_stores() -> None:
    """Drop every cached home: a process restart, for tests. The next open reads the file again."""
    with _REGISTRY_LOCK:
        _BY_PATH.clear()


def bypass_for_turn(
    *, seeded: bool, project_on: Callable[[], bool] | None, session_bypass: bool = False,
) -> Callable[[], bool]:
    """The bypass a turn may read, as a question asked at each use.

    A conversation started from a tainted card never has the bypass, whatever the project saved. Any
    other turn keeps what the owner's switches already give (the conversation's own, or the
    project's, which a new conversation inherits: decision of 28/10).
    """
    if seeded:
        return lambda: False
    return lambda: bool(session_bypass or (project_on is not None and project_on()))


def card_screen_turn(*, background: object | None, via_screen: bool, channel_origin: str, guest: bool) -> bool:
    """Whether a turn is one whose screen can show a card and start one.

    A background work, a linked chat's turn, a guest's turn and a turn with no screen are not. The
    card tools are offered only where the owner is watching the stream they would appear on.
    """
    return background is None and via_screen and not channel_origin and not guest


#: The source the seeded taint names in the ledger, so a receipt says where the taint came from.
SEED_SOURCE = "task-card: conversa iniciada de um cartão de um turno que leu conteúdo externo"


def seed_taint(ledger: Any) -> None:
    """Put the card's taint on a fresh run ledger: the run has read external content already."""
    ledger.record_fetch(SEED_SOURCE, "", requested_by="agent", seen=False)
