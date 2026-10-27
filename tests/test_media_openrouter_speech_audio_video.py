"""OpenRouter speech, audio and video media flows, with provider traffic simulated."""
from __future__ import annotations

import base64
import json
import wave
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest

from chimera.config import get_settings
from chimera.tools.media import GenerateAudioTool, GenerateVideoTool, ImageGenTool, TextToSpeechTool
from chimera.tools.write_region import WriteRegion

_STATUS_URL = "https://openrouter.ai/api/v1/videos/job_1"
_CONTENT_URL = "https://openrouter.ai/api/v1/videos/job_1/content?index=0"


def _response(
    url: str, status: int = 200, *, content: bytes = b"", body: Any = None, headers: dict[str, str] | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if body is not None:
        return httpx.Response(status, json=body, headers=headers, request=request)
    return httpx.Response(status, content=content, headers=headers, request=request)


def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-key")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    get_settings.cache_clear()


class _Clock:
    """A clock the test moves: sleeping advances it, so deadlines pass without any real waiting."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _no_post(url: str, **kwargs: Any) -> httpx.Response:
    raise AssertionError(f"a paid request was sent to {url}")


# --- text_to_speech -------------------------------------------------------------------------------


def test_openrouter_speech_sends_options_and_saves_pcm_as_wav(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    requests: list[dict[str, Any]] = []
    # 24 kHz mono 16-bit samples, the shape the measured Gemini TTS endpoint answers with.
    payload = b"\x01\x00" * 480

    def post(url: str, **kwargs: Any) -> httpx.Response:
        requests.append({"url": url, **kwargs})
        return _response(url, content=payload, headers={"content-type": "audio/pcm;rate=24000;channels=1"})

    monkeypatch.setattr(httpx, "post", post)
    result = TextToSpeechTool(tmp_path).run(
        text="Olá", voice="Kore", instructions="calorosa, português do Brasil", out="voice.mp3",
    )
    assert requests[0]["url"] == "https://openrouter.ai/api/v1/audio/speech"
    assert requests[0]["json"] == {
        "model": "google/gemini-3.8-flash-tts", "input": "Olá", "voice": "Kore",
        "response_format": "pcm", "instructions": "calorosa, português do Brasil",
    }
    # The mp3 name is replaced: the bytes are WAV, and the path the owner is shown must open.
    assert not (tmp_path / "voice.mp3").exists()
    stored = (tmp_path / "voice.wav").read_bytes()
    assert stored[:4] == b"RIFF" and stored[8:12] == b"WAVE"
    assert stored[44:] == payload
    assert "voice.wav" in result and "bytes" in result


def test_speech_answer_that_is_not_pcm_keeps_its_own_format(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    payload = b"ID3-audio"
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: _response(
        url, content=payload, headers={"content-type": "audio/mpeg"},
    ))
    TextToSpeechTool(tmp_path).run(text="Olá", out="voice.mp3")
    assert (tmp_path / "voice.mp3").read_bytes() == payload


def test_speech_l16_is_pcm_and_is_swapped_to_little_endian(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # RFC 3551 defines L16 as big-endian. WAV stores little-endian, so each sample's bytes are swapped.
    _setup(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: _response(
        url, content=b"\x01\x02\x03\x04", headers={"content-type": "audio/L16;rate=16000"},
    ))
    TextToSpeechTool(tmp_path).run(text="Olá", out="voice.mp3")
    with wave.open(str(tmp_path / "voice.wav"), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.readframes(2) == b"\x02\x01\x04\x03"


def test_speech_refuses_the_wav_name_before_the_paid_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The region allows speech.mp3 only. The request would be stored as speech.wav, which is not allowed,
    # so nothing may be synthesised: the refusal has to come first.
    _setup(monkeypatch)
    monkeypatch.setattr(httpx, "post", _no_post)
    region = WriteRegion(["speech.mp3"], tmp_path)
    result = TextToSpeechTool(tmp_path, write_region=region).run(text="Olá", out="speech.mp3")
    assert result.startswith("error:")
    assert not (tmp_path / "speech.wav").exists()


def test_elevenlabs_preferred_when_both_keys_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "eleven-key")
    get_settings.cache_clear()
    urls: list[str] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        urls.append(url)
        return _response(url, content=b"eleven-mp3")

    monkeypatch.setattr(httpx, "post", post)
    TextToSpeechTool(tmp_path).run(text="Olá")
    assert urls == ["https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"]
    assert (tmp_path / "speech.mp3").read_bytes() == b"eleven-mp3"


def test_speech_provider_error_keeps_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)

    def post(url: str, **kwargs: Any) -> httpx.Response:
        response = _response(url, 402, body={"error": {"code": 402, "message": "credits exhausted"}})
        raise httpx.HTTPStatusError("bad", request=response.request, response=response)

    monkeypatch.setattr(httpx, "post", post)
    result = TextToSpeechTool(tmp_path).run(text="Olá")
    assert "HTTP 402" in result and "credits exhausted" in result
    assert not (tmp_path / "speech.mp3").exists()


# --- generate_audio -------------------------------------------------------------------------------


def _sse(*events: Any) -> list[str]:
    return [f"data: {json.dumps(event)}" for event in events] + ["data: [DONE]"]


class _StreamingResponse:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def __enter__(self) -> _StreamingResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self) -> list[str]:
        return self.lines


def test_audio_stream_uses_chat_audio_format_and_saves_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    payload = b"mp3-stream"
    events = _sse(
        {"choices": [{"delta": {"audio": {"data": base64.b64encode(payload).decode()}}}]},
        {"choices": [], "usage": {"cost": 0.04}},
    )
    captured: dict[str, Any] = {}

    def stream(method: str, url: str, **kwargs: Any) -> _StreamingResponse:
        captured.update(method=method, url=url, **kwargs)
        return _StreamingResponse(events)

    monkeypatch.setattr(httpx, "stream", stream)
    result = GenerateAudioTool(tmp_path).run(prompt="Soft short piano melody")
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["json"]["modalities"] == ["text", "audio"]
    assert captured["json"]["audio"] == {"voice": "alloy", "format": "mp3"}
    assert captured["json"]["stream"] is True
    assert (tmp_path / "generated_audio.mp3").read_bytes() == payload
    assert "0.04" in result


def test_audio_chunks_are_joined_before_one_decode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Five-character chunks: no chunk ends on a multiple of four, so decoding each one alone would fail.
    _setup(monkeypatch)
    payload = bytes(range(37))
    encoded = base64.b64encode(payload).decode()
    chunks = [encoded[i:i + 5] for i in range(0, len(encoded), 5)]
    events = _sse(*({"choices": [{"delta": {"audio": {"data": chunk}}}]} for chunk in chunks))
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _StreamingResponse(events))
    GenerateAudioTool(tmp_path).run(prompt="sound")
    assert (tmp_path / "generated_audio.mp3").read_bytes() == payload


def test_audio_error_at_the_top_of_the_stream_is_shown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    events = _sse({"error": {"code": 503, "message": "upstream down"}})
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _StreamingResponse(events))
    result = GenerateAudioTool(tmp_path).run(prompt="sound")
    assert "upstream down" in result
    assert not (tmp_path / "generated_audio.mp3").exists()


def test_audio_event_that_is_not_an_object_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _StreamingResponse(["data: [1, 2, 3]"]))
    result = GenerateAudioTool(tmp_path).run(prompt="sound")
    assert "not a JSON object" in result


def test_audio_provider_error_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    events = _sse({"choices": [{"error": {"code": 400, "message": "audio output unavailable"}}]})
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _StreamingResponse(events))
    result = GenerateAudioTool(tmp_path).run(prompt="sound")
    assert "audio output unavailable" in result
    assert not (tmp_path / "generated_audio.mp3").exists()


def test_audio_refuses_the_path_before_the_paid_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)

    def stream(*args: Any, **kwargs: Any) -> _StreamingResponse:
        raise AssertionError("a paid audio request was sent")

    monkeypatch.setattr(httpx, "stream", stream)
    region = WriteRegion(["other/**"], tmp_path)
    result = GenerateAudioTool(tmp_path, write_region=region).run(prompt="sound")
    assert result.startswith("error:")


# --- generate_video -------------------------------------------------------------------------------


def test_video_submits_polls_downloads_and_returns_cost(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    monkeypatch.setenv("CHIMERA_VIDEO_MODEL", "x-ai/grok-imagine-video-1.5-lite")
    get_settings.cache_clear()
    posts: list[dict[str, Any]] = []
    gets: list[dict[str, Any]] = []
    states = iter(["queued", "completed"])

    def post(url: str, **kwargs: Any) -> httpx.Response:
        posts.append({"url": url, **kwargs})
        return _response(url, body={"id": "job_1", "status": "queued"})

    def get(url: str, **kwargs: Any) -> httpx.Response:
        gets.append({"url": url, **kwargs})
        if url == _CONTENT_URL:
            return _response(url, content=b"mp4-video")
        return _response(url, body={"id": "job_1", "status": next(states), "usage": {"cost": 0.1}})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    clock = _Clock()
    result = GenerateVideoTool(tmp_path, clock=clock, sleep=clock.sleep).run(
        prompt="A cloud crossing a blue sky", duration=5, resolution="480p",
    )
    assert posts[0]["json"] == {
        "model": "x-ai/grok-imagine-video-1.5-lite", "prompt": "A cloud crossing a blue sky",
        "duration": 5, "aspect_ratio": "16:9", "resolution": "480p",
    }
    assert [g["url"] for g in gets] == [_STATUS_URL, _STATUS_URL, _CONTENT_URL]
    # The content download is the one call that needs the key, so it is the one that must carry it.
    assert gets[-1]["headers"]["Authorization"] == "Bearer router-test-key"
    assert (tmp_path / "generated_video.mp4").read_bytes() == b"mp4-video"
    assert "cost $0.1" in result and "job job_1" in result


def test_video_failed_job_names_the_status_and_the_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: _response(url, body={"id": "job_1"}))
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: _response(
        url, body={"status": "failed", "error": "moderation rejected"},
    ))
    result = GenerateVideoTool(tmp_path).run(prompt="test")
    assert "failed" in result and "moderation rejected" in result and "job job_1" in result
    assert not (tmp_path / "generated_video.mp4").exists()


@pytest.mark.parametrize("state", ["cancelled", "expired", "archived"])
def test_a_status_that_is_not_pending_or_completed_ends_the_wait_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str,
) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: _response(url, body={"id": "job_1"}))
    gets: list[str] = []

    def get(url: str, **kwargs: Any) -> httpx.Response:
        gets.append(url)
        return _response(url, body={"status": state})

    monkeypatch.setattr(httpx, "get", get)
    clock = _Clock()
    result = GenerateVideoTool(tmp_path, clock=clock, sleep=clock.sleep).run(prompt="test")
    assert state in result and "job job_1" in result
    assert len(gets) == 1, "a job that has ended must not be polled again"
    assert not (tmp_path / "generated_video.mp4").exists()


def test_transient_poll_failures_are_survived(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: _response(url, body={"id": "job_1"}))
    statuses: list[Any] = [
        _response(_STATUS_URL, 503, body={"error": "busy"}),
        httpx.ReadTimeout("slow poll"),
        _response(_STATUS_URL, body={"status": "completed"}),
    ]
    calls: list[str] = []

    def get(url: str, **kwargs: Any) -> httpx.Response:
        calls.append(url)
        if url == _CONTENT_URL:
            return _response(url, content=b"mp4-video")
        item = statuses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(httpx, "get", get)
    clock = _Clock()
    result = GenerateVideoTool(tmp_path, clock=clock, sleep=clock.sleep).run(prompt="test")
    assert (tmp_path / "generated_video.mp4").read_bytes() == b"mp4-video", result
    assert calls.count(_STATUS_URL) == 3


def test_three_poll_failures_in_a_row_give_up_with_the_job_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: _response(url, body={"id": "job_1"}))

    def get(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ReadTimeout("slow poll")

    monkeypatch.setattr(httpx, "get", get)
    clock = _Clock()
    result = GenerateVideoTool(tmp_path, clock=clock, sleep=clock.sleep).run(prompt="test")
    assert "failed 3 times in a row" in result and "job job_1" in result


def test_deadline_stops_the_wait_and_names_the_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    monkeypatch.setenv("CHIMERA_VIDEO_TIMEOUT_SECONDS", "60")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: _response(url, body={"id": "job_1"}))
    gets: list[str] = []

    def get(url: str, **kwargs: Any) -> httpx.Response:
        gets.append(url)
        return _response(url, body={"status": "in_progress"})

    monkeypatch.setattr(httpx, "get", get)
    clock = _Clock()
    result = GenerateVideoTool(tmp_path, clock=clock, sleep=clock.sleep).run(prompt="test")
    assert "exceeded its configured deadline" in result and "job job_1" in result
    assert 2 <= len(gets) <= 10, "the wait should poll several times and then stop at the deadline"
    assert clock.now >= 60
    assert not (tmp_path / "generated_video.mp4").exists()


def test_submit_timeout_warns_that_the_job_may_have_been_charged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)

    def post(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ReadTimeout("slow submit")

    monkeypatch.setattr(httpx, "post", post)
    result = GenerateVideoTool(tmp_path).run(prompt="test")
    assert "charged" in result and "before retrying" in result


def test_video_refuses_the_path_before_the_paid_submission(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(httpx, "post", _no_post)
    region = WriteRegion(["other/**"], tmp_path)
    result = GenerateVideoTool(tmp_path, write_region=region).run(prompt="test")
    assert result.startswith("error:")


def test_video_duration_that_is_not_a_number_is_a_clean_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(httpx, "post", _no_post)
    result = GenerateVideoTool(tmp_path).run(prompt="test", duration="five")
    assert "whole number of seconds" in result


def test_conditional_catalogue_gates_audio_video_and_speech() -> None:
    from chimera.tools.conditional import CONDITIONAL_TOOLS

    by_name = {tool.name: tool for tool in CONDITIONAL_TOOLS}
    assert by_name["text_to_speech"].variables == ("ELEVENLABS_API_KEY", "OPENROUTER_API_KEY")
    assert by_name["generate_audio"].variables == ("OPENROUTER_API_KEY",)
    assert by_name["generate_video"].variables == ("OPENROUTER_API_KEY",)


def test_default_video_model_is_the_cheapest_that_takes_five_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Measured on GET /api/v1/videos/models, 2026-10-27: veo-3.1-lite takes only 4 s and 720p, so the
    # 5 s, 480p request this tool makes would be refused there. This model takes both.
    monkeypatch.delenv("CHIMERA_VIDEO_MODEL", raising=False)
    get_settings.cache_clear()
    assert get_settings().video_model == "x-ai/grok-imagine-video-1.5-lite"


# --- generate_image: the stored extension follows the bytes, and the answer names the cost ---

_IMAGE_MODEL = "google/gemini-3.1-flash-image"


def _image_reply(payload: bytes, mime: str, *, cost: float | None) -> httpx.Response:
    url = "data:" + mime + ";base64," + base64.b64encode(payload).decode()
    reply: dict[str, Any] = {"choices": [{"message": {"images": [{"image_url": {"url": url}}]}}]}
    if cost is not None:
        reply["usage"] = {"cost": cost}
    return httpx.Response(200, json=reply, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))


def _image_setup(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: response)
    # The model index is a network read; these cases do not need it.
    monkeypatch.setattr("chimera.tools.media.openrouter_output_modalities", lambda model, **_: None)


def test_generated_jpeg_is_stored_as_jpg_and_linked_to_that_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    jpeg = b"\xff\xd8\xff\xe0" + b"jpeg-data"
    _image_setup(monkeypatch, _image_reply(jpeg, "image/jpeg", cost=0.04))
    result = ImageGenTool(tmp_path).run(prompt="a cat", model=_IMAGE_MODEL, out="photo.png")
    # The bytes decide the extension: a JPEG saved as .png would be a lie the viewer trips over.
    assert (tmp_path / "photo.jpg").read_bytes() == jpeg
    assert not (tmp_path / "photo.png").exists()
    link = urlencode({"path": "photo.jpg", "workspace": str(tmp_path.resolve())})
    assert f"![Generated image](/api/fs/image?{link})" in result


def test_generated_webp_is_stored_as_webp_and_linked_to_that_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    webp = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 data"
    _image_setup(monkeypatch, _image_reply(webp, "image/webp", cost=0.04))
    result = ImageGenTool(tmp_path).run(prompt="a cat", model=_IMAGE_MODEL, out="photo.png")
    assert (tmp_path / "photo.webp").read_bytes() == webp
    assert not (tmp_path / "photo.png").exists()
    link = urlencode({"path": "photo.webp", "workspace": str(tmp_path.resolve())})
    assert f"![Generated image](/api/fs/image?{link})" in result


def test_image_answer_reports_the_cost_the_provider_charged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"png-data"
    _image_setup(monkeypatch, _image_reply(png, "image/png", cost=0.0412))
    result = ImageGenTool(tmp_path).run(prompt="a cat", model=_IMAGE_MODEL, out="cat.png")
    assert "using google/gemini-3.1-flash-image, cost $0.0412;" in result


def test_image_answer_without_a_reported_cost_says_nothing_about_cost(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A missing usage object is not a free image: the answer simply does not state a cost.
    png = b"\x89PNG\r\n\x1a\n" + b"png-data"
    _image_setup(monkeypatch, _image_reply(png, "image/png", cost=None))
    result = ImageGenTool(tmp_path).run(prompt="a cat", model=_IMAGE_MODEL, out="cat.png")
    assert "cost" not in result
