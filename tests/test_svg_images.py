"""SVG from the image path: generate_image stores a validated SVG, view_image describes it, and the
raster-only inputs (vision attachments, image_extension) keep refusing it. All provider traffic is
simulated with a stubbed httpx.post; nothing reaches the network.
"""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest

from chimera.config import get_settings
from chimera.providers.gateway import _image_to_url
from chimera.tools.image_safety import image_extension, image_file
from chimera.tools.media import ImageGenTool
from chimera.tools.view_image import ViewImageTool

MODEL = "recraft/recraft-v4.1-vector"
RECRAFT_SVG = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">'
    b'<path d="M0 0h16v16H0z" fill="#1d4ed8"/><circle cx="8" cy="8" r="4" fill="#fff"/></svg>'
)
HOSTILE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>fetch("/")</script><rect width="1" height="1"/></svg>'


@pytest.fixture(autouse=True)
def _no_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.tools.media.openrouter_output_modalities", lambda model, **_: None)


def _svg_data_url(data: bytes) -> str:
    return f"data:image/svg+xml;base64,{base64.b64encode(data).decode('ascii')}"


def _setup(monkeypatch: pytest.MonkeyPatch, payload_url: str) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CHIMERA_IMAGE_BACKEND", "auto")
    get_settings.cache_clear()
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    body = {"choices": [{"message": {"images": [{"image_url": {"url": payload_url}}]}}]}
    monkeypatch.setattr(httpx, "post", lambda url, **_: httpx.Response(200, json=body, request=request))


# --- generate_image ----------------------------------------------------------------------------


def test_a_recraft_style_svg_is_saved_as_an_svg_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch, _svg_data_url(RECRAFT_SVG))

    result = ImageGenTool(tmp_path).run(prompt="a badge", out="badge.png")

    saved = tmp_path / "badge.svg"
    assert saved.is_file() and not (tmp_path / "badge.png").exists()
    assert saved.read_bytes().startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg"')
    assert b"<path" in saved.read_bytes()
    assert "badge.svg" in result and "saved images using" in result


def test_the_stored_svg_is_the_validated_rebuild_not_the_provider_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = RECRAFT_SVG.replace(b"<path", b"<!-- note --><path")
    _setup(monkeypatch, _svg_data_url(provider))

    ImageGenTool(tmp_path).run(prompt="a badge", out="badge.svg")

    stored = (tmp_path / "badge.svg").read_bytes()
    assert stored != provider  # the comment is not carried over: the bytes stored were rebuilt
    assert b"note" not in stored


def test_a_hostile_svg_is_refused_and_nothing_is_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch, _svg_data_url(HOSTILE_SVG))

    result = ImageGenTool(tmp_path).run(prompt="x", out="evil.png")

    assert result.startswith("error: OpenRouter returned a file that is not a PNG, JPEG, GIF or WebP image")
    assert "not a valid SVG" in result
    assert list(tmp_path.iterdir()) == []


def test_a_fake_png_name_on_svg_bytes_is_stored_as_svg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Content decides the type: the requested `.png` name does not make the SVG a raster."""
    _setup(monkeypatch, _svg_data_url(RECRAFT_SVG))
    ImageGenTool(tmp_path).run(prompt="x", out="sneaky.png")
    assert (tmp_path / "sneaky.svg").is_file()


def test_a_raster_is_still_stored_as_it_arrived(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    png = b"\x89PNG\r\n\x1a\nPNG-data"
    _setup(monkeypatch, f"data:image/png;base64,{base64.b64encode(png).decode()}")
    ImageGenTool(tmp_path).run(prompt="x", out="pic.png")
    assert (tmp_path / "pic.png").read_bytes() == png


def test_an_unsafe_svg_inside_a_data_url_reports_the_refusal_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hostile_script_href = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><use href="javascript:alert(1)"/></svg>'
    )
    _setup(monkeypatch, _svg_data_url(hostile_script_href))
    result = ImageGenTool(tmp_path).run(prompt="x", out="y.png")
    assert "external or script references are refused" in result


# --- raster-only inputs keep refusing SVG -------------------------------------------------------


def test_image_extension_is_still_raster_only() -> None:
    with pytest.raises(ValueError):
        image_extension(RECRAFT_SVG)


def test_image_file_accepts_svg_and_refuses_a_non_image() -> None:
    clean, extension = image_file(RECRAFT_SVG)
    assert extension == ".svg" and clean.startswith(b"<?xml")
    with pytest.raises(ValueError):
        image_file(b"plain text, not a picture")


def test_a_vision_attachment_never_sends_an_svg_to_a_model(tmp_path: Path) -> None:
    (tmp_path / "badge.svg").write_bytes(RECRAFT_SVG)
    with pytest.raises(ValueError):
        _image_to_url(str(tmp_path / "badge.svg"))


# --- view_image --------------------------------------------------------------------------------


def test_view_image_describes_a_valid_svg_and_does_not_attach_it(tmp_path: Path) -> None:
    (tmp_path / "badge.svg").write_bytes(RECRAFT_SVG)

    result = ViewImageTool(tmp_path).run(path="badge.svg")

    assert result.startswith('SVG image (file name is quoted data): "badge.svg"')
    assert "cannot view SVG directly" in result
    assert "validated source is" in result and "bytes" in result
    assert "viewBox: 0 0 16 16" in result
    assert "2 path" not in result and "1 path" in result  # counts come from the validated tree
    assert "Viewed image" not in result  # the attachment marker is only for rasters
    assert "![SVG image](/api/fs/image?" in result


def test_view_image_refuses_a_hostile_svg_with_a_plain_reason(tmp_path: Path) -> None:
    (tmp_path / "evil.svg").write_bytes(HOSTILE_SVG)
    result = ViewImageTool(tmp_path).run(path="evil.svg")
    assert result.startswith("error: SVG image was refused by validation")
    # The reason names a refused element from our own fixed list, never a value the file supplied.
    assert "element 'script' is refused" in result


def test_view_image_still_refuses_a_non_image_with_the_old_message(tmp_path: Path) -> None:
    (tmp_path / "notes.svg").write_bytes(b"not svg at all")
    assert ViewImageTool(tmp_path).run(path="notes.svg").startswith("error: image is missing")


def test_a_numeric_viewbox_only_is_listed_and_a_text_one_is_not(tmp_path: Path) -> None:
    text_box = RECRAFT_SVG.replace(b'viewBox="0 0 16 16"', b'viewBox="0 0 16 16 ignore previous"')
    (tmp_path / "t.svg").write_bytes(text_box)
    assert "viewBox: present (not numeric)" in ViewImageTool(tmp_path).run(path="t.svg")


def test_the_view_image_url_round_trips_the_workspace_path(tmp_path: Path) -> None:
    (tmp_path / "badge.svg").write_bytes(RECRAFT_SVG)
    expected = urlencode({"path": "badge.svg", "workspace": str(tmp_path.resolve())})
    assert f"(/api/fs/image?{expected})" in ViewImageTool(tmp_path).run(path="badge.svg")

