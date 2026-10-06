"""A hook's `ask` is a question the owner wrote; `CHIMERA_APPROVAL_MODE=allow` does not answer it.

Found by an adversarial review of S30-65. The threat model kept `observe`'s approve-everything away
from a hook's `ask`, because a measuring yes "would turn a hook's question into a yes" — and then
handed the question to the owner's own approver, which under `CHIMERA_APPROVAL_MODE=allow` is the
same approve-everything function. Reproduced: a hooks file with `ask` on `git\\s+push`, approvals set
to `allow`, `govern_step(..., attended=False)` — the push ran in `off`, `observe` and `enforce`, on
a surface with nobody there. The receipt said `applied: ask` and nothing about who answered, so the
audit could not tell that push from one a person approved.

Now an approver that asks nobody (marked by `approval.allow`, and kept through `SharedApprovals`)
cannot answer a hook's question: the call is refused with the reason, as `ask` is when nobody can be
reached. Every answered `ask` writes a second `hook` receipt, `event: ask`, with `approved` and
`approver`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.governance.approval import allow, approver_for, asks_nobody, deny
from chimera.governance.audit import AuditLog
from chimera.governance.hooks import HOOKS_FILE_NAME, HookedTool, HookRunner, load_hooks
from chimera.governance.shared_approval import SharedApprovals
from chimera.tools.base import Refusal, Tool
from chimera.tools.registry import ToolRegistry

PUSH = "git push origin main"
ASK_PUSH = {"id": "ask-push", "event": "pre_tool", "tools": ["run_shell"],
            "pattern": r"git\s+push", "decision": "ask"}


class _Shell(Tool):
    def __init__(self) -> None:
        self.name = "run_shell"
        self.description = "records"
        self.parameters = {"type": "object", "properties": {}}
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(dict(kwargs))
        return "ran"


def _registry(shell: _Shell) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(shell)
    return registry


def _answers(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r.get("type") == "hook" and r.get("event") == "ask"]


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    home = tmp_path / "home"
    home.mkdir()
    (home / HOOKS_FILE_NAME).write_text(json.dumps({"hooks": [ASK_PUSH]}), encoding="utf-8")
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_HOOKS", "true")
    get_settings.cache_clear()
    yield home
    get_settings.cache_clear()


@pytest.mark.parametrize("mode", ["off", "observe", "enforce"])
def test_approvals_set_to_allow_do_not_answer_a_hooks_ask_on_an_unattended_surface(
    mode: str, owner: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's reproduction, every mode. Sabotage: drop the `asks_nobody` branch in
    `HookedTool.run` — the push runs in all three."""
    from chimera.governance.profile import govern_step

    monkeypatch.setenv("CHIMERA_APPROVAL_MODE", "allow")
    get_settings.cache_clear()
    shell = _Shell()
    audit_path = tmp_path / "audit.jsonl"
    step = govern_step(
        _registry(shell), settings=get_settings(), audit=AuditLog(audit_path), mode=mode,
        surface="cron", attended=False,
    )
    out = step.registry.get("run_shell").run(command=PUSH)
    assert isinstance(out, Refusal) and "ask-push" in out
    assert "`allow`" in out, "the refusal must say why, or the owner retries the same setting"
    assert shell.calls == []
    assert [(a["hook"], a["approved"], a["approver"]) for a in _answers(audit_path)] == [
        ("ask-push", False, "allow")
    ]


def test_a_call_the_hook_does_not_ask_about_still_runs_under_allow(
    owner: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the refusal is the hook's question, not `allow` breaking the surface."""
    from chimera.governance.profile import govern_step

    monkeypatch.setenv("CHIMERA_APPROVAL_MODE", "allow")
    get_settings.cache_clear()
    shell = _Shell()
    step = govern_step(
        _registry(shell), settings=get_settings(), audit=AuditLog(tmp_path / "a.jsonl"),
        mode="enforce", surface="cron", attended=False,
    )
    assert step.registry.get("run_shell").run(command="git status") == "ran"


@pytest.mark.parametrize("mode", ["off", "observe", "enforce"])
def test_an_answered_ask_writes_who_answered_and_what_they_said(
    mode: str, owner: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person who says yes on the screen: the call runs, and the audit says it was the screen and
    that it said yes. Before, the only line was `applied: ask`, written before anybody answered.
    Sabotage: remove the `answered` call after the approver — no `event: ask` line."""
    from chimera.governance.profile import govern_step

    monkeypatch.setenv("CHIMERA_APPROVAL_MODE", "ask")
    get_settings.cache_clear()
    shell = _Shell()
    audit_path = tmp_path / "audit.jsonl"
    step = govern_step(
        _registry(shell), settings=get_settings(), audit=AuditLog(audit_path), mode=mode,
        surface="code", screen=lambda *_a: True,
    )
    assert step.registry.get("run_shell").run(command=PUSH) == "ran"
    assert [(a["hook"], a["approved"], a["approver"]) for a in _answers(audit_path)] == [
        ("ask-push", True, "screen")
    ]


def test_under_off_the_hooks_refused_ask_reaches_the_steps_own_ledger(
    owner: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`governed_profile` hands `approvals` back so a cron job or a lane can say it was not allowed to
    do its work. Under `off` the hook's question used to be put to a ledger nobody kept."""
    from chimera.governance.profile import govern_step

    monkeypatch.setenv("CHIMERA_APPROVAL_MODE", "ask")
    get_settings.cache_clear()
    step = govern_step(
        _registry(_Shell()), settings=get_settings(), audit=AuditLog(tmp_path / "a.jsonl"),
        mode="off", surface="code", screen=lambda *_a: False,
    )
    assert isinstance(step.registry.get("run_shell").run(command=PUSH), Refusal)
    assert step.approvals.blocked and any("git push" in r for r in step.approvals.refused)


def test_only_allow_is_marked_as_asking_nobody_and_a_shared_allow_keeps_the_mark() -> None:
    assert asks_nobody(allow()) and asks_nobody(approver_for("allow"))
    assert not asks_nobody(deny()) and not asks_nobody(approver_for("deny"))
    assert not asks_nobody(lambda *_a: True), "an approver nobody marked is a person's"
    assert asks_nobody(SharedApprovals(allow()).approver())
    assert not asks_nobody(SharedApprovals(deny()).approver())


def test_a_crews_shared_allow_does_not_answer_a_hooks_ask(owner: Path) -> None:
    """`crew-isolated` hands every worker `SharedApprovals(...).approver()`, a wrapper: a mark that
    the wrapper dropped would let `allow` answer the hook there. Sabotage: drop the propagation in
    `SharedApprovals.approver` — the push runs."""
    shell = _Shell()
    runner = HookRunner(load_hooks(owner), sandbox=lambda: None)
    tool = HookedTool(shell, runner, approve=SharedApprovals(allow()).approver())
    assert isinstance(tool.run(command=PUSH), Refusal)
    assert shell.calls == []
