"""Provider-bound image bytes are smaller without changing the stored attachment or governance."""

from __future__ import annotations

import builtins
import io
import random
from typing import Any

import pytest


def _png(size: tuple[int, int], *, noise: bool = False) -> bytes:
    from PIL import Image

    image = Image.new("RGB", size)
    if noise:
        generator = random.Random(7)
        image.putdata([
            (generator.randrange(256), generator.randrange(256), generator.randrange(256))
            for _ in range(size[0] * size[1])
        ])
    else:
        image.putdata([
            (x % 256, y % 256, (x + y) % 256)
            for y in range(size[1])
            for x in range(size[0])
        ])
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_a_large_image_shrinks_to_the_side_limit_and_keeps_its_proportions() -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    from chimera.core.images import resize_image

    resized, media_type = resize_image(_png((4000, 2000)), "image/png", max_side=2000)
    with Image.open(io.BytesIO(resized)) as image:
        assert image.size == (2000, 1000)
    assert media_type == "image/png"


def test_an_image_that_already_fits_is_returned_byte_for_byte() -> None:
    pytest.importorskip("PIL")
    from chimera.core.images import resize_image

    original = _png((32, 24))
    assert resize_image(original, "image/png") == (original, "image/png")


def test_a_png_over_the_base64_limit_uses_the_quality_ladder() -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    from chimera.core.images import resize_image

    original = _png((128, 128), noise=True)
    bound = 20_000
    resized, media_type = resize_image(
        original, "image/png", max_side=0, max_base64_bytes=bound
    )

    assert media_type == "image/jpeg"
    assert 4 * ((len(resized) + 2) // 3) <= bound
    assert resized != original
    with Image.open(io.BytesIO(resized)) as image:
        assert image.format == "JPEG"


def test_an_image_that_exceeds_pillow_pixel_safety_is_refused(monkeypatch: Any) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    from chimera.core.images import resize_image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="pixel safety limit|decompression bomb"):
        resize_image(_png((11, 10)), "image/png")


def test_zero_limits_disable_resizing_and_preserve_identical_bytes() -> None:
    pytest.importorskip("PIL")
    from chimera.core.images import resize_image

    original = _png((32, 24))
    assert resize_image(
        original, "image/png", max_side=0, max_base64_bytes=0
    ) == (original, "image/png")


def test_missing_pillow_keeps_the_original_attachment_bytes(monkeypatch: Any) -> None:
    from chimera.core.images import resize_image_optional

    original_import = builtins.__import__

    def without_pil(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("Pillow is not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pil)
    original = b"legacy image bytes"
    assert resize_image_optional(
        original, "image/png", max_side=2000, max_base64_bytes=5 * 1024 * 1024
    ) == (original, "image/png")


def test_an_image_that_cannot_be_decoded_is_not_forwarded_as_raw_bytes() -> None:
    pytest.importorskip("PIL")
    from chimera.core.images import resize_image

    with pytest.raises(ValueError, match="could not be decoded"):
        resize_image(b"not an encoded image", "image/png")
