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
from chimera.tools.media import ImageGenTool


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
    assert result == "error: OpenRouter image generation returned no image"
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
    assert result == "error: OpenRouter image generation failed"
    assert "router-test-secret" not in result and "router-test-secret" not in caplog.text
    assert not (tmp_path / "generated_image.png").exists()
