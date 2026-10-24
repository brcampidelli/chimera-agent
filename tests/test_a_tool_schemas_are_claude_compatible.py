"""Every tool advertised by a product registry must use an object parameter schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.providers.gateway import LLMGateway
from chimera.tools import ToolRegistry, default_registry


class _ApprovalSink:
    def emit(self, *args: Any, **kwargs: Any) -> None:
        pass


def _product_registries(home: Path) -> dict[str, ToolRegistry]:
    settings = Settings(CHIMERA_HOME=str(home))
    code, _ = assemble_registry(
        CodeSeams(), home, settings, LLMGateway(), steps=2,
        surface="api:turn", approval_sink=_ApprovalSink(),
    )
    # Chat, bot and scheduled surfaces use the native registry as their base; Code additionally
    # exercises the attended-only question tool and all of its governance wrappers.
    native = default_registry(home, host_exec_confirm=None)
    return {"chat/bot/cron": native, "Code": code}


def _assert_wire_schemas_are_objects(registry_name: str, registry: ToolRegistry) -> None:
    for index, tool_schema in enumerate(registry.to_openai_schema(), start=1):
        name = tool_schema.get("function", {}).get("name", "<unnamed>")
        parameters = tool_schema.get("function", {}).get("parameters")
        assert isinstance(parameters, dict), f"{registry_name} tool {index} {name}: parameters are not an object"
        assert parameters.get("type") == "object", (
            f"{registry_name} tool {index} {name}: parameters top level must have type object"
        )
        forbidden = {"oneOf", "anyOf", "allOf"} & parameters.keys()
        assert not forbidden, (
            f"{registry_name} tool {index} {name}: top-level combinator(s) {sorted(forbidden)}"
        )


def test_all_product_tool_schemas_have_object_top_level(tmp_path: Path) -> None:
    for registry_name, registry in _product_registries(tmp_path).items():
        _assert_wire_schemas_are_objects(registry_name, registry)
