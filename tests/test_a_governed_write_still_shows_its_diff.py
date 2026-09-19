"""The live per-edit diff, through the governance wrappers the desktop puts on every tool.

Found live on 2026-09-18 while testing background works: a turn created `hello.py` in plain sight
and the stream carried no `edit` frame and no `verified` frame after it — so the Code screen drew
no diff and offered no undo. The capture (`Agent._edit_before`) asked the registry's tool for its
`workspace`, and on the desktop that tool is `LedgeredTool(GovernedTool(WriteFileTool))`: the
wrappers have none, so the capture returned None on every governed turn. The workspace is the
innermost tool's, and the capture now looks through the wrappers for it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.api.code_api import CodeTurnRequest, assemble_registry
from chimera.config import Settings
from chimera.core import Agent, AgentConfig
from chimera.providers import CompletionResult, LLMGateway, ToolCall


class _Scripted:
    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        if self._responses:
            return self._responses.pop(0)
        return CompletionResult(content="created hello.py", model="fake")


def test_a_write_through_the_governed_registry_emits_its_diff_and_a_create_counts(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    req = CodeTurnRequest(message="create hello.py", workspace=str(ws))
    registry, _ledger = assemble_registry(
        req, ws, Settings(CHIMERA_HOME=str(tmp_path / "home")),  # type: ignore[call-arg]
        LLMGateway(), steps=3, surface="api:turn", instruction=req.message,
    )
    # The desktop's chain, as built: a wrapper without a workspace around the tool that has one.
    outer = registry.get("write_file")
    assert getattr(outer, "workspace", None) is None
    assert getattr(outer, "inner", None) is not None

    backend = _Scripted([
        CompletionResult(
            content="", model="fake",
            tool_calls=[ToolCall(id="c1", name="write_file", arguments={"path": "hello.py", "content": 'print("olá")\n'})],
        ),
    ])
    edits: list[tuple[str, str]] = []
    result = Agent(backend, registry, AgentConfig(max_steps=3, project_root=ws)).run(
        "create hello.py", on_edit=lambda path, patch: edits.append((path, patch))
    )
    assert (ws / "hello.py").read_text(encoding="utf-8") == 'print("olá")\n'
    assert result.tool_names == ["write_file"]
    # The diff of a created file: from nothing to its line.
    assert [p for p, _ in edits] == ["hello.py"]
    assert '+print("olá")' in edits[0][1]
