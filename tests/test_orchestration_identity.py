"""The owner's instructions on the two surfaces that were not getting them.

Settings says these are "applied to every turn, on every surface". They were applied on Code, on
Runs and in chat — not in Orchestration, and not in Automation. Because the same rendered block
carries the "always answer in {language}" line, the visible symptom was not a missing persona: an
owner who had configured the app in Portuguese got English answers out of those two screens, in an
app whose entire interface is translated into ten languages.

The tests below pin where the block goes and, just as deliberately, where it must not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.core.instructions import AgentIdentity, render
from chimera.orchestration.artifacts import ArtifactStore
from chimera.orchestration.envelope_verify import EnvelopeVerifier
from chimera.orchestration.hierarchy import HierarchicalOrchestrator
from chimera.orchestration.roles import Role, RoleAgent
from chimera.providers.gateway import CompletionResult, Message, MessageLike

WEAK, MID, TOP = "w/model:free", "m/model", "t/model"

IDENTITY = render(
    AgentIdentity(name="Quimera", language="português", instructions="Prefira respostas curtas.")
)

_DECOMPOSITION = json.dumps(
    [
        {"objective": "Summarize doc A", "output_format": "3 bullets", "boundaries": "doc A only"},
        {"objective": "Summarize doc B", "output_format": "3 bullets", "boundaries": "doc B only"},
    ]
)

_READ_TASK = (
    "Research and compare the release notes in doc A and doc B, extract the breaking "
    "changes from each, and summarize what upgrading requires; list the risks as well."
)


class _Backend:
    """Records the system prompt of every call, which is the whole assertion surface here."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, messages: list[MessageLike], *, model: str | None = None, **kwargs: Any
    ) -> CompletionResult:
        first = messages[0]
        data = first.as_dict() if isinstance(first, Message) else first
        system = str(data.get("content", "")) if data.get("role") == "system" else ""
        self.calls.append({"model": model, "system": system, "roles": len(messages)})
        if "Split the user's task" in system:
            content = _DECOMPOSITION
        elif "Synthesize ONE final answer" in system:
            content = "Resposta final."
        elif "focused sub-worker" in system:
            content = "Findings: tudo certo.\n\nGaps\n(none)"
        else:
            content = "resposta de agente único"
        return CompletionResult(
            content=content, model=model or "?", prompt_tokens=100, completion_tokens=50
        )


def _orchestrator(backend: _Backend, tmp_path: Path, *, identity: str) -> HierarchicalOrchestrator:
    store = ArtifactStore(tmp_path / "artifacts")
    return HierarchicalOrchestrator(
        backend,
        weak_model=WEAK,
        mid_model=MID,
        top_model=TOP,
        store=store,
        verifier=EnvelopeVerifier(store=store, backend=None, spot_rate=0.0),
        receipts_path=tmp_path / "delegations.jsonl",
        identity=identity,
    )


def _system_of(backend: _Backend, needle: str) -> str:
    return next(c["system"] for c in backend.calls if needle in c["system"])


# --- the hierarchy --------------------------------------------------------------------------------


def test_the_stage_that_answers_a_person_carries_the_owners_instructions(tmp_path: Path) -> None:
    backend = _Backend()

    _orchestrator(backend, tmp_path, identity=IDENTITY).run(_READ_TASK)

    synth = _system_of(backend, "Synthesize ONE final answer")
    assert "português" in synth, "the language line is the visible half of this"
    assert "Prefira respostas curtas." in synth
    # In front of the stage prompt: the stage instruction is the more specific one, and the
    # convention everywhere else in the stack is that closer-to-the-task text comes last.
    assert synth.index("Quimera") < synth.index("Synthesize ONE final answer")


def test_the_decomposer_is_deliberately_left_alone(tmp_path: Path) -> None:
    """It must emit ONLY a JSON array.

    "Always answer in Portuguese" against "reply with ONLY a JSON array" is a conflict, not a
    preference, and the decomposer's output is parsed by code — no person ever reads it.
    """
    backend = _Backend()

    _orchestrator(backend, tmp_path, identity=IDENTITY).run(_READ_TASK)

    assert "português" not in _system_of(backend, "Split the user's task")


def test_sub_workers_are_left_alone_too(tmp_path: Path) -> None:
    """They answer the synthesizer, not the user, and `WORKER_SYSTEM` already dictates their form.

    It is also byte-identical across workers on purpose — an identical prefix is a shared provider
    cache prefix — and the owner's line about *how to answer* would fight the stage's own.
    """
    backend = _Backend()

    _orchestrator(backend, tmp_path, identity=IDENTITY).run(_READ_TASK)

    worker_calls = [c for c in backend.calls if "focused sub-worker" in c["system"]]
    assert worker_calls, "this task must actually fan out, or the test asserts nothing"
    assert all("português" not in c["system"] for c in worker_calls)


def test_the_single_agent_fallback_carries_them_too(tmp_path: Path) -> None:
    """The path that answers the user most directly was sending no system message at all."""
    backend = _Backend()

    # `simple` shape: short and single-source, so it never fans out.
    _orchestrator(backend, tmp_path, identity=IDENTITY).run("What is the capital of France?")

    assert any("português" in c["system"] for c in backend.calls)


def test_without_an_identity_nothing_changes(tmp_path: Path) -> None:
    """The default is "" and the behaviour on it must be byte-identical to before.

    Twenty existing tests construct this orchestrator without the argument; a default that quietly
    altered a prompt would have moved them all without anyone deciding to.
    """
    backend = _Backend()

    _orchestrator(backend, tmp_path, identity="").run(_READ_TASK)

    synth = _system_of(backend, "Synthesize ONE final answer")
    assert synth.startswith("You are the lead orchestrator.")


# --- the crew -------------------------------------------------------------------------------------


def test_a_crew_role_carries_the_owners_instructions() -> None:
    """A crew worker's answer lands in front of the user, so the same rule applies to it."""
    backend = _Backend()

    RoleAgent(Role("writer", "You write the final answer."), backend, identity=IDENTITY).act("faça")

    system = backend.calls[0]["system"]
    assert "português" in system
    assert system.index("Quimera") < system.index("You write the final answer.")


def test_a_crew_role_without_an_identity_sends_only_its_role() -> None:
    backend = _Backend()

    RoleAgent(Role("writer", "You write the final answer."), backend).act("faça")

    assert backend.calls[0]["system"] == "You write the final answer."


def test_a_tool_using_crew_role_carries_them_through_AgentConfig() -> None:
    """The path the real crew takes.

    `IsolatedCrew` always passes `tools=`, so a role with no tools is not the configuration that
    ships. Asserting only on the tool-free branch let a sabotaged `AgentConfig(instructions=...)`
    pass this file — which is how this test came to exist.
    """
    from chimera.tools.registry import ToolRegistry

    backend = _Backend()

    RoleAgent(
        Role("writer", "You write the final answer."),
        backend,
        tools=ToolRegistry(),
        identity=IDENTITY,
    ).act("faça")

    assert any("português" in c["system"] for c in backend.calls)


def test_a_tool_using_crew_role_reads_the_projects_conventions(tmp_path: Path) -> None:
    """`project_root` is what makes an agent read AGENTS.md, and this was the one worker without it.

    Invisible until a screen put a crew in front of a real repository: the workers that edit files
    were the only ones in the stack ignoring the file that says how this repository wants edits.
    """
    from chimera.tools.registry import ToolRegistry

    (tmp_path / "AGENTS.md").write_text("Sempre use aspas simples.", encoding="utf-8")
    backend = _Backend()

    RoleAgent(
        Role("writer", "You write the final answer."),
        backend,
        tools=ToolRegistry(),
        project_root=tmp_path,
    ).act("faça")

    assert any("aspas simples" in c["system"] for c in backend.calls)
