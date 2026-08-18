"""A refused tool call reported itself as a successful one.

`chimera/governance/approval.py` already names this in its own docstring — *"the failure this module
is really built against is not the refusal, it is the silence"* — and then the silence stayed. A
gate declining to run a tool returned an ordinary observation string, `Agent.run` computed
`ok = not observation.startswith("error:")`, and the answer was `True`.

Measured end to end before this changed, with the real kernel and a spy tool:

    tool ran   : False
    ToolActivity: name='run_shell', ok=True
    the model  : "Done. I force-pushed the branch to origin as requested."

Three surfaces read that flag — the SSE frame the desktop draws, the step log a receipt is built
from, and the drift detector — so a run in which every dangerous action was refused was, in every
structured field, a run that succeeded. On the 24/7 path that is a position guardian reporting green
while it guarded nothing, which from outside is indistinguishable from a daemon that stopped.
"""

from __future__ import annotations

from typing import Any

from chimera.governance.governed_tool import GovernedTool
from chimera.governance.kernel import TrustKernel
from chimera.governance.ledger import TaintLedger
from chimera.governance.ledger_tool import LedgeredTool
from chimera.providers.gateway import ToolCall
from chimera.tools.base import Tool, is_refusal, refusal


class _Spy(Tool):
    """A tool that records whether it actually ran. The only fact that matters here."""

    name = "run_shell"
    description = "spy"
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}

    def __init__(self) -> None:
        self.ran = False

    def run(self, **kwargs: Any) -> str:
        self.ran = True
        return "the command output"


def test_a_governance_refusal_says_the_tool_did_not_run() -> None:
    spy = _Spy()
    out = GovernedTool(spy, TrustKernel()).run(command="git push --force origin main")

    assert spy.ran is False, "precondition: the gate really did refuse"
    assert is_refusal(out)
    # The text is for the MODEL, which is the reader that produced "Done. I force-pushed the branch"
    # from the old wording. `ok` is invisible to it; the observation is all it sees.
    assert "did NOT run" in out


def test_a_taint_refusal_says_it_too() -> None:
    led = TaintLedger()
    led.record_fetch("body from https://example.test/")
    out = LedgeredTool(_Spy(), led, narrow_on_taint=True).run(command="echo hi")

    assert is_refusal(out) and "did NOT run" in out


def test_the_loop_no_longer_calls_a_refusal_a_success() -> None:
    """Driven through the REAL `Agent.run`, and it had to be.

    The first version of this test recomputed the loop's expression inside itself and asserted on
    that. It passed with `is_refusal` deleted from `agent.py` — the source-level companion check
    survived too, because the import line stayed behind. A test that recomputes the rule it is
    meant to be watching is testing its own arithmetic, and that is the fourth time in one day this
    project has caught that shape. So this one runs the loop and reads what the loop reported.
    """
    from chimera.core.agent import Agent, AgentConfig
    from chimera.providers.gateway import CompletionResult
    from chimera.tools.registry import ToolRegistry

    class _Backend:
        """One tool call, then a final answer — the shape a refused run really has."""

        def __init__(self) -> None:
            self._first = True

        def complete(self, messages: Any, *, tools: Any = None, **kwargs: Any) -> CompletionResult:
            if self._first:
                self._first = False
                return CompletionResult(
                    content="",
                    model="fake",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="run_shell",
                            arguments={"command": "git push --force origin main"},
                        )
                    ],
                )
            return CompletionResult(content="Done. I force-pushed the branch.", model="fake")

    spy = _Spy()
    registry = ToolRegistry()
    registry.register(GovernedTool(spy, TrustKernel()))

    seen: list[Any] = []
    Agent(_Backend(), registry, AgentConfig(max_steps=3)).run("push it", on_tool=seen.append)

    assert spy.ran is False, "precondition: the gate refused, so nothing ran"
    assert seen, "precondition: the loop reported the tool call"
    assert seen[0].ok is False, "the loop told its three readers that a refused call succeeded"


def test_an_error_is_still_an_error() -> None:
    """The other half of the rule must not have been traded away for this one."""
    assert is_refusal("error: file not found") is False


def test_an_ordinary_result_is_neither() -> None:
    assert is_refusal("the command output") is False


def test_already_done_is_not_a_refusal() -> None:
    """`[idempotent: …]` is the trap, and it is the reason this is a predicate rather than a prefix.

    That string means the effect ALREADY HAPPENED and is not being repeated. Calling it a refusal
    would tell a reader the action did not occur, when it did — the same class of lie as the one
    being fixed, pointing the other way.
    """
    # A SIDE-EFFECT tool, because only those are deduplicated (`SIDE_EFFECT_TOOLS`) — my first
    # fixture used `run_shell`, which is never deduplicated, so the precondition could not hold and
    # the test was asserting nothing about the case it was written for.
    class _Sender(_Spy):
        name = "send_email"

    led = TaintLedger()
    tool = LedgeredTool(_Sender(), led)
    first = tool.run(to="a@b.test")
    second = tool.run(to="a@b.test")

    assert "idempotent" in second, "precondition: the second call was deduplicated"
    assert is_refusal(second) is False
    assert is_refusal(first) is False


def test_both_places_that_judge_a_call_use_the_same_rule() -> None:
    """`Agent.run` and `ToolRegistry.run` each computed this independently, in the same words.

    Two copies of one rule is how one of them silently stops recognising a case — which is exactly
    what had happened. This asserts the SOURCE, because the registry's copy sets a telemetry span
    attribute that no unit test observes; a behavioural assertion here would need a tracer, and the
    thing worth pinning is that neither copy drifts again.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for path in ("chimera/core/agent.py", "chimera/tools/registry.py"):
        source = (root / path).read_text(encoding="utf-8")
        # The computing lines, not the file: an `import is_refusal` survives deleting the call,
        # which is exactly how the earlier version of this check passed against the bug.
        # Comments quote the old rule on purpose — this is looking for the CODE that applies it.
        judging = [
            ln
            for ln in source.splitlines()
            if "startswith(\"error:\")" in ln and not ln.strip().startswith("#")
        ]
        assert judging, f"{path} no longer judges a tool call the way this test assumes"
        assert all("is_refusal" in ln for ln in judging), (
            f"{path} judges a call without asking about refusals: {judging}"
        )


def test_the_marker_is_built_in_one_place() -> None:
    """Every gate must mint its refusal through `refusal()`.

    A gate that formats the string by hand would be invisible to the predicate — a refusal the
    product does not recognise as one, which is the original bug with a new spelling.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for path in ("chimera/governance/governed_tool.py", "chimera/governance/ledger_tool.py"):
        source = (root / path).read_text(encoding="utf-8")
        assert "refusal(" in source, f"{path} returns a refusal without minting it through refusal()"

    assert refusal("x").startswith(refusal("").strip() or "⛔")
