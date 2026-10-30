"""Admit supported raster images and web fonts, identified by bytes and checked against limits.

The declared MIME type is a claim; the first bytes are the evidence. A file is accepted only when its
signature is supported, its header yields dimensions inside the cap when it is an image, and any
declared type agrees with what was found. That last rule matters for the exporter: an asset is later
embedded as ``data:<mime>;base64,...``, and the mime written there comes from the sniff, never from
the uploader.

**SVG is refused**, with the reason in the error. It is XML that can carry ``<script>``, event handlers
and external references, and making one safe takes a real sanitizer this repo does not ship; a MIME
check cannot do it (the handoff's reference says the same and then accepted the type anyway).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from chimera.design.model import ID_PATTERN, DesignError

MAX_ASSET_BYTES = 5 * 1024 * 1024
MAX_DESIGN_ASSET_BYTES = 50 * 1024 * 1024
MAX_ASSET_SIDE = 8_192
MAX_ASSET_PIXELS = 40_000_000

_PNG = b"\x89PNG\r\n\x1a\n"
_SVG_HINT = re.compile(
    rb"^\s*(?:<\?xml[^>]*>\s*)?(?:<!--.*?-->\s*)*(?:<!DOCTYPE\s+svg|<svg\b)",
    re.IGNORECASE | re.DOTALL,
)
_ID = re.compile(ID_PATTERN)
#: JPEG start-of-frame markers, which carry the dimensions (not C4/C8/CC: those are tables).
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


class AssetRejected(DesignError):
    """The bytes do not match a supported image or font."""


@dataclass(frozen=True)
class AssetInfo:
    mime: str
    width: int | None
    height: int | None
    size: int


def is_safe_asset_id(value: str) -> bool:
    return bool(_ID.fullmatch(value))


def validate_asset(
    data: bytes, *, declared_mime: str | None = None, max_bytes: int = MAX_ASSET_BYTES
) -> AssetInfo:
    """Identify ``data`` and check it against the caps, or raise :class:`AssetRejected` saying why."""
    if not isinstance(data, bytes):
        raise AssetRejected("asset data must be bytes")
    if not data:
        raise AssetRejected("the file is empty")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise AssetRejected("asset byte limit must be a positive integer")
    if len(data) > max_bytes:
        raise AssetRejected(f"the file is {len(data)} bytes; the limit is {max_bytes} bytes (5 MB)")
    if declared_mime is not None and not isinstance(declared_mime, str):
        raise AssetRejected("declared MIME must be text")
    head = data[:1024].lstrip(b"\xef\xbb\xbf")
    if _SVG_HINT.match(head) or (declared_mime or "").lower().startswith("image/svg"):
        raise AssetRejected(
            "SVG is not accepted: it can carry scripts and external references, and there is no "
            "trusted sanitizer here. Export it as PNG or WebP."
        )
    mime, width, height = _sniff(data)
    if declared_mime and declared_mime.lower().split(";", 1)[0].strip() not in _aliases(mime):
        raise AssetRejected(f"declared as {declared_mime!r}, but the bytes are {mime}")
    if width is not None and height is not None:
        if not (1 <= width <= MAX_ASSET_SIDE and 1 <= height <= MAX_ASSET_SIDE):
            raise AssetRejected(f"{width}x{height} is outside 1..{MAX_ASSET_SIDE} per side")
        if width * height > MAX_ASSET_PIXELS:
            raise AssetRejected(f"{width}x{height} is more than {MAX_ASSET_PIXELS} pixels")
    return AssetInfo(mime=mime, width=width, height=height, size=len(data))


def _aliases(mime: str) -> set[str]:
    return {"image/jpeg", "image/jpg", "image/pjpeg"} if mime == "image/jpeg" else {mime}


def _sniff(data: bytes) -> tuple[str, int | None, int | None]:
    if data.startswith(_PNG):
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise AssetRejected("a PNG without its header")
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", width, height
    if data.startswith(b"\xff\xd8\xff"):
        width, height = _jpeg_size(data)
        return "image/jpeg", width, height
    if len(data) >= 16 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        width, height = _webp_size(data)
        return "image/webp", width, height
    if data.startswith((b"GIF87a", b"GIF89a")):
        if len(data) < 10:
            raise AssetRejected("a GIF without its logical screen descriptor")
        width, height = struct.unpack("<HH", data[6:10])
        return "image/gif", width, height
    if data.startswith(b"wOF2") and len(data) >= 48:
        return "font/woff2", None, None
    if data.startswith(b"wOFF") and len(data) >= 44:
        return "font/woff", None, None
    if data.startswith((b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")) and len(data) >= 12:
        return "font/ttf", None, None
    raise AssetRejected("not a PNG, JPEG, WebP, GIF or supported WOFF/TTF font")


def _jpeg_size(data: bytes) -> tuple[int, int]:
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise AssetRejected("a malformed JPEG")
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        if length < 2:
            raise AssetRejected("a malformed JPEG")
        if marker in _SOF:
            if pos + 9 > len(data):
                break
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            return width, height
        pos += 2 + length
    raise AssetRejected("a JPEG without a frame header")


def _webp_size(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    body = data[20:]
    if chunk == b"VP8 " and len(body) >= 10 and body[3:6] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", body[6:10])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(body) >= 5 and body[0] == 0x2F:
        bits = int.from_bytes(body[1:5], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(body) >= 10:
        width = int.from_bytes(body[4:7], "little") + 1
        height = int.from_bytes(body[7:10], "little") + 1
        return width, height
    raise AssetRejected("a WebP without a readable frame header")
