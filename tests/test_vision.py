"""Tests for multimodal (vision) Message content."""

from __future__ import annotations

from pathlib import Path

from chimera.providers.gateway import Message


def test_text_only_message_is_unchanged() -> None:
    assert Message(role="user", content="hi").as_dict() == {"role": "user", "content": "hi"}


def test_url_image_passes_through() -> None:
    parts = Message(role="user", content="what is this?", images=["https://x.test/a.png"]).as_dict()[
        "content"
    ]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1] == {"type": "image_url", "image_url": {"url": "https://x.test/a.png"}}


def test_local_image_becomes_a_data_url(tmp_path: Path) -> None:
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")
    parts = Message(role="user", content="", images=[str(img)]).as_dict()["content"]
    # no text part when content is empty
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
