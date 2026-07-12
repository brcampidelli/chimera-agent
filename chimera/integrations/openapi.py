"""Turn an OpenAPI/REST spec into Chimera tools.

Lets a user add *any* HTTP service without waiting for a dedicated MCP server:
point Chimera at an OpenAPI document and each operation becomes a callable tool.
Parsing and tool generation are fully offline; only ``run`` performs network I/O.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from chimera.integrations.connectors import Connector
from chimera.telemetry import get_logger
from chimera.tools.base import Tool

_log = get_logger("integrations.openapi")
_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}
_MAX_BODY_CHARS = 20_000
_NON_NAME = re.compile(r"[^a-zA-Z0-9_]+")
# Statuses worth retrying: rate-limited (429) and transient server errors (5xx).
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _sanitize(text: str) -> str:
    return _NON_NAME.sub("_", text).strip("_") or "op"


def _retry_delay(attempt: int, retry_after: str | None, *, backoff: float, cap: float) -> float:
    """Honour a Retry-After header if present, else exponential backoff, capped."""
    if retry_after:
        try:
            return min(float(retry_after), cap)  # seconds form; date form falls through
        except ValueError:
            pass
    return min(backoff * (2.0**attempt), cap)


class RestApiTool(Tool):
    """A single REST operation generated from an OpenAPI spec."""

    #: Output comes from a remote REST API — untrusted external content. The operation name is chosen
    #: by the spec, so it won't be in FETCH_TOOLS; this marker tells ``LedgeredTool`` to fence +
    #: taint-track the response regardless of name.
    untrusted_output = True

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        method: str,
        base_url: str,
        path_template: str,
        path_params: list[str],
        query_params: list[str],
        has_body: bool,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        retries: int = 2,
        backoff: float = 0.5,
        max_backoff: float = 20.0,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.method = method.upper()
        self.base_url = base_url.rstrip("/")
        self.path_template = path_template
        self.path_params = path_params
        self.query_params = query_params
        self.has_body = has_body
        self.headers = headers or {}
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.max_backoff = max_backoff

    def _url(self, kwargs: dict[str, Any]) -> str:
        path = self.path_template
        for name in self.path_params:
            path = path.replace("{" + name + "}", str(kwargs.get(name, "")))
        return f"{self.base_url}{path}"

    def run(self, **kwargs: Any) -> str:
        import time

        import httpx  # lazy

        # Fail fast on a missing required path param: substituting "" would build a DIFFERENT URL
        # (e.g. GET /items/ instead of /items/{id}) and silently hit the wrong endpoint.
        missing = [p for p in self.path_params if not str(kwargs.get(p, "")).strip()]
        if missing:
            return f"error: missing required path parameter(s): {', '.join(missing)}"
        url = self._url(kwargs)
        query = {name: kwargs[name] for name in self.query_params if name in kwargs}
        body = kwargs.get("body") if self.has_body else None
        last_error = "request failed"
        for attempt in range(self.retries + 1):
            try:
                response = httpx.request(
                    self.method,
                    url,
                    params=query or None,
                    json=body,
                    headers=self.headers or None,
                    timeout=self.timeout,
                    follow_redirects=True,
                )
            except httpx.HTTPError as exc:  # transient transport error — retry
                last_error = f"request failed: {exc}"
                if attempt < self.retries:
                    time.sleep(_retry_delay(attempt, None, backoff=self.backoff, cap=self.max_backoff))
                    continue
                return f"error: {last_error}"
            if response.status_code in _RETRY_STATUS and attempt < self.retries:
                delay = _retry_delay(
                    attempt, response.headers.get("Retry-After"), backoff=self.backoff, cap=self.max_backoff
                )
                _log.debug("retrying %s %s after %s (status %d)", self.method, url, delay, response.status_code)
                time.sleep(delay)
                continue
            text = response.text
            if len(text) > _MAX_BODY_CHARS:
                text = text[:_MAX_BODY_CHARS] + f"\n... [truncated, {len(text)} chars total]"
            return f"[{response.status_code}] {self.method} {url}\n{text}"
        return f"error: {last_error}"


def _build_param_schema(
    operation: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str], bool]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    path_params: list[str] = []
    query_params: list[str] = []

    for param in operation.get("parameters", []) or []:
        pname = param.get("name")
        if not pname:
            continue
        param_schema = dict(param.get("schema") or {"type": "string"})
        if param.get("description"):
            param_schema.setdefault("description", param["description"])
        properties[pname] = param_schema
        if param.get("required"):
            required.append(pname)
        location = param.get("in")
        if location == "path":
            path_params.append(pname)
        elif location == "query":
            query_params.append(pname)

    has_body = False
    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        json_schema = (
            request_body.get("content", {}).get("application/json", {}).get("schema")
        )
        if json_schema is not None:
            properties["body"] = json_schema
            has_body = True
            if request_body.get("required"):
                required.append("body")

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema, path_params, query_params, has_body


def tools_from_openapi(
    spec: dict[str, Any],
    *,
    base_url: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 2,
    backoff: float = 0.5,
) -> list[RestApiTool]:
    """Generate one :class:`RestApiTool` per operation in ``spec``."""
    servers = spec.get("servers") or []
    resolved_base = base_url or (servers[0].get("url", "") if servers else "")
    tools: list[RestApiTool] = []

    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId") or f"{method}_{_sanitize(path)}"
            description = (
                operation.get("summary")
                or operation.get("description")
                or f"{method.upper()} {path}"
            )
            schema, path_params, query_params, has_body = _build_param_schema(operation)
            tools.append(
                RestApiTool(
                    name=op_id,
                    description=description,
                    parameters=schema,
                    method=method,
                    base_url=resolved_base,
                    path_template=path,
                    path_params=path_params,
                    query_params=query_params,
                    has_body=has_body,
                    headers=headers,
                    timeout=timeout,
                    retries=retries,
                    backoff=backoff,
                )
            )
    _log.debug("generated %d tools from openapi spec", len(tools))
    return tools


def load_spec(path: str | Path) -> dict[str, Any]:
    """Load an OpenAPI spec from a .json or .yaml/.yml file."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"openapi spec at {path} is not a mapping")
    return data


class OpenAPIConnector(Connector):
    """A connector that exposes an OpenAPI service's operations as tools."""

    def __init__(
        self,
        name: str,
        spec: dict[str, Any],
        *,
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        retries: int = 2,
        backoff: float = 0.5,
    ) -> None:
        self.name = name
        self._tools = tools_from_openapi(
            spec, base_url=base_url, headers=headers, timeout=timeout, retries=retries, backoff=backoff
        )

    def tools(self) -> list[Tool]:
        return list(self._tools)
