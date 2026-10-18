"""A chat, linked by its owner to a conversation of the Code screen (study 33, S4).

The owner types ``/link <id>`` in Discord or Telegram and, from then on, what THEY write in that chat
is a turn of that Code conversation - the same conversation the app shows, continued from a phone.
``/unlink`` puts the chat back on its own bot session. What decides whether this is safe:

**Only the listed owner.** Both the commands and the routing ask :func:`is_listed_owner`, the rule
the chat approvals use: an id the owner wrote in that platform's allowlist, never a bot, and with no
allowlist NOBODY - an open bot answers anyone, which says nothing about who the owner is. A stranger
in a linked group room keeps talking to the bot's own session, as before; their text never reaches
the owner's conversation, and their ``/link`` changes nothing.

**Text only.** A voice note or an image is untrusted input on the bot (the gateway transcribes it
into a TAINTED turn). A Code turn has no such door: the owner's message is the turn's instruction.
So a linked chat refuses media with a sentence instead of passing the caption on and dropping what
was attached, or - worse - handing a transcript to the conversation as if the owner had typed it.

**Files go back only as the bot's own turns send them.** A linked turn that wrote a deliverable (a
Chimera Design PDF, say) has it attached to the reply by the same rules as the bot's own turns
(:mod:`chimera.server.attachments`): only when the owner turned attachments on, only what this turn
wrote, and only inside the folder the bot may send from. The turn reports its tool calls and folder
through a :class:`LinkedTrace`; a turn that cannot (an older ``turn`` callable) answers in text only.

**The file is the owner's.** ``<CHIMERA_HOME>/channel_links.json``, replaced atomically, inside the
data folder that the app's file routes, the desktop bridge and the agent's write tools all refuse
(`chimera.core.own_files.protected_reason`); no route reads or writes it. It is read again on every
lookup and before every change, because the app builds one gateway per platform and two copies held
in memory would each save their own view, and the second save would erase the first one's link.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

#: One lock for every :class:`ChannelLinks` on any path in this process: the read-modify-replace of
#: two gateways (Discord and Telegram) must not interleave.
_FILE_LOCK = Lock()

#: The most conversations ``/link`` lists, and how much of each title: a chat message, not a page.
LIST_LIMIT = 20
TITLE_CHARS = 70
#: How much of an id ``/link`` shows and echoes back.
SHORT_ID = 8

NOT_OWNER = "Only the listed owner can link this chat to a Code conversation."
NO_CODE_HERE = (
    "There are no Code conversations in this process: linking works in the desktop app, where "
    "the conversations live."
)
NOT_FOUND = "No Code conversation matches that id, or the prefix matches more than one."
NONE_YET = "There are no Code conversations yet."
UNLINKED = "This chat is no longer linked to a Code conversation."
NOT_LINKED = "This chat was not linked to a Code conversation."
MEDIA_REFUSED = (
    "This chat is linked to a Code conversation, which takes text only: voice notes and images "
    "are not sent to it. Write the message, or send /unlink to talk to the bot instead."
)


class ChannelLinks:
    """``platform:chat_id`` -> Code session id, kept in one JSON file under the data folder."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @staticmethod
    def key(platform: str, chat_id: str) -> str:
        return f"{platform.strip().lower()}:{chat_id.strip()}"

    def _read(self) -> dict[str, str]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(key): value for key, value in payload.items() if isinstance(value, str) and value
        }

    def get(self, platform: str, chat_id: str) -> str | None:
        with _FILE_LOCK:
            return self._read().get(self.key(platform, chat_id))

    def set(self, platform: str, chat_id: str, session_id: str) -> None:
        with _FILE_LOCK:
            links = self._read()
            links[self.key(platform, chat_id)] = session_id
            self._write(links)

    def remove(self, platform: str, chat_id: str) -> bool:
        with _FILE_LOCK:
            links = self._read()
            removed = links.pop(self.key(platform, chat_id), None) is not None
            if removed:
                self._write(links)
            return removed

    def _write(self, links: dict[str, str]) -> None:
        """Replace the file whole: a reader sees the old map or the new one, never half of one."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(json.dumps(links, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)


def short_list(sessions: list[dict[str, Any]]) -> str:
    """The newest conversations as ``title - prefix`` lines, for the owner to pick one."""
    rows = []
    for item in sessions[:LIST_LIMIT]:
        ident = str(item.get("id", ""))
        if ident:
            title = " ".join(str(item.get("title") or "").split())[:TITLE_CHARS] or "Conversation"
            rows.append(f"{title} - {ident[:SHORT_ID]}")
    if not rows:
        return NONE_YET
    return "Recent Code conversations (send /link <id>):\n" + "\n".join(rows)


def resolve_session(sessions: list[dict[str, Any]], argument: str) -> str | None:
    """A full id, or a prefix that matches exactly one conversation. Ambiguity is never guessed."""
    candidate = argument.strip()
    if not candidate:
        return None
    ids = [str(item.get("id", "")) for item in sessions]
    if candidate in ids:
        return candidate
    matches = [ident for ident in ids if ident.startswith(candidate)]
    return matches[0] if len(matches) == 1 else None


def _is_owner(settings: Any, message: Any) -> bool:
    from chimera.server.allowlist import is_listed_owner

    return not getattr(message, "from_bot", False) and is_listed_owner(settings, message)


def _carries_media(message: Any) -> bool:
    return bool(
        getattr(message, "media_kind", "")
        or getattr(message, "media_data", None)
        or getattr(message, "images", None)
        or getattr(message, "media_refusal", False)
    )


class ChannelCommands:
    """``/link`` and ``/unlink``, answered only for the listed owner; None for any other text.

    ``sessions`` lists the Code conversations newest first, or is None in a process that has none
    (``chimera serve`` on a VPS): there ``/link`` says so instead of reaching the model as a prompt.
    """

    def __init__(
        self,
        *,
        settings: Any,
        links: ChannelLinks,
        sessions: Callable[[], list[dict[str, Any]]] | None,
    ) -> None:
        self.settings = settings
        self.links = links
        self.sessions = sessions

    def handle(self, message: Any) -> str | None:
        command, _, argument = str(getattr(message, "text", "") or "").strip().partition(" ")
        # Telegram addresses a command to a bot as `/link@SomeBot` in a group.
        command = command.lower().split("@", 1)[0]
        if command not in {"/link", "/unlink"}:
            return None
        if not _is_owner(self.settings, message):
            return NOT_OWNER
        if command == "/unlink":
            removed = self.links.remove(str(message.platform), str(message.chat_id))
            return UNLINKED if removed else NOT_LINKED
        if self.sessions is None:
            return NO_CODE_HERE
        available = self.sessions()
        if not argument.strip():
            return short_list(available)
        session_id = resolve_session(available, argument)
        if session_id is None:
            return NOT_FOUND
        self.links.set(str(message.platform), str(message.chat_id), session_id)
        return f"This chat is linked to Code conversation {session_id[:SHORT_ID]}. /unlink undoes it."


@dataclass(frozen=True)
class TracedTool:
    """One tool call of a linked turn, as its ``tool`` frame reported it — the four fields
    :func:`chimera.server.attachments.written_paths` reads."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    observation: str = ""


@dataclass
class LinkedTrace:
    """What a linked turn did, for the reply's attachments: its tool calls and the folder it ran in.

    Filled by the Code turn (``code_api.channel_turn``) from the frames it already publishes; read
    here only after the turn finished.
    """

    workspace: Path | None = None
    tools: list[TracedTool] = field(default_factory=list)

    def tool(self, frame: dict[str, Any]) -> None:
        arguments = frame.get("arguments")
        self.tools.append(TracedTool(
            name=str(frame.get("name") or ""),
            arguments=dict(arguments) if isinstance(arguments, dict) else {},
            ok=bool(frame.get("ok")),
            observation=str(frame.get("observation") or ""),
        ))


#: ``(tool calls, the conversation's folder) -> Attachments``: `attachments.linked_attachments`
#: bound to the bot's folder.
LinkedAttach = Callable[[list[Any], Path], Any]


def linked_reply(
    message: Any,
    links: ChannelLinks,
    turn: Callable[..., str],
    settings: Any,
    *,
    attach: LinkedAttach | None = None,
) -> str | None:
    """The Code turn's answer for the owner's message in a linked chat; None to route as before.

    None for everybody but the listed owner, and for a chat with no link - so a stranger in a linked
    room, and every unlinked chat, reach the bot's own session exactly as they did.

    With ``attach`` (the bot attaches files) and a ``turn`` that takes a ``trace``, the answer is a
    :class:`~chimera.server.gateway.Reply` carrying the files this turn wrote that may be sent, and a
    line for each it wrote and will not send.
    """
    if not _is_owner(settings, message):
        return None
    session_id = links.get(str(message.platform), str(message.chat_id))
    if not session_id:
        return None
    if _carries_media(message):
        return MEDIA_REFUSED
    text = str(getattr(message, "text", "") or "").strip()
    if not text:
        return None
    if attach is None or not _takes_trace(turn):
        return str(turn(session_id, text, str(message.platform)))
    trace = LinkedTrace()
    answer = str(turn(session_id, text, str(message.platform), trace=trace))
    if trace.workspace is None:
        return answer
    from chimera.server.gateway import Reply, with_warnings

    attached = attach(trace.tools, trace.workspace)
    return Reply(
        with_warnings(answer, [f"not attached: {why}" for why in attached.skipped]), attached.files
    )


def _takes_trace(turn: Callable[..., str]) -> bool:
    from chimera.core.code_session import _accepts

    return _accepts(turn, "trace")
