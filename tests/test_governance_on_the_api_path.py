"""Everything served over HTTP was assembled without the trust kernel.

Measured on `main` before this existed, with the deployment default:

    veredito do kernel para 'git push --force origin main': review  (force push)
    cadeia de embrulhos: LedgeredTool -> WriteFileTool
    TEM GovernedTool no caminho da API? False

`POST /api/runs`, `POST /api/agents` and `POST /api/code/turn` all converge on
`code_api.assemble_registry`, and it built the write region, the denylist union and the taint ledger
— but never the kernel. So the one verdict the policy has an opinion about was reached by nobody:
there was no wrapper on that path to ask.

The fix is deliberately NOT `governed_profile(assemble_registry(...))`. That would have built a
SECOND `TaintLedger` outermost while the caller kept the inner one, and `assemble_registry` already
says why that is fatal — "the run that got tainted and the run that gets asked about it would be
different objects, and the pause would never fire". The kernel step moved into `govern_step`
instead, which both callers now share.
"""

from __future__ import annotations

import builtins
import json
import pathlib
from typing import Any

import pytest

from chimera.api.code_api import CodeSeams, assemble_registry
from chimera.config import Settings
from chimera.providers import LLMGateway
from chimera.tools.base import is_refusal


def _settings(tmp_path: pathlib.Path, **kw: Any) -> Settings:
    # The alias, never the field name. `Settings(home=...)` is silently DROPPED — every field here
    # carries a `validation_alias`, so the kwarg is accepted, ignored, and the object comes back
    # holding the default. The first version of the probe that produced the numbers above did that
    # and reported "the kernel is still missing" in all three modes; the kernel was fine, the
    # measurement was not.
    return Settings(CHIMERA_HOME=str(tmp_path / "home"), **kw)  # type: ignore[arg-type]


def _chain(registry: Any, name: str = "write_file") -> list[str]:
    """The wrapper stack around one tool, outermost first."""
    out: list[str] = []
    tool = registry.get(name)
    while tool is not None and len(out) < 8:
        out.append(type(tool).__name__)
        tool = getattr(tool, "_inner", None) or getattr(tool, "inner", None)
    return out


def _unwrap_to_kernel(registry: Any, name: str = "run_shell") -> Any:
    tool = registry.get(name)
    while tool is not None and type(tool).__name__ != "GovernedTool":
        tool = getattr(tool, "_inner", None) or getattr(tool, "inner", None)
    return tool


def _assemble(tmp_path: pathlib.Path, **kw: Any) -> Any:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    registry, _ = assemble_registry(
        CodeSeams(), ws, _settings(tmp_path, **kw), LLMGateway(), steps=4
    )
    return registry


# --- the three modes on this path ---------------------------------------------------------------


def test_the_default_install_is_untouched(tmp_path: pathlib.Path) -> None:
    """Governance arriving through an upgrade is not a thing an upgrade may decide."""
    assert "GovernedTool" not in _chain(_assemble(tmp_path))


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_asking_for_governance_puts_the_kernel_on_this_path(
    tmp_path: pathlib.Path, mode: str
) -> None:
    assert "GovernedTool" in _chain(_assemble(tmp_path, CHIMERA_GOVERNANCE=mode))


def test_a_force_push_over_http_is_refused_and_says_so(tmp_path: pathlib.Path) -> None:
    """The whole point, end to end: the kernel's `review` reaches the tool and stops it.

    Read through the wrappers rather than around them — this is the observation the model gets back,
    and since the refusal marker landed it is also what makes `ok=False` on the frame the desktop
    draws. Before this, the same call ran.
    """
    governed = _unwrap_to_kernel(_assemble(tmp_path, CHIMERA_GOVERNANCE="enforce"))
    assert governed is not None, "the kernel is not on the shell tool"

    out = governed.run(command="git push --force origin main")
    assert is_refusal(out)
    assert "did NOT run" in out


# --- the property that made this safe to ship ----------------------------------------------------


def test_observe_never_weakens_what_was_already_protecting(tmp_path: pathlib.Path) -> None:
    """`observe` adds measurement. It must not subtract protection.

    The obvious implementation hands the mode's approver to BOTH layers, the way `governed_profile`
    does. On this path that would be a regression pointing the wrong way: taint narrowing here runs
    with no approver, so a dangerous call after untrusted input REFUSES — and `observe`'s approver
    says yes to everything. Somebody switching to `observe` in order to measure would have quietly
    turned a refusal into an execution.
    """
    from chimera.governance.ledger_tool import LedgeredTool

    def taint_verdict(mode: str | None) -> str:
        kw = {"CHIMERA_TAINT_NARROW": "1"}
        if mode is not None:
            kw["CHIMERA_GOVERNANCE"] = mode
        registry = _assemble(tmp_path, **kw)
        tool = registry.get("write_file")
        assert isinstance(tool, LedgeredTool), "the taint layer is not outermost any more"
        tool.ledger.record_fetch("body from https://example.test/")
        return str(tool.run(path="x.txt", content="hi"))

    assert is_refusal(taint_verdict(None)), (
        "precondition: the tainted call already refused before governance existed here"
    )
    assert is_refusal(taint_verdict("observe")), "observe turned a refusal into an execution"


def test_this_surface_never_builds_a_prompt_on_the_servers_terminal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ask` reads the SERVER's stdin, and degrades to deny only when that stdin is not a terminal.

    A `chimera serve` started from a shell has one. Under `enforce` that would stop an HTTP request
    and wait for whoever is looking at the console to type `y` — a person who did not make the
    request, cannot see what it was for, and whose answer blocks the worker until it comes. So the
    API asks for `attended=False` and the prompt is never built.

    Both directions are asserted, because a test that only checks the quiet one passes just as well
    against a `govern_step` that ignores the flag entirely.
    """
    from chimera.governance.audit import AuditLog
    from chimera.governance.profile import govern_step
    from chimera.tools.registry import ToolRegistry

    class _Tty:
        def isatty(self) -> bool:
            return True

    asked: list[str] = []

    def _fake_input(*_args: Any) -> str:
        asked.append("prompted")
        return ""

    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr(builtins, "input", _fake_input)

    settings = _settings(tmp_path, CHIMERA_GOVERNANCE="enforce", CHIMERA_APPROVAL_MODE="ask")
    audit = AuditLog(tmp_path / "audit.jsonl")

    quiet = govern_step(
        ToolRegistry(), settings=settings, audit=audit, surface="api", attended=False
    )
    assert quiet.approve is not None
    quiet.approve("some reason", "run_shell {}")
    assert asked == [], "the API path prompted on the server's terminal"

    loud = govern_step(ToolRegistry(), settings=settings, audit=audit, surface="cli", attended=True)
    assert loud.approve is not None
    loud.approve("some reason", "run_shell {}")
    assert asked == ["prompted"], "attended=True no longer prompts, so the flag proves nothing"


# --- what lands in the log a screen reads --------------------------------------------------------


def test_the_refusal_is_recorded_and_the_ordinary_calls_are_not(tmp_path: pathlib.Path) -> None:
    """`TrustKernel.evaluate` records EVERY verdict, and ALLOW is nearly all of them.

    One line per tool call on an interactive coding turn, against a Security screen that reads the
    newest 200, means about twenty-five turns bury every taint and narrowing event — the rare ones
    that screen exists for. `assemble_registry` had already reached this conclusion for
    `restrict_registry`: "a trail nobody can read is the same as no trail."
    """
    registry = _assemble(tmp_path, CHIMERA_GOVERNANCE="enforce")

    registry.get("write_file").run(path="ok.txt", content="hi")
    governed = _unwrap_to_kernel(registry)
    assert governed is not None
    governed.run(command="git push --force origin main")

    log = tmp_path / "home" / "audit.jsonl"
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
    decisions = [e["decision"] for e in entries if e.get("type") == "governance"]
    assert decisions == ["review"], f"expected only the refusal to be written, got {decisions}"


def test_the_scoreboard_stops_claiming_the_kernel_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`api/governance.py` reported `"trust_kernel": False` as a LITERAL.

    It was true when written and would have stayed false through the change that made it true — a
    hardcoded fact about the rest of the system is a fact with no way to notice the system moved.
    The desktop draws a warning off this flag, so it would have kept warning about a layer that was
    running.
    """
    from chimera.api.governance import run_injection_suite

    monkeypatch.delenv("CHIMERA_GOVERNANCE", raising=False)
    assert run_injection_suite(Settings())["trust_kernel"] is False

    monkeypatch.setenv("CHIMERA_GOVERNANCE", "observe")
    assert run_injection_suite(Settings())["trust_kernel"] is True


def test_the_exemption_still_says_something_true() -> None:
    """`tests/test_governed_surfaces.py` exempts `assemble_registry` from the build gate.

    The gate cannot see this surface's governance: it recognises a `default_registry(...)` passed as
    an ARGUMENT to `governed_profile`, and here the registry is assigned to a variable first and
    wrapped four steps later. So the exemption is prose, and prose is what goes stale. This pins the
    sentence to the code it describes.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    source = (root / "chimera/api/code_api.py").read_text(encoding="utf-8")
    body = source.split("def assemble_registry", 1)[1].split("\nclass ", 1)[0]
    assert "govern_step(" in body, "assemble_registry lost the kernel the exemption promises"
