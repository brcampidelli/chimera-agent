"""Three defects a user hit within a minute of opening the app, and what each one broke.

They arrived together and they are the same kind of bug: something that was *known* to the system
and never reached the person who needed it.

1. **The composer warned about the wrong model.** ``/api/vision`` always answered about
   ``default_model``, so the moment the model became a per-conversation choice, the sentence under
   the paperclip described a model the turn was not going to use.
2. **Then the turn died anyway.** The warning says an image "will not be seen" — and the server sent
   it regardless, so OpenRouter answered ``No endpoints found that support image input`` and the
   whole turn failed. The interface promised a degraded turn; the system delivered no turn.
3. **And the reason was hidden.** Every native failure rendered as "the coding turn failed", which
   cannot be told apart from a crash. The one sentence the user could have acted on was in the
   exception and nowhere else.

Plus the oldest of the four: dictation never worked on the local model, because ``str(None)`` is
``"None"`` and that five-character string travelled all the way to faster-whisper as a language code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.code_api import _native_failure

# --- the language code that was the word "None" --------------------------------------------------


def test_no_language_hint_means_detect_it_not_the_word_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API layer passes ``language=None`` for "work it out", and that is the case that broke.

    ``str(None).strip() or None`` never yields None: the string is five characters long, so the
    ``or`` never fires. faster-whisper then refused the whole call — `'None' is not a valid language
    code` — which is what the app showed under the microphone button, for every dictation, forever.
    """
    from chimera.tools.media import TranscribeAudioTool

    seen: dict[str, Any] = {}

    def fake(path: str, language: str | None) -> str:
        seen["language"] = language
        return "transcribed"

    monkeypatch.setattr("chimera.tools.media._transcribe_faster_whisper", fake)

    tool = TranscribeAudioTool(Path.cwd())
    audio = Path.cwd() / "speech.webm"
    audio.write_bytes(b"not really audio")
    try:
        tool.run(path="speech.webm", language=None)
    finally:
        audio.unlink()

    assert seen["language"] is None, "the absence of a hint was turned into the string 'None'"


def test_a_real_language_hint_still_travels(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.tools.media import TranscribeAudioTool

    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "chimera.tools.media._transcribe_faster_whisper",
        lambda path, language: seen.setdefault("language", language) or "ok",
    )

    tool = TranscribeAudioTool(Path.cwd())
    audio = Path.cwd() / "speech2.webm"
    audio.write_bytes(b"not really audio")
    try:
        tool.run(path="speech2.webm", language="pt")
    finally:
        audio.unlink()

    assert seen["language"] == "pt"


# --- the failure message the user was not shown --------------------------------------------------


def test_the_provider_sentence_reaches_the_user() -> None:
    """The exact string that cost a user two failed turns and told them nothing."""
    raw = (
        "litellm.NotFoundError: NotFoundError: OpenrouterException - "
        '{"error":{"message":"No endpoints found that support image input","code":404}}'
    )

    assert _native_failure(Exception(raw)) == "No endpoints found that support image input"


def test_an_internal_failure_stays_generic() -> None:
    """A bug in this repository is ours to debug, and a stack trace in the composer helps nobody.

    This is the half of the old behaviour worth keeping: the reason "the coding turn failed" existed
    at all. What was wrong was applying it to the provider's refusals too.
    """
    assert _native_failure(KeyError("some internal state")) == "the coding turn failed"
    assert _native_failure(Exception("")) == "the coding turn failed"


@pytest.mark.parametrize(
    "raw",
    [
        "litellm.RateLimitError: rate limit exceeded",
        "litellm.AuthenticationError: invalid api key",
        "litellm.BadRequestError: this model does not exist",
        "litellm.ContextWindowExceededError: maximum context length is 8192 tokens",
    ],
)
def test_every_class_the_user_can_act_on_is_forwarded(raw: str) -> None:
    assert _native_failure(Exception(raw)) != "the coding turn failed"


def test_a_wall_of_json_is_bounded() -> None:
    # A provider that answers with a page of JSON must not paste it into the transcript.
    raw = "litellm.BadRequestError: not found " + ("x" * 1000)
    out = _native_failure(Exception(raw))
    assert len(out) <= 300
    assert out.endswith("…")


# --- the image the model could not see -----------------------------------------------------------


def test_vision_is_asked_about_the_model_that_will_answer() -> None:
    """The endpoint takes the composer's pick. Before, it always answered about the default — so the
    warning named one model while a different one ran, which is worse than no warning: it reads as
    authoritative."""
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.interface import ChatSession

    client = TestClient(build_api_app(lambda: ChatSession(None)))  # type: ignore[arg-type]

    asked = client.get("/api/vision", params={"model": "openrouter/google/gemini-2.5-flash"}).json()
    assert asked["model"] == "openrouter/google/gemini-2.5-flash"

    # Omitted still means the install's default — what a caller with no picker means.
    default = client.get("/api/vision").json()
    assert default["model"] and default["model"] != asked["model"]
