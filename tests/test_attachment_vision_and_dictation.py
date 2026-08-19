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


# --- the capability table that was wrong in both directions ---------------------------------------


def test_the_providers_own_answer_beats_the_static_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Both halves of the bug, in one test.

    LiteLLM's table had never heard of DeepSeek V4 Flash, so the app read "unknown", sent the image
    and OpenRouter killed the turn. The same table reports "no" for Mistral Small 3.2, which reads
    images — so believing it there withholds an image from a model that could have used it. One
    unknown that costs a turn, one false negative that removes a capability, from the same source.
    """
    import json

    from chimera.api.attachments import vision_support
    from chimera.providers import listing

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    listing._index_cache = None
    (tmp_path / listing.PRICE_CACHE_NAME).write_text(
        json.dumps(
            {
                "fetched_at": "2026-08-19T00:00:00+00:00",
                "models": {
                    "openrouter/deepseek/deepseek-v4-flash": {"vision": False, "tools": True},
                    "openrouter/mistralai/mistral-small-3.2-24b-instruct": {"vision": True},
                },
            }
        ),
        encoding="utf-8",
    )

    assert vision_support("openrouter/deepseek/deepseek-v4-flash") == "no"
    assert vision_support("openrouter/mistralai/mistral-small-3.2-24b-instruct") == "yes"


def test_a_model_the_index_never_saw_still_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # A local Ollama tag, a vendor not on OpenRouter, an install that has never fetched: the answer
    # has to come from wherever it came from before, not from an absence read as "no".
    from chimera.api.attachments import vision_support
    from chimera.providers import listing

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "empty"))
    listing._index_cache = None

    assert vision_support("ollama/llama3") in {"yes", "no", "unknown"}


def test_the_remembered_index_survives_the_older_file_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """0.48.0rc2 wrote `{"prices": {slug: [in, out]}}`. Upgrading must not throw those prices away to
    gain capabilities — the user would silently lose the receipt figures they already had."""
    import json

    from chimera.providers import listing

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    listing._index_cache = None
    (tmp_path / listing.PRICE_CACHE_NAME).write_text(
        json.dumps({"fetched_at": "x", "prices": {"openrouter/vendor/model": [0.25, 0.95]}}),
        encoding="utf-8",
    )

    assert listing.known_price("openrouter/vendor/model") == (0.25, 0.95)
    assert listing.known_vision("openrouter/vendor/model") is None


# --- the picture that never left the building ----------------------------------------------------


def test_a_plain_dict_with_images_still_becomes_a_multimodal_message() -> None:
    """The bug that made every attached image useless, and one provider answer 500.

    `Agent.run` builds this turn's user message as a LITERAL dict, because it is assembling a list
    that also holds the history's dicts:

        {"role": "user", "content": task, "images": [...]}

    `_to_message_dicts` converted `Message` objects and passed dicts through untouched. So the
    images never became `image_url` parts — the model was never shown the picture — and the key
    `images`, carrying a local file path, travelled to the provider, which answered
    `500 Internal Server Error`. The composer showed the attachment, the upload succeeded and the
    vision check said yes; the request contained neither a picture nor a valid body.
    """
    from chimera.providers.gateway import _to_message_dicts

    out = _to_message_dicts(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "what is this?", "images": ["https://example.invalid/a.png"]},
        ]
    )

    assert out[0] == {"role": "system", "content": "be brief"}
    parts = out[1]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url"]
    assert parts[1]["image_url"]["url"] == "https://example.invalid/a.png"
    # And the key no provider knows must not survive the trip.
    assert "images" not in out[1]


def test_an_empty_images_key_is_dropped_rather_than_sent() -> None:
    from chimera.providers.gateway import _to_message_dicts

    out = _to_message_dicts([{"role": "user", "content": "hello", "images": []}])

    assert out == [{"role": "user", "content": "hello"}]


def test_a_local_path_is_encoded_rather_than_named(tmp_path: Any) -> None:
    """A file path means nothing on the other end of an HTTP request — the bytes have to go."""
    from chimera.providers.gateway import _to_message_dicts

    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    out = _to_message_dicts([{"role": "user", "content": "read it", "images": [str(png)]}])

    url = out[0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert str(png) not in url
