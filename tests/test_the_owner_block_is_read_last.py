"""The owner's block is the last thing in every assembled system prompt.

Study 25 (plan §5.1, rule 4) fixes one precedence for every situation: owner > situation contract >
project convention > retrieved advice. The owner's block says it outranks what came before it, so
it has to come last. The todo sentence used to be appended after it whenever `todo_write` was
registered, which is every terminal and desktop session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from chimera.core.agent import TODO_PROMPT, Agent, AgentConfig
from chimera.prompts.snapshots import render_all
from chimera.providers.gateway import SupportsComplete
from chimera.tools.registry import ToolRegistry
from chimera.tools.todo import TodoWriteTool

OWNER = "Instructions from the person who runs you.\nYou are called Chimera."


class _NoBackend:
    def complete(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - never reached
        raise AssertionError("composing a system prompt must not call the model")


def _agent(root: Path | None) -> Agent:
    tools = ToolRegistry()
    tools.register(TodoWriteTool())
    config = AgentConfig(
        inject_skill_context=False, prefix_nonce="", project_root=root, instructions=OWNER
    )
    return Agent(cast(SupportsComplete, _NoBackend()), tools, config)


def test_the_owner_block_ends_the_prompt_even_with_the_todo_tool() -> None:
    prompt = _agent(None).compose_system_prompt("task")
    assert TODO_PROMPT in prompt
    assert prompt.endswith(OWNER)


def test_the_todo_sentence_comes_before_the_project_and_the_owner(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Run `make test` first.\n", encoding="utf-8")
    prompt = _agent(tmp_path).compose_system_prompt("task")
    assert prompt.index(TODO_PROMPT) < prompt.index("make test") < prompt.index(OWNER)


def test_the_reviewed_snapshot_ends_with_the_owner() -> None:
    text = render_all()["assembled.loop_project_owner_todo"]
    assert text.rstrip().endswith("Prefer short answers.")
