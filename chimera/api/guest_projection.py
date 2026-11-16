"""What a guest may see of a conversation: one projection, an allowlist, fail-closed.

The session bus carries every frame of a conversation, the owner's turns included, because the
owner's own screen follows the same stream. A guest follows it too — live, and again from the
stored conversation — so **where a frame leaves for a guest is where it is decided what the guest
sees**, not where the frame is made. The decision (owner, P1b): a guest reads the *answers* the
owner's turns gave, and not what the machine looked at to give them — tool arguments and results,
diffs, approval previews, browser pictures, the verifier's output, instruction sources, the
receipt's cost and the host's paths.

The earlier version was a denylist (``memory_facts`` out, the rest through), which is the shape that
fails the day someone adds a frame: every new field would reach a guest by default. This is the
inverse. Each event names the fields a guest may have; every other field of a known event is
removed, and an event nobody listed is dropped whole. Adding a frame to the product therefore costs
a guest nothing until somebody decides, here, that a guest should see it.

**What stays visible is a choice the owner makes by sharing.** The answer text is the model's own
words, and the model may have put a private thing in them — a figure from a file it read, a line of
a log. Nothing here can tell which words those are; sharing a conversation means sharing its
answers (``docs/security.md``, and the sharing panel says so).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chimera.core.guest_history import (
    ANSWER_UNAVAILABLE,
    AUTO_PLACEHOLDER,
    HUMAN,
    HUMAN_TEXT_KEY,
    IN_PROGRESS_TEXT,
    ORIGIN_KEY,
    UNFINISHED_TEXT,
)

#: The generic sentence a guest reads when a turn failed. The real one can carry the host's log
#: path or a provider's request id; the owner's screen still has it.
GUEST_ERROR_TEXT = "the coding turn failed"

#: Receipt fields a guest keeps from a stored turn. ``memory_facts_used`` is a bare count (the
#: records themselves never leave) and is kept so a stored conversation and its live replay agree.
_DONE_KEYS = ("author", "memory_facts_used")

def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _opening(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: payload[k] for k in ("author", "started_at") if k in payload}
    # Fail-closed: only a message provably a person's is shown (`origin` is "human"); an automatic
    # request (a CI log, a repair prompt) or a frame without the mark reads as the placeholder.
    out["message"] = (
        _text(payload.get("message")) if payload.get(ORIGIN_KEY) == HUMAN else AUTO_PLACEHOLDER
    )
    return out


def _token(payload: dict[str, Any]) -> dict[str, Any]:
    return {"text": _text(payload.get("text"))}


def _kind(name: Any) -> str:
    """A tool's name reduced to the part that carries no detail.

    An external (ACP) agent names a call ``"<kind>: <title>"`` and the title is the operation, path
    included (``read: Read C:/…/clientes.csv``). Only what precedes the first colon is kept, and it is
    bounded: a name is a label, not a place to carry a sentence.
    """
    return _text(name).split(":", 1)[0].strip()[:64]


def _tool_start(payload: dict[str, Any]) -> dict[str, Any]:
    return {"name": _kind(payload.get("name")), "status": "running"}


def _tool(payload: dict[str, Any]) -> dict[str, Any]:
    ok = payload.get("ok") is not False
    return {"name": _kind(payload.get("name")), "ok": ok, "status": "ok" if ok else "error"}


def _waiting(_: dict[str, Any]) -> dict[str, Any]:
    # The guest is told the turn waits on the owner and nothing of what it asks.
    return {"status": "waiting"}


def _done(payload: dict[str, Any]) -> dict[str, Any]:
    # `final_answer` is the model's own answer, sealed only when the turn ended normally with it;
    # `answer` is that with the server's verdict, notes and receipt summary appended (or an error
    # sentence that can carry a path). Only a sealed final answer is sent; any other ending reads
    # as a turn that did not conclude.
    out = {k: payload[k] for k in _DONE_KEYS if k in payload}
    sealed = payload.get("final_sealed") is True and isinstance(payload.get("final_answer"), str)
    out["answer"] = payload["final_answer"] if sealed else UNFINISHED_TEXT
    return out


def _error(_: dict[str, Any]) -> dict[str, Any]:
    return {"message": GUEST_ERROR_TEXT}


def _presence(payload: dict[str, Any]) -> dict[str, Any]:
    names = payload.get("names")
    return {"names": [n for n in names if isinstance(n, str)] if isinstance(names, list) else []}


#: event -> the function that builds the guest's payload from the real one. An event that is not a
#: key here does not reach a guest. Not listed on purpose: ``edit`` (diffs), ``plan``, ``todo``,
#: ``verified`` (command output), ``browser``, ``chart``, ``design``, ``guidance``, ``notice``,
#: ``context``, ``user_question``, and anything added later.
_PROJECTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "turn_started": _opening,
    "token": _token,
    "tool_start": _tool_start,
    "tool": _tool,
    "approval": _waiting,
    "done": _done,
    "error": _error,
    "presence": _presence,
}


def project_frame(frame: Any, viewer: str = "") -> dict[str, Any] | None:
    """The frame as the guest ``viewer`` (a share link's identity) may see it, or ``None``."""
    if not isinstance(frame, dict):
        return None
    build = _PROJECTIONS.get(str(frame.get("event")))
    if build is None:
        return None
    if frame["event"] == "token" and not (viewer and frame.get("guest") == viewer):
        # Only THIS guest's own turn is typed out to them. The owner's turn (a screen's or a
        # channel's, Discord included) and other guests' carry the text between tool calls, which is
        # what the machine said while it read; their final answer arrives whole, on `done`.
        return None
    payload = frame.get("payload")
    return {
        "session_seq": frame.get("session_seq"),
        "event": frame["event"],
        "turn_id": frame.get("turn_id", ""),
        "author": frame.get("author", ""),
        "payload": build(payload if isinstance(payload, dict) else {}),
    }


def _stored_tool(tool: Any) -> dict[str, Any] | None:
    if not isinstance(tool, dict):
        return None
    ok = tool.get("ok") is not False
    return {"name": _kind(tool.get("name")), "ok": ok, "status": "ok" if ok else "error"}


def project_exchange(exchange: Any, *, running: bool = False) -> dict[str, Any] | None:
    """A stored exchange as a guest may read it: the question, the answer, the tools' names.

    Arguments, observations, edits (diffs), the verifier's verdict and the receipt's accounting are
    left out, as on the live stream. ``edits`` and ``verified`` keep their keys, empty, because the
    guest page draws an exchange from that shape.
    """
    if not isinstance(exchange, dict):
        return None
    tools = [t for t in (_stored_tool(t) for t in exchange.get("tools") or []) if t is not None]
    done = exchange.get("done")
    # The sealed final answer, or a placeholder where the turn worked and sealed none (an older
    # conversation, a turn that stopped short); never a guess from the text.
    # `running`: this is the conversation's last exchange and its turn has not closed. The seal is
    # written only when the turn closes, so until then there is no final answer to show.
    sealed = exchange.get("final_answer")
    if isinstance(sealed, str):
        final = sealed
    elif running:
        final = IN_PROGRESS_TEXT
    else:
        final = ANSWER_UNAVAILABLE if exchange.get("answer") or exchange.get("tools") else ""
    typed = exchange.get(HUMAN_TEXT_KEY)
    return {
        "you": typed if exchange.get(ORIGIN_KEY) == HUMAN and isinstance(typed, str) else AUTO_PLACEHOLDER,
        "answer": final,
        "tools": tools,
        "edits": [],
        "done": (
            _done({**done, "final_answer": final, "final_sealed": isinstance(sealed, str)})
            if isinstance(done, dict) else None
        ),
        "verified": None,
    }
