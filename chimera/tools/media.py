"""Reference media tools: image generation (OpenAI/OpenRouter) and text-to-speech (ElevenLabs).

Key-gated like :mod:`chimera.tools.web`: :func:`~chimera.tools.builtin.default_registry`
registers each only when its credential is present, so the agent sees it the moment you
add the key. Both save their output to a file and return the path; network I/O is lazy.
"""

from __future__ import annotations

import base64
import functools
import re
import time
from pathlib import Path
from typing import Any

from chimera.config import get_settings
from chimera.tools.base import Tool
from chimera.tools.image_safety import (
    MAX_DATA_URL_CHARS,
    MAX_RESPONSE_BYTES,
    decode_data_url,
    download_image,
    image_extension,
    time_left,
    unique_path,
)
from chimera.tools.image_url import markdown_image
from chimera.tools.workspace import resolve_for
from chimera.tools.write_region import WriteRegion, refuse_write

_OPENAI_IMAGES = "https://api.openai.com/v1/images/generations"
_ELEVEN_TTS = "https://api.elevenlabs.io/v1/text-to-speech"
_OPENAI_TRANSCRIBE = "https://api.openai.com/v1/audio/transcriptions"
_OPENROUTER_IMAGES = "https://openrouter.ai/api/v1/chat/completions"
#: One overall budget per image request, across its POST, redirects and downloads.
_IMAGE_DEADLINE_SECONDS = 240.0


def _capped_json(response: Any) -> Any:
    """The parsed body of a provider response, refused when it is too large to be a plausible answer."""
    # `getattr`: a response object that carries no body has nothing to cap, and parses as before.
    if len(getattr(response, "content", b"")) > MAX_RESPONSE_BYTES:
        raise ValueError("provider response exceeds its size cap")
    return response.json()


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
    description = "Generate image(s) from a text prompt (OpenRouter or OpenAI), save them to workspace files, and return Markdown image links. Repeat each image line verbatim in the final answer so the owner can see it."
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What the image should depict."},
            "out": {"type": "string", "description": "Output file path (default generated_image.png)."},
            "size": {"type": "string", "description": "Image size, e.g. 1024x1024 (default)."},
            "model": {"type": "string", "description": "Optional OpenRouter image model override."},
        },
        "required": ["prompt"],
    }

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        model: str = "gpt-image-1",
        write_region: WriteRegion | None = None,
    ) -> None:
        # This tool took no workspace at all, which is why its output path was used verbatim: there
        # was nothing to resolve against. An absolute `out` wrote wherever it pointed.
        self.workspace = (workspace or Path.cwd()).resolve()
        self.model = model
        self.write_region = write_region

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy

        settings = get_settings()
        prompt = str(kwargs["prompt"])
        out = resolve_for(self, str(kwargs.get("out") or "generated_image.png"), verb="write")
        if err := refuse_write(self.workspace, out, self.write_region):
            return err
        size = str(kwargs.get("size") or "1024x1024")
        router_keys = settings.key_pool("openrouter")
        model = str(kwargs.get("model") or getattr(settings, "image_model", self.model))

        # Prefer the router when configured: image models are called through its separate
        # image-generation endpoint, never as a tool call in the conversation request.
        if settings.image_backend == "auto" and router_keys:
            return self._generate_openrouter(httpx, router_keys[0], prompt, out, model)

        keys = settings.key_pool("openai")
        # Backend: 'hosted' explicitly selects OpenAI, even when an OpenRouter key is present.
        # 'local' forces diffusers; 'auto' prefers OpenRouter then OpenAI then local.
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
        deadline = time.monotonic() + _IMAGE_DEADLINE_SECONDS
        try:
            response = httpx.post(
                _OPENAI_IMAGES,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": self.model, "prompt": prompt, "size": size, "n": 1},
                timeout=time_left(deadline, 120.0),
            )
            response.raise_for_status()
            item = (_capped_json(response).get("data") or [{}])[0]
        except (httpx.HTTPError, ValueError):
            return "error: image generation failed"
        try:
            if item.get("b64_json"):
                if len(item["b64_json"]) > MAX_DATA_URL_CHARS:
                    return "error: OpenAI returned an invalid image"
                data = base64.b64decode(item["b64_json"], validate=True)
            elif item.get("url"):
                data = download_image(str(item["url"]), deadline=deadline)
            else:
                return "error: image generation returned no image"
        except (httpx.HTTPError, ValueError):
            return "error: image download failed"
        # Every byte that reaches disk is an image by its magic bytes, on this path as on OpenRouter's.
        try:
            extension = image_extension(data)
        except ValueError:
            return "error: OpenAI returned an invalid image"
        if out.suffix.lower() != extension:
            out = out.with_suffix(extension)
        out = unique_path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        relative = out.relative_to(self.workspace).as_posix()
        return f"saved image ({len(data)} bytes):\n{markdown_image(self.workspace, relative, 'Generated image')}"

    def _generate_openrouter(self, httpx: Any, key: str, prompt: str, out: Path, model: str) -> str:
        from chimera.providers.privacy import openrouter_privacy

        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        }
        # The owner's data-collection / ZDR preference travels with every OpenRouter call, as it
        # does for chat (`gateway._call_kwargs`); the privacy card lists this route as scoped.
        if privacy := openrouter_privacy(get_settings()):
            body["provider"] = privacy
        deadline = time.monotonic() + _IMAGE_DEADLINE_SECONDS
        try:
            response = httpx.post(
                _OPENROUTER_IMAGES,
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=time_left(deadline, 180.0),
            )
            response.raise_for_status()
            reply = _capped_json(response)
            message = reply["choices"][0]["message"]
            images = list(message.get("images") or [])
            reasoning = str(message.get("reasoning") or message.get("reasoning_content") or "")
            for data_url in re.findall(
                r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+", reasoning
            ):
                images.append({"image_url": {"url": data_url}})
            if not images:
                return "error: OpenRouter image generation returned no image"
            payloads: list[bytes] = []
            for image in images:
                url = image.get("image_url", {}).get("url", "") if isinstance(image, dict) else ""
                if url.startswith("data:image/") and ";base64," in url:
                    payloads.append(decode_data_url(url))
                elif url.startswith("https://"):
                    payloads.append(download_image(url, deadline=deadline))
                else:
                    return "error: OpenRouter returned an invalid image URL"
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
            # Provider exceptions may contain request headers; never echo them or the bearer key.
            return "error: OpenRouter image generation failed"

        # Names are reserved as they are chosen, so a second image in the batch cannot take the name
        # the first one was given just because nothing is on disk yet (`cats.png` existing made both
        # `cats_2.png`, and the first was overwritten).
        taken: set[Path] = set()
        planned: list[tuple[Path, bytes]] = []
        for index, data in enumerate(payloads):
            try:
                extension = image_extension(data)
            except ValueError:
                return "error: OpenRouter returned an invalid image"
            candidate = out.with_suffix(extension)
            if index:
                candidate = candidate.with_name(f"{candidate.stem}_{index + 1}{candidate.suffix}")
            candidate = unique_path(candidate, taken)
            if err := refuse_write(self.workspace, candidate, self.write_region):
                return err
            taken.add(candidate)
            planned.append((candidate, data))
        for path, data in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        links = "\n".join(
            markdown_image(self.workspace, path.relative_to(self.workspace).as_posix(), "Generated image")
            for path, _ in planned
        )
        return f"saved images using {model}; repeat these Markdown image lines in the final answer:\n{links}"

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
        self,
        workspace: Path | None = None,
        *,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        model_id: str = "eleven_multilingual_v2",
        write_region: WriteRegion | None = None,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.voice_id = voice_id
        self.model_id = model_id
        self.write_region = write_region

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy

        # Where the audio would land is settled BEFORE the key check and before the request: a
        # synthesis we are not allowed to save is an API call nobody wanted.
        out = resolve_for(self, str(kwargs.get("out") or "speech.mp3"), verb="write")
        if err := refuse_write(self.workspace, out, self.write_region):
            return err
        key = get_settings().elevenlabs_api_key
        if not key:
            return "error: text_to_speech needs ELEVENLABS_API_KEY (set it in .env)."
        text = str(kwargs["text"])
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


@functools.lru_cache(maxsize=2)
def _whisper_model(size: str) -> Any:
    """The local model, built once per process and kept.

    It was built on every call, and construction is most of a call: measured on 2026-09-17 (24-core
    laptop, `base`, CPU) at 0.4–0.85 s to construct against 0.18 s to transcribe a four-second
    clip — and the desktop's hands-free mode, which transcribes every sentence a person says,
    showed "heard in 8.1 s" on its first utterance, the model coming off a cold disk each time the
    page cache had moved on. The constructed model is ~150 MB of memory for `base`; keeping it is
    what a speech mode costs. CTranslate2 serves concurrent `transcribe` calls, so one instance is
    shared by dictation, the voice mode and the agent's own tool.
    """
    from faster_whisper import WhisperModel

    return WhisperModel(size)


def warm_transcriber() -> bool:
    """Build the local model now, so the first sentence spoken does not pay for it.

    The first transcription of a process imports the runtime and reads the weights off the disk —
    8.3 s measured cold on 2026-09-17, against 0.3 s for every one after. A person who switches
    the voice mode on is seconds away from saying something; those seconds are when this runs.
    False when there is no local model to warm (the hosted route has nothing to load).
    """
    import importlib.util
    import os

    if importlib.util.find_spec("faster_whisper") is None:
        return False
    _whisper_model(os.environ.get("CHIMERA_WHISPER_MODEL", "base"))
    return True


def _transcribe_faster_whisper(path: str, language: str | None) -> str | None:
    """Local speech-to-text via faster-whisper (the `stt` extra). None if it isn't installed."""
    import importlib.util
    import os

    if importlib.util.find_spec("faster_whisper") is None:
        return None
    model = _whisper_model(os.environ.get("CHIMERA_WHISPER_MODEL", "base"))
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
        path = resolve_for(self, rel, verb="read")
        if not path.is_file():
            return f"error: audio file not found: {rel}"
        # `str(None)` is `"None"` — a five-character string that is not empty, so `or None` never
        # fires and the literal word travels on as a language code. faster-whisper then refuses the
        # whole call with `'None' is not a valid language code`, and dictation in the desktop app
        # has never worked on the local model because of it: the API layer passes `language=None`
        # for "detect it", which is precisely the case this line converted into a wrong answer.
        raw = kwargs.get("language")
        language = (str(raw).strip() or None) if isinstance(raw, str) else None
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
