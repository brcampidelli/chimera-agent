"""Live integration: import a REAL public OpenAPI spec and call a generated tool.

Uses httpbin.org (a stable, no-auth public API with a published spec). Marked
``integration`` so it is deselected by default and only runs with ``-m integration``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.integrations import OpenAPIConnector, load_spec, tools_from_openapi
from chimera.integrations.connectors import ConnectorRegistry
from chimera.tools.registry import ToolRegistry

pytestmark = pytest.mark.integration

SPEC_URL = "https://httpbin.org/spec.json"
BASE_URL = "https://httpbin.org"


def _fetch_spec(tmp_path: Path) -> dict:
    import httpx

    text = httpx.get(SPEC_URL, timeout=20.0).text
    spec_path = tmp_path / "httpbin.openapi.json"
    spec_path.write_text(text, encoding="utf-8")
    return load_spec(spec_path)


def test_import_real_openapi_and_register_tools(tmp_path: Path) -> None:
    spec = _fetch_spec(tmp_path)
    tools = tools_from_openapi(spec, base_url=BASE_URL)
    assert len(tools) > 5  # httpbin exposes many operations

    # The connector path the agent actually uses: pour the tools into a registry.
    connector = OpenAPIConnector("httpbin", spec, base_url=BASE_URL)
    registry = ConnectorRegistry()
    registry.register(connector)
    count = registry.into_tool_registry(ToolRegistry())
    assert count == len(connector.tools()) > 5


def test_call_a_generated_tool_live(tmp_path: Path) -> None:
    spec = _fetch_spec(tmp_path)
    tools = tools_from_openapi(spec, base_url=BASE_URL)

    def required(tool: object) -> list[str]:
        return getattr(tool, "parameters", {}).get("required", [])

    chosen = None
    for path in ("/uuid", "/json", "/ip", "/user-agent", "/get"):
        chosen = next(
            (t for t in tools if t.method == "GET" and t.path_template == path and not required(t)),
            None,
        )
        if chosen is not None:
            break
    assert chosen is not None, "no zero-parameter GET operation found in the spec"

    result = chosen.run()  # a real HTTP request to httpbin
    assert result.startswith("[200]"), result[:200]
