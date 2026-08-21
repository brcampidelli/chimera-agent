"""Tests for the tools inventory (a pure read-model over the tool registry).

The load-bearing property: capability ``tags`` are derived PURELY from the tool NAME against the
governance sets — ``read_file`` → read, ``write_file`` → write, ``run_shell`` → exec, ``http_get`` →
network — and never from executing anything. ``untrusted_output`` is read as-is (False for the native
desktop registry).
"""

from __future__ import annotations

from typing import Any

from chimera.api.tools_api import list_tools
from chimera.tools.base import Tool
from chimera.tools.builtin import default_registry
from chimera.tools.registry import ToolRegistry


class _FakeTool(Tool):
    """A minimal Tool for structural tests: name/description/parameters set per instance."""

    def __init__(
        self, name: str, params: dict[str, Any] | None = None, *, untrusted: bool = False
    ) -> None:
        self.name = name
        self.description = f"desc for {name}"
        self.parameters = {"type": "object", "properties": params or {}}
        if untrusted:
            self.untrusted_output = True

    def run(self, **kwargs: Any) -> str:
        return ""


def _by_name(infos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {i["name"]: i for i in infos}


def test_list_tools_returns_names_params_tags() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("read_file", {"path": {"type": "string"}}))
    registry.register(_FakeTool("echo", {"text": {"type": "string"}, "n": {"type": "integer"}}))
    infos = _by_name(list_tools(registry))

    assert set(infos) == {"read_file", "echo"}
    assert infos["read_file"]["description"] == "desc for read_file"
    assert infos["read_file"]["params"] == ["path"]
    assert infos["echo"]["params"] == ["text", "n"]  # preserves property order
    # echo is in no governance set → no tags, and that empty list is shown as-is.
    assert infos["echo"]["tags"] == []


def test_no_params_is_empty_list() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("http_get"))  # parameters.properties == {}
    assert list_tools(registry)[0]["params"] == []


def test_known_native_tools_get_the_right_tag() -> None:
    registry = ToolRegistry()
    for name in ("read_file", "write_file", "run_shell", "http_get"):
        registry.register(_FakeTool(name))
    infos = _by_name(list_tools(registry))

    assert infos["read_file"]["tags"] == ["read"]
    assert infos["write_file"]["tags"] == ["write"]
    assert infos["run_shell"]["tags"] == ["exec"]
    assert infos["http_get"]["tags"] == ["network"]


def test_untrusted_output_read_as_is() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("read_file"))
    registry.register(_FakeTool("imported", untrusted=True))
    infos = _by_name(list_tools(registry))

    assert infos["read_file"]["untrusted_output"] is False  # native default
    assert infos["imported"]["untrusted_output"] is True


def test_real_default_registry_is_readable_and_native(tmp_path: Any) -> None:
    infos = list_tools(default_registry(tmp_path))
    by_name = _by_name(infos)

    assert len(infos) >= 1
    # The always-on native tools are present with their honest tags.
    assert by_name["read_file"]["tags"] == ["read"]
    assert by_name["write_file"]["tags"] == ["write"]
    assert by_name["run_shell"]["tags"] == ["exec"]
    assert by_name["http_get"]["tags"] == ["network"]
    # A native tool flags its output untrusted when it READS SOMETHING SOMEONE ELSE WROTE — a PDF or
    # a DOCX, a transcript of a recording. Both can carry an injection, so their text must not reach
    # the model looking like an instruction.
    #
    # This used to assert `read_document` was the only one, which was true only because the speech
    # extra was absent from the test environment: `transcribe_audio` registers when faster-whisper
    # is installed OR an OpenAI key is present, and it flags untrusted output for exactly the same
    # reason. Naming the SET is the durable claim; "there is one of them" was a fact about a machine.
    # `skill_view` joins these when a skill bundle is switched on: it reads files downloaded from
    # somebody else's repository, and a reference file is not the SKILL.md a person reviewed.
    READS_EXTERNAL = {"read_document", "transcribe_audio", "skill_view"}
    flagged = {i["name"] for i in infos if i["untrusted_output"]}
    assert flagged <= READS_EXTERNAL
    assert "read_document" in flagged  # always registered; the others depend on optional extras
