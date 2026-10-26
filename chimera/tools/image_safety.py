"""Shared validation and bounded fetching for provider-returned images."""
from __future__ import annotations

import base64
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

MAX_IMAGE_BYTES = 20 * 1024 * 1024
#: A JSON body that carries one base64 image is about 4/3 of it, plus the envelope around it.
MAX_RESPONSE_BYTES = 40 * 1024 * 1024
#: The longest `data:` URL worth decoding: base64 of MAX_IMAGE_BYTES, with a little for the header.
MAX_DATA_URL_CHARS = (MAX_IMAGE_BYTES * 4) // 3 + 1024
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"RIFF", ".webp"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


def image_extension(data: bytes) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds 20 MB")
    for magic, extension in _MAGIC:
        if data.startswith(magic) and (extension != ".webp" or data[8:12] == b"WEBP"):
            return extension
    raise ValueError("unsupported or invalid image type")


def decode_data_url(url: str) -> bytes:
    """Decode a base64 image `data:` URL, refusing an oversized one before any decoding happens."""
    if len(url) > MAX_DATA_URL_CHARS:
        raise ValueError("image exceeds 20 MB")
    if not url.startswith("data:image/") or ";base64," not in url:
        raise ValueError("not a base64 image data URL")
    try:
        payload = base64.b64decode(url.split(";base64,", 1)[1], validate=True)
    except ValueError as exc:  # binascii.Error is a ValueError
        raise ValueError("invalid base64 image data") from exc
    if len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds 20 MB")
    return payload


def time_left(deadline: float | None, cap: float) -> float:
    """Seconds left for one request: the per-request cap, bounded by the operation's overall deadline."""
    if deadline is None:
        return cap
    left = deadline - time.monotonic()
    if left <= 0:
        raise ValueError("image fetch exceeded its deadline")
    return min(cap, left)


def download_image(url: str, *, deadline: float | None = None) -> bytes:
    """Fetch public HTTPS image content with every redirect checked and a streaming size cap.

    ``deadline`` is a ``time.monotonic()`` instant for the whole operation (every redirect and the
    body included), so a slow host cannot stretch one image into several per-request timeouts.

    Known gap, shared with the scrape guard in ``chimera/scrape/ssrf.py``: ``check_url`` resolves the
    host once, and httpx resolves it again to connect. A DNS rebinding answer between the two lookups
    can reach an internal address. Closing it needs the connection pinned to the checked IP, which
    neither path does yet; the redirect and literal-IP checks here are what is enforced today.
    """
    import httpx

    from chimera.scrape.ssrf import check_url

    current = url
    for _ in range(6):
        if urlparse(current).scheme != "https":
            raise ValueError("image URL must use https")
        check_url(current)
        timeout = time_left(deadline, 60.0)
        with httpx.Client(follow_redirects=False, timeout=timeout) as client, client.stream("GET", current) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirect has no location")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                raise ValueError("image exceeds 20 MB")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ValueError("image exceeds 20 MB")
                time_left(deadline, 60.0)
                chunks.append(chunk)
            return b"".join(chunks)
    raise ValueError("too many redirects")


def validate_base64_image(payload: bytes) -> tuple[bytes, str]:
    if len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds 20 MB")
    return payload, image_extension(payload)


def unique_path(path: Path, reserved: set[Path] | None = None) -> Path:
    """Return a path that is neither on disk nor already ``reserved`` by this batch.

    ``reserved`` is how two images from one response avoid both taking the same suffixed name: a
    name is only free if no file has it AND no earlier image in the batch was given it. The caller
    adds the returned path to ``reserved`` before it writes anything.
    """
    taken = reserved or set()
    candidate = path
    suffix = 2
    while candidate.exists() or candidate in taken:
        candidate = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        suffix += 1
    return candidate
