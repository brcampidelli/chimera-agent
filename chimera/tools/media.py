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
from chimera.governance.sanitize import fence, sanitize_untrusted
from chimera.providers.listing import openrouter_output_modalities, openrouter_supported_parameters
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
_OPENROUTER_CHAT_IMAGES = "https://openrouter.ai/api/v1/chat/completions"
#: The images endpoint: the only one image-only models answer (chat/completions returns 404 for them).
_OPENROUTER_IMAGE_GENERATIONS = "https://openrouter.ai/api/v1/images"
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


#: How much of a provider's own text an error may carry. Enough for "credits exhausted" or "model
#: does not support image output"; not enough to smuggle a page into the transcript.
_PROVIDER_TEXT_CAP = 300


def _provider_text(text: object) -> str:
    """Provider text, truncated, defanged and fenced: remote data the owner reads, never instructions."""
    return fence(sanitize_untrusted(str(text)[:_PROVIDER_TEXT_CAP]))


def _openrouter_failure(model: str, reason: str, status: int | None = None) -> str:
    """The one shape every OpenRouter image failure takes: what failed, for which model, and why."""
    where = f"HTTP {status}, model {model}" if status is not None else f"model {model}"
    return f"error: OpenRouter image generation failed ({where}): {reason}"


def _provider_error_detail(error: object) -> str:
    """OpenRouter's ``error`` object as a reason: its code, and its message fenced as data."""
    if isinstance(error, str):
        return _provider_text(error) if error.strip() else "OpenRouter error"
    if not isinstance(error, dict):
        return "OpenRouter error with no detail"
    code = error.get("code")
    label = f"OpenRouter error code {str(code)[:40]}" if code is not None else "OpenRouter error"
    message = error.get("message")
    if message is None or not str(message).strip():
        return label
    return f"{label}: {_provider_text(message)}"


def _http_failure(response: Any, model: str) -> str:
    """An HTTP error answer, with the status and the provider's own explanation when it sent one."""
    detail = "OpenRouter sent no error body"
    # The body is read only within the cap: an error page is not exempt from the size check.
    if len(getattr(response, "content", b"")) <= MAX_RESPONSE_BYTES:
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("error") is not None:
            detail = _provider_error_detail(body["error"])
    return _openrouter_failure(model, detail, status=int(response.status_code))


def _image_only(model: str, deadline: float) -> bool:
    """True when OpenRouter's index says ``model`` outputs images and no text.

    Those models are served by the images endpoint. Unknown (unreadable index, or a model the index
    does not list) is not image-only: the chat request stays, and any refusal names itself.
    """
    try:
        timeout = time_left(deadline, 10.0)
    except ValueError:
        return False
    declared = openrouter_output_modalities(model, timeout_s=timeout)
    return declared is not None and "image" in declared and "text" not in declared


def _generated_images(reply: Any, model: str) -> list[Any] | str:
    """Images from an images-endpoint answer (``{"data": [{"b64_json": ...}]}``), or the failure."""
    data = reply.get("data") if isinstance(reply, dict) else None
    if not isinstance(data, list):
        return _openrouter_failure(model, "the images response had no data list")
    images: list[Any] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        encoded = item.get("b64_json")
        media = item.get("media_type")
        if isinstance(encoded, str) and encoded:
            kind = media if isinstance(media, str) and media.startswith("image/") else "image/png"
            images.append({"image_url": {"url": f"data:{kind};base64,{encoded}"}})
        elif isinstance(item.get("url"), str):
            images.append({"image_url": {"url": item["url"]}})
    if not images:
        return _openrouter_failure(model, "the images response held no image")
    return images


def _chat_images(reply: Any, model: str) -> list[Any] | str:
    """Images from a chat answer (``message.images``, or a data URL in its reasoning), or the failure."""
    choice, message = _first_choice(reply)
    if message is None:
        return _openrouter_failure(model, "the response had no completion message")
    raw_images = message.get("images")
    images: list[Any] = list(raw_images) if isinstance(raw_images, list) else []
    reasoning = str(message.get("reasoning") or message.get("reasoning_content") or "")
    for data_url in re.findall(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+", reasoning):
        images.append({"image_url": {"url": data_url}})
    if not images:
        return _no_image_reason(model, choice, message)
    return images


def _image_request(model: str, prompt: str, size: str, deadline: float) -> dict[str, Any]:
    """The images-endpoint body for ``model``, naming only the size parameters the model declares.

    FLUX takes a resolution tier and an aspect ratio and refuses a pixel size (400, "Received size
    1024x1024"); other image models take ``size``. The index says which one a model takes, so the
    request sends that one and nothing the model would refuse.
    """
    body: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1}
    try:
        timeout = time_left(deadline, 10.0)
    except ValueError:
        return body
    declared = openrouter_supported_parameters(model, timeout_s=timeout) or frozenset()
    if "resolution" in declared:
        body["resolution"] = "1K"
    if "aspect_ratio" in declared:
        body["aspect_ratio"] = "1:1"
    if "size" in declared:
        body["size"] = size
    return body


def _first_choice(reply: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The first choice and its message, or ``None`` for the message when the shape is not there."""
    choices = reply.get("choices") if isinstance(reply, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message")
    return choice, (message if isinstance(message, dict) else None)


def _message_text(content: object) -> str:
    """Text out of a chat message's ``content``, whether it is a plain string or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    return ""


def _no_image_reason(model: str, choice: dict[str, Any], message: dict[str, Any]) -> str:
    """A 200 with no image: say so, give the finish reason, and show what the model said instead."""
    finish = choice.get("finish_reason") or choice.get("native_finish_reason")
    head = f"error: OpenRouter returned no image from model {model}"
    if finish:
        head += f" (finish_reason {str(finish)[:40]})"
    text = _message_text(message.get("content"))
    if text.strip():
        return f"{head}; the model replied with text instead: {_provider_text(text)}"
    return f"{head}; the response held neither an image nor text"


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
            return self._generate_openrouter(httpx, router_keys[0], prompt, out, model, size)

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

    def _generate_openrouter(self, httpx: Any, key: str, prompt: str, out: Path, model: str, size: str) -> str:
        from chimera.providers.privacy import openrouter_privacy

        deadline = time.monotonic() + _IMAGE_DEADLINE_SECONDS
        # An image-only model is not a chat model: OpenRouter answers 404 to chat/completions for it
        # and asks for the images endpoint. Models that also output text keep the chat request.
        image_only = _image_only(model, deadline)
        if image_only:
            url = _OPENROUTER_IMAGE_GENERATIONS
            body = _image_request(model, prompt, size, deadline)
        else:
            url = _OPENROUTER_CHAT_IMAGES
            body = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "modalities": ["image", "text"],
            }
        # The owner's data-collection / ZDR preference travels with every OpenRouter call, as it
        # does for chat (`gateway._call_kwargs`); the privacy card lists this route as scoped.
        if privacy := openrouter_privacy(get_settings()):
            body["provider"] = privacy
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=time_left(deadline, 180.0),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return _http_failure(exc.response, model)
        except httpx.TimeoutException:
            return _openrouter_failure(model, "the request timed out before OpenRouter answered")
        except httpx.HTTPError as exc:
            return _openrouter_failure(model, f"no response from OpenRouter ({type(exc).__name__})")
        except ValueError as exc:  # time_left: the budget was spent before any request was sent
            return _openrouter_failure(model, f"not sent: the local time budget was spent ({exc})")
        try:
            reply = _capped_json(response)
        except ValueError:
            return _openrouter_failure(model, "the response was not readable JSON or exceeded the size cap")
        if isinstance(reply, dict) and reply.get("error") is not None:
            return _openrouter_failure(model, _provider_error_detail(reply["error"]))

        found = _generated_images(reply, model) if image_only else _chat_images(reply, model)
        if isinstance(found, str):
            return found
        images = found

        payloads: list[bytes] = []
        try:
            for image in images:
                url_value = ""
                if isinstance(image, dict) and isinstance(image.get("image_url"), dict):
                    url_value = str(image["image_url"].get("url", ""))
                if url_value.startswith("data:image/") and ";base64," in url_value:
                    payloads.append(decode_data_url(url_value))
                elif url_value.startswith("https://"):
                    payloads.append(download_image(url_value, deadline=deadline))
                else:
                    return f"error: OpenRouter returned an invalid image URL (model {model})"
        except httpx.HTTPError as exc:
            return f"error: could not download an image OpenRouter returned ({type(exc).__name__}, model {model})"
        except ValueError as exc:  # our own refusals: size, type, scheme, deadline. Never a URL or header.
            return f"error: OpenRouter returned an image that was refused: {exc} (model {model})"

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
