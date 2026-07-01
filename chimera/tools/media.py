"""Reference media tools: image generation (OpenAI) and text-to-speech (ElevenLabs).

Key-gated like :mod:`chimera.tools.web`: :func:`~chimera.tools.builtin.default_registry`
registers each only when its credential is present, so the agent sees it the moment you
add the key. Both save their output to a file and return the path; network I/O is lazy.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from chimera.config import get_settings
from chimera.tools.base import Tool

_OPENAI_IMAGES = "https://api.openai.com/v1/images/generations"
_ELEVEN_TTS = "https://api.elevenlabs.io/v1/text-to-speech"


class ImageGenTool(Tool):
    name = "generate_image"
    description = "Generate an image from a text prompt (OpenAI) and save it to a file; returns the path."
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What the image should depict."},
            "out": {"type": "string", "description": "Output file path (default generated_image.png)."},
            "size": {"type": "string", "description": "Image size, e.g. 1024x1024 (default)."},
        },
        "required": ["prompt"],
    }

    def __init__(self, *, model: str = "gpt-image-1") -> None:
        self.model = model

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy

        keys = get_settings().key_pool("openai")
        if not keys:
            return "error: generate_image needs OPENAI_API_KEY (set it in .env)."
        key = keys[0]
        prompt = str(kwargs["prompt"])
        out = Path(str(kwargs.get("out") or "generated_image.png"))
        size = str(kwargs.get("size") or "1024x1024")
        try:
            response = httpx.post(
                _OPENAI_IMAGES,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": self.model, "prompt": prompt, "size": size, "n": 1},
                timeout=120.0,
            )
            response.raise_for_status()
            item = (response.json().get("data") or [{}])[0]
        except httpx.HTTPError as exc:
            return f"error: image generation failed: {exc}"
        if item.get("b64_json"):
            data = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            try:
                data = httpx.get(str(item["url"]), timeout=120.0).content
            except httpx.HTTPError as exc:
                return f"error: image download failed: {exc}"
        else:
            return "error: image generation returned no image"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return f"saved image ({len(data)} bytes) to {out}"


class TextToSpeechTool(Tool):
    name = "text_to_speech"
    description = "Synthesize speech from text (ElevenLabs), save an mp3 file, and return the path."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to speak."},
            "out": {"type": "string", "description": "Output mp3 path (default speech.mp3)."},
            "voice_id": {"type": "string", "description": "ElevenLabs voice id (optional)."},
        },
        "required": ["text"],
    }

    def __init__(
        self, *, voice_id: str = "21m00Tcm4TlvDq8ikWAM", model_id: str = "eleven_multilingual_v2"
    ) -> None:
        self.voice_id = voice_id
        self.model_id = model_id

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy

        key = get_settings().elevenlabs_api_key
        if not key:
            return "error: text_to_speech needs ELEVENLABS_API_KEY (set it in .env)."
        text = str(kwargs["text"])
        out = Path(str(kwargs.get("out") or "speech.mp3"))
        voice = str(kwargs.get("voice_id") or self.voice_id)
        try:
            response = httpx.post(
                f"{_ELEVEN_TTS}/{voice}",
                headers={"xi-api-key": key},
                json={"text": text, "model_id": self.model_id},
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.content
        except httpx.HTTPError as exc:
            return f"error: text_to_speech failed: {exc}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return f"saved audio ({len(data)} bytes) to {out}"
