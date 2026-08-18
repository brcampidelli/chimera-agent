"""Run tools through the trust kernel.

``GovernedTool`` wraps any :class:`~chimera.tools.base.Tool` so its ``run`` is gated
by the kernel: BLOCK refuses, REVIEW requires approval, WARN/ALLOW proceed. Because
it *is* a Tool, a registry of governed tools drops straight into the existing agent
loop with no other changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chimera.governance.kernel import TrustKernel
from chimera.governance.policy import Decision, Verdict
from chimera.tools.base import Tool, is_untrusted_output, refusal
from chimera.tools.registry import ToolRegistry

ApproveFn = Callable[[Verdict, str], bool]
#: Supplies the reason an action is being taken, read fresh at each tool call.
ContextFn = Callable[[], str]


#: Argument names whose value is a document, not an identifier. Their contents never reach the audit.
#:
#: By NAME rather than by length, because the useful half of an audit line is exactly the short
#: values — `run_shell {'command': 'git push --force origin main'}` is the whole point of the
#: record, and a blanket length cap would keep the first 200 characters of a `.env` while throwing
#: away the command someone opens the log to read.
_DOCUMENT_ARGS = frozenset(
    {"content", "patch", "diff", "text", "body", "data", "new_str", "old_str", "new_text"}
)


def elide_values(kwargs: dict[str, Any]) -> dict[str, Any]:
    """``kwargs`` with document-shaped values replaced by their size."""
    return {
        key: (f"<{len(str(value))} chars>" if key in _DOCUMENT_ARGS else value)
        for key, value in kwargs.items()
    }


def render_action(name: str, kwargs: dict[str, Any]) -> tuple[str, str]:
    """Split a tool call into ``(action, document)`` for the rules to judge separately.

    The action used to be ``f"{name} {kwargs}"``. Interpolating a dict calls ``repr`` on it, and
    ``repr`` escapes a newline into the two characters ``\\`` and ``n`` — so the ``n`` fused with the
    next word (``\\nrm`` arrived as the text ``nrm``) and killed the ``\\b`` that every rule in
    :mod:`~chimera.governance.policy` starts with. Measured before the fix:

        review  git_force_push  'git push --force origin main'
        allow   default         'echo hi\\ngit push --force origin main'
        block   rm_rf_root      'rm -rf /var/lib/data'
        allow   default         'set -e\\nrm -rf /var/lib/data'

    The protection was inverted: every real shell script has more than one line, so the two-line
    version of a blocked command walked through, while a markdown file *quoting* ``rm -rf /tmp/x``
    was hard-blocked. Values now go in raw, one per line — the separator matters, because the rules
    are already written line-aware (``[^\\n]*``), so a newline between arguments is what stops two
    unrelated values from fusing into a signature neither of them contains.

    Document bodies go to the second return value instead of the first, reusing the one definition
    of "this is a body, not an identifier" that :data:`_DOCUMENT_ARGS` already carries for the audit
    — so a gap in that list is one gap to fix, not two that can drift apart.

    The tool NAME leads the action. That was checked rather than assumed: all ten default rules are
    shell or credential signatures and not one of them mentions a tool, so putting the name on its
    own line costs no match today and keeps the judge told what was called.
    """
    command = "\n".join(
        [name, *(str(value) for key, value in kwargs.items() if key not in _DOCUMENT_ARGS)]
    )
    document = "\n".join(
        str(value) for key, value in kwargs.items() if key in _DOCUMENT_ARGS
    )
    return command, document


class GovernedTool(Tool):
    """A tool whose execution is gated by the trust kernel."""

    def __init__(
        self,
        inner: Tool,
        kernel: TrustKernel,
        *,
        approve: ApproveFn | None = None,
        context: ContextFn | None = None,
    ) -> None:
        self.inner = inner
        self.kernel = kernel
        self.approve = approve
        # Why the action is happening — the kernel's ``context``, which used to be declared and
        # discarded. A callable rather than a string because the task changes between runs while
        # the wrapped registry is built once: a fixed string would pin the first task forever and
        # label every later action with a stale reason, which is worse than no reason at all.
        self.context = context
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters
        # Mirror the taint marker too. Dropping it here is what disarmed fencing, sanitisation and
        # run-tainting whenever governance wrapped a tool before the ledger did (`--guard --taint`).
        self.untrusted_output = is_untrusted_output(inner)

    def run(self, **kwargs: Any) -> str:
        action, document = render_action(self.name, kwargs)
        # The rules read both halves — command signatures against `action`, credential signatures
        # against both. The audit gets the elided rendering and neither half raw. Redaction alone was
        # not enough: it catches credential SHAPES, and the body of a private key has no shape — a
        # governed `write_file` of `deploy/id_rsa` put `-----BEGIN RSA PRIVATE KEY-----` and the
        # first lines of the key into a file the app serves over HTTP. Nothing about an audit trail
        # needs the contents of the file that was written; it needs to know which file, and why the
        # call was stopped.
        verdict = self.kernel.evaluate(
            action,
            context=self._context(),
            record_as=f"{self.name} {elide_values(kwargs)}",
            document=document,
        )
        if verdict.decision == Decision.BLOCK:
            return refusal(f"[governance: BLOCKED — {verdict.reason}] "
                           f"The tool did NOT run. Do not report this as done.")
        if verdict.decision == Decision.REVIEW:
            approved = self.approve(verdict, action) if self.approve else False
            if not approved:
                return refusal(f"[governance: needs review — {verdict.reason}] "
                               f"The tool did NOT run and nobody approved it. Do not "
                               f"report this as done.")
        return self.inner.run(**kwargs)

    def _context(self) -> str:
        """The current task, or ``""``. Never raises: a broken provider must not block a tool.

        Governance failing *open* on a missing reason is the right trade — the verdict is still
        made, it is just made without the extra signal. Failing closed here would let a typo in
        somebody's context callable take down every tool call in the run.
        """
        if self.context is None:
            return ""
        try:
            return str(self.context() or "")
        except Exception:  # noqa: BLE001 — see docstring: never block a tool over a missing reason
            return ""


def govern_registry(
    registry: ToolRegistry,
    kernel: TrustKernel,
    *,
    approve: ApproveFn | None = None,
    context: ContextFn | None = None,
) -> ToolRegistry:
    """Return a new registry with every tool wrapped in a :class:`GovernedTool`."""
    governed = ToolRegistry()
    for tool in registry.tools():
        governed.register(GovernedTool(tool, kernel, approve=approve, context=context))
    return governed
