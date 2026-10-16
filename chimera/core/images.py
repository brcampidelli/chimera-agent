"""Optional, bounded image preparation for vision-provider requests.

Pillow is deliberately imported at the call site: a machine without the imaging extra must still
send images exactly as it did before, rather than losing the ability to use vision altogether.
"""

from __future__ import annotations

import io
import warnings

from chimera.telemetry import get_logger

_log = get_logger("core.images")
_pillow_unavailable_logged = False


def image_dimensions(data: bytes) -> tuple[int, int]:
    """Read dimensions after checking the header against Pillow's decompression-bomb ceiling."""
    from PIL import Image, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                width, height = source.size
                pixel_limit = Image.MAX_IMAGE_PIXELS
                if pixel_limit is not None and width * height > pixel_limit:
                    raise ValueError(
                        f"image has {width * height} pixels, exceeding Pillow's {pixel_limit} pixel safety limit"
                    )
                if width <= 0 or height <= 0:
                    raise ValueError("image has invalid dimensions")
                return width, height
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"image could not be decoded: {exc}") from exc
    except Image.DecompressionBombError as exc:
        raise ValueError(f"image rejected as a decompression bomb: {exc}") from exc
    except Image.DecompressionBombWarning as exc:
        raise ValueError(f"image rejected as a decompression bomb: {exc}") from exc


def resize_image(
    data: bytes,
    media_type: str,
    *,
    max_side: int = 2000,
    max_base64_bytes: int = 5 * 1024 * 1024,
) -> tuple[bytes, str]:
    """Bound decoded dimensions and base64 payload without changing images that already fit.

    The provider accounts for the base64 representation, not just the upload bytes. Dimensions
    therefore shrink before encoding is considered, which avoids spending CPU re-encoding a
    full-resolution camera photo that can first be reduced losslessly in geometry. Pillow's
    decompression-bomb threshold is checked against the header before ``load`` allocates pixels;
    an unreadable or dangerous input is an error, never a raw-data fallback.

    Pillow remains optional. Its ``ImportError`` is intentionally allowed to reach the caller so
    that the API can retain its current pass-through behavior and report that degradation once.
    """
    if max_side < 0 or max_base64_bytes < 0:
        raise ValueError("image limits cannot be negative")

    from PIL import Image, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                width, height = source.size
                if width <= 0 or height <= 0:
                    raise ValueError("image has invalid dimensions")
                pixel_limit = Image.MAX_IMAGE_PIXELS
                if pixel_limit is not None and width * height > pixel_limit:
                    raise ValueError(
                        f"image has {width * height} pixels, exceeding Pillow's {pixel_limit} pixel safety limit"
                    )
                source.load()
                if (not max_side or max(width, height) <= max_side) and (
                    not max_base64_bytes or 4 * ((len(data) + 2) // 3) <= max_base64_bytes
                ):
                    return data, media_type
                image = source.copy()
                if max_side and max(width, height) > max_side:
                    ratio = max_side / max(width, height)
                    target = (max(1, round(width * ratio)), max(1, round(height * ratio)))
                    image.thumbnail(target, Image.Resampling.LANCZOS)
                original_format = source.format or "PNG"
                # Geometry shrank: the new encoding may legitimately hold more bytes than a tiny
                # original (a flat-colour PNG), and is accepted on the base64 limit alone. Only a
                # re-encode with the same dimensions has to come out smaller to be worth sending.
                shrunk = image.size != (width, height)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"image could not be decoded: {exc}") from exc
    except Image.DecompressionBombError as exc:
        raise ValueError(f"image rejected as a decompression bomb: {exc}") from exc
    except Image.DecompressionBombWarning as exc:
        raise ValueError(f"image rejected as a decompression bomb: {exc}") from exc

    # A resized source is first encoded in its existing format. Only PNG (the common lossless,
    # high-payload format) uses the specified JPEG quality ladder when that first encoding is too
    # large. The final MIME type follows the bytes rather than the upload's original extension.
    if original_format not in ("PNG", "JPEG", "WEBP", "BMP", "GIF"):
        raise ValueError(f"unsupported image format: {original_format}")
    if original_format == "PNG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    encoded_format = original_format
    output = io.BytesIO()
    try:
        if original_format == "JPEG":
            image.save(output, format=original_format, quality=95)
        else:
            image.save(output, format=original_format)
    except (OSError, ValueError) as exc:
        raise ValueError(f"image could not be re-encoded: {exc}") from exc
    candidate = output.getvalue()
    if (
        (not max_base64_bytes or 4 * ((len(candidate) + 2) // 3) <= max_base64_bytes)
        and (shrunk or len(candidate) <= len(data))
    ):
        return candidate, media_type if encoded_format != "JPEG" else "image/jpeg"
    if not max_base64_bytes and not shrunk and len(candidate) > len(data):
        raise ValueError("image re-encoding would increase the attachment size")

    if original_format == "PNG":
        for quality in (85, 70, 55):
            output = io.BytesIO()
            try:
                image.convert("RGB").save(output, format="JPEG", quality=quality, optimize=True)
            except (OSError, ValueError) as exc:
                raise ValueError(f"image could not be re-encoded as JPEG: {exc}") from exc
            candidate = output.getvalue()
            if (shrunk or len(candidate) <= len(data)) and (
                4 * ((len(candidate) + 2) // 3) <= max_base64_bytes
            ):
                return candidate, "image/jpeg"
    raise ValueError(
        f"image remains larger than the {max_base64_bytes}-byte base64 limit after encoding"
    )


def resize_image_optional(
    data: bytes,
    media_type: str,
    *,
    max_side: int,
    max_base64_bytes: int,
) -> tuple[bytes, str]:
    """Resize when Pillow exists; preserve the original request path and log absence once."""
    global _pillow_unavailable_logged
    try:
        return resize_image(
            data, media_type, max_side=max_side, max_base64_bytes=max_base64_bytes
        )
    except ImportError:
        if not _pillow_unavailable_logged:
            _log.info("Pillow is unavailable; image resizing is skipped")
            _pillow_unavailable_logged = True
        return data, media_type
