"""Fill-in-the-middle completions from a local Ollama.

**Why FIM and not "continue the prefix".** A completion that has not seen the text *after* the
cursor writes the closing brace you already have and re-declares the variable on the next line. The
`/api/generate` endpoint takes a `suffix` alongside the `prompt`, and a model published with a base
(non-instruct) tag has the template that uses it. An instruct tag ignores `suffix` and answers in
prose — which is why the model name is a setting with a base tag as its default, and why the note
says so when the model is missing.

**Cancellation is the whole engineering problem here.** Every keystroke starts a request; if the
one before it keeps generating, a local GPU is occupied producing text nobody will read, and the
next request queues behind it. The symptom is "the model is slow" and the cause is a leak — which
is a bad pair, because it sends you optimising the model instead of fixing the plumbing. So the
upstream call is STREAMED and the loop checks two things between chunks: whether a newer request
has superseded this one, and whether the deadline has passed. Either one leaves the `async with`,
which closes the connection, which is how Ollama is told to stop. Nothing here relies on a timeout
firing somewhere else, and nothing relies on `asyncio.CancelledError` arriving at the right frame.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("complete.inline")

#: Hard cut. Past this the suggestion has stopped being a suggestion and become an interruption —
#: the cursor has moved on and whatever arrives is about text that is no longer there.
DEFAULT_BUDGET_MS = 600

#: Tokens to ask for. A completion long enough to be worth a Tab and short enough that a wrong one
#: costs an Escape, not a read.
MAX_TOKENS = 96

#: How much of the file to send either side of the cursor. Characters, not tokens, because the
#: budget being protected is latency and the prompt is what dominates it on a small local model.
PREFIX_CHARS = 2000
SUFFIX_CHARS = 1000


@dataclass(frozen=True)
class Completion:
    """One suggestion, or an honest account of why there is none."""

    text: str
    #: False ONLY for a standing problem: no server, model not pulled, an error from the runtime.
    #: A superseded request, an expired budget and a model with nothing to say are all `True` with
    #: an empty `text` — unlike a diagnostic, an absent suggestion asserts nothing about the file,
    #: so raising a banner for the ordinary cases would be noise that buries the real ones.
    available: bool
    note: str
    ms: int
    model: str


class _Slot:
    """One in-flight completion for a key. `abandon` is set when a newer one arrives."""

    __slots__ = ("abandon",)

    def __init__(self) -> None:
        self.abandon = asyncio.Event()


#: In-flight completion per key (one editor, one file). Not a lock: a queue of stale suggestions is
#: worse than none, so a newer request does not wait for the older one — it abandons it.
_slots: dict[str, _Slot] = {}


def reset_slots() -> None:
    """Drop the single-flight table. For tests, and for a server shutting down."""
    for slot in _slots.values():
        slot.abandon.set()
    _slots.clear()


def _trim(text: str, suffix: str) -> str:
    """Clean up what the model returned.

    Two habits are worth undoing. A base model often re-emits the text that follows the cursor,
    which would show the user their own file as a suggestion; and it will happily keep going for
    fifty lines, which is a refactor arriving unannounced through a Tab key.
    """
    text = text.replace("\r\n", "\n")
    head = suffix.strip()[:40].rstrip()
    if head:
        # Only the tail is checked: a suggestion that CONTAINS the suffix somewhere in the middle is
        # a coincidence, but one that ends by restating it is the duplication we mean.
        stripped = text.rstrip()
        if stripped.endswith(head):
            text = stripped[: -len(head)]
    lines = text.split("\n")
    if len(lines) > 8:
        text = "\n".join(lines[:8])
    return text.rstrip() if text.strip() else ""


async def complete_inline(
    prefix: str,
    suffix: str,
    *,
    base_url: str,
    model: str,
    key: str = "default",
    budget_ms: int = DEFAULT_BUDGET_MS,
) -> Completion:
    """Ask the local model what comes next. Never raises."""
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    base = (base_url or "").rstrip("/")
    if not base:
        return Completion("", False, "no local model server is configured", 0, model)
    if not model:
        return Completion("", False, "no completion model is configured", 0, model)
    if not prefix.strip() and not suffix.strip():
        return Completion("", True, "", 0, model)

    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is a hard dependency
        return Completion("", False, "httpx is not installed", elapsed(), model)

    slot = _Slot()
    previous = _slots.get(key)
    _slots[key] = slot
    if previous is not None:
        # Set BEFORE the new request goes out, so the old one stops competing for the same GPU
        # rather than finishing first and being thrown away.
        previous.abandon.set()

    body = {
        "model": model,
        "prompt": prefix[-PREFIX_CHARS:],
        "suffix": suffix[:SUFFIX_CHARS],
        "stream": True,
        "options": {"num_predict": MAX_TOKENS, "temperature": 0.1, "stop": ["\n\n\n"]},
    }
    deadline = started + budget_ms / 1000
    pieces: list[str] = []

    try:
        # `timeout` is the connect/read ceiling, not the answer's — the deadline below is what
        # bounds the completion, and it is checked between chunks so a model that streams forever
        # is cut at the same moment as one that hangs.
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(budget_ms / 1000 + 1.0)) as client,
            client.stream("POST", f"{base}/api/generate", json=body) as response,
        ):
            if response.status_code == 404:
                await response.aread()
                return Completion(
                    "", False, f"the model {model} is not pulled (ollama pull {model})",
                    elapsed(), model,
                )
            if response.status_code >= 400:
                await response.aread()
                return Completion(
                    "", False, f"the model server answered {response.status_code}",
                    elapsed(), model,
                )
            async for line in response.aiter_lines():
                if slot.abandon.is_set():
                    # Leaving the `async with` closes the connection, which is how the runtime
                    # learns to stop generating. Returning the partial text instead would show
                    # a suggestion for a cursor position that has already moved.
                    return Completion("", True, "", elapsed(), model)
                if time.monotonic() > deadline:
                    return Completion("", True, "", elapsed(), model)
                if not line.strip():
                    continue
                try:
                    chunk: dict[str, Any] = json.loads(line)
                except ValueError:
                    continue
                if chunk.get("error"):
                    return Completion("", False, str(chunk["error"])[:200], elapsed(), model)
                piece = chunk.get("response")
                if isinstance(piece, str):
                    pieces.append(piece)
                if chunk.get("done"):
                    break
    except Exception as exc:  # noqa: BLE001 — a missing Ollama is a normal state, not a 500
        _log.debug("inline completion failed: %s", exc)
        return Completion(
            "", False, f"could not reach a model server at {base}", elapsed(), model
        )
    finally:
        if _slots.get(key) is slot:
            del _slots[key]

    return Completion(_trim("".join(pieces), suffix), True, "", elapsed(), model)
