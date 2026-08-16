"""Three defects a code audit found, each cheap to fix and each undermining something load-bearing.

They share a shape with the sweep that preceded them: in all three the RIGHT behaviour is written
down somewhere in this repository, and a second site never got it.

1. The tool-loop breaker leaves declared `tool_calls` unanswered and sends the malformed list. The
   mechanism built to save a spinning run is what a real provider rejects with a 400.
2. `--gen-tests` writes its test file inside the diff-gate's snapshot window, so the verifier's own
   artifact counts as the agent's work and `--require-diff` can never fire.
3. The cascade's top rung was a bare `FusionEngine`, which falls through to the frontier default
   panel — the exact bug `api/roles.py` documents at length as already measured and already fixed
   for role fusion.

Each test here fails against the pre-fix code, and the reason each was invisible is worth stating:
the existing suites use stubs that declare ONE tool call per step, never run `--gen-tests` with a
diff gate attached, and never assert which models a cascade would convene.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.config import Settings
from chimera.core.agent import Agent, AgentConfig
from chimera.providers import CompletionResult, ToolCall
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

# --- 1. the breaker must not leave a tool_call unanswered ----------------------------------------


class _Echo(Tool):
    name = "echo"
    description = "echoes"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        return "same observation every time"


class _ThreeCallsPerStep:
    """A backend that declares THREE identical tool calls per step, so the loop detector trips
    STRICTLY INSIDE the batch and leaves a call behind.

    Three, not two, and the difference is the whole test: with two, the breaker trips on the second
    — the last of its batch — nothing is skipped, and the assertion passes while proving nothing.
    The first draft of this file did exactly that. Every other stub in the suite declares one call
    per step, which is why a decade of green runs never saw this."""

    def __init__(self) -> None:
        self.sent: list[list[dict[str, Any]]] = []
        self.step = 0

    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> CompletionResult:
        self.sent.append([dict(m) for m in messages])
        self.step += 1
        if kwargs.get("tools") is None or self.step > 6:
            return CompletionResult(content="done", model="fake")
        return CompletionResult(
            content="",
            model="fake",
            tool_calls=[
                ToolCall(id=f"c{self.step}a", name="echo", arguments={"x": 1}),
                ToolCall(id=f"c{self.step}b", name="echo", arguments={"x": 1}),
                ToolCall(id=f"c{self.step}c", name="echo", arguments={"x": 1}),
            ],
        )


def _orphans(messages: list[dict[str, Any]]) -> list[str]:
    """tool_call ids announced by an assistant message and never answered by a `role:"tool"`."""
    declared: list[str] = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            ident = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
            if ident:
                declared.append(ident)
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    return [ident for ident in declared if ident not in answered]


def test_the_loop_breaker_answers_every_call_it_announced(tmp_path: Path) -> None:
    """The invariant a real provider enforces: one `role:"tool"` per declared `tool_call`.

    Pre-fix the `break` left the second call of the batch unanswered and the very next request
    carried that list — a 400 from any endpoint that validates, which is all of them.
    """
    registry = ToolRegistry()
    registry.register(_Echo())
    backend = _ThreeCallsPerStep()

    result = Agent(backend, registry, AgentConfig(model="fake", max_steps=8)).run("go")

    assert result.stopped_reason == "tool_loop", "the breaker did not trip; test is not exercising it"
    for sent in backend.sent:
        assert not _orphans(sent), f"a malformed list reached the provider: {_orphans(sent)}"
    assert not _orphans(result.transcript), "the malformed transcript is what CodeSession persists"


def test_the_skipped_calls_are_reported_as_not_run(tmp_path: Path) -> None:
    """Not silently: the model has to be able to tell "this tool failed" from "this tool never ran",
    because the next turn is written from what it reads here."""
    registry = ToolRegistry()
    registry.register(_Echo())
    backend = _ThreeCallsPerStep()

    result = Agent(backend, registry, AgentConfig(model="fake", max_steps=8)).run("go")

    synthetic = [
        m for m in result.transcript
        if m.get("role") == "tool" and "not run" in str(m.get("content", ""))
    ]
    assert synthetic, "the skipped call was answered, but with nothing that says it did not run"


# --- 2. the verifier's own file is not the agent's work -------------------------------------------


def test_the_generated_test_file_does_not_count_as_a_productive_diff(tmp_path: Path) -> None:
    """`--require-diff` exists because SWE-bench run 1 returned 11/19 empty patches. With
    `--gen-tests` on, the verifier writes into the workspace between the two snapshots, so every
    attempt looked productive and the gate could not fire."""
    from chimera.core.autonomous import _without_verifier_artifacts
    from chimera.core.checkpoint import WorkspaceGuard
    from chimera.core.spec_test import _TEST_FILE
    from chimera.evolution.diff_gate import diff_snapshots

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    guard = WorkspaceGuard(workspace)
    before = guard.snapshot()

    # The agent changes nothing; only the verifier writes.
    (workspace / _TEST_FILE).write_text("def test_x():\n    assert True\n", encoding="utf-8")
    after = guard.snapshot()

    assert diff_snapshots(before, after).is_productive, "precondition: raw diff sees it as work"

    filtered = diff_snapshots(before, _without_verifier_artifacts(after))

    assert not filtered.is_productive, "the verifier's own file still counts as the agent's work"
    assert filtered.added == []


def test_a_real_edit_is_still_productive_alongside_the_test_file(tmp_path: Path) -> None:
    """The other direction, and the one that matters more: filtering the artifact must not swallow
    the work. A fix that made every `--gen-tests` run look empty would be worse than the bug."""
    from chimera.core.autonomous import _without_verifier_artifacts
    from chimera.core.checkpoint import WorkspaceGuard
    from chimera.core.spec_test import _TEST_FILE
    from chimera.evolution.diff_gate import diff_snapshots

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    guard = WorkspaceGuard(workspace)
    before = guard.snapshot()

    (workspace / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")  # the real work
    (workspace / _TEST_FILE).write_text("def test_x():\n    assert True\n", encoding="utf-8")
    after = guard.snapshot()

    filtered = diff_snapshots(before, _without_verifier_artifacts(after))

    assert filtered.is_productive
    assert filtered.modified == ["app.py"]
    assert _TEST_FILE not in filtered.added


def test_filtering_leaves_the_snapshot_alone(tmp_path: Path) -> None:
    """A copy, not a mutation — the caller keeps the real snapshot for `--keep-workspace`, and the
    generated tests stay on disk for whoever wants to read them."""
    from chimera.core.autonomous import _without_verifier_artifacts
    from chimera.core.checkpoint import WorkspaceGuard
    from chimera.core.spec_test import _TEST_FILE

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / _TEST_FILE).write_text("x = 1\n", encoding="utf-8")
    snapshot = WorkspaceGuard(workspace).snapshot()

    filtered = _without_verifier_artifacts(snapshot)

    assert _TEST_FILE not in filtered.present
    assert _TEST_FILE in snapshot.present, "the original was mutated"
    assert (workspace / _TEST_FILE).exists(), "the file was deleted from disk"


# --- 3. the cascade convenes the user's ladder, not the frontier ----------------------------------


def _panel_of(build: Any, tmp_path: Path) -> list[str]:
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_COST_MODE="cheap")
    cascade = build(object(), settings)
    engine = getattr(cascade, "fusion", None) or getattr(cascade, "_fusion", None)
    assert engine is not None, "could not reach the cascade's top rung"
    return list(engine.config.panel)


def test_the_cli_cascade_does_not_escalate_to_the_frontier(tmp_path: Path) -> None:
    """A user on `cost_mode=cheap` who turns on the cascade was billed Opus + GPT-5.5 + Gemini at
    the top rung, judged by Opus, with nothing in the run naming the models that answered.

    `api/roles.py` documents this exact failure as already measured and already fixed for role
    fusion. The cascade kept constructing the bare engine.
    """
    from chimera.cli.main import _cascade_backend

    panel = _panel_of(_cascade_backend, tmp_path)

    assert panel, "no panel at all"
    assert not any("opus" in m or "gpt-5" in m or "gemini-3" in m for m in panel), (
        f"the cheap ladder still convenes the frontier default panel: {panel}"
    )


def test_the_desktop_cascade_mirrors_the_cli(tmp_path: Path) -> None:
    """`api/app.py` calls itself "a local mirror" of the CLI builder. A mirror that copied the shape
    and dropped the fix would put the desktop on frontier rates for the same user."""
    from chimera.api.app import _api_cascade_backend as api_cascade
    from chimera.cli.main import _cascade_backend as cli_cascade

    assert _panel_of(api_cascade, tmp_path) == _panel_of(cli_cascade, tmp_path)


def test_an_explicit_panel_still_wins(tmp_path: Path) -> None:
    """Somebody who named a panel meant it. `fusion_for_role` distinguishes "explicitly provided"
    from "happens to equal the default", and the cascade must not undo that."""
    from chimera.cli.main import _cascade_backend

    settings = Settings(
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_COST_MODE="cheap",
        CHIMERA_FUSION_PANEL="model-a,model-b",
    )
    cascade = _cascade_backend(object(), settings)
    engine = getattr(cascade, "fusion", None) or getattr(cascade, "_fusion", None)

    assert list(engine.config.panel) == ["model-a", "model-b"]
