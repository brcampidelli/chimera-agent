"""What a share link's guest's MODEL is shown of the conversation before it.

A guest's turn resumes the stored conversation like any other, and the stored conversation is the
model's own message list: the owner's ``read_file`` results, the arguments of every call, the
text the model wrote between steps, the verifier's output appended to an answer, and the
automatic requests the harness wrote as ``user`` messages (a repair prompt carrying a failing
command's output, a compaction summary, a CI log). A guest who asks "repeat the last read_file
result" would get it back from the model without a tool running. So the history a guest's turn
sends is a PROJECTION of the stored one, built here:

* the owner's turns become what the owner said and the model's FINAL answer — nothing between;
* a message that is not provably the owner's own words (``origin`` is not ``human``) is replaced
  by a placeholder: for a guest it is worth more to hide an old human request than to show an
  automatic one, so an unmarked (older) message is treated as automatic;
* a guest's own turns (their opening message carries an ``author``) enter whole, because they are
  the guest's: confined when they ran, and theirs to read.

The marks are ours, local to Chimera, and dropped before a provider sees a message
(``providers.gateway._LOCAL_KEYS``), like ``guidance``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from chimera.core.agent_batch_note import is_batch_note

#: Who wrote a ``user`` message: a person (``human``) or the harness (``auto``). Absent on messages
#: stored before the mark existed, and an absent mark is read as "not provably a person".
ORIGIN_KEY = "origin"
HUMAN = "human"
AUTO = "auto"
#: Set on the opening message of a turn a share link's guest sent: the guest's name.
AUTHOR_KEY = "author"
#: The identity of the share link a guest turn came through (`Share.id`): what makes a turn THIS
#: guest's own. A name is a label anybody can type; a turn that came through a channel (Discord) or
#: from another link is not this guest's, whatever it is called.
GUEST_KEY = "guest_id"
#: What the person TYPED, sealed when the message was created: before the text of an attached
#: document, an expanded command or a mention's context was appended to the message sent to the
#: model. A guest is shown this and never the sent text.
HUMAN_TEXT_KEY = "human_text"
#: Set on the assistant message that is a turn's FINAL answer, and only when the turn ended
#: normally with the model's answer. Nothing is inferred from content: a message without the seal
#: is not a final answer, whatever it says (an error sentence, a fragment before an internal retry).
FINAL_KEY = "final_seal"
#: The model's own final answer, kept on the assistant message when the server replaced its text
#: (a verification verdict, a note, a receipt summary appended after the model spoke).
MODEL_ANSWER_KEY = "model_answer"

#: What a guest reads where an automatic request, or one that cannot be shown to be a person's, was.
AUTO_PLACEHOLDER = "(pedido automático do Chimera)"
#: What a guest reads where a stored turn has no sealed final answer (an older conversation, or a
#: turn that stopped before concluding): not a guess at what the text was.
ANSWER_UNAVAILABLE = "(resposta não disponível ao convidado)"
#: What a guest reads for the last turn of a conversation while it is still running, before the
#: turn's answer is sealed (the seal is written only once the turn has finished, repairs included).
IN_PROGRESS_TEXT = "(em andamento)"
#: What is stored as ``human_text`` for a turn whose request the server wrote from a model-made
#: card (``suggest_task``): the card's prompt is generated, so it is never kept as what a person said.
CARD_TURN_TEXT = "(executou um card sugerido)"
#: What a guest's ``done`` carries when the turn did not end normally with an answer.
UNFINISHED_TEXT = "(o turno não concluiu)"

#: A user-role continuation created after the agent's own view_image result. Presentation only:
#: persisted dictionaries bearing this key never confer authority to rewrite a turn's answer.
WORKSPACE_IMAGE_KEY = "workspace_image"
WORKSPACE_IMAGE_ORIGIN = "auto:workspace_image"
_IMAGE_INSTRUCTION = "Inspect this workspace image and answer the user's task."
_IMAGE_KEYS = frozenset({"role", "content", "images", ORIGIN_KEY, WORKSPACE_IMAGE_KEY})


class WorkspaceImageFrame(dict[str, Any]):
    """Runtime identity minted only at the image-tool seam; JSON/request fields cannot create it."""

    def __init__(self, path: str, tool_call_id: str) -> None:
        super().__init__({"role": "user", "content": _IMAGE_INSTRUCTION, "images": [path],
                          ORIGIN_KEY: WORKSPACE_IMAGE_ORIGIN, WORKSPACE_IMAGE_KEY: {"tool_call_id": tool_call_id}})


def is_workspace_image_frame(message: Any) -> bool:
    """Strict internal shape, never enough by itself to hide a persisted user separator."""
    if (not isinstance(message, dict) or message.get("role") != "user"
            or message.get(ORIGIN_KEY) != WORKSPACE_IMAGE_ORIGIN or set(message) != _IMAGE_KEYS):
        return False
    mark, images = message.get(WORKSPACE_IMAGE_KEY), message.get("images")
    return bool(
        message.get("content") == _IMAGE_INSTRUCTION
        and isinstance(mark, dict) and set(mark) == {"tool_call_id"}
        and isinstance(mark.get("tool_call_id"), str) and mark["tool_call_id"]
        and isinstance(images, list) and len(images) == 1 and isinstance(images[0], str) and images[0]
    )


def workspace_image_positions(messages: Sequence[Any]) -> frozenset[int]:
    """Prove persisted continuations against the immediately preceding view_image tool trace.

    Human/guest authorship fields make the shape fail. Missing/ambiguous provenance is a separator,
    even when a copied flag or an image is present. No provider or request can mint runtime identity.
    """
    positions: set[int] = set()
    image_calls: set[str] = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            image_calls.clear()
            continue
        role = message.get("role")
        if role == "assistant":
            image_calls.clear()
            calls = message.get("tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                        continue
                    function = call.get("function")
                    if isinstance(function, dict) and function.get("name") == "view_image":
                        image_calls.add(call["id"])
        elif role == "user":
            previous = messages[index - 1] if index else None
            if (is_workspace_image_frame(message) and isinstance(previous, dict)
                    and previous.get("role") == "tool" and isinstance(previous.get("content"), str)
                    and previous.get("tool_call_id") == message[WORKSPACE_IMAGE_KEY]["tool_call_id"]
                    and previous.get("tool_call_id") in image_calls):
                positions.add(index)
            else:
                image_calls.clear()
        elif role != "tool":
            image_calls.clear()
    return frozenset(positions)


#: Keys the marks above use, for the gateway to drop from the wire.
LOCAL_MESSAGE_KEYS = frozenset(
    {ORIGIN_KEY, AUTHOR_KEY, MODEL_ANSWER_KEY, GUEST_KEY, HUMAN_TEXT_KEY, FINAL_KEY, WORKSPACE_IMAGE_KEY}
)


def message_text(content: Any) -> str:
    """Message content as text, whether it arrived as a string or as content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def is_turn_opener(message: Any) -> bool:
    """A user boundary. An image mark alone cannot suppress it; the full-list trace must prove that.

    Mixed/ambiguous image metadata stays a separator, including when another continuation flag
    was copied with it. Normal guidance and batch records without this mark keep their behavior.
    """
    return (
        isinstance(message, dict)
        and message.get("role") == "user"
        and (not message.get("guidance") or WORKSPACE_IMAGE_KEY in message)
        and (not is_batch_note(message) or WORKSPACE_IMAGE_KEY in message)
    )


def model_final_answer(message: dict[str, Any]) -> str:
    """What the MODEL said in this assistant message, before the server changed the text."""
    kept = message.get(MODEL_ANSWER_KEY)
    return kept if isinstance(kept, str) else message_text(message.get("content"))


def is_sealed_final(message: Any) -> bool:
    """Whether ``message`` is the sealed final answer of a turn that ended normally."""
    return (
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and not message.get("tool_calls")
        and message.get(FINAL_KEY) is True
    )


def shown_request(opener: Any) -> str:
    """A turn's opening message as a guest may read it: the person's words, or the placeholder."""
    if isinstance(opener, dict) and opener.get(ORIGIN_KEY) == HUMAN:
        typed = opener.get(HUMAN_TEXT_KEY)
        if isinstance(typed, str):
            return typed
    return AUTO_PLACEHOLDER


def _turns(messages: list[Any]) -> list[tuple[dict[str, Any] | None, list[dict[str, Any]]]]:
    """The messages cut into turns: (opening message or None, every message of the turn)."""
    turns: list[tuple[dict[str, Any] | None, list[dict[str, Any]]]] = []
    images = workspace_image_positions(messages)
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        marked_user = message.get("role") == "user" and WORKSPACE_IMAGE_KEY in message
        if is_batch_note(message) and not marked_user:
            continue
        if index not in images and is_turn_opener(message):
            turns.append((message, [message]))
        elif turns:
            turns[-1][1].append(message)
        else:
            turns.append((None, [message]))
    return turns


def _is_guests_own(opener: dict[str, Any] | None, viewer: str) -> bool:
    """A turn THIS guest sent: the opening message carries this link's identity."""
    return (
        bool(viewer)
        and opener is not None
        and opener.get(ORIGIN_KEY) == HUMAN
        and opener.get(GUEST_KEY) == viewer
    )


def project_history(messages: list[Any], viewer: str = "") -> list[dict[str, Any]]:
    """The stored messages as the model of ``viewer``'s turn may be shown them.

    ``viewer`` is the share link's identity. Everything not written by this guest is projected:
    the owner's turns, the owner's turns that came through a channel, other guests', automatic ones.
    """
    out: list[dict[str, Any]] = []
    for opener, turn in _turns(messages):
        if _is_guests_own(opener, viewer):
            out.extend(dict(m) for m in turn)
            continue
        if opener is None:
            continue  # a turn whose opening was trimmed away: nothing to pair an answer with
        out.append({"role": "user", "content": shown_request(opener)})
        final = next((m for m in reversed(turn) if is_sealed_final(m)), None)
        if final is not None:
            out.append({"role": "assistant", "content": model_final_answer(final)})
        elif any(m.get("role") == "assistant" for m in turn):
            # Worked, but no sealed final answer (an older conversation, or a turn that stopped
            # short): a placeholder, never the text, which cannot be told from what a server added.
            out.append({"role": "assistant", "content": ANSWER_UNAVAILABLE})
    return out
