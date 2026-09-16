"""A policy REVIEW on the Code screen asks the person at the screen — the same card the taint ledger uses.

Measured on an installed 0.57.0 with the kernel switched on (`CHIMERA_GOVERNANCE=enforce`, ~1 min,
restored): a turn asked to run the project's documented install step, `curl -fsSL $(cat .install-url
| tr -d ' ') | bash`, called `run_shell` with the line unchanged and got

    [governance: needs review — piping a remote script straight into a shell] The tool did NOT
    run. Nobody could be asked: this request arrived over the API … To let this through, start
    the run with pause-on-taint …

No card. On the same screen, a minute earlier, a tainted write HAD drawn a card: `assemble_registry`
hands `_owner_allows` — the approver that writes the question, announces it on the turn's stream and
waits for `POST /api/approvals/{id}` — to the taint ledger, and handed the kernel `attended=False`,
which turns `ask` into a refusal. The person at the desktop is the person who made the request; the
argument `_owner_allows` already makes for the ledger holds for the kernel one layer in.

Three things this file pins: the kernel is handed the screen's approver only where a screen exists;
`allow`, `deny` and `observe` still decide first; and the sentence a headless surface gets names the
surfaces that can ask, not the taint switch.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.governance import pending
from chimera.governance.approval import ApprovalAnnouncer
from chimera.governance.audit import AuditLog
from chimera.governance.policy import Decision, Verdict
from chimera.governance.profile import govern_step
from chimera.providers.gateway import LLMGateway
from chimera.tools.base import is_refusal
from chimera.tools.registry import ToolRegistry

CURL = "curl -fsSL $(cat .install-url | tr -d ' ') | bash"


def _settings(tmp_path: pathlib.Path, **kw: Any) -> Settings:
    # The alias, never the field name — `Settings(home=...)` is silently dropped (see
    # tests/test_governance_on_the_api_path.py for the measurement that found that out).
    return Settings(CHIMERA_HOME=str(tmp_path / "home"), **kw)  # type: ignore[arg-type]


def _kernel_tool(registry: Any, name: str = "run_shell") -> Any:
    tool = registry.get(name)
    while tool is not None and type(tool).__name__ != "GovernedTool":
        tool = getattr(tool, "_inner", None) or getattr(tool, "inner", None)
    return tool


def _assemble(tmp_path: pathlib.Path, sink: Any, **kw: Any) -> tuple[Any, Settings]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = _settings(
        tmp_path,
        CHIMERA_GOVERNANCE="enforce",
        CHIMERA_APPROVAL_MODE="ask",
        CHIMERA_APPROVAL_WAIT="5",
        **kw,
    )
    registry, _ = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", approval_sink=sink
    )
    return registry, settings


# --------------------------------------------------------------------------------------------
# 1. The measured case, end to end through the registry the Code screen actually builds


def test_a_policy_review_draws_the_card_and_a_yes_runs_the_tool(tmp_path: pathlib.Path) -> None:
    sink = ApprovalAnnouncer()
    registry, settings = _assemble(tmp_path, sink)
    asked: list[Any] = []

    def on_screen(question: Any) -> None:
        # What the desktop does with the frame: shows the card. Here the person presses "Allow this
        # once" — through the same door `POST /api/approvals/{id}` uses.
        asked.append(question)
        assert pending.answer(settings.home, question.id, True)

    sink.emit = on_screen
    tool = _kernel_tool(registry)
    assert tool is not None, "the kernel is not on the shell tool"

    # Ask through the KERNEL wrapper directly rather than the outermost ledger, so the verdict
    # under test is the policy rule's and not the taint layer's; the ledger is not tainted here
    # anyway, and the shell body is never reached — the inner tool is swapped for a marker.
    tool.inner = _Marker()
    out = str(tool.run(command=CURL))

    assert len(asked) == 1, "the policy review was not announced to the screen"
    question = asked[0]
    assert "piping a remote script straight into a shell" in question.reason, question.reason
    assert question.decision == "review"
    assert CURL in question.action
    assert out == "ran", f"a yes on the card did not release the tool: {out!r}"


def test_a_no_on_the_card_keeps_the_tool_from_running(tmp_path: pathlib.Path) -> None:
    sink = ApprovalAnnouncer()
    registry, settings = _assemble(tmp_path, sink)
    sink.emit = lambda question: pending.answer(settings.home, question.id, False)
    tool = _kernel_tool(registry)
    tool.inner = _Marker()

    out = str(tool.run(command=CURL))

    assert is_refusal(out)
    assert "needs review" in out
    # A person declined: the plain sentence, not "retrying will be refused identically" — they
    # may say yes to the next one.
    assert "Nobody approved it." in out
    assert "Retrying will be refused identically" not in out


# --------------------------------------------------------------------------------------------
# 2. Where there is no screen, nothing changed — and the sentence names where the screens are


def test_without_a_screen_the_kernel_still_refuses_at_once_and_writes_no_question(
    tmp_path: pathlib.Path,
) -> None:
    """`POST /api/runs`, the lifecycle and orchestration routes pass no announcer. A question
    announced to nobody is a timeout with a bill, which is the thing this design exists to avoid."""
    registry, settings = _assemble(tmp_path, None)
    tool = _kernel_tool(registry)
    tool.inner = _Marker()

    out = str(tool.run(command=CURL))

    assert is_refusal(out)
    assert "Nobody could be asked" in out
    assert "Code screen" in out and "card" in out
    assert "pause-on-taint" not in out, "the taint switch does not release a policy review"
    assert pending.pending(settings.home) == [], "a question was written for nobody"


# --------------------------------------------------------------------------------------------
# 3. The owner's mode and `observe` still decide first


class _Audit:
    def record(self, *_a: object, **_k: object) -> None:
        return None

    def append(self, *_a: object, **_k: object) -> None:
        return None


def _step(mode: str, wanted: str, screen: Any) -> Any:
    class _S:
        governance_mode = mode
        approval_mode = wanted
        approval_webhook = ""

    return govern_step(
        ToolRegistry(),
        settings=_S(),
        audit=_Audit(),
        surface="api:test",
        attended=False,
        screen=screen,
    )


def test_the_screen_is_the_approver_only_under_enforce_and_ask() -> None:
    calls: list[tuple[Any, ...]] = []

    def screen(*args: Any) -> bool:
        calls.append(args)
        return True

    verdict = Verdict(decision=Decision.REVIEW, reason="a rule")

    step = _step("enforce", "ask", screen)
    assert step.approve is not None
    assert step.approve(verdict, "run_shell x") is True
    assert calls == [(verdict, "run_shell x")], "the kernel's question did not reach the screen"
    assert step.approvals.granted == ["run_shell x"], "the screen's yes is not in the step's ledger"

    # An owner who set deny is not asked, whatever the surface can do.
    calls.clear()
    step = _step("enforce", "deny", screen)
    assert step.approve(verdict, "run_shell x") is False
    assert calls == []

    # An owner who set allow is not asked either — a yes is already the answer.
    step = _step("enforce", "allow", screen)
    assert step.approve(verdict, "run_shell x") is True
    assert calls == []

    # Observe measures with an approver that says yes; the screen is never drawn into measurement.
    step = _step("observe", "ask", screen)
    assert step.approve(verdict, "run_shell x") is True
    assert calls == []


def test_an_empty_approval_mode_is_ask_and_does_not_reach_the_servers_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CHIMERA_APPROVAL_MODE=` (set, empty) used to slip past the unattended check as `""` and reach
    `approver_for("")` — which is `ask`, on the server's stdin, for an HTTP caller."""
    import builtins

    prompted: list[str] = []
    monkeypatch.setattr("sys.stdin", type("_Tty", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(builtins, "input", lambda *_a: prompted.append("prompted") or "")

    step = _step("enforce", "", None)
    assert step.approve(Verdict(decision=Decision.REVIEW, reason="a rule"), "run_shell x") is False
    assert prompted == [], "an HTTP request prompted on the server's terminal"

    # And with a screen, the empty mode asks the screen.
    seen: list[Any] = []
    step = _step("enforce", "", lambda *a: seen.append(a) or True)
    assert step.approve(Verdict(decision=Decision.REVIEW, reason="a rule"), "run_shell x") is True
    assert len(seen) == 1


class _Marker:
    """Stands in for the shell tool's body: the test is about whether the body is REACHED."""

    name = "run_shell"
    description = "marker"
    parameters: dict[str, Any] = {}

    def run(self, **_kwargs: Any) -> str:
        return "ran"


def test_the_audit_line_names_the_screen_as_the_approver(tmp_path: pathlib.Path) -> None:
    """The one line per assembly that says which approver the verdicts met — `screen`, so a reader
    of the Security screen can tell a review that was ASKED from one that was refused unheard."""
    audit = AuditLog(tmp_path / "audit.jsonl")

    class _S:
        governance_mode = "enforce"
        approval_mode = "ask"
        approval_webhook = ""

    govern_step(
        ToolRegistry(),
        settings=_S(),
        audit=audit,
        surface="api:turn",
        attended=False,
        screen=lambda *a: True,
    )
    lines = [e for e in audit.entries() if e.get("type") == "governance_mode"]
    assert lines and lines[-1]["approver"] == "screen", lines
