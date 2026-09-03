"""Four runs, US$ 5.11, nothing written — and no message named the cause or the cure.

Measured on a live install. A task asked for a shop front built from a real SQLite catalogue, read
through an MCP server. MCP output is untrusted content — correctly — so reading it tainted the run.
A tainted write needs human approval, and on an HTTP surface there is nobody who can give it:

    approver_for("ask") prompts on the server's stdin, and degrades to deny only when
    that stdin is not a terminal ... an unattended caller passes attended=False and the
    prompt is never built.
                                            -- chimera/governance/profile.py

That decision is right. Whoever is looking at the console did not make the request and cannot
consent for whoever did. What was wrong is that the refusal said only *"nobody approved it"*, which
is true in three different situations and actionable in none of them. The agent read it, retried,
spent its whole three-attempt budget on an answer that was structurally identical every time, and
reported the environment as broken.

The paired measurement that closed the diagnosis — same task, same model, same folder, same
write-region, changing only how the catalogue was read:

    through the MCP server    3 attempts, nothing written, 4 times over    US$ 5.11
    through code_interpreter  delivered on the first pass, 236 s          US$ 0.37

And the way out already existed on the same screen: *"pause for my approval if the run reads
untrusted content"*, which parks the run for a verdict instead of refusing it. It was simply never
mentioned at the moment it would have helped.
"""

from __future__ import annotations

import pytest

from chimera.governance import governed_tool
from chimera.governance.governed_tool import GovernedTool, govern_registry
from chimera.governance.policy import Decision
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry


class _Escreve(Tool):
    name = "write_file"
    description = "escreve um arquivo"
    parameters: dict = {}

    def run(self, **_kwargs: object) -> str:
        return "wrote it"


class _Kernel:
    """A kernel that always asks for review — the state a tainted run puts every write into."""

    def __init__(self, reason: str = "the run read untrusted content") -> None:
        self.reason = reason

    def evaluate(self, action: str, **_kwargs: object) -> object:
        from chimera.governance.policy import Verdict

        return Verdict(decision=Decision.REVIEW, reason=self.reason)


class _Audit:
    """The audit log the assembly writes its one line to. Records nothing here."""

    def record(self, *_a: object, **_k: object) -> None:
        return None

    def append(self, *_a: object, **_k: object) -> None:
        return None


def _recusa(no_approver: str) -> str:
    tool = GovernedTool(_Escreve(), _Kernel(), approve=None, no_approver=no_approver)
    return tool.run(path="index.html", content="<html>")


# --------------------------------------------------------------------------------------------
# 1. The measured case: an API run, nobody who can approve


def test_the_refusal_says_nobody_COULD_be_asked_not_that_nobody_did() -> None:
    saida = _recusa("unattended")
    assert "Nobody could be asked" in saida
    assert "over the API" in saida, "name the surface — the reader cannot see which one they are on"


def test_it_says_retrying_cannot_help() -> None:
    """Retrying is the only thing the old sentence suggested, and the one thing that cannot work."""
    assert "Retrying will be refused identically" in _recusa("unattended")


def test_it_names_the_switch_that_exists_on_the_same_screen() -> None:
    saida = _recusa("unattended")
    assert "pause-on-taint" in saida
    assert "pause for my approval if the run reads untrusted content" in saida, (
        "quote the control as it is labelled, or the reader has to guess which switch is meant"
    )


def test_it_names_the_other_way_out_too() -> None:
    """A user who does not want to pause has a second option, and it is the cheaper one."""
    saida = _recusa("unattended")
    assert "MCP" in saida
    assert "built-in tools" in saida


def test_it_explains_why_the_console_cannot_consent() -> None:
    """Without this the rule reads as an arbitrary restriction rather than as the point."""
    saida = _recusa("unattended")
    assert "did not make it" in saida and "cannot consent" in saida


# --------------------------------------------------------------------------------------------
# 2. The other two cases keep their own sentences, because the fixes differ


def test_an_owner_who_set_deny_is_told_that_and_not_the_taint_story() -> None:
    saida = _recusa("owner_denies")
    assert "sets approvals to deny" in saida
    assert "pause-on-taint" not in saida, (
        "the pause switch cannot release a refusal the owner configured; offering it sends the "
        "reader to a control that will not help"
    )


def test_a_real_decline_keeps_the_plain_sentence() -> None:
    """Somebody was asked and said no. Nothing here needs explaining away."""
    saida = _recusa("")
    assert "Nobody approved it." in saida
    assert "Retrying will be refused identically" not in saida, (
        "a human who declined once may approve the next one — telling them otherwise is false"
    )


# --------------------------------------------------------------------------------------------
# 3. What must not change


def test_the_tool_still_does_not_run() -> None:
    assert "wrote it" not in _recusa("unattended")


@pytest.mark.parametrize("causa", ["unattended", "owner_denies", ""])
def test_every_refusal_still_forbids_reporting_it_as_done(causa: str) -> None:
    assert "Do not report this as done" in _recusa(causa)


@pytest.mark.parametrize("causa", ["unattended", "owner_denies", ""])
def test_every_refusal_still_carries_the_kernel_reason(causa: str) -> None:
    tool = GovernedTool(
        _Escreve(), _Kernel("the run read untrusted content"), approve=None, no_approver=causa
    )
    assert "the run read untrusted content" in tool.run(path="x", content="y")


def test_an_approved_review_still_runs() -> None:
    tool = GovernedTool(
        _Escreve(), _Kernel(), approve=lambda _v, _a: True, no_approver="unattended"
    )
    assert tool.run(path="x", content="y") == "wrote it"


# --------------------------------------------------------------------------------------------
# 4. The wiring: the reason has to reach every wrapped tool, and be chosen by the one place
#    that knows


def test_govern_registry_passes_the_reason_to_every_tool() -> None:
    reg = ToolRegistry()
    reg.register(_Escreve())

    governed = govern_registry(reg, _Kernel(), no_approver="unattended")

    saida = next(iter(governed.tools())).run(path="x", content="y")
    assert "Nobody could be asked" in saida, (
        "a reason that reaches the wrapper and not the tools is a field nobody ever reads"
    )


def test_an_unattended_ask_becomes_the_unattended_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """`profile.py` is the only place that knows the surface is unattended."""
    from chimera.governance import profile

    capturado: dict[str, str] = {}
    # Patched where it is DEFINED, not on `profile`: the import is inside the function, so a name
    # bound on the module object is never the one the call resolves.
    monkeypatch.setattr(
        governed_tool, "govern_registry",
        lambda reg, kernel, **kw: capturado.update(no_approver=kw.get("no_approver", "")) or reg,
    )

    class _S:
        governance_mode = "enforce"
        approval_mode = "ask"

    profile.govern_step(ToolRegistry(), settings=_S(), audit=_Audit(), surface="api:test", attended=False)

    assert capturado["no_approver"] == "unattended"


def test_an_attended_ask_leaves_the_reason_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Somebody IS there — a decline is a decline, and must not be explained away."""
    from chimera.governance import profile

    capturado: dict[str, str] = {}
    # Patched where it is DEFINED, not on `profile`: the import is inside the function, so a name
    # bound on the module object is never the one the call resolves.
    monkeypatch.setattr(
        governed_tool, "govern_registry",
        lambda reg, kernel, **kw: capturado.update(no_approver=kw.get("no_approver", "")) or reg,
    )

    class _S:
        governance_mode = "enforce"
        approval_mode = "ask"

    profile.govern_step(ToolRegistry(), settings=_S(), audit=_Audit(), surface="cli", attended=True)

    assert capturado["no_approver"] == ""


def test_a_configured_deny_is_reported_as_the_owners_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.governance import profile

    capturado: dict[str, str] = {}
    # Patched where it is DEFINED, not on `profile`: the import is inside the function, so a name
    # bound on the module object is never the one the call resolves.
    monkeypatch.setattr(
        governed_tool, "govern_registry",
        lambda reg, kernel, **kw: capturado.update(no_approver=kw.get("no_approver", "")) or reg,
    )

    class _S:
        governance_mode = "enforce"
        approval_mode = "deny"

    profile.govern_step(ToolRegistry(), settings=_S(), audit=_Audit(), surface="cli", attended=True)

    assert capturado["no_approver"] == "owner_denies"
