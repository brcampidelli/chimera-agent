"""OpenRouter image generation: all provider traffic is simulated."""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest

from chimera.config import get_settings
from chimera.governance.sanitize import FENCE_OPEN
from chimera.tools.media import ImageGenTool

MODEL = "google/gemini-3.1-flash-image"


@pytest.fixture(autouse=True)
def _no_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model index is a network read; these tests say what it would answer, and never ask."""
    monkeypatch.setattr("chimera.tools.media.openrouter_output_modalities", lambda model, **_: None)


def _response(message: dict[str, Any]) -> httpx.Response:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return httpx.Response(200, json={"choices": [{"message": message}]}, request=request)


def _setup(monkeypatch: pytest.MonkeyPatch, message: dict[str, Any], *, openai: bool = False) -> list[dict[str, Any]]:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-secret")
    if openai:
        monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CHIMERA_IMAGE_BACKEND", "auto")
    get_settings.cache_clear()
    requests: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        requests.append({"url": url, **kwargs})
        return _response(message)

    monkeypatch.setattr(httpx, "post", post)
    return requests


def test_openrouter_data_url_is_written_as_image_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nPNG-data").decode()
    _setup(monkeypatch, {"images": [{"image_url": {"url": f"data:image/png;base64,{payload}"}}]})
    result = ImageGenTool(tmp_path).run(prompt="a lighthouse", out="lighthouse.png")
    assert (tmp_path / "lighthouse.png").read_bytes() == b"\x89PNG\r\n\x1a\nPNG-data"
    assert "lighthouse.png" in result
    expected_query = urlencode({"path": "lighthouse.png", "workspace": str(tmp_path.resolve())})
    assert f"![Generated image](/api/fs/image?{expected_query})" in result


def test_openrouter_writes_two_images_to_separate_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payloads = [base64.b64encode(value).decode() for value in (b"\x89PNG\r\n\x1a\none", b"\x89PNG\r\n\x1a\ntwo")]
    _setup(monkeypatch, {"images": [{"image_url": {"url": f"data:image/png;base64,{value}"}} for value in payloads]})
    result = ImageGenTool(tmp_path).run(prompt="two cats", out="cats.png")
    assert (tmp_path / "cats.png").read_bytes() == b"\x89PNG\r\n\x1a\none"
    assert (tmp_path / "cats_2.png").read_bytes() == b"\x89PNG\r\n\x1a\ntwo"
    assert f"![Generated image](/api/fs/image?{urlencode({'path': 'cats.png', 'workspace': str(tmp_path.resolve())})})" in result
    assert f"![Generated image](/api/fs/image?{urlencode({'path': 'cats_2.png', 'workspace': str(tmp_path.resolve())})})" in result


def test_openrouter_missing_image_is_clear_and_creates_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch, {"content": "no image"})
    result = ImageGenTool(tmp_path).run(prompt="nothing", out="missing.png")
    assert result.startswith(f"error: OpenRouter returned no image from model {MODEL}")
    assert "the model replied with text instead" in result and "no image" in result
    assert FENCE_OPEN in result
    assert not (tmp_path / "missing.png").exists()


def test_openrouter_refuses_outside_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nPNG-data").decode()
    _setup(monkeypatch, {"images": [{"image_url": {"url": f"data:image/png;base64,{payload}"}}]})
    elsewhere = tmp_path.parent / "outside-openrouter.png"
    with pytest.raises(Exception, match="escapes workspace"):
        ImageGenTool(tmp_path).run(prompt="a cat", out=str(elsewhere))
    assert not elsewhere.exists()


def test_openrouter_key_is_not_in_log_or_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nPNG-data").decode()
    requests = _setup(monkeypatch, {"images": [{"image_url": {"url": f"data:image/png;base64,{payload}"}}]})
    caplog.set_level(logging.DEBUG)
    result = ImageGenTool(tmp_path).run(prompt="a cat")
    assert "router-test-secret" not in result
    assert "router-test-secret" not in caplog.text
    assert requests[0]["headers"]["Authorization"] == "Bearer router-test-secret"
    assert "router-test-secret" not in str(result)


def test_openrouter_precedes_openai_when_both_keys_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nrouter-image").decode()
    requests = _setup(monkeypatch, {"images": [{"image_url": {"url": f"data:image/png;base64,{payload}"}}]}, openai=True)
    result = ImageGenTool(tmp_path).run(prompt="a cat")
    assert (tmp_path / "generated_image.png").read_bytes() == b"\x89PNG\r\n\x1a\nrouter-image"
    assert len(requests) == 1 and requests[0]["url"].startswith("https://openrouter.ai/")
    assert "google/gemini-3.1-flash-image" in result


def test_imagegen_openai_backend_remains_available_without_router(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    monkeypatch.setenv("CHIMERA_IMAGE_BACKEND", "auto")
    get_settings.cache_clear()
    requests: list[str] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        requests.append(url)
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"\x89PNG\r\n\x1a\nopenai-image").decode()}]}, request=request)

    monkeypatch.setattr(httpx, "post", post)
    ImageGenTool(tmp_path).run(prompt="a cat")
    assert requests == ["https://api.openai.com/v1/images/generations"]
    assert (tmp_path / "generated_image.png").read_bytes() == b"\x89PNG\r\n\x1a\nopenai-image"


def test_imagegen_local_backend_still_selects_diffusers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    monkeypatch.setenv("CHIMERA_IMAGE_BACKEND", "local")
    get_settings.cache_clear()
    called: list[tuple[str, str]] = []

    def local(prompt: str, out: Path, model: str, size: str) -> None:
        called.append((prompt, str(out)))
        out.write_bytes(b"local-image")

    monkeypatch.setattr("chimera.tools.media._generate_local", local)
    ImageGenTool(tmp_path).run(prompt="a cat")
    assert called == [("a cat", str(tmp_path / "generated_image.png"))]
    assert (tmp_path / "generated_image.png").read_bytes() == b"local-image"


def test_openrouter_http_errors_do_not_expose_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()

    def fail(*args: Any, **kwargs: Any) -> None:
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": "Bearer router-test-secret"})
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr(httpx, "post", fail)
    caplog.set_level(logging.DEBUG)
    result = ImageGenTool(tmp_path).run(prompt="a cat")
    assert result == f"error: OpenRouter image generation failed (HTTP 401, model {MODEL}): OpenRouter sent no error body"
    assert "router-test-secret" not in result and "router-test-secret" not in caplog.text
    assert not (tmp_path / "generated_image.png").exists()


def _status(monkeypatch: pytest.MonkeyPatch, status: int, body: Any) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(status, json=body, request=request)

    def post(url: str, **kwargs: Any) -> httpx.Response:
        return response

    monkeypatch.setattr(httpx, "post", post)


def test_an_http_400_says_the_status_and_openrouters_own_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-secret")
    get_settings.cache_clear()
    _status(monkeypatch, 400, {"error": {"code": 400, "message": "This model does not support image output."}})
    result = ImageGenTool(tmp_path).run(prompt="a cat", out="cat.png")
    assert result.startswith(f"error: OpenRouter image generation failed (HTTP 400, model {MODEL}): ")
    assert "OpenRouter error code 400" in result
    assert "This model does not support image output." in result
    assert FENCE_OPEN in result, "provider text is external data and is fenced"
    assert "router-test-secret" not in result


def test_a_provider_message_is_truncated_to_300_characters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-secret")
    get_settings.cache_clear()
    _status(monkeypatch, 402, {"error": {"code": 402, "message": "z" * 1000}})
    result = ImageGenTool(tmp_path).run(prompt="a cat")
    assert "HTTP 402" in result
    assert "z" * 300 in result and "z" * 301 not in result


def _choice_post(text: str, finish: str) -> Any:
    def post(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        body = {"choices": [{"finish_reason": finish, "message": {"content": text}}]}
        return httpx.Response(200, json=body, request=request)

    return post


def test_a_200_with_no_image_says_why_and_shows_the_text_instead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch, {})
    monkeypatch.setattr(httpx, "post", _choice_post("I cannot draw people from photographs.", "content_filter"))
    result = ImageGenTool(tmp_path).run(prompt="a person")
    assert f"no image from model {MODEL}" in result
    assert "finish_reason content_filter" in result
    assert "I cannot draw people from photographs." in result
    assert FENCE_OPEN in result


def test_a_timeout_says_it_timed_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-secret")
    get_settings.cache_clear()

    def post(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr(httpx, "post", post)
    result = ImageGenTool(tmp_path).run(prompt="a cat")
    assert result == f"error: OpenRouter image generation failed (model {MODEL}): the request timed out before OpenRouter answered"


def test_an_image_only_model_goes_to_the_images_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nflux").decode()
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    requests: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        requests.append({"url": url, **kwargs})
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"data": [{"b64_json": payload, "media_type": "image/png"}]}, request=request)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr("chimera.tools.media.openrouter_output_modalities", lambda model, **_: ("image",))
    result = ImageGenTool(tmp_path).run(prompt="a cat", model="black-forest-labs/flux-3-image-klein", out="cat.png")
    assert requests[0]["url"] == "https://openrouter.ai/api/v1/images"
    body = requests[0]["json"]
    assert body["model"] == "black-forest-labs/flux-3-image-klein" and body["prompt"] == "a cat" and body["n"] == 1
    assert "messages" not in body and "modalities" not in body
    assert (tmp_path / "cat.png").read_bytes() == b"\x89PNG\r\n\x1a\nflux"
    assert "cat.png" in result


def test_a_text_and_image_model_is_asked_for_both(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\ngemini").decode()
    requests = _setup(monkeypatch, {"images": [{"image_url": {"url": f"data:image/png;base64,{payload}"}}]})
    monkeypatch.setattr("chimera.tools.media.openrouter_output_modalities", lambda model, **_: ("image", "text"))
    ImageGenTool(tmp_path).run(prompt="a cat")
    assert requests[0]["json"]["modalities"] == ["image", "text"]


def test_an_undescribed_model_keeps_the_both_ways_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nunknown").decode()
    requests = _setup(monkeypatch, {"images": [{"image_url": {"url": f"data:image/png;base64,{payload}"}}]})
    ImageGenTool(tmp_path).run(prompt="a cat", model="some/unlisted-model")
    assert requests[0]["json"]["modalities"] == ["image", "text"]


def test_the_catalogue_is_read_once_and_its_output_modalities_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import listing

    reads: list[int] = []

    def index(**_: Any) -> tuple[list[dict[str, Any]], str]:
        reads.append(1)
        return [
            {"id": "black-forest-labs/flux-3-image-klein", "architecture": {"output_modalities": ["image"]}},
            {"id": "google/gemini-3.1-flash-image", "architecture": {"output_modalities": ["image", "text"]}},
            {"id": "broken/entry", "architecture": "not a dict"},
        ], ""

    monkeypatch.setattr(listing, "fetch_openrouter_index", index)
    monkeypatch.setattr(listing, "_outputs", None)
    assert listing.openrouter_output_modalities("black-forest-labs/flux-3-image-klein") == ("image",)
    assert listing.openrouter_output_modalities("google/gemini-3.1-flash-image") == ("image", "text")
    assert listing.openrouter_output_modalities("broken/entry") is None
    assert listing.openrouter_output_modalities("not/listed") is None
    assert len(reads) == 2, "two reads (default index and the image-filtered one), then cached"


def test_an_unreadable_catalogue_is_unknown_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import listing

    monkeypatch.setattr(listing, "fetch_openrouter_index", lambda **_: ([], "unreachable"))
    monkeypatch.setattr(listing, "_outputs", None)
    assert listing.openrouter_output_modalities("any/model") is None


@pytest.mark.parametrize(
    ("declared", "expected_keys"),
    [
        ({"resolution": {}, "aspect_ratio": {}, "n": {}}, {"resolution", "aspect_ratio", "n", "model", "prompt"}),
        ({"size": {}, "n": {}}, {"size", "n", "model", "prompt"}),
    ],
)
def test_the_images_request_names_only_the_size_parameters_the_model_declares(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, declared: dict[str, Any], expected_keys: set[str]
) -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nsized").decode()
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    requests: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        requests.append({"url": url, **kwargs})
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"data": [{"b64_json": payload}]}, request=request)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr("chimera.tools.media.openrouter_output_modalities", lambda model, **_: ("image",))
    monkeypatch.setattr("chimera.tools.media.openrouter_supported_parameters", lambda model, **_: frozenset(declared))
    ImageGenTool(tmp_path).run(prompt="a cat", model="some/image-only", out="cat.png")
    body = requests[0]["json"]
    assert set(body) == expected_keys
    if "resolution" in expected_keys:
        assert body["resolution"] == "1K" and body["aspect_ratio"] == "1:1"


def test_the_catalogue_parses_declared_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import listing

    def index(**_: Any) -> tuple[list[dict[str, Any]], str]:
        return [
            {
                "id": "black-forest-labs/flux-3-image-klein",
                "architecture": {"output_modalities": ["image"]},
                "supported_parameters": {"resolution": {"type": "enum"}, "aspect_ratio": {}},
            }
        ], ""

    monkeypatch.setattr(listing, "fetch_openrouter_index", index)
    monkeypatch.setattr(listing, "_outputs", None)
    assert listing.openrouter_supported_parameters("black-forest-labs/flux-3-image-klein") == frozenset(
        {"resolution", "aspect_ratio"}
    )
    assert listing.openrouter_supported_parameters("not/listed") is None
