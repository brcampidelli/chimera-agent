"""The Code Explorer's internal tools use the turn's governance and taint ledger."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, _explorer_registry, assemble_registry
from chimera.config import Settings
from chimera.governance.audit import AuditLog
from chimera.governance.ledger import TaintLedger
from chimera.providers.gateway import CompletionResult, MessageLike, ToolCall
from chimera.tools.base import Tool


def _settings(tmp_path: Path, **kwargs: Any) -> Settings:
    # Settings uses environment aliases; `CHIMERA_HOME` is the documented constructor input.
    return Settings(CHIMERA_HOME=str(tmp_path / "home"), **kwargs)  # type: ignore[arg-type]


def _inner_registry(tmp_path: Path, *, settings: Settings, denied: list[str] | None = None):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "notes.txt").write_text("untrusted instruction", encoding="utf-8")
    audit = AuditLog(settings.home / "audit.jsonl")
    ledger = TaintLedger(authority=settings.taint_authority)
    registry = _explorer_registry(
        workspace,
        trust_workspace=bool(settings.trust_workspace),
        settings=settings,
        audit=audit,
        ledger=ledger,
        narrow_on_taint=settings.taint_narrow,
        attended=False,
        home=settings.home,
        allowed=None,
        denied=denied or [],
    )
    return registry, ledger, audit, workspace


def test_explorer_read_is_audited_and_untrusted_content_taints_ledger(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        CHIMERA_GOVERNANCE="observe",
        CHIMERA_TRUST_WORKSPACE=False,
        CHIMERA_TOOL_DENYLIST="run_shell",
    )
    registry, ledger, _, workspace = _inner_registry(tmp_path, settings=settings)

    result = registry.run("read_file", path=str(workspace / "notes.txt"))

    assert "untrusted instruction" in result
    assert ledger.taint_epoch == 1
    rows = AuditLog(settings.home / "audit.jsonl").entries()
    assert any(
        row.get("type") == "governance_mode"
        and row.get("surface") == "code:explorer"
        for row in rows
    )
    assert str(workspace / "notes.txt") in ledger.taint_sources()[0]
    assert any(
        row.get("type") == "governance" and "read_file" in row.get("action", "")
        for row in rows
    )


def test_owner_denylist_reaches_explorer_internal_tools(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, CHIMERA_GOVERNANCE="off", CHIMERA_TOOL_DENYLIST="read_file"
    )
    registry, _, _, _ = _inner_registry(tmp_path, settings=settings)

    assert "read_file" not in registry.names()


def test_explorer_without_visible_approver_allows_pure_reads_without_waiting(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        CHIMERA_GOVERNANCE="enforce",
        CHIMERA_APPROVAL_MODE="ask",
    )
    registry, _, _, workspace = _inner_registry(tmp_path, settings=settings)

    # No screen or unattended terminal is available; a pure read is ALLOW, not a request for approval.
    assert "notes.txt" in registry.run("list_dir", path=str(workspace))
    assert "untrusted instruction" in registry.run("read_file", path=str(workspace / "notes.txt"))


def test_deployment_allowlist_also_filters_explorer_tools(tmp_path: Path) -> None:
    settings = _settings(tmp_path, CHIMERA_TOOL_ALLOWLIST="grep")
    registry, _, _, _ = _inner_registry(tmp_path, settings=settings)

    assert registry.names() == ["grep"]


def test_code_seams_deny_are_preserved_for_explorer(tmp_path: Path) -> None:
    # Denials passed by the Code assembly are applied to this separate registry too.
    settings = _settings(tmp_path, CHIMERA_GOVERNANCE="off")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry, _ = assemble_registry(
        CodeSeams(explorer=True, deny_tools=["glob"]),
        workspace,
        settings,
        object(),
        steps=4,
        surface="test:explorer",
    )
    explorer = registry.get("explore_repository")
    while not hasattr(explorer, "_explorer"):
        explorer = explorer.inner
    assert "glob" not in explorer._explorer._registry().names()


class _ScriptedBackend:
    def __init__(self, calls: list[ToolCall], answer: str = "finished") -> None:
        self.calls = list(calls)
        self.answer = answer
        self.messages: list[list[MessageLike]] = []

    def complete(self, messages: list[MessageLike], **kwargs: object) -> CompletionResult:
        self.messages.append(messages)
        if self.calls:
            call = self.calls.pop(0)
            return CompletionResult(content="", model="fake", tool_calls=[call])
        return CompletionResult(content=self.answer, model="fake")


def test_assemble_registry_explorer_reads_through_turn_audit_and_ledger(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        CHIMERA_GOVERNANCE="observe",
        CHIMERA_TRUST_WORKSPACE=False,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt"
    target.write_text("private note", encoding="utf-8")
    seams = CodeSeams(explorer=True)
    registry, ledger = assemble_registry(
        seams, workspace, settings, object(), steps=4, surface="test:explorer"
    )
    explorer = registry.get("explore_repository")
    wrapped = explorer
    while not hasattr(wrapped, "_explorer"):
        wrapped = wrapped.inner
    wrapped._explorer.backend = _ScriptedBackend(
        [
            ToolCall(id="read", name="read_file", arguments={"path": str(target)}),
            ToolCall(id="list", name="glob", arguments={"pattern": "*.py"}),
        ],
        answer="<final_answer>\nnotes.txt:1 (contains the note)\n</final_answer>",
    )
    wrapped._explorer.contract = False

    result = explorer.run(query="read the notes file")

    assert "notes.txt" in result
    assert ledger.taint_epoch == 1
    assert str(target) in ledger.taint_sources()[0]
    rows = AuditLog(settings.home / "audit.jsonl").entries()
    assert any(
        row.get("type") == "governance_mode" and row.get("surface") == "code:explorer"
        for row in rows
    )
    assert any(
        row.get("type") == "governance" and "read_file" in row.get("action", "")
        for row in rows
    )


class _InjectedWriteRegistry:
    """Test-only malicious/custom factory result for the Explorer boundary."""

    def __call__(self):
        from chimera.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(_InjectedWriteTool())
        return registry


class _InjectedWriteTool(Tool):
    name = "write_file"
    description = "write a file"
    parameters = {"type": "object", "properties": {}}

    def run(self, **kwargs: object) -> str:
        return "wrote"


def test_explorer_rejects_custom_registry_with_write_tool(tmp_path: Path) -> None:
    from chimera.core.explorer import ContextExplorer

    with pytest.raises(ValueError, match="only register read-only discovery tools.*write_file"):
        ContextExplorer(
            _ScriptedBackend([]), tmp_path, registry_factory=_InjectedWriteRegistry()
        ).explore("inspect the repository")


def test_explorer_review_refusal_does_not_read_sensitive_path(tmp_path: Path) -> None:
    import re

    from chimera.core.explorer import ContextExplorer
    from chimera.governance.policy import Decision, Rule, Scope

    settings = _settings(tmp_path, CHIMERA_GOVERNANCE="enforce")
    registry, _, _, workspace = _inner_registry(tmp_path, settings=settings)
    sensitive = workspace / "secrets.env"
    sensitive.write_text("TOP_SECRET_VALUE", encoding="utf-8")

    # Use the real Explorer registry assembly (including its GovernedTool and LedgeredTool
    # wrappers), then add a rule to that assembly's kernel to review this otherwise read-only path.
    governed: Any = registry.get("read_file")
    while not hasattr(governed, "kernel"):
        governed = governed.inner
    governed.kernel.ruleset.add(Rule(
        "sensitive_path",
        re.compile(r"secrets\.env"),
        Decision.REVIEW,
        "sensitive path review required",
        scope=Scope.ANY_TEXT,
    ))

    backend = _ScriptedBackend(
        [ToolCall(id="review", name="read_file", arguments={"path": str(sensitive)})]
    )
    explorer = ContextExplorer(
        backend, workspace, contract=False, registry_factory=lambda: registry
    )
    explorer.explore("inspect the sensitive configuration")

    tool_messages = [
        str(message.get("content", ""))
        for call_messages in backend.messages
        for message in call_messages
        if isinstance(message, dict) and message.get("role") == "tool"
    ]
    assert any("sensitive path review required" in message for message in tool_messages)
    assert any("The tool did NOT run" in message for message in tool_messages)
    assert all("TOP_SECRET_VALUE" not in message for message in tool_messages)


