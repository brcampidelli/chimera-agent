"""Reference media tools: image generation (OpenAI/OpenRouter) and text-to-speech (ElevenLabs).

Key-gated like :mod:`chimera.tools.web`: :func:`~chimera.tools.builtin.default_registry`
registers each only when its credential is present, so the agent sees it the moment you
add the key. Both save their output to a file and return the path; network I/O is lazy.
"""

from __future__ import annotations

import base64
import functools
import io
import re
import time
import wave
from collections.abc import Callable
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
    image_file,
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
_OPENROUTER_SPEECH = "https://openrouter.ai/api/v1/audio/speech"
_OPENROUTER_VIDEOS = "https://openrouter.ai/api/v1/videos"
#: One overall budget per image request, across its POST, redirects and downloads.
_IMAGE_DEADLINE_SECONDS = 240.0
_MEDIA_RESPONSE_LIMIT = 100 * 1024 * 1024


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


def _openrouter_failure(
    model: str, reason: str, status: int | None = None, *, action: str = "image generation",
) -> str:
    """The shared shape for OpenRouter media failures: what failed, for which model, and why.

    The default wording is the one image callers and their tests have always matched on; the other
    media tools name their own action so an audio or video failure does not read as an image one.
    """
    where = f"HTTP {status}, model {model}" if status is not None else f"model {model}"
    return f"error: OpenRouter {action} failed ({where}): {reason}"


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


#: Image ids OpenRouter still lists whose OpenAI route no longer serves them. Measured 2026-10-27:
#: ``openai/gpt-image-1`` answers 400 "does not exist" on the images endpoint, and ``openai/gpt-5-image``
#: fails upstream on the same id ("has been deprecated"). The replacements answered 200 with an image.
#: The failure names the replacement; the request is never silently swapped to another model.
_RETIRED_IMAGE_MODELS = {
    "openai/gpt-image-1": "openai/gpt-image-2",
    "openai/gpt-5-image": "openai/gpt-5.4-image-2",
}


def _retirement_hint(model: str, detail: str) -> str:
    """A pointer to the replacement, only when the provider's own words say the id is gone."""
    replacement = _RETIRED_IMAGE_MODELS.get(model)
    if replacement is None or not re.search(r"does not exist|deprecated", detail, re.IGNORECASE):
        return ""
    return f"; OpenRouter lists {model}, but its upstream no longer serves it. Use {replacement}"


def _provider_failure(model: str, error: object, *, action: str = "image generation") -> str:
    """An OpenRouter ``error`` object as the failure line, with the retirement pointer when it applies."""
    detail = _provider_error_detail(error)
    return _openrouter_failure(model, detail + _retirement_hint(model, detail), action=action)


def _http_failure(response: Any, model: str, *, action: str = "image generation") -> str:
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
            detail += _retirement_hint(model, detail)
    return _openrouter_failure(model, detail, status=int(response.status_code), action=action)


def _speech_file(data: bytes, content_type: str) -> tuple[bytes, str]:
    """The bytes to store and their extension: raw PCM is wrapped as WAV, anything else is kept as sent.

    The provider names the PCM layout in its Content-Type (``audio/pcm;rate=24000;channels=1``); a
    missing rate falls back to the 24 kHz that the measured model answers with.
    """
    kind = content_type.lower()
    if not (kind.startswith("audio/pcm") or kind.startswith("audio/l16")):
        return data, ""
    samples = data
    if kind.startswith("audio/l16"):
        # RFC 3551 defines L16 as big-endian; WAV stores little-endian, so each 16-bit sample is swapped.
        # Not seen on the live endpoint (only audio/pcm was measured): this follows the RFC.
        even = bytearray(data[: len(data) - (len(data) % 2)])
        even[0::2], even[1::2] = even[1::2], even[0::2]
        samples = bytes(even)
    rate = re.search(r"rate=(\d+)", kind)
    channels = 2 if re.search(r"channels=2\b", kind) else 1
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)  # 16-bit samples: the only width the endpoint describes
        wav.setframerate(int(rate.group(1)) if rate else 24000)
        wav.writeframes(samples)
    return buffer.getvalue(), ".wav"


def _cost_note(reply: Any) -> str:
    """The provider's own cost for one answer as a suffix; empty when the answer names none."""
    usage = reply.get("usage") if isinstance(reply, dict) else None
    cost = usage.get("cost") if isinstance(usage, dict) else None
    return f", cost ${cost}" if cost is not None else ""


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
    # Measured 2026-10-27: an upstream failure arrives as HTTP 200 with ``choices[0].error`` (code 502,
    # finish_reason "error") and null content. Without this check it reads as "returned no image".
    if choice.get("error") is not None:
        return _provider_failure(model, choice["error"])
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
            data, extension = image_file(data)
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
            # Measured 2026-10-27 on google/gemini-3.1-flash-image: a wide prompt with no image_config came
            # back 1584x672; the same prompt with image_config aspect_ratio 1:1 came back 1024x1024. Only
            # that vendor is measured, so only it is asked; other chat image models keep their own ratio.
            if model.startswith("google/"):
                body["image_config"] = {"aspect_ratio": "1:1"}
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
            return _provider_failure(model, reply["error"])

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
        for index, payload in enumerate(payloads):
            try:
                # A raster is stored as it arrived; an SVG is stored only as the validated rebuild.
                data, extension = image_file(payload)
            except ValueError as exc:
                return (
                    f"error: OpenRouter returned a file that is not a PNG, JPEG, GIF or WebP image "
                    f"(and not a valid SVG; model {model}): {exc}"
                )
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
        cost = _cost_note(reply)
        return f"saved images using {model}{cost}; repeat these Markdown image lines in the final answer:\n{links}"


#: Statuses a video job passes through on its way to `completed`. Any other status that is not
#: `completed` ends the wait at once: `failed`, `cancelled`, `expired`, or a word this code never heard of.
_VIDEO_PENDING = frozenset({"pending", "queued", "in_progress", "processing"})
#: Status checks that may fail in a row (a 5xx answer or a timeout) before the job is given up on.
_VIDEO_POLL_RETRIES = 2
#: A timed-out submission may still have been accepted, and charged. The owner must hear that.
_VIDEO_SUBMIT_TIMEOUT = (
    "the submission timed out; OpenRouter may have accepted and charged the job, so check the account before retrying"
)


class TextToSpeechTool(Tool):
    name = "text_to_speech"
    description = "Synthesize speech from text (ElevenLabs or OpenRouter), save the audio file, and return the path."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to speak."},
            "out": {"type": "string", "description": "Output path (default speech.mp3; OpenRouter speech is saved as .wav)."},
            "voice_id": {"type": "string", "description": "ElevenLabs voice id (optional)."},
            "voice": {"type": "string", "description": "OpenRouter voice (optional)."},
            "instructions": {"type": "string", "description": "Delivery tone or accent instructions."},
            "model": {"type": "string", "description": "OpenRouter TTS model override."},
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
        settings = get_settings()
        text = str(kwargs["text"])
        if not settings.elevenlabs_api_key:
            return self._openrouter(httpx, settings, out, text, kwargs)
        voice = str(kwargs.get("voice_id") or self.voice_id)
        try:
            response = httpx.post(
                f"{_ELEVEN_TTS}/{voice}",
                headers={"xi-api-key": settings.elevenlabs_api_key},
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

    def _openrouter(self, httpx: Any, settings: Any, out: Path, text: str, kwargs: dict[str, Any]) -> str:
        router_keys = settings.key_pool("openrouter")
        if not router_keys:
            return "error: text_to_speech needs ELEVENLABS_API_KEY or OPENROUTER_API_KEY."
        # A PCM answer is stored as `.wav`, so that name is checked against the write region BEFORE the
        # paid request too: a path the owner may not write must not cost a synthesis.
        wav = out.with_suffix(".wav")
        if err := refuse_write(self.workspace, wav, self.write_region):
            return err
        model = str(kwargs.get("model") or settings.tts_model)
        voice = str(kwargs.get("voice") or settings.tts_voice or "Kore")
        # Measured 2026-10-27 on google/gemini-3.8-flash-tts: the endpoint answers 400 to "mp3" and
        # takes only "pcm", which comes back as 24 kHz mono 16-bit samples. The file is wrapped as WAV
        # below, so its extension tells the truth; an mp3 would need an encoder this tool does not ship.
        body: dict[str, Any] = {"model": model, "input": text, "voice": voice, "response_format": "pcm"}
        if kwargs.get("instructions"):
            body["instructions"] = str(kwargs["instructions"])
        try:
            response = httpx.post(
                _OPENROUTER_SPEECH,
                headers={"Authorization": f"Bearer {router_keys[0]}", "Content-Type": "application/json"},
                json=body, timeout=180.0,
            )
            response.raise_for_status()
            raw = response.content
            content_type = str(response.headers.get("content-type", ""))
            if not raw or len(raw) > _MEDIA_RESPONSE_LIMIT:
                return _openrouter_failure(model, "the audio response was empty or exceeded its size cap", action="speech generation")
        except httpx.HTTPStatusError as exc:
            return _http_failure(exc.response, model, action="speech generation")
        except httpx.TimeoutException:
            return _openrouter_failure(model, "the request timed out before OpenRouter answered", action="speech generation")
        except httpx.HTTPError as exc:
            return _openrouter_failure(model, f"no response from OpenRouter ({type(exc).__name__})", action="speech generation")
        data, extension = _speech_file(raw, content_type)
        target = out.with_suffix(extension) if extension else out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"saved audio ({len(data)} bytes) to {target} (OpenRouter: {model})"


class GenerateAudioTool(Tool):
    name = "generate_audio"
    description = (
        "Generate a short music or sound-effect clip with OpenRouter and save it as an mp3 file. "
        "The model decides the duration: the default Lyria model returns about 30 seconds, whatever the prompt asks for."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Describe the music or sound effect."},
            "out": {"type": "string", "description": "Output audio path (default generated_audio.mp3)."},
            "model": {"type": "string", "description": "Optional OpenRouter audio model override."},
        },
        "required": ["prompt"],
    }

    def __init__(self, workspace: Path | None = None, *, write_region: WriteRegion | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.write_region = write_region

    def run(self, **kwargs: Any) -> str:
        import json

        import httpx

        settings = get_settings()
        keys = settings.key_pool("openrouter")
        model = str(kwargs.get("model") or settings.audio_model)
        out = resolve_for(self, str(kwargs.get("out") or "generated_audio.mp3"), verb="write")
        if err := refuse_write(self.workspace, out, self.write_region):
            return err
        if not keys:
            return "error: generate_audio needs OPENROUTER_API_KEY."
        # Only the default model (google/lyria-3-clip-preview) has been checked against the live API:
        # on 2026-10-27 it returned an mp3 of about 30 s for USD 0.04. Other audio models take the same
        # request, but their answers have not been measured here.
        chunks: list[str] = []
        cost: object = None
        try:
            with httpx.stream(
                "POST", _OPENROUTER_CHAT_IMAGES,
                headers={"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": str(kwargs["prompt"])}],
                      "modalities": ["text", "audio"], "audio": {"voice": "alloy", "format": "mp3"},
                      "stream": True},
                timeout=300.0,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        continue
                    event = json.loads(payload)
                    if not isinstance(event, dict):
                        return _openrouter_failure(model, "the stream sent an event that is not a JSON object", action="audio generation")
                    # A failure can arrive at the top of the stream, outside any choice.
                    if event.get("error") is not None:
                        return _provider_failure(model, event["error"], action="audio generation")
                    usage = event.get("usage")
                    if isinstance(usage, dict) and usage.get("cost") is not None:
                        cost = usage["cost"]
                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        continue
                    choice = choices[0]
                    if choice.get("error") is not None:
                        return _provider_failure(model, choice["error"], action="audio generation")
                    delta = choice.get("delta")
                    segment = delta.get("audio") if isinstance(delta, dict) else None
                    encoded = segment.get("data") if isinstance(segment, dict) else None
                    if isinstance(encoded, str) and encoded:
                        chunks.append(encoded)
        except httpx.HTTPStatusError as exc:
            return _http_failure(exc.response, model, action="audio generation")
        except httpx.TimeoutException:
            return _openrouter_failure(model, "the request timed out before OpenRouter answered", action="audio generation")
        except (httpx.HTTPError, ValueError) as exc:
            return _openrouter_failure(model, f"invalid audio response ({type(exc).__name__})", action="audio generation")
        if not chunks:
            return _openrouter_failure(model, "the response contained no audio data", action="audio generation")
        # Chunks are joined before decoding: a base64 chunk need not end on a multiple of four characters.
        try:
            audio = base64.b64decode("".join(chunks), validate=True)
        except ValueError:
            return _openrouter_failure(model, "the audio data was not valid base64", action="audio generation")
        if len(audio) > _MEDIA_RESPONSE_LIMIT:
            return _openrouter_failure(model, "the audio response exceeded its size cap", action="audio generation")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        cost_note = _cost_note({"usage": {"cost": cost}}) if cost is not None else ""
        return f"saved audio ({len(audio)} bytes) to {out} (OpenRouter: {model}{cost_note})"


class GenerateVideoTool(Tool):
    name = "generate_video"
    description = "Generate a short video with OpenRouter, wait for completion, and save the mp4 in the workspace."
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Describe the video to generate."},
            "out": {"type": "string", "description": "Output path (default generated_video.mp4)."},
            "model": {"type": "string", "description": "Optional OpenRouter video model override."},
            "duration": {"type": "integer", "description": "Clip duration in seconds (default 5)."},
            "aspect_ratio": {"type": "string", "description": "Aspect ratio (default 16:9)."},
            "resolution": {"type": "string", "description": "Resolution tier (default 480p)."},
        },
        "required": ["prompt"],
    }

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        write_region: WriteRegion | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.write_region = write_region
        # Injected so the deadline and the poll pauses can be driven by a test clock, not by a
        # hand-written sequence of readings of the real one.
        self._clock = clock
        self._sleep = sleep

    def run(self, **kwargs: Any) -> str:
        import re

        import httpx

        settings = get_settings()
        keys = settings.key_pool("openrouter")
        model = str(kwargs.get("model") or settings.video_model)
        out = resolve_for(self, str(kwargs.get("out") or "generated_video.mp4"), verb="write")
        if err := refuse_write(self.workspace, out, self.write_region):
            return err
        if not keys:
            return "error: generate_video needs OPENROUTER_API_KEY."
        try:
            duration = max(1, min(int(kwargs.get("duration") or 5), 30))
        except (TypeError, ValueError):
            return _openrouter_failure(model, "duration must be a whole number of seconds", action="video generation")
        timeout = max(1, min(int(settings.video_timeout_seconds), 3600))
        deadline = self._clock() + timeout
        headers = {"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json"}
        body = {
            "model": model, "prompt": str(kwargs["prompt"]), "duration": duration,
            "aspect_ratio": str(kwargs.get("aspect_ratio") or "16:9"),
            "resolution": str(kwargs.get("resolution") or "480p"),
        }
        try:
            response = httpx.post(_OPENROUTER_VIDEOS, headers=headers, json=body, timeout=30.0)
            response.raise_for_status()
            job = _capped_json(response)
        except httpx.HTTPStatusError as exc:
            return _http_failure(exc.response, model, action="video generation")
        except httpx.TimeoutException:
            return _openrouter_failure(model, _VIDEO_SUBMIT_TIMEOUT, action="video generation")
        except (httpx.HTTPError, ValueError) as exc:
            return _openrouter_failure(model, f"invalid video submission response ({type(exc).__name__})", action="video generation")
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            return _openrouter_failure(model, "video submission returned no job id", action="video generation")
        job_id = job["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", job_id):
            return _openrouter_failure(model, "video submission returned an invalid job id", action="video generation")
        # Every failure from here on names the job, so the owner can look it up on OpenRouter.
        status = self._await_completion(httpx, model, job_id, headers, deadline)
        if isinstance(status, str):
            return f"{status} (job {job_id})"
        remaining = deadline - self._clock()
        if remaining <= 0:
            return _openrouter_failure(model, "video generation exceeded its configured deadline", action="video generation") + f" (job {job_id})"
        try:
            content = httpx.get(
                f"{_OPENROUTER_VIDEOS}/{job_id}/content?index=0", headers=headers,
                timeout=min(120.0, remaining),
            )
            content.raise_for_status()
            data = content.content
        except httpx.HTTPStatusError as exc:
            return _http_failure(exc.response, model, action="video generation") + f" (job {job_id})"
        except httpx.TimeoutException:
            return _openrouter_failure(model, "the video download timed out", action="video generation") + f" (job {job_id})"
        except httpx.HTTPError as exc:
            return _openrouter_failure(model, f"video download failed ({type(exc).__name__})", action="video generation") + f" (job {job_id})"
        if not data or len(data) > _MEDIA_RESPONSE_LIMIT:
            return _openrouter_failure(model, "video content was empty or exceeded its size cap", action="video generation") + f" (job {job_id})"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return f"saved video ({len(data)} bytes) to {out} (OpenRouter: {model}{_cost_note(status)}; job {job_id})"

    def _await_completion(
        self, httpx: Any, model: str, job_id: str, headers: dict[str, str], deadline: float,
    ) -> dict[str, Any] | str:
        """The completed job's status, or the failure line that ends the wait (without its job id)."""
        failures = 0
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return _openrouter_failure(model, "video generation exceeded its configured deadline", action="video generation")
            try:
                response = httpx.get(f"{_OPENROUTER_VIDEOS}/{job_id}", headers=headers, timeout=min(30.0, remaining))
                response.raise_for_status()
                status = _capped_json(response)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    return _http_failure(exc.response, model, action="video generation")
                failures += 1  # a 5xx is the provider having a bad moment, not the job failing
            except httpx.TimeoutException:
                failures += 1
            except (httpx.HTTPError, ValueError) as exc:
                return _openrouter_failure(model, f"video status check failed ({type(exc).__name__})", action="video generation")
            else:
                if not isinstance(status, dict):
                    return _openrouter_failure(model, "video status response was invalid", action="video generation")
                failures = 0
                state = status.get("status")
                if state == "completed":
                    return status
                if state in _VIDEO_PENDING:
                    self._sleep(min(10.0, max(0.0, deadline - self._clock())))
                    continue
                reason = f"the job ended with status {_provider_text(state)}"
                if status.get("error") is not None:
                    reason += f": {_provider_error_detail(status['error'])}"
                return _openrouter_failure(model, reason, action="video generation")
            if failures > _VIDEO_POLL_RETRIES:
                return _openrouter_failure(
                    model, f"video status checks failed {failures} times in a row", action="video generation",
                )
            self._sleep(min(10.0, max(0.0, deadline - self._clock())))


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
