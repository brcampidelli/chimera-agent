"""Named OpenAI-compatible endpoints, validated without ever exposing credentials."""

from __future__ import annotations

import ipaddress
import json
import os
import re
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
        result.append({"name": name, "base_url": base_url.strip().rstrip("/"), "key_env": key_env})
    return result


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
