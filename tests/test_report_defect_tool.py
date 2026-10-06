"""Offline tests for receipt-only defect reporting and deterministic impossible-twin generation."""
from __future__ import annotations

import json
from pathlib import Path

from chimera.api.runs import build_receipt
from chimera.config import Settings
from chimera.tools.builtin import default_registry
from chimera.tools.report_defect import ReportDefectTool


def test_tool_records_claim_and_continues_without_control_state() -> None:
    observed: list[dict[str, str]] = []
    tool = ReportDefectTool()
    tool.bind(observed.append)
    response = tool.run(claim="contradictory assertion", evidence="contract says two")
    assert "execution continues" in response
    assert observed == [{"claim": "contradictory assertion", "evidence": "contract says two"}]
    assert tool.claims == observed
    assert "blocked" not in response.lower()


def test_receipt_carries_bounded_report_claim() -> None:
    from types import SimpleNamespace

    result = SimpleNamespace(success=False, paused=False, attempts=[], answer="", stopped_reason="", ending="exhausted")
    receipt = build_receipt(result, "task", None, "now", report_defects=[{"claim": "bad", "evidence": "proof"}])
    assert receipt.model_dump()["report_defects"] == [{"claim": "bad", "evidence": "proof"}]


def test_report_defect_off_by_default_and_not_added_to_default_registry() -> None:
    settings = Settings(_env_file=None, home=Path("."))
    assert settings.report_defect_tool is False
    registry = default_registry(Path("."), host_exec_confirm=None)
    assert "report_defect" not in registry


def test_impossible_twins_are_one_mutation_and_have_unmodified_pair() -> None:
    from bench.report_defect.runner import generate

    twins = generate()
    assert len(twins) == 24
    for twin in twins:
        assert twin["original_test_src"] != twin["mutated_test_src"]
        assert twin["test"]
        assert twin["files"]
        # Exactly one extra contradictory assertion is introduced; the paired source is pristine.
        before = twin["original_test_src"].splitlines()
        after = twin["mutated_test_src"].splitlines()
        contradiction = [line for line in after if "impossible-twin contradiction" in line]
        assert len(after) == len(before) + 1
        assert len(contradiction) == 1
        assert [line for line in after if "impossible-twin contradiction" not in line] == before
        assert json.loads(json.dumps(twin))["id"] == twin["id"]


def test_fake_backend_arm_schema_differs_only_by_optional_report_tool() -> None:
    from chimera.config import get_settings
    from chimera.tools.builtin import default_registry

    old = get_settings()
    from unittest.mock import patch

    with patch("chimera.config.get_settings", return_value=old.model_copy(update={"report_defect_tool": False})):
        control = default_registry(Path("."), host_exec_confirm=None)
    with patch("chimera.config.get_settings", return_value=old.model_copy(update={"report_defect_tool": True})):
        treatment = default_registry(Path("."), host_exec_confirm=None)
    assert "report_defect" not in control
    assert "report_defect" in treatment


def test_fake_backend_can_report_defect_into_only_its_run_state() -> None:
    from chimera.core import Agent, AgentConfig
    from chimera.providers import CompletionResult, ToolCall
    from chimera.tools import ToolRegistry

    class FakeBackend:
        def __init__(self) -> None:
            self.responses = [
                CompletionResult(
                    content="",
                    model="fake",
                    tool_calls=[ToolCall(
                        id="defect-1", name="report_defect",
                        arguments={"claim": "contradiction", "evidence": "same output equals 1 and 2"},
                    )],
                ),
                CompletionResult(content="Noted.", model="fake"),
            ]

        def complete(self, messages: list[object], *, tools: object = None, **kwargs: object) -> CompletionResult:
            del messages, tools, kwargs
            return self.responses.pop(0)

    registry = ToolRegistry()
    tool = ReportDefectTool()
    registry.register(tool)
    agent = Agent(FakeBackend(), registry, AgentConfig())
    agent.run("inspect impossible checker")
    assert agent.run_state.report_defects == [
        {"claim": "contradiction", "evidence": "same output equals 1 and 2"}
    ]
    assert tool.claims == agent.run_state.report_defects
