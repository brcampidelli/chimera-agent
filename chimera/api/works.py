"""Background works: a coding turn that runs while the conversation goes on.

The shape hands-free use needs, as the owner described it (2026-09-18): a fast model to converse,
a strong one to do the work, and the two at once — "refactor the login" spoken aloud starts the
work on the strong model and the voice says it started, the person keeps talking, asks how it is
going and hears an answer read off the work's real state, and can stop or undo it by voice. Several
works at once, in the folders their requests name, stoppable, undoable.

What a work IS: a coding turn (`code_api._launch_turn`) run in the background on its own session
record — its own transcript, its own receipt, its own undo snapshot — attached to the conversation
it was spoken in (``parent``). Not a turn of the parent's transcript: a turn holds the parent's
per-session lock for its whole run, and the point is that the parent stays free. The parent's
transcript gets one compact exchange (the request, and "started as work N"), the parent's watchers
get compact ``work`` frames on the conversation's bus (state changes, tools, edits, cards, done),
and every talk turn of the parent carries this module's note in its system prompt, so "how is it
going?" is answered from the record rather than from memory.

Several at once, with one rule: **one running work per folder**. Two agents editing the same tree
at the same time clobber each other's files and each other's undo; a second work asked of a folder
that already has one running waits in that folder's queue and starts when it ends. Works in
different folders run in parallel. (An isolated copy per work — a worktree merged at the end, the
crew's mechanism — is the way to run two in one folder; not here, and the queue says "waiting",
never "running".)

Stopping is the agent loop's own cooperative stop, polled per step: a model call in flight ends,
what was done stays, the receipt says ``cancelled``. Undo is the same snapshot the receipt's undo
button uses. Both reachable from the screen and from the talking model's tools (``work_stop``,
``work_undo``, ``work_status``), which act on this conversation's works and nothing else.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger
from chimera.tools.base import Tool

_log = get_logger("api.works")

WORKS_FILE = "code_works.json"
#: How much of a work's answer the record keeps — enough for the voice to read the gist.
ANSWER_KEEP = 1200

STATES = ("queued", "running", "waiting", "done", "failed", "stopped", "undone")


@dataclass
class Work:
    id: str
    parent: str
    """The conversation this work was asked in."""
    session_id: str
    """The work's own session record (its transcript and receipt), in the works store."""
    turn_id: str
    """Its run's frames, for `GET /api/code/turns/{turn_id}`."""
    workspace: str
    title: str
    model: str = ""
    state: str = "queued"
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    edits: list[str] = field(default_factory=list)
    tools: int = 0
    steps: int = 0
    usd: float | None = None
    answer: str = ""
    error: str = ""
    verified: str = ""
    """``passed`` | ``failed`` | ``abstained`` | ``none`` | "" (did not edit)."""
    revert_token: str = ""
    """The single-use undo token, while the process lives. Empty once used or never minted."""
    reported: bool = False
    """The parent conversation was told this work ended (in a later turn's note)."""
    author: str = ""
    number: int = 0
    """1, 2, 3… within its conversation: what the voice calls it."""
    request: dict[str, Any] = field(default_factory=dict)
    """The turn request the work was spoken in — its posture, ceilings and seams — so a queued
    work starts later under exactly the terms it was asked under."""

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        # The token is an in-memory offer; the record says whether an undo is still possible.
        out["can_undo"] = bool(self.revert_token)
        del out["revert_token"]
        del out["request"]
        return out

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running", "waiting")


class WorkStore:
    """The records, on disk, so a reopened conversation still lists its works."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._works: dict[str, Work] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _log.warning("works file unreadable, starting empty: %s", exc)
            return
        for item in raw.get("works", []) if isinstance(raw, dict) else []:
            try:
                item = {k: v for k, v in item.items() if k in Work.__dataclass_fields__}
                work = Work(**item)
            except TypeError:
                continue
            # A process that died mid-work leaves "running" on disk; the record is honest about it.
            if work.active:
                work.state = "failed"
                work.error = "the app stopped while this work was running"
                work.finished_at = work.finished_at or time.time()
            work.revert_token = ""  # an undo offer dies with the process that minted it
            self._works[work.id] = work

    def _save(self) -> None:
        payload = {"works": [asdict(w) for w in self._works.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, work_id: str) -> Work | None:
        with self._lock:
            return self._works.get(work_id)

    def for_parent(self, parent: str) -> list[Work]:
        with self._lock:
            return sorted((w for w in self._works.values() if w.parent == parent), key=lambda w: w.created_at)

    def put(self, work: Work) -> None:
        with self._lock:
            self._works[work.id] = work
            self._save()

    def forget_parent(self, parent: str) -> None:
        with self._lock:
            for key in [k for k, w in self._works.items() if w.parent == parent]:
                del self._works[key]
            self._save()


class WorkManager:
    """Creates, queues, runs, stops and undoes works; tells the conversation about them."""

    def __init__(
        self,
        store: WorkStore,
        *,
        launch: Callable[[Work], None],
        publish: Callable[[str, str, dict[str, Any]], Any],
        revert: Callable[[str], dict[str, Any]],
    ) -> None:
        self.store = store
        self._launch = launch
        self._publish = publish
        self._revert = revert
        self._lock = threading.RLock()
        self._stops: dict[str, threading.Event] = {}
        self._running: dict[str, str] = {}  # folder -> work id
        self._queues: dict[str, deque[str]] = {}

    # ------------------------------------------------------------------ creating and starting

    def create(
        self,
        *,
        parent: str,
        workspace: Path,
        title: str,
        model: str,
        author: str = "",
        request: dict[str, Any] | None = None,
    ) -> Work:
        """A new work, started now if its folder is free, queued behind the one running there."""
        with self._lock:
            number = len(self.store.for_parent(parent)) + 1
            work = Work(
                id=uuid.uuid4().hex,
                parent=parent,
                session_id=uuid.uuid4().hex,
                turn_id=uuid.uuid4().hex,
                workspace=str(workspace),
                title=title.strip(),
                model=model,
                created_at=time.time(),
                author=author,
                number=number,
                request=dict(request or {}),
            )
            self._stops[work.id] = threading.Event()
            folder = self._folder(workspace)
            if folder in self._running:
                work.state = "queued"
                self._queues.setdefault(folder, deque()).append(work.id)
                self.store.put(work)
                self._announce(work)
                return work
            self._running[folder] = work.id
            self._begin(work)
            return work

    def _begin(self, work: Work) -> None:
        work.state = "running"
        work.started_at = time.time()
        self.store.put(work)
        self._announce(work)
        try:
            self._launch(work)
        except Exception as exc:  # noqa: BLE001 — a launch that fails is a failed work, not a crash
            self.fail(work.id, f"could not start: {exc}")

    @staticmethod
    def _folder(workspace: Path | str) -> str:
        return str(Path(workspace).resolve()).lower() if os.name == "nt" else str(Path(workspace).resolve())

    def should_stop(self, work_id: str) -> Callable[[], bool]:
        event = self._stops.setdefault(work_id, threading.Event())
        return event.is_set

    # ------------------------------------------------------------------ what happens to a work

    def waiting(self, work_id: str, waiting: bool) -> None:
        """A governance card is up (or was answered): the work is waiting on a person."""
        with self._lock:
            work = self.store.get(work_id)
            if work is None or not work.active:
                return
            work.state = "waiting" if waiting else "running"
            self.store.put(work)
            self._announce(work)

    def progress(self, work_id: str, *, tool: str | None = None, edit: str | None = None) -> None:
        with self._lock:
            work = self.store.get(work_id)
            if work is None:
                return
            if tool:
                work.tools += 1
            if edit and edit not in work.edits:
                work.edits.append(edit)
            # A tool ran, so the card it was waiting on was answered.
            if work.state == "waiting":
                work.state = "running"
            self.store.put(work)
            self._publish(work.parent, "work_progress", {"work": work.to_dict(), "tool": tool, "edit": edit})

    def finished(self, work_id: str, payload: dict[str, Any], *, revert_token: str = "", verified: str = "") -> None:
        """The run ended, however it ended; the receipt's payload is what the record keeps."""
        with self._lock:
            work = self.store.get(work_id)
            if work is None:
                return
            stopped = str(payload.get("stopped_reason") or "")
            work.state = "stopped" if stopped == "cancelled" else "done"
            work.finished_at = time.time()
            work.answer = str(payload.get("answer") or "")[:ANSWER_KEEP]
            work.steps = int(payload.get("steps") or 0)
            work.usd = payload.get("usd")
            work.model = str(payload.get("model") or work.model)
            work.revert_token = revert_token
            work.verified = verified
            self.store.put(work)
            self._announce(work)
            self._release(work)

    def fail(self, work_id: str, message: str) -> None:
        with self._lock:
            work = self.store.get(work_id)
            if work is None:
                return
            work.state = "failed"
            work.error = message[:600]
            work.finished_at = time.time()
            self.store.put(work)
            self._announce(work)
            self._release(work)

    def _release(self, work: Work) -> None:
        """The folder is free: the next work queued for it starts."""
        folder = self._folder(work.workspace)
        if self._running.get(folder) != work.id:
            return
        del self._running[folder]
        self._stops.pop(work.id, None)
        queue = self._queues.get(folder)
        while queue:
            next_id = queue.popleft()
            nxt = self.store.get(next_id)
            if nxt is None or nxt.state != "queued":
                continue
            self._running[folder] = nxt.id
            self._begin(nxt)
            return

    # ------------------------------------------------------------------ what a person (or the voice) does

    def stop(self, work_id: str) -> Work | None:
        """Stop a work: a queued one leaves the queue now; a running one ends at its next step."""
        with self._lock:
            work = self.store.get(work_id)
            if work is None:
                return None
            if work.state == "queued":
                queue = self._queues.get(self._folder(work.workspace))
                if queue and work.id in queue:
                    queue.remove(work.id)
                work.state = "stopped"
                work.finished_at = time.time()
                self.store.put(work)
                self._announce(work)
                return work
            if work.active:
                self._stops.setdefault(work.id, threading.Event()).set()
            return work

    def undo(self, work_id: str) -> dict[str, Any]:
        """Undo a finished work's edits, through the same offer the receipt makes."""
        with self._lock:
            work = self.store.get(work_id)
            if work is None:
                return {"ok": False, "reason": "no such work"}
            if work.active:
                return {"ok": False, "reason": "still running — stop it first"}
            if not work.revert_token:
                return {"ok": False, "reason": "nothing to undo, or the offer expired with the app"}
            result = self._revert(work.revert_token)
            work.revert_token = ""
            if result.get("ok"):
                work.state = "undone"
            self.store.put(work)
            self._announce(work)
            return {**result, "work": work.to_dict()}

    # ------------------------------------------------------------------ what the conversation is told

    def _announce(self, work: Work) -> None:
        self._publish(work.parent, "work_state", {"work": work.to_dict()})

    def note(self, parent: str) -> str:
        """What the talking model is told about this conversation's works — every turn, in the
        system prompt, so "how is it going?" is answered from the record. Ended works are told
        once as news and thereafter as history."""
        works = self.store.for_parent(parent)
        if not works:
            return ""
        lines = []
        for w in works:
            age = f"{int(time.time() - w.started_at)}s" if w.started_at and w.active else ""
            piece = f"- work {w.number} ({w.id}) — \"{w.title}\" in {Path(w.workspace).name}: {w.state}"
            if age:
                piece += f" for {age}"
            if w.edits:
                piece += f"; edited {', '.join(w.edits[:6])}"
            if w.state in ("done", "stopped", "failed", "undone") and not w.reported:
                piece += " — NEWS, not yet told to the person"
                if w.answer:
                    piece += f": {w.answer[:300]}"
                if w.error:
                    piece += f": {w.error}"
            lines.append(piece)
        return (
            "Background works of this conversation — tasks the person asked for by voice, being done "
            "by ANOTHER agent right now while you talk. Do not do, redo or continue any of them "
            "yourself: a work listed here is already in hands, and doing it again writes the same "
            "files twice. Answer questions about them from this list, never from memory; call "
            "work_status for details, work_stop to stop one, work_undo to undo a finished one's "
            "edits. (Measured live on 2026-09-18 before this sentence existed: asked \"how is it "
            "going?\" the talking model wrote the work's file itself.)\n" + "\n".join(lines)
        )

    def mark_reported(self, parent: str) -> None:
        with self._lock:
            for w in self.store.for_parent(parent):
                if not w.active and not w.reported:
                    w.reported = True
                    self.store.put(w)


# ---------------------------------------------------------------------- the talker's tools


class _WorkTool(Tool):
    def __init__(self, manager: WorkManager, parent: str) -> None:
        self.manager = manager
        self.parent = parent

    def _find(self, ref: str) -> Work | None:
        """By id, by number, or by a word of the title — the voice says "the login one"."""
        works = self.manager.store.for_parent(self.parent)
        ref = (ref or "").strip().lower()
        for w in works:
            if w.id == ref or str(w.number) == ref:
                return w
        for w in works:
            if ref and ref in w.title.lower():
                return w
        if len(works) == 1:
            return works[0]
        return None


class WorkStatusTool(_WorkTool):
    name = "work_status"
    description = "The state of this conversation's background works: running, waiting, done, what they edited."
    parameters = {"type": "object", "properties": {"work": {"type": "string", "description": "Number, id or a word of the title; empty for all."}}}

    def run(self, **kwargs: Any) -> str:
        ref = str(kwargs.get("work") or "")
        works = [self._find(ref)] if ref else self.manager.store.for_parent(self.parent)
        found = [w for w in works if w is not None]
        if not found:
            return "no such work" if ref else "no background works in this conversation"
        return "\n".join(json.dumps(w.to_dict(), ensure_ascii=False) for w in found)


class WorkStopTool(_WorkTool):
    name = "work_stop"
    description = "Stop a background work of this conversation. What it did so far stays; its edits can then be undone with work_undo."
    parameters = {"type": "object", "properties": {"work": {"type": "string", "description": "Number, id or a word of the title."}}, "required": ["work"]}

    def run(self, **kwargs: Any) -> str:
        work = self._find(str(kwargs.get("work") or ""))
        if work is None:
            return "error: no such work"
        if not work.active:
            return f"work {work.number} is not running (state: {work.state})"
        self.manager.stop(work.id)
        return f"stopping work {work.number} ({work.title}) — it ends at its next step"


class WorkUndoTool(_WorkTool):
    name = "work_undo"
    description = "Undo the edits of a finished or stopped background work of this conversation."
    parameters = {"type": "object", "properties": {"work": {"type": "string", "description": "Number, id or a word of the title."}}, "required": ["work"]}

    def run(self, **kwargs: Any) -> str:
        work = self._find(str(kwargs.get("work") or ""))
        if work is None:
            return "error: no such work"
        result = self.manager.undo(work.id)
        if not result.get("ok"):
            return f"could not undo work {work.number}: {result.get('reason', 'unknown')}"
        return f"undid work {work.number} ({work.title}): {result.get('restored', 0)} file(s) restored"


def work_tools(manager: WorkManager, parent: str) -> list[Tool]:
    return [WorkStatusTool(manager, parent), WorkStopTool(manager, parent), WorkUndoTool(manager, parent)]
