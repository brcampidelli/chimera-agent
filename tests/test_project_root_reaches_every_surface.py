"""`AGENTS.md` reached four surfaces out of twenty-seven, and not the one in the README.

`serve --workspace X` accepts the flag, roots every tool at X, and then built its agent with
`project_root=None` — so `chimera/core/agent.py` returned "" at the gate and the project's own
conventions were never read. That is the headless Docker deployment the README documents: the
gateway, the Discord bot and the cron dispatch, all running with nobody at a terminal to restate
what the flag was supposed to have said.

The incoherence is the argument. The same workspace was good enough to grant **file capability** and
not good enough to convey **file conventions**. Nothing chose that; `project_root` was added later
and wired at whichever call sites the author was looking at — four of them, and `instructions` at a
different four, so the two halves of a project's identity were each remembered somewhere the other
was not.

Two kinds of test here, and they answer different questions — worth stating, because one of them
does NOT catch this bug and pretending otherwise would be the same kind of overclaim.

The **AST gate** encodes the rule — *a surface that roots its tools in a workspace passes that
workspace as `project_root`* — and is what fails the build on a new site. Against the shipped code
it names all fifteen.

The **canary** is the reporter's own repro turned into a test: an `AGENTS.md` in the workspace, and
its text asserted into the prompt. It passes against the broken code too, because the mechanism was
never broken — only the wiring was. Its job is to keep the mechanism honest and to document, in one
readable place, exactly what `project_root` buys.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from chimera.core import Agent, AgentConfig
from chimera.tools.registry import ToolRegistry

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "chimera"

#: Sites that root a registry in a workspace and legitimately pass no `project_root`, with why.
#: Listed as exemptions rather than obligations, for the reason the governance gate learned the hard
#: way: a list of things to CHECK fails open, and the site nobody added is the one that breaks.
EXEMPT: dict[str, str] = {
    "chimera/workflow/executors.py:build_executors.solve_step": "delegates to solve, which sets it",
    # `lifecycle_crew` was exempt as "the caller owns the root" and no caller set one, so the
    # lifecycle worker was the one agent in the app that did not read the project's own AGENTS.md.
    # It sets `project_root` now, which is why the entry is gone rather than reworded — and this
    # gate is what noticed, on the same commit that gave the crew an HTTP surface.
    "chimera/cli/main.py:sandbox_bench.factory": "benchmark harness, not a user's repository",
    "chimera/cli/main.py:evolve_tune.__init__": "offline tuning over recorded trajectories",
}


def _sites_without_project_root() -> list[tuple[str, int]]:
    """Every `AgentConfig` that lacks a project root inside a scope rooted in a workspace.

    Each call is attributed to its **innermost** enclosing function, and a scope counts as rooted
    when that function or any function enclosing it builds a `default_registry`. Attributing to an
    ancestor instead would name `serve` where the code lives in `serve.factory` — and it is inside
    the factory, every time, that this goes missing.
    """
    found: list[tuple[str, int]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "default_registry(" not in source or "AgentConfig(" not in source:
            continue
        module = path.relative_to(PACKAGE.parent).as_posix()
        rooted, configs = _scan(ast.parse(source))
        for chain, lineno, has_root in configs:
            if has_root:
                continue
            # Rooted here, or anywhere up the chain: `serve` opens the workspace and `serve.factory`
            # is where the agent is built.
            if any(chain[:n] in rooted for n in range(1, len(chain) + 1)):
                found.append((f"{module}:{'.'.join(chain)}", lineno))
    return found


def _scan(
    tree: ast.Module,
) -> tuple[set[tuple[str, ...]], list[tuple[tuple[str, ...], int, bool]]]:
    """One pass: which scopes root a registry, and every `AgentConfig` with its scope chain.

    A module-level function rather than a closure inside the loop above — a nested one would capture
    the accumulators from an enclosing `for`, which is a real footgun and which ruff's B023 catches.
    """
    rooted: set[tuple[str, ...]] = set()
    configs: list[tuple[tuple[str, ...], int, bool]] = []

    def walk(node: ast.AST, chain: tuple[str, ...]) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            chain = (*chain, node.name)
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", "")
            if name == "default_registry":
                rooted.add(chain)
            elif name == "AgentConfig":
                configs.append(
                    (chain, node.lineno, "project_root" in {kw.arg for kw in node.keywords})
                )
        for child in ast.iter_child_nodes(node):
            walk(child, chain)

    walk(tree, ())
    return rooted, configs


# --- the gate -------------------------------------------------------------------------------------


def test_a_workspace_that_roots_the_tools_also_carries_the_conventions() -> None:
    """The rule, over the whole package.

    Deliberately phrased as an equivalence rather than a list of surfaces: if a workspace is trusted
    enough to decide which files the agent may touch, it is the workspace whose conventions apply.
    A surface that disagrees says so in EXEMPT, in one line.
    """
    ungoverned = [(key, line) for key, line in _sites_without_project_root() if key not in EXEMPT]

    assert not ungoverned, (
        "these surfaces root their tools in a workspace and then run without its conventions: "
        + ", ".join(f"{key} (line {line})" for key, line in ungoverned)
        + " — pass `project_root=<the same workspace>`, or add it to EXEMPT with the reason."
    )


def test_the_gate_catches_the_shape_it_was_written_for() -> None:
    """Proof the walk is not vacuous, on the exact code that shipped: a factory that roots a
    registry and builds a config without the root."""
    source = """
def serve(workspace):
    def factory():
        registry = default_registry(workspace)
        return Agent(backend, registry, AgentConfig(model=m, max_steps=8))
"""
    tree = ast.parse(source)
    offenders = [
        call.lineno
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and getattr(call.func, "id", "") == "AgentConfig"
        and "project_root" not in {kw.arg for kw in call.keywords}
    ]

    assert offenders == [5]


def test_every_exemption_still_points_at_real_code() -> None:
    """An exemption that outlives its call site is a line claiming something was considered, sitting
    next to sites that still are."""
    live = {key for key, _ in _sites_without_project_root()}

    assert not sorted(set(EXEMPT) - live), f"stale EXEMPT entries: {sorted(set(EXEMPT) - live)}"


# --- the canary -----------------------------------------------------------------------------------


class _Capture:
    """A backend that records the prompt it was handed and answers nothing useful."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> Any:
        from chimera.providers import CompletionResult

        self.messages = list(messages)
        return CompletionResult(content="ok", model="fake")


def _prompt_text(backend: _Capture) -> str:
    return "\n".join(str(m.get("content", "")) for m in backend.messages)


def test_agents_md_reaches_the_prompt_when_the_root_is_set(tmp_path: pathlib.Path) -> None:
    """The reporter's canary, as a test: with an `AGENTS.md` in the workspace naming a command, the
    agent that was told about the workspace sees it."""
    (tmp_path / "AGENTS.md").write_text(
        "# Conventions\n\nAlways run the suite with `just verify`, never with `pytest` directly.\n",
        encoding="utf-8",
    )
    backend = _Capture()

    Agent(backend, ToolRegistry(), AgentConfig(model="m", project_root=tmp_path)).run("how do I test?")

    assert "just verify" in _prompt_text(backend)


def test_without_the_root_the_same_file_is_invisible(tmp_path: pathlib.Path) -> None:
    """The other half, and the reason the bug was silent: nothing errors, nothing warns. The agent
    simply answers from general knowledge and looks like it is working."""
    (tmp_path / "AGENTS.md").write_text("Always run `just verify`.\n", encoding="utf-8")
    backend = _Capture()

    Agent(backend, ToolRegistry(), AgentConfig(model="m")).run("how do I test?")

    assert "just verify" not in _prompt_text(backend)


@pytest.mark.parametrize("command", ["serve", "chat", "assist", "tui"])
def test_the_named_surfaces_pass_the_workspace_they_were_given(command: str) -> None:
    """Named explicitly, because these are the ones a user hands a `--workspace` to and then expects
    the flag to mean something. `serve` is the one in the README's Docker deployment."""
    source = (PACKAGE / "cli" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    scope = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == command
    )

    configs = [
        call
        for call in ast.walk(scope)
        if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "AgentConfig"
    ]

    assert configs, f"`{command}` no longer builds an AgentConfig — retarget this test"
    assert all("project_root" in {kw.arg for kw in call.keywords} for call in configs)
