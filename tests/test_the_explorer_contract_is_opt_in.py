"""The explorer's contract (study 25, S12) is a setting, off by default.

Off, the explorer sends the text and the tool schema it always sent, byte for byte. On, the caller
names a thoroughness level that sets the step ceiling, the explorer reports findings with a location
each and a gaps section, and the harness checks every cited location against the workspace — the
half of "path:line for every claim" that a prompt cannot do by asking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core import explorer as explorer_module
from chimera.core.explorer import (
    EXPLORER_CONTRACT_SYSTEM,
    EXPLORER_SYSTEM,
    ContextExplorer,
    ExploreRepositoryTool,
    check_locations,
    thoroughness_steps,
)
from chimera.providers.gateway import CompletionResult, MessageLike

_CONTRACT_REPLY = """Let me summarise.
<final_answer>
Findings:
- The retry ceiling is read from settings, not hard-coded. src/config.py:2-3
- The client applies it on every call. src/client.py:1
Gaps:
- Verified: both files were read.
- Inferred: nothing overrides it at runtime.
- Not found: a test for the ceiling.
</final_answer>
chatter after the block"""


class _Recorder:
    """A model that answers at once, and keeps what it was sent."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.sent: list[list[MessageLike]] = []

    def complete(self, messages: list[MessageLike], **kwargs: object) -> CompletionResult:
        self.sent.append(list(messages))
        return CompletionResult(content=self.content, model="fake")


def _switch(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    if on:
        monkeypatch.setenv("CHIMERA_EXPLORER_CONTRACT", "1")
    else:
        monkeypatch.delenv("CHIMERA_EXPLORER_CONTRACT", raising=False)
    get_settings.cache_clear()


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.py").write_text("a = 1\nretries = 3\nb = 2\n", encoding="utf-8")
    (tmp_path / "src" / "client.py").write_text("call(retries)\n", encoding="utf-8")
    return tmp_path


def test_off_by_default_the_explorer_sends_todays_prompt_and_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _switch(monkeypatch, on=False)
    backend = _Recorder("<final_answer>\nsrc/config.py:2\n</final_answer>")
    tool = ExploreRepositoryTool(backend, _workspace(tmp_path))

    out = tool.run(query="where is the retry ceiling?", thoroughness="thorough")

    system, task = backend.sent[0][0]["content"], backend.sent[0][-1]["content"]
    assert system.startswith(EXPLORER_SYSTEM)
    assert task.startswith("Find the code locations most relevant")
    assert "Thoroughness" not in task
    assert tool.parameters == ExploreRepositoryTool.parameters  # no new argument in any prompt
    assert out.startswith("Relevant code locations")
    assert "location check" not in out


def test_the_setting_turns_the_contract_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _switch(monkeypatch, on=True)
    backend = _Recorder(_CONTRACT_REPLY)
    tool = ExploreRepositoryTool(backend, _workspace(tmp_path))

    out = tool.run(query="where is the retry ceiling?")

    system, task = backend.sent[0][0]["content"], backend.sent[0][-1]["content"]
    assert system.startswith(EXPLORER_CONTRACT_SYSTEM)
    assert "Thoroughness: medium. You have at most 8 tool steps." in task
    assert tool.parameters["properties"]["thoroughness"]["enum"] == ["quick", "medium", "thorough"]
    assert ExploreRepositoryTool.parameters["properties"].keys() == {"query"}  # class untouched
    # Conclusions and gaps reach the caller, the chatter does not, and the harness speaks last.
    assert "The retry ceiling is read from settings" in out
    assert "- Inferred: nothing overrides it at runtime." in out
    assert "chatter after the block" not in out
    assert out.endswith("[location check: all 2 cited locations exist in the workspace]")


def test_thoroughness_sets_the_step_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert [thoroughness_steps(level, 8) for level in ("quick", "medium", "thorough")] == [4, 8, 16]
    assert thoroughness_steps("exhaustive", 8) == 8  # a misspelt level costs only the level
    assert thoroughness_steps("quick", 3) == 2  # never below two steps

    ceilings: list[int] = []

    class _Agent:
        def __init__(self, backend: Any, tools: Any, config: Any) -> None:
            ceilings.append(config.max_steps)

        def run(self, task: str, **kwargs: Any) -> Any:
            return type("R", (), {"answer": "", "steps": 0, "tool_calls_made": 0})()

    monkeypatch.setattr(explorer_module, "Agent", _Agent)
    explorer = ContextExplorer(_Recorder(""), tmp_path, max_turns=8, contract=True)
    for level in ("quick", "medium", "thorough"):
        explorer.explore("q", level)
    assert ceilings == [4, 8, 16]


def test_a_cited_location_that_does_not_exist_is_named_in_the_receipt(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    (tmp_path.parent / "outside.py").write_text("x\n", encoding="utf-8")
    report = (
        "- real: src/config.py:1-3\n"
        "- past the end of the file: src/config.py:40\n"
        "- no such file: src/ghost.py:2\n"
        "- outside the workspace: ../outside.py:1\n"
    )

    check = check_locations(report, ws)

    assert check.cited == (
        "src/config.py:1-3", "src/config.py:40", "src/ghost.py:2", "../outside.py:1"
    )
    assert check.missing == ("src/config.py:40", "src/ghost.py:2", "../outside.py:1")
    assert "1 of 4 cited locations exist" in check.receipt()
    assert "src/ghost.py:2" in check.receipt()
