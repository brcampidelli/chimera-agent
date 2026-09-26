"""The exact text of every snapshotted prompt, plus the system messages the loop assembles from them.

`tests/prompt_snapshots/` keeps one file per name returned by :func:`render_all`. The test compares
bytes, and `scripts/prompt_snapshots.py --write` rewrites the files after an intended edit. The diff
of that rewrite is how a change to a prompt is reviewed: it shows up as text, not as a line moved
inside a Python string.

The assembled examples fix every input — no skill retrieval, no cards, no nonce — because their job
is to show the **order** the pieces are put together in. Two things the plan changes live in that
order:
- the owner block is read last;
- the untrusted-data sentence comes with the base prompt.

A snapshot of each piece alone would not notice either one moving.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, cast

from chimera.prompts.registry import SECTIONS

#: A small but real AGENTS.md, so the project block renders with its header and a body.
EXAMPLE_AGENTS_MD = "Run the tests with `make test` before calling anything done.\n"
#: The task every assembled example is composed for. Skills are off, so the words do not matter.
EXAMPLE_TASK = "fix the failing test in the parser"


class _NoBackend:
    """Stands in for a model. Composing a system prompt never calls one."""

    def complete(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - never reached
        raise AssertionError("composing a system prompt must not call the model")


def assembled_examples() -> dict[str, str]:
    """The system message the agent loop sends, for fixed inputs, keyed by snapshot name."""
    from chimera.core.agent import Agent, AgentConfig
    from chimera.core.instructions import AgentIdentity, render
    from chimera.providers.gateway import SupportsComplete
    from chimera.tools.registry import ToolRegistry
    from chimera.tools.todo import TodoWriteTool

    backend = cast(SupportsComplete, _NoBackend())
    owner = render(
        AgentIdentity(name="Chimera", language="Portuguese (Brazil)", instructions="Prefer short answers.")
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "AGENTS.md").write_text(EXAMPLE_AGENTS_MD, encoding="utf-8")
        bare = Agent(backend, ToolRegistry(), AgentConfig(inject_skill_context=False, prefix_nonce=""))
        tools = ToolRegistry()
        tools.register(TodoWriteTool())
        full = Agent(
            backend,
            tools,
            AgentConfig(
                inject_skill_context=False, prefix_nonce="", project_root=root, instructions=owner
            ),
        )
        from chimera.orchestration.hierarchy import WORKER_SYSTEM

        # A role that replaces the default prompt, as the hierarchy's workers do.
        worker = Agent(
            backend,
            ToolRegistry(),
            AgentConfig(inject_skill_context=False, prefix_nonce="", system_prompt=WORKER_SYSTEM),
        )
        from chimera.tools.browser import BrowserTool

        # The browser situation module (study 25, S11): the flag on and the browser registered,
        # with a project and an owner, so the snapshot shows where the module sits between them.
        browsing = ToolRegistry()
        browsing.register(BrowserTool())
        browser = Agent(
            backend,
            browsing,
            AgentConfig(
                inject_skill_context=False, prefix_nonce="", project_root=root, instructions=owner,
                browser_situation=True,
            ),
        )
        texts = {
            "assembled.loop_bare": bare.compose_system_prompt(EXAMPLE_TASK),
            "assembled.loop_project_owner_todo": full.compose_system_prompt(EXAMPLE_TASK),
            "assembled.hierarchy_worker": worker.compose_system_prompt(EXAMPLE_TASK),
            "assembled.loop_browser_situation": browser.compose_system_prompt(EXAMPLE_TASK),
        }
        # The temporary directory's name is not part of the prompt anyone reviews.
        return {name: text.replace(str(root), "<project>") for name, text in texts.items()}


def render_all() -> dict[str, str]:
    """Every snapshot, by name: each registered constant, then the assembled examples."""
    out: dict[str, str] = {}
    for section in SECTIONS:
        text = section.text()
        if text is not None:
            out[section.id] = text
    out.update(assembled_examples())
    return out
