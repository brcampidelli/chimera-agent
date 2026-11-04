"""Named OpenAI-compatible endpoints, validated without ever exposing credentials."""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def validate_endpoints(value: object) -> list[dict[str, Any]]:
    """Parse CHIMERA_ENDPOINTS and return only its non-secret endpoint descriptors."""
    if isinstance(value, str):
        if not value.strip():
            return []  # an empty variable is "none configured", as for every other setting
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("CHIMERA_ENDPOINTS must be a JSON list") from exc
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("CHIMERA_ENDPOINTS must be a JSON list")
    if len(value) > 64:
        raise ValueError("CHIMERA_ENDPOINTS may contain at most 64 endpoints")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        required = {"name", "base_url", "key_env"}
        if not isinstance(item, dict) or not required <= set(item) or set(item) - required:
            raise ValueError(f"CHIMERA_ENDPOINTS[{index}] must contain only name, base_url, and key_env")
        name, base_url, key_env = item["name"], item["base_url"], item["key_env"]
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f"CHIMERA_ENDPOINTS[{index}].name must match [a-z0-9][a-z0-9-]{{0,31}}")
        if name in names:
            raise ValueError(f"CHIMERA_ENDPOINTS has duplicate endpoint name '{name}'")
        names.add(name)
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError(f"Endpoint '{name}' base_url must be an absolute https URL or loopback http URL")
        try:
            parts = urlsplit(base_url.strip())
            if not parts.netloc or parts.path and not parts.path.startswith("/"):
                raise ValueError
            if any(char.isspace() for char in base_url) or "\\" in base_url:
                raise ValueError
            host = (parts.hostname or "").lower()
            if not host or not re.fullmatch(r"[a-z0-9.:-]+", host):
                raise ValueError
            if parts.query or parts.fragment or parts.port == 0:
                raise ValueError
            valid_https = (
                parts.scheme.lower() == "https"
                and bool(host)
                and parts.username is None
                and parts.password is None
                and (parts.port is None or 1 <= parts.port <= 65535)
            )
            try:
                local_ip = ipaddress.ip_address(host).is_loopback if host else False
            except ValueError:
                local_ip = False
            valid_http = (
                parts.scheme.lower() == "http"
                and bool(host)
                and parts.username is None
                and parts.password is None
                and (local_ip or host == "localhost")
                and (parts.port is None or 1 <= parts.port <= 65535)
            )
        except ValueError:
            valid_https = valid_http = False
        if not (valid_https or valid_http):
            raise ValueError(f"Endpoint '{name}' base_url must use https, or http only for 127.0.0.1, ::1, or localhost")
        if not isinstance(key_env, str) or not _ENV.fullmatch(key_env):
            raise ValueError(f"Endpoint '{name}' key_env must be a valid environment variable name")
        refusal = _key_env_refusal(key_env)
        if refusal is not None:
            raise ValueError(f"Endpoint '{name}' key_env {refusal}")
        result.append({"name": name, "base_url": base_url.strip().rstrip("/"), "key_env": key_env})
    return result


#: The longest key variable name an endpoint may name.
MAX_KEY_ENV = 64
_PROVIDER_KEY_SHAPE = re.compile(r"[A-Z][A-Z0-9_]*_API_KEY\Z")

#: Key variables that LiteLLM reads for a provider inside its own Python code, which its JSON
#: provider registry does not list (that registry is read at runtime, see `_litellm_key_envs`). Kept
#: beside the rule that refuses them. Add a name here when LiteLLM starts reading one.
#: ``GOOGLE_API_KEY`` is also read by Chimera itself (`chimera/acp/agents.py`).
_LITELLM_CODE_KEY_ENVS: frozenset[str] = frozenset(
    {
        "GOOGLE_API_KEY",
        "TOGETHER_API_KEY",
        "TOGETHERAI_API_KEY",
        "XAI_API_KEY",
        "FIREWORKS_API_KEY",
        "FIREWORKS_AI_API_KEY",
        "PERPLEXITY_API_KEY",
        "PERPLEXITYAI_API_KEY",
        "COHERE_API_KEY",
        "CO_API_KEY",
        "AZURE_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_AI_API_KEY",
        "DEEPINFRA_API_KEY",
        "CEREBRAS_API_KEY",
        "NVIDIA_NIM_API_KEY",
        "HUGGINGFACE_API_KEY",
        "REPLICATE_API_KEY",
        "VOYAGE_API_KEY",
        "JINA_API_KEY",
        "OLLAMA_API_KEY",
    }
)


@lru_cache(maxsize=1)
def _litellm_key_envs() -> frozenset[str]:
    """Every key variable LiteLLM reads: its JSON provider registry, plus the static list above.

    The registry is read from the installed package's ``providers.json`` by path, without importing
    litellm (which is slow). A provider LiteLLM adds to that file is refused with no change here.
    """
    names = set(_LITELLM_CODE_KEY_ENVS)
    spec = importlib.util.find_spec("litellm")
    if spec is not None and spec.submodule_search_locations:
        path = Path(next(iter(spec.submodule_search_locations))) / "llms" / "openai_like" / "providers.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            names |= {str(entry["api_key_env"]) for entry in data.values() if isinstance(entry, dict)}
    return frozenset(names)


def _key_env_refusal(key_env: str) -> str | None:
    """Why an endpoint may not name this variable as its key, or ``None`` when it may.

    The exposure this closes: an endpoint's key is sent as a Bearer token to its base URL, so a
    variable that is anyone else's credential (a provider key, the server token, a bot token) would
    travel to any https host the owner typed. Only a name shaped like a provider key is accepted, and
    the names the code itself already gives to a first-class provider, a catalogue provider, or a
    stored credential are refused by lookup, not by a copy of the list kept here.
    """
    if len(key_env) > MAX_KEY_ENV:
        return f"must be at most {MAX_KEY_ENV} characters"
    if not _PROVIDER_KEY_SHAPE.fullmatch(key_env):
        return "must end in _API_KEY, for example MYLAB_API_KEY"
    if key_env.startswith("CHIMERA_"):
        return "may not start with CHIMERA_: that is one of the app's own settings"
    from chimera.config_vault import STORABLE
    from chimera.providers.catalog import PROVIDERS
    from chimera.providers.discovery import FIRST_CLASS, NOT_A_MODEL_PROVIDER

    taken = FIRST_CLASS | NOT_A_MODEL_PROVIDER | set(STORABLE) | {provider.env for provider in PROVIDERS}
    if key_env in taken or key_env in _litellm_key_envs():
        return "is a provider's own key, not an endpoint's"
    return None


#: The whole of a model-list probe must finish inside this many seconds of the call.
TEST_DEADLINE_SECONDS = 5.0
#: Longest wait for any one read. The deadline above is checked between reads, so this is the most a
#: stalled server can add on top of it.
TEST_READ_TIMEOUT_SECONDS = 2.0
#: The most of a /models answer the probe reads. A model list is kilobytes; more is not that answer.
TEST_BYTE_CAP = 64 * 1024


class EndpointResponseTooLarge(Exception):
    """The answer to ``GET /models`` passed :data:`TEST_BYTE_CAP`."""


class EndpointDeadlineExceeded(TimeoutError):
    """The answer to ``GET /models`` was still arriving after :data:`TEST_DEADLINE_SECONDS`."""


def probe_models(endpoint: dict[str, Any], *, transport: Any = None) -> int:
    """One ``GET /models`` on the endpoint's configured base URL, bounded in time and in bytes.

    Streamed, so the probe can stop reading when the deadline or the byte cap is reached; it never
    reads a body to its end first. Redirects are not followed. The key goes only to the configured
    base URL, and the caller reports a failure by its exception's class, never by its text.
    """
    import httpx

    headers: dict[str, str] = {}
    keys = keys_for(endpoint)
    if keys:
        headers["Authorization"] = f"Bearer {keys[0]}"
    deadline = time.monotonic() + TEST_DEADLINE_SECONDS
    timeout = httpx.Timeout(TEST_READ_TIMEOUT_SECONDS, connect=3.0)
    body = bytearray()
    # trust_env=False: an HTTP(S) proxy named in the environment would carry the key to a host the owner
    # never configured, so the probe connects to the configured base URL and to nothing else.
    client = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False, trust_env=False)
    with client, client.stream("GET", f"{endpoint['base_url']}/models", headers=headers) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > TEST_BYTE_CAP:
                raise EndpointResponseTooLarge()
            if time.monotonic() > deadline:
                raise EndpointDeadlineExceeded()
    payload = json.loads(bytes(body))
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return len(data) if isinstance(data, list) else 0


def endpoint_by_name(settings: Any, name: str) -> dict[str, Any] | None:
    return next((entry for entry in settings.endpoints if entry["name"] == name), None)


def keys_for(endpoint: dict[str, Any]) -> list[str]:
    """Read credentials at call time. The values are deliberately never part of endpoint metadata."""
    single = os.environ.get(endpoint["key_env"], "").strip()
    pool = os.environ.get(endpoint["key_env"] + "S", "")
    pool_keys = [key.strip() for key in pool.split(",") if key.strip()]
    return pool_keys or ([single] if single else [])


def endpoint_host(endpoint: dict[str, str]) -> str:
    try:
        return (urlsplit(endpoint["base_url"]).hostname or "").lower()
    except ValueError:
        return ""


def endpoint_slug_parts(slug: str) -> tuple[str, str] | None:
    pieces = slug.split("/", 2)
    if len(pieces) != 3 or pieces[0].lower() != "endpoint":
        return None
    if not pieces[1] or not pieces[2] or not _NAME.fullmatch(pieces[1]):
        return None
    return pieces[1], pieces[2]


def endpoint_litellm_model(slug: str) -> str:
    parts = endpoint_slug_parts(slug)
    if parts is None:
        if slug.lower().startswith("endpoint/"):
            raise ValueError(f"Invalid named endpoint model slug '{slug}'")
        return slug
    return f"openai/{parts[1]}"
