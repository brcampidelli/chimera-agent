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
from chimera.tools.workspace import resolve_in_workspace

_OPENAI_IMAGES = "https://api.openai.com/v1/images/generations"
_ELEVEN_TTS = "https://api.elevenlabs.io/v1/text-to-speech"
_OPENAI_TRANSCRIBE = "https://api.openai.com/v1/audio/transcriptions"


def _generate_local(prompt: str, out: Path, model: str, size: str) -> None:
    """Generate an image locally with diffusers (the `imagegen-local` extra). Heavy: GPU + weights.

    Uses the maintained `diffusers` library — not the model repos directly — with FLUX.1-schnell
    (Apache-2.0) by default. Raises ImportError if the extra isn't installed (caller degrades).
    Chimera *runs* a diffusion model here; it does not train one.
    """
    import torch
    from diffusers import DiffusionPipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    pipe = DiffusionPipeline.from_pretrained(model, torch_dtype=dtype).to(device)
    try:
        width, height = (int(part) for part in size.lower().split("x"))
    except ValueError:
        width = height = 1024
    image = pipe(prompt, width=width, height=height).images[0]
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)


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

        settings = get_settings()
        prompt = str(kwargs["prompt"])
        out = Path(str(kwargs.get("out") or "generated_image.png"))
        size = str(kwargs.get("size") or "1024x1024")
        keys = settings.key_pool("openai")

        # Backend: 'local' forces diffusers; 'auto' uses local only when there's no hosted key.
        if settings.image_backend == "local" or (settings.image_backend == "auto" and not keys):
            try:
                _generate_local(prompt, out, settings.image_model_local, size)
            except ImportError:
                return (
                    "error: local image generation needs the 'imagegen-local' extra "
                    "(pip install 'chimera-agent[imagegen-local]') — a large GPU download"
                )
            except Exception as exc:  # noqa: BLE001 — a model/GPU failure is a tool error
                return f"error: local image generation failed: {exc}"
            return f"saved image to {out} (local: {settings.image_model_local})"

        if not keys:
            return "error: generate_image needs OPENAI_API_KEY (or set CHIMERA_IMAGE_BACKEND=local)."
        key = keys[0]
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


def _transcribe_faster_whisper(path: str, language: str | None) -> str | None:
    """Local speech-to-text via faster-whisper (the `stt` extra). None if it isn't installed."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    import os

    model = WhisperModel(os.environ.get("CHIMERA_WHISPER_MODEL", "base"))
    segments, _info = model.transcribe(path, language=language)
    return " ".join(seg.text.strip() for seg in segments).strip()


def _transcribe_openai(path: str, key: str, language: str | None) -> str:
    """Hosted speech-to-text via the OpenAI Whisper API (leanest path — no GPU, no weights)."""
    import httpx

    with open(path, "rb") as fh:
        data: dict[str, str] = {"model": "whisper-1"}
        if language:
            data["language"] = language
        resp = httpx.post(
            _OPENAI_TRANSCRIBE,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (Path(path).name, fh, "application/octet-stream")},
            data=data,
            timeout=300.0,
        )
    resp.raise_for_status()
    return str(resp.json().get("text", ""))


class TranscribeAudioTool(Tool):
    """Speech-to-text — the symmetric partner to image-gen + TTS. Chimera *orchestrates* a Whisper
    model (it doesn't train one): local faster-whisper if the `stt` extra is installed, else the
    hosted OpenAI Whisper API. Wave/mp3/m4a etc."""

    name = "transcribe_audio"
    # An audio file is external content just like a document or a web page — a transcript can carry a
    # "ignore your instructions" payload spoken aloud. Mark its output untrusted so it taints the run
    # and fences, exactly like read_document (ledger_tool.py honours `untrusted_output`). Without this
    # a poisoned recording transcribes in "clean" and the taint gate never arms.
    untrusted_output = True
    description = (
        "Transcribe an audio file to text (speech-to-text). Args: path (audio file: wav/mp3/m4a/…); "
        "optional language (e.g. 'en', 'pt'). Uses local faster-whisper if installed, else the OpenAI "
        "Whisper API."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Audio file path (relative to the workspace)."},
            "language": {"type": "string", "description": "Optional language hint, e.g. 'en', 'pt'."},
        },
        "required": ["path"],
    }

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()

    def run(self, **kwargs: Any) -> str:
        rel = str(kwargs.get("path", "")).strip()
        if not rel:
            return "error: transcribe_audio needs a 'path'"
        path = resolve_in_workspace(self.workspace, rel)
        if not path.is_file():
            return f"error: audio file not found: {rel}"
        language = str(kwargs.get("language", "")).strip() or None
        try:
            text = _transcribe_faster_whisper(str(path), language)  # local first (offline/private)
            if text is None:  # no stt extra -> hosted Whisper
                keys = get_settings().key_pool("openai")
                if not keys:
                    return (
                        "error: transcription needs the 'stt' extra (pip install 'chimera-agent[stt]') "
                        "or an OpenAI key (CHIMERA_OPENAI_KEYS)"
                    )
                text = _transcribe_openai(str(path), keys[0], language)
        except Exception as exc:  # noqa: BLE001 — a bad file / API error is a tool error, not a crash
            return f"error: transcription failed: {exc}"
        return text or "(no speech detected)"
