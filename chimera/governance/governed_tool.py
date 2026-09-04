"""Run tools through the trust kernel.

``GovernedTool`` wraps any :class:`~chimera.tools.base.Tool` so its ``run`` is gated
by the kernel: BLOCK refuses, REVIEW requires approval, WARN/ALLOW proceed. Because
it *is* a Tool, a registry of governed tools drops straight into the existing agent
loop with no other changes.

**A BLOCK never reaches the approver, and that is deliberate — but it used to be invisible too.**
The approver is consulted only on REVIEW, so `observe` (whose approver says yes to everything)
measured the REVIEWs and silently *applied* the BLOCKs. Measured on a 33-call corpus of real tool
calls, on the tree that fixed the newline defect (PR #122):

    allow 20 · warn 2 · review 3 · block 8
    refused in observe: 8   ->   approvals.granted=3  refused=0

Eight refusals, none of them in the report the rollout reads. Both halves are wrong in the same
direction: the count under-states what enforcement costs, and the mode's documentation said it
"refuses none of them". The refusal itself stays — see :mod:`chimera.governance.profile` for why a
fixed signature is not the thing `observe` stages — but it is now recorded like every other one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chimera.governance.approval import ApprovalLedger
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
#:
#: The criterion, so the next person extends it by rule instead of by anecdote: a **body** is text
#: the tool writes to disk, executes, or transmits whole — the kind of value that can carry an
#: entire secret. An **identifier** says *which* thing is being acted on (`path`, `url`, `ref`) or
#: *what to look for* (`query`, `pattern`, `prompt`); those stay, because they are the half of an
#: audit line worth reading. `tests/test_document_args_match_the_tools.py` holds every argument the
#: shipped tools declare, on one side of that line or the other, and fails when a new one appears.
#:
#: This list had drifted from the tools it is supposed to describe. Measured against every `Tool`
#: subclass in the package: it named five arguments **no tool has** (`data`, `diff`, `new_str`,
#: `old_str`, `new_text`) and missed four that carry bodies — `old`/`new` (`edit_file`), `code`
#: (`execute_code`, `code_interpreter`) and `spec` (`render_chart`). The dead names are deleted
#: rather than kept "for future tools": a name with no tool behind it cannot be tested, gives false
#: assurance that `diff` is covered, and costs real safety the day some tool uses `data` as an
#: identifier — it would drop out of the command scope and out of the audit, silently. The
#: exhaustiveness test is what covers the future case, and it does it by forcing a decision.
_DOCUMENT_ARGS = frozenset(
    {
        "content",  # write_file: the file body. extract: the page text to pull fields from.
        "patch",  # apply_patch: SEARCH/REPLACE hunks — both halves are file content.
        "old",  # edit_file: the text being removed (it was in the file, so it is file content).
        "new",  # edit_file: the replacement text.
        "code",  # execute_code, code_interpreter — but see _EXECUTED_DOCUMENT_ARGS.
        "body",  # send_email: the message body.
        "text",  # echo; browser(action=type) — a password is typed through this one; text_to_speech.
        "spec",  # render_chart: a Vega-Lite document, whose `data.values` can embed a whole dataset.
        # mcp_call: the argument object, transmitted whole to a server this project did not write.
        #
        # A body and NOT a nested one, deliberately. `_NESTED_DOCUMENT_ARGS` walks in and elides the
        # keys it recognises — which works for `edit_batch`, whose key names are ours. An MCP server
        # names its own parameters, so nothing here would match `api_token` or `password`, and
        # walking in would print them. The tool name stays as the identifier, so the audit line
        # still says WHAT was called; only the payload becomes a character count.
        "arguments",
        # todo_write: the whole task list. A body rather than an identifier, on both counts the
        # list is read for. The identity of the call is "recorded a task list"; the text is prose
        # the model wrote, of no fixed length, and a step reading "delete the old keys from
        # config/secrets.env" would be judged by shell rules for words that describe an intention
        # rather than issue a command. It is NOT nested: the keys inside are ours (`task`,
        # `status`), but neither is a secret-shaped name, so walking in would print the same
        # prose the elision exists to keep out of a log the app serves over HTTP.
        "items",
    }
)

#: The subset of :data:`_DOCUMENT_ARGS` that the tool **executes**.
#:
#: This is the one place the two consumers of the list must disagree, so they are given two lists
#: instead of one. `execute_code(code=...)` is a body — the whole program lands in the audit line
#: otherwise, truncated at 200 characters and served over HTTP by `GET /api/governance/audit`. But
#: it is a body that then *runs*, so taking it out of the command half would stop every shell rule
#: from reading it: `code="import os; os.system('rm -rf /')"` is BLOCK today (rule `rm_rf_root`) and
#: would become ALLOW. Eliding it from the log is a privacy fix; moving it out of the command scope
#: would be a hole. One list could only do one of the two.
_EXECUTED_DOCUMENT_ARGS = frozenset({"code", "command"})

#: Arguments that do not *hold* a body but *contain* ones, in nested items.
#:
#: `edit_batch(edits=[{"path": …, "old": …, "new": …}, …])` is the only one today, and it is the
#: reason this is a third set rather than a third entry in the first. Treating `edits` as a plain
#: document elides the whole array, and with it every `path` — on the one tool that writes to
#: SEVERAL files at once, which is exactly when "which file was touched" is worth the most. The
#: audit therefore walks into these and elides only the nested bodies, while the rules treat the
#: whole thing as a document (no nested field is a command, and `secret_material`, being
#: `Scope.ANY_TEXT`, reads it either way).
_NESTED_DOCUMENT_ARGS = frozenset({"edits"})


def _summarise(value: Any) -> str:
    return f"<{len(str(value))} chars>"


def _elide_nested(value: Any) -> Any:
    """Elide bodies inside a container, keeping the identifiers that say what was touched."""
    if isinstance(value, list):
        return [_elide_nested(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (_summarise(inner) if key in _DOCUMENT_ARGS else inner)
            for key, inner in value.items()
        }
    return value


def elide_values(kwargs: dict[str, Any]) -> dict[str, Any]:
    """``kwargs`` with document-shaped values replaced by their size."""
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in _NESTED_DOCUMENT_ARGS:
            out[key] = _elide_nested(value)
        elif key in _DOCUMENT_ARGS:
            out[key] = _summarise(value)
        else:
            out[key] = value
    return out


def _is_document(key: str) -> bool:
    """True if ``key``'s value must be judged as prose rather than as a command.

    Bodies the tool executes are the exception: they are documents for the audit and commands for
    the rules. See :data:`_EXECUTED_DOCUMENT_ARGS`.
    """
    return (
        key in _DOCUMENT_ARGS or key in _NESTED_DOCUMENT_ARGS
    ) and key not in _EXECUTED_DOCUMENT_ARGS


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

    With ONE deliberate exception, :data:`_EXECUTED_DOCUMENT_ARGS`: a body the tool *executes* stays
    in the command half. Sharing a single list would have forced a choice between leaking
    `execute_code`'s program into the audit and letting `os.system('rm -rf /')` through the shell
    rules; the two consumers want opposite things about that one argument, so they get two lists and
    this paragraph saying why they differ.

    The tool NAME leads the action. That was checked rather than assumed: all ten default rules are
    shell or credential signatures and not one of them mentions a tool, so putting the name on its
    own line costs no match today and keeps the judge told what was called.
    """
    command = "\n".join(
        [name, *(str(value) for key, value in kwargs.items() if not _is_document(key))]
    )
    document = "\n".join(
        str(value) for key, value in kwargs.items() if _is_document(key)
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
        ledger: ApprovalLedger | None = None,
        no_approver: str = "",
    ) -> None:
        self.inner = inner
        self.kernel = kernel
        self.approve = approve
        # WHY no approver can say yes here, or "" when one genuinely can. Two situations produce the
        # same refusal and need different sentences: an approver was asked and declined, and no
        # approver could be asked at all. Only the assembly knows which — `profile.py` is where the
        # unattended surface turns `ask` into `deny` — so it is passed in rather than guessed.
        #
        # Measured, and this is what it cost: a run reading a data catalogue over MCP tainted itself,
        # every write then needed approval, and on an HTTP surface nothing can approve. The agent was
        # told "nobody approved it", retried, spent its whole budget, and reported the environment as
        # broken. Four runs, US$ 5.11, nothing written. The identical task without the MCP delivered
        # in 236 seconds for US$ 0.37. The refusal was correct every time and named none of it.
        self.no_approver = no_approver
        # Where a BLOCK gets written down. Deliberately NOT the approver: `observe`'s approver says
        # yes to everything, so routing a BLOCK through it would either record a grant that did not
        # happen or turn `rm -rf /` into an execution. What a BLOCK needs is the ledger's `record`,
        # not its `approve` — the refusal is not a question.
        self.ledger = ledger
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
            # Says WHOSE decision it was. The old text named only the reason, so an operator reading
            # this line under `observe` — a mode documented as refusing nothing — had every reason to
            # conclude the mode had started refusing. It had not: a BLOCK is a fixed signature and it
            # is applied in every mode that installs the kernel at all. Telling the agent the same
            # thing is the other half: there is no approver behind this one, so retrying with softer
            # wording is a loop, not a path.
            # Written down, and this is the line the whole change exists for. `record` rather than
            # `approve`: the approver in `observe` says yes to everything, so routing a BLOCK
            # through it would either log a grant that never happened or execute `rm -rf /`. A
            # refusal is not a question. Measured before this line existed, over 33 real tool calls
            # under `observe`: eight refusals, `refused=0`.
            if self.ledger is not None:
                self.ledger.record(action, approved=False)
            return refusal(f"[governance: BLOCKED — {verdict.reason}] "
                           f"The tool did NOT run. A fixed signature refused it, not the governance "
                           f"mode: no approver can release it. Do not report this as done.")
        if verdict.decision == Decision.REVIEW:
            approved = self.approve(verdict, action) if self.approve else False
            if not approved:
                return refusal(f"[governance: needs review — {verdict.reason}] "
                               f"The tool did NOT run. {self._why_nobody_approved()} Do not "
                               f"report this as done.")
        return self.inner.run(**kwargs)

    def _why_nobody_approved(self) -> str:
        """The sentence after "the tool did NOT run" — different per reason, because the fixes are.

        "Nobody approved it" is true in all three cases and actionable in none. Retrying is the only
        thing it suggests, and retrying is the one thing that cannot work when no approver exists:
        the answer is structurally the same every time, so a three-attempt budget buys three
        identical refusals and a bill.
        """
        if self.no_approver == "unattended":
            return (
                "Nobody could be asked: this request arrived over the API, where the only terminal "
                "belongs to somebody who did not make it and cannot consent for whoever did. "
                "Retrying will be refused identically. To let this through, start the run with "
                "pause-on-taint — in the app, 'pause for my approval if the run reads untrusted "
                "content' — which parks it for a verdict instead of refusing it. Or keep untrusted "
                "content out of the run: reading data through an MCP server taints it, and the same "
                "read done with the built-in tools does not."
            )
        if self.no_approver == "unreachable":
            return (
                "Nobody could be asked: this run has no console, and this deployment has not "
                "said where an approval question should go. Retrying will be refused "
                "identically. Setting CHIMERA_APPROVAL_WEBHOOK to a channel webhook lets the "
                "question be sent and answered with `chimera approve <id> --yes`; until then a "
                "review on this surface is a refusal."
            )
        if self.no_approver == "owner_denies":
            return (
                "Nobody was asked: this deployment sets approvals to deny, so a review is a refusal "
                "by configuration. Retrying will be refused identically."
            )
        return "Nobody approved it."

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
    ledger: ApprovalLedger | None = None,
    no_approver: str = "",
) -> ToolRegistry:
    """Return a new registry with every tool wrapped in a :class:`GovernedTool`.

    ``ledger`` is shared by every wrapper on purpose: the report is per RUN, not per tool, and one
    ledger per tool would answer "how much was this run refused" with as many numbers as there are
    tools in the registry.
    """
    governed = ToolRegistry()
    for tool in registry.tools():
        governed.register(
            GovernedTool(
                tool, kernel, approve=approve, context=context, ledger=ledger,
                no_approver=no_approver,
            )
        )
    return governed
