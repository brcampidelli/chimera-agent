"""Owner lifecycle hooks: run around every tool call, and able only to tighten.

Owner's decision of 2026-10-05 (study 30, S30-65). The design is the one in
`docs/hooks-threat-model.md`, written and committed before this module; each class it names is
pointed at below where the code contains it. The short version:

* One file, ``<CHIMERA_HOME>/chimera-hooks.json``, written by the owner with an editor and read only
  when ``CHIMERA_HOOKS`` is on. It lives in the data folder, which no agent tool and no app route may
  write (A1); the shell fence refuses commands that name it (`queue_fence.reaches_hooks_file`).
* A hook answers ``deny``, ``ask`` or ``annotate``, or nothing. There is no ``allow`` to answer and
  no way to change the call's arguments: the composition below has no value that widens (A3).
* A shell hook runs through the sandbox, never on the host unless the owner set
  ``CHIMERA_HOOKS_HOST_EXEC`` (A2); its command is run as written, with the event in a file beside
  it rather than in the string (A5); what it says to the model is fenced and taints the run (A4).
* Every invocation writes a ``hook`` receipt to the audit log (A7), and a hook that fails refuses the
  call unless its ``on_error`` says ``ignore`` (A8).

Where it sits: :func:`hook_registry` wraps the registry the trust kernel produced, and the taint
ledger wraps the result — so a hook sees a call before the kernel judges it, may stop it, and cannot
release anything the kernel or the ledger would stop.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.governance.approval import asks_nobody
from chimera.governance.audit import AuditLog
from chimera.governance.claude_hooks import (
    CLAUDE,
    ClaudeLifecycle,
    claude_tool_name,
    discover,
    narrowed,
    new_session_id,
    run_batch,
    tool_input,
    trust_state,
)
from chimera.governance.governed_tool import elide_values, render_action
from chimera.governance.ledger_tool import fence
from chimera.governance.policy import Decision, Verdict
from chimera.governance.proxy import see_through
from chimera.governance.sanitize import sanitize_untrusted
from chimera.telemetry import get_logger
from chimera.tools.base import Refusal, Tool, is_untrusted_output, refusal
from chimera.tools.registry import ToolRegistry

_log = get_logger("governance.hooks")

#: The one file hooks are read from. Named, not just ``hooks.json``, so the shell fence can refuse a
#: command that mentions it without refusing every repository that has a ``hooks.json`` of its own.
HOOKS_FILE_NAME = "chimera-hooks.json"
CLAUDE_PROTOCOL = CLAUDE

PRE_TOOL = "pre_tool"
POST_TOOL = "post_tool"
EVENTS = (PRE_TOOL, POST_TOOL)

#: The answers a hook may give, weakest first. The strictest among the hooks that matched wins. The
#: tuple IS the containment of A3: there is no member that widens, so no composition can.
NONE, ANNOTATE, ASK, DENY = "none", "annotate", "ask", "deny"
_STRENGTH = {NONE: 0, ANNOTATE: 1, ASK: 2, DENY: 3}
#: Which answers each event can apply. After the call there is nothing left to ask about: the tool
#: ran. ``deny`` there withholds the output from the model, which still only narrows what it sees.
_HONOURED = {PRE_TOOL: frozenset({DENY, ASK, ANNOTATE}), POST_TOOL: frozenset({DENY, ANNOTATE})}

#: The keys a shell hook's JSON answer may carry. Anything else — ``arguments``, ``updated_input``,
#: ``permission``, ``continue`` — is how other harnesses let a hook rewrite a call or wave it
#: through, and is listed in the receipt as refused instead of being read.
_ANSWER_KEYS = frozenset({"decision", "reason", "note"})
#: The keys an entry in the file may carry. Strict on purpose: an owner who writes ``"allow": true``
#: must be told it does nothing, not left believing it does.
_ENTRY_KEYS = frozenset(
    {"id", "event", "tools", "pattern", "decision", "reason", "note", "command", "timeout",
     "on_error"}
)
DEFAULT_TIMEOUT = 10
MAX_TIMEOUT = 60
_ERROR_POLICIES = frozenset({"deny", "ignore"})


def hooks_file(home: Path) -> Path:
    """Where the owner's hooks live: inside the data folder, which no agent tool may write."""
    return Path(home).expanduser() / HOOKS_FILE_NAME


@dataclass(frozen=True)
class Hook:
    """One entry of the hooks file."""

    id: str
    event: str
    tools: frozenset[str] | None  # None: every tool
    pattern: re.Pattern[str] | None
    decision: str = ""  # a static hook's answer; "" for a shell hook
    reason: str = ""
    note: str = ""
    command: str = ""
    timeout: int = DEFAULT_TIMEOUT
    on_error: str = "deny"
    #: Where the hook came from: ``owner`` (chimera-hooks.json), or ``repo`` / ``user`` for a hook
    #: read from a Claude Code settings file (`chimera/governance/claude_hooks.py`).
    origin: str = "owner"
    protocol: str = "chimera"  # "claude": JSON on stdin, exit 2 blocks, permissionDecision
    matcher: re.Pattern[str] | None = None  # a Claude Code matcher over the tool's name
    claude_event: str = ""  # PreToolUse, Stop, ... for a Claude-format hook
    source: str = ""  # the settings file it was read from
    source_sha256: str = ""

    @property
    def kind(self) -> str:
        return "shell" if self.command else "static"

    def matches(self, tool: str, text: str) -> bool:
        if self.tools is not None and tool not in self.tools:
            return False
        if self.matcher is not None:
            from chimera.governance.claude_hooks import matcher_hits

            if not matcher_hits(self.matcher, tool):
                return False
        return self.pattern is None or bool(self.pattern.search(text))


@dataclass(frozen=True)
class HookConfig:
    """What was read from the hooks file: the hooks, the file's digest, or why it was refused."""

    hooks: tuple[Hook, ...] = ()
    sha256: str = ""
    error: str = ""
    path: str = ""
    #: Every Claude-format settings file looked at, switched on or not (the Settings screen lists them).
    sources: tuple[Any, ...] = ()


class HookConfigError(ValueError):
    """The hooks file says something this module will not do."""


def _parse_entry(raw: Any, index: int) -> Hook:
    if not isinstance(raw, dict):
        raise HookConfigError(f"hook #{index} is not an object")
    unknown = sorted(set(raw) - _ENTRY_KEYS)
    if unknown:
        raise HookConfigError(
            f"hook #{index} has keys hooks do not read: {', '.join(unknown)} (hooks can only deny, "
            "ask or annotate; there is no allow and no way to change a call's arguments)"
        )
    hook_id = str(raw.get("id") or f"hook-{index}").strip()
    event = str(raw.get("event") or "").strip()
    if event not in EVENTS:
        raise HookConfigError(f"hook {hook_id!r}: event must be one of {', '.join(EVENTS)}")
    tools_raw = raw.get("tools", ["*"])
    if isinstance(tools_raw, str):
        tools_raw = [tools_raw]
    if not isinstance(tools_raw, list) or not all(isinstance(t, str) for t in tools_raw):
        raise HookConfigError(f"hook {hook_id!r}: tools must be a list of tool names")
    tools = None if "*" in tools_raw else frozenset(t.strip() for t in tools_raw if t.strip())
    pattern = None
    if raw.get("pattern"):
        try:
            pattern = re.compile(str(raw["pattern"]), re.IGNORECASE)
        except re.error as exc:
            raise HookConfigError(
                f"hook {hook_id!r}: pattern is not a regular expression: {exc}"
            ) from exc
    command = str(raw.get("command") or "").strip()
    decision = str(raw.get("decision") or "").strip().lower()
    if bool(command) == bool(decision):
        raise HookConfigError(f"hook {hook_id!r}: give exactly one of `command` or `decision`")
    if decision and decision not in _HONOURED[event]:
        raise HookConfigError(
            f"hook {hook_id!r}: a {event} hook can answer {', '.join(sorted(_HONOURED[event]))}, "
            f"not {decision!r} — hooks can only tighten"
        )
    try:
        timeout = int(raw.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        raise HookConfigError(
            f"hook {hook_id!r}: timeout must be a whole number of seconds"
        ) from None
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise HookConfigError(f"hook {hook_id!r}: timeout must be between 1 and {MAX_TIMEOUT}")
    on_error = str(raw.get("on_error") or "deny").strip().lower()
    if on_error not in _ERROR_POLICIES:
        raise HookConfigError(f"hook {hook_id!r}: on_error must be deny or ignore")
    return Hook(
        id=hook_id, event=event, tools=tools, pattern=pattern, decision=decision,
        reason=str(raw.get("reason") or ""), note=str(raw.get("note") or ""), command=command,
        timeout=timeout, on_error=on_error,
    )


def load_hooks(home: Path) -> HookConfig:
    """Read the owner's hooks file. Never raises: a broken file comes back with ``error`` set, and
    the wrapper refuses every call with it rather than running without the hooks the owner believes
    are there (A7)."""
    path = hooks_file(home)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return HookConfig(path=str(path))
    except OSError as exc:
        return HookConfig(error=f"could not read {path}: {exc}", path=str(path))
    digest = hashlib.sha256(data).hexdigest()
    try:
        parsed = json.loads(data.decode("utf-8"))
        entries = parsed.get("hooks") if isinstance(parsed, dict) else None
        if not isinstance(entries, list):
            raise HookConfigError('the file must be an object with a "hooks" list')
        hooks = tuple(_parse_entry(raw, i) for i, raw in enumerate(entries, start=1))
    except (HookConfigError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return HookConfig(sha256=digest, error=f"{path}: {exc}", path=str(path))
    return HookConfig(hooks=hooks, sha256=digest, path=str(path))


@dataclass
class _Said:
    """A sentence a hook wants the model to read, and whether it came out of a command."""

    hook: str
    text: str
    from_command: bool


@dataclass
class HookOutcome:
    """The composed answer of every hook that matched one event of one call."""

    decision: str = NONE
    deciding_hook: str = ""
    reasons: list[_Said] = field(default_factory=list)
    notes: list[_Said] = field(default_factory=list)
    #: A Claude-format PreToolUse hook's ``updatedInput``, kept only if it narrows the call.
    updated_input: dict[str, Any] | None = None
    updated_by: str = ""
    #: Where the deciding hook came from (``owner`` / ``repo`` / ``user``) and its file, so the card
    #: that puts an ``ask`` to a person can say whose question it is.
    deciding_origin: str = "owner"
    deciding_source: str = ""

    def take(self, decision: str, hook: str, source: Any = None) -> None:
        if _STRENGTH[decision] > _STRENGTH[self.decision]:
            self.decision, self.deciding_hook = decision, hook
            if source is not None:
                self.deciding_origin = getattr(source, "origin", "owner")
                self.deciding_source = getattr(source, "source", "")


#: Starts a sandbox for one hook run. A seam for the tests; production passes ``get_sandbox``.
SandboxFactory = Callable[[], Any]
#: Records untrusted text into the run's taint ledger: ``TaintLedger.record_fetch(source, content)``.
TaintSink = Callable[[str, str], object]
#: The owner's approver for a hook's ``ask``: the kernel's signature, ``(Verdict, action) -> bool``.
HookApprover = Callable[[Verdict, str], bool]


def _sandbox_name(sandbox: Any) -> str:
    return type(sandbox).__name__.removesuffix("Sandbox").lower() or "unknown"


class HookRunner:
    """Runs the hooks of one assembly and writes a receipt for every invocation."""

    def __init__(
        self,
        config: HookConfig,
        *,
        sandbox: SandboxFactory,
        host_exec: bool = False,
        audit: AuditLog | None = None,
        taint: TaintSink | None = None,
        workspace: Path | None = None,
        user_host_exec: bool = False,
    ) -> None:
        self.config = config
        self._sandbox = sandbox
        self.host_exec = host_exec
        #: ``CHIMERA_CLAUDE_USER_HOOKS_HOST_EXEC``: the user's own Claude-format hooks on the host.
        #: A repository's never run there, whatever either switch says.
        self.user_host_exec = user_host_exec
        self.audit = audit
        self.taint = taint
        #: The project a Claude-format hook is told about (payload ``cwd``, ``CLAUDE_PROJECT_DIR``).
        self.workspace = Path(workspace) if workspace is not None else Path.cwd()
        self.session_id = new_session_id()
        #: Every receipt this runner wrote, newest last — the same payloads the audit log received.
        self.receipts: list[dict[str, Any]] = []

    def run(
        self, event: str, tool: str, args: dict[str, Any], *, result: str | None = None
    ) -> HookOutcome:
        outcome = HookOutcome()
        command, document = render_action(tool, args)
        text = f"{command}\n{document}" if document else command
        claude: list[Hook] = []
        for hook in self.config.hooks:
            if hook.event != event or not hook.matches(tool, text):
                continue
            if hook.protocol == CLAUDE_PROTOCOL:
                claude.append(hook)
            elif hook.command:
                self._run_shell(hook, event, tool, args, result, outcome)
            else:
                self._run_static(hook, event, tool, outcome)
        if claude:
            payload: dict[str, Any] = {
                "cwd": str(self.workspace), "tool_name": claude_tool_name(tool),
                "tool_input": tool_input(args),
            }
            if result is not None:
                # The size and whether it failed, never the text (A6), as for the owner's hooks.
                payload["tool_response"] = {"chars": len(result), "failed": result.startswith("error:")}
            run_batch(self, claude, event, outcome, payload, tool=tool)
        return outcome

    def receipt_for(
        self, hook: Hook, event: str, tool: str, done: Any, *, requested: str, applied: str,
        refused: list[str], error: str,
    ) -> None:
        """The receipt of one Claude-format invocation (same shape as the owner's, plus the origin)."""
        self._receipt(
            hook, event, tool, ran=done.ran, requested=requested, applied=applied,
            sandbox=done.sandbox, isolated=done.isolated, exit_code=done.exit_code,
            duration_ms=done.duration_ms, refused=refused, error=error,
        )
    def _run_static(self, hook: Hook, event: str, tool: str, outcome: HookOutcome) -> None:
        self._apply(hook, hook.decision, hook.reason, hook.note, outcome, from_command=False)
        self._receipt(
            hook, event, tool, ran=True, requested=hook.decision, applied=hook.decision,
            sandbox="", isolated=None, exit_code=None, duration_ms=0.0, refused=[],
        )

    def _apply(
        self, hook: Hook, decision: str, reason: str, note: str, outcome: HookOutcome,
        *, from_command: bool,
    ) -> None:
        outcome.take(decision, hook.id)
        if decision in (DENY, ASK) and reason:
            outcome.reasons.append(_Said(hook.id, reason, from_command))
        if decision == ANNOTATE and (note or reason):
            outcome.notes.append(_Said(hook.id, note or reason, from_command))

    def _run_shell(
        self, hook: Hook, event: str, tool: str, args: dict[str, Any], result: str | None,
        outcome: HookOutcome,
    ) -> None:
        from chimera.sandbox.confirm import sandbox_is_isolated

        sandbox = self._sandbox()
        isolated = sandbox_is_isolated(sandbox)
        name = _sandbox_name(sandbox)
        if not isolated and not self.host_exec:
            # A2: no sandbox that isolates, and the owner has not accepted host execution. Refused,
            # and the call with it: skipping a guard the owner configured is the widening ruled out.
            outcome.take(DENY, hook.id)
            outcome.reasons.append(_Said(
                hook.id,
                "this machine has no sandbox that isolates, and shell hooks do not run on the host "
                "unless the owner sets CHIMERA_HOOKS_HOST_EXEC",
                False,
            ))
            self._receipt(
                hook, event, tool, ran=False, requested="", applied=DENY, sandbox=name,
                isolated=False, exit_code=None, duration_ms=0.0, refused=[],
                error="no isolating sandbox; host execution not accepted",
            )
            return
        payload: dict[str, Any] = {"event": event, "tool": tool, "arguments": elide_values(args)}
        if result is not None:
            # The size and whether it failed, never the text: a hook that needs the output is a hook
            # that receives whatever the tool read, secrets included (A6).
            payload["result"] = {"chars": len(result), "failed": result.startswith("error:")}
        started = time.perf_counter()
        error = ""
        exit_code: int | None = None
        requested = ""
        refused: list[str] = []
        decision, reason, note = NONE, "", ""
        with tempfile.TemporaryDirectory(prefix="chimera-hook-") as tmp:
            # A5: the event goes in a file, never into the command string.
            Path(tmp, "event.json").write_text(json.dumps(payload, default=str), encoding="utf-8")
            try:
                ran = sandbox.run(hook.command, timeout=hook.timeout, cwd=Path(tmp))
                exit_code = int(ran.exit_code)
                if ran.timed_out:
                    error = f"timed out after {hook.timeout}s"
                elif exit_code != 0:
                    error = f"exited {exit_code}"
                else:
                    decision, reason, note, requested, refused, error = _read_answer(
                        ran.stdout or "", event
                    )
            except Exception as exc:  # a hook that cannot start is a failed hook
                error = f"could not run: {type(exc).__name__}: {exc}"
        duration_ms = (time.perf_counter() - started) * 1000
        if error:
            # A8: a failed hook refuses the call unless the owner wrote `on_error: ignore`.
            decision = DENY if hook.on_error == "deny" else NONE
            reason = f"the hook failed ({error})" if decision == DENY else ""
            note = ""
            self._apply(hook, decision, reason, note, outcome, from_command=False)
        else:
            self._apply(hook, decision, reason, note, outcome, from_command=True)
        self._receipt(
            hook, event, tool, ran=True, requested=requested, applied=decision, sandbox=name,
            isolated=isolated, exit_code=exit_code, duration_ms=duration_ms, refused=refused,
            error=error,
        )

    def update_receipt(self, hook_id: str, tool: str, *, applied: bool) -> None:
        """Whether a hook's ``updatedInput`` was applied (it narrowed the call) or ignored."""
        receipt: dict[str, Any] = {
            "hook": hook_id, "event": "updated_input", "tool": tool, "applied": applied,
            "refused": [] if applied else ["updatedInput: not provably narrower; ignored"],
            "config_sha256": self.config.sha256,
        }
        self.receipts.append(receipt)
        if self.audit is not None:
            try:
                self.audit.record("hook", receipt)
            except Exception:
                _log.warning("could not write the updatedInput receipt for %s", hook_id, exc_info=True)

    def answered(self, hook_id: str, tool: str, *, approved: bool, approver: str) -> None:
        """The receipt for the ANSWER to a hook's ``ask``, written after the question was put.

        The invocation's own receipt says ``applied: ask`` and is written before anybody answers,
        so on its own the audit could not tell a question the owner approved from one nobody read:
        a `git push` that passed a hook's `ask` left the same line either way. This is the second
        line, with who answered and what they said.
        """
        receipt: dict[str, Any] = {
            "hook": hook_id,
            "event": "ask",
            "tool": tool,
            "approved": approved,
            "approver": approver,
            "config_sha256": self.config.sha256,
        }
        self.receipts.append(receipt)
        if self.audit is not None:
            try:
                self.audit.record("hook", receipt)
            except Exception:  # the in-memory receipt stands; log and go on
                _log.warning("could not write the answer receipt for %s", hook_id, exc_info=True)

    def _receipt(
        self, hook: Hook, event: str, tool: str, *, ran: bool, requested: str, applied: str,
        sandbox: str, isolated: bool | None, exit_code: int | None, duration_ms: float,
        refused: list[str], error: str = "",
    ) -> None:
        receipt: dict[str, Any] = {
            "hook": hook.id,
            "event": event,
            "tool": tool,
            "kind": hook.kind,
            "ran": ran,
            "requested": requested,
            "applied": applied,
            "refused": refused,
            "sandbox": sandbox,
            "isolated": isolated,
            "exit_code": exit_code,
            "duration_ms": round(duration_ms, 3),
            "error": error,
            "config_sha256": self.config.sha256,
        }
        if hook.protocol == CLAUDE_PROTOCOL:
            # Where a third-party hook came from, and the exact bytes of the file it was read from.
            receipt.update(origin=hook.origin, claude_event=hook.claude_event, source=hook.source,
                           source_sha256=hook.source_sha256)
        self.receipts.append(receipt)
        if self.audit is not None:
            try:
                self.audit.record("hook", receipt)
            except Exception:  # the in-memory receipt stands; log and go on
                _log.warning("could not write the hook receipt for %s", hook.id, exc_info=True)


def _read_answer(stdout: str, event: str) -> tuple[str, str, str, str, list[str], str]:
    """``(decision, reason, note, requested, refused, error)`` from a shell hook's stdout.

    Empty output is "nothing to say". Anything else must be one JSON object. A3 lives here: the
    decision is read by name from the event's honoured set, and every other key or decision is
    returned in ``refused`` instead of being read.
    """
    text = stdout.strip()
    if not text:
        return NONE, "", "", "", [], ""
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        return NONE, "", "", "", [], "printed something that is not a JSON object"
    if not isinstance(answer, dict):
        return NONE, "", "", "", [], "printed JSON that is not an object"
    refused = sorted(f"key:{k}" for k in set(answer) - _ANSWER_KEYS)
    requested = str(answer.get("decision") or "").strip().lower()
    decision = requested if requested in _HONOURED[event] else NONE
    if requested and decision == NONE and requested != NONE:
        refused.append(f"decision:{requested}")
    return (
        decision, str(answer.get("reason") or ""), str(answer.get("note") or ""), requested,
        refused, "",
    )


def _spoken(said: Iterable[_Said], taint: TaintSink | None) -> str:
    """What the hooks said, as the model will read it: fenced, and tainting when a command said it."""
    parts: list[str] = []
    for item in said:
        if item.from_command and taint is not None:
            # A4: a command's words were computed from data; the run has now read untrusted text.
            taint(f"hook:{item.hook}", item.text)
        parts.append(f"[hook {item.hook}]\n{fence(sanitize_untrusted(item.text))}")
    return "\n".join(parts)


class HookedTool(Tool):
    """A tool with the owner's hooks around it. Can refuse, ask, or add a note; never more."""

    def __init__(
        self, inner: Tool, runner: HookRunner, *, approve: HookApprover | None = None,
        approver_name: str = "",
    ) -> None:
        self.inner = inner
        self.runner = runner
        self.approve = approve
        #: What the answer receipt calls the approver; the assembly knows its name, this does not.
        self.approver_name = approver_name
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters
        self.untrusted_output = is_untrusted_output(inner)

    def run(self, **kwargs: Any) -> str:
        config = self.runner.config
        if config.error:
            return refusal(
                f"[hooks: the tool did NOT run] Hooks are on and the hooks file cannot be used: "
                f"{config.error}. Every tool call is refused until the owner fixes the file or "
                "switches hooks off; retrying will be refused identically."
            )
        judged, judged_args = see_through(self.name, kwargs)
        args = dict(judged_args)
        pre = self.runner.run(PRE_TOOL, judged, args)
        if pre.decision == DENY:
            return self._refused(pre, "denied it")
        if pre.decision == ASK:
            action = render_action(judged, args)[0]
            verdict = Verdict(
                Decision.REVIEW,
                reason=_ask_reason(pre),
                rule=f"hook:{pre.deciding_hook}",
            )
            approved = False
            if self.approve is not None and asks_nobody(self.approve):
                # `CHIMERA_APPROVAL_MODE=allow` answers every question yes without anybody reading
                # it. For the kernel's REVIEW that is the owner's chosen trade; for a question the
                # owner wrote into their own hooks file it would make `ask` an allow, on every mode
                # and every unattended surface, with a receipt that said only `applied: ask`. The
                # hook's question needs an approver who can say no, so with none it is a refusal:
                # the same answer `ask` gets when nobody can be reached.
                self.runner.answered(pre.deciding_hook, judged, approved=False, approver="allow")
                return self._refused(
                    pre,
                    "asked, and the owner's approval mode is `allow`, which answers every question "
                    "yes without asking anyone; a hook's question is refused until approvals are "
                    "set to `ask` or the hook says `deny`",
                )
            if self.approve is not None:
                try:
                    approved = bool(self.approve(verdict, action))
                except Exception:  # an approver that fails has not approved
                    approved = False
            self.runner.answered(
                pre.deciding_hook, judged, approved=approved,
                approver=(self.approver_name or "owner") if self.approve is not None else "none",
            )
            if not approved:
                return self._refused(pre, "asked, and nobody approved it")
        # The model's own arguments (A3) — except a Claude-format hook's `updatedInput`, applied only
        # when it provably narrows the call (a list cut down to some of its own items) and the call
        # was not seen through a proxy. In any doubt it is ignored, and the receipt says which.
        call = kwargs
        if pre.updated_input is not None:
            fit = narrowed(judged, kwargs, pre.updated_input) if judged == self.name else None
            self.runner.update_receipt(pre.updated_by, judged, applied=fit is not None)
            if fit is not None:
                call = fit
        result = self.inner.run(**call)
        if isinstance(result, Refusal):
            # The tool did not run; there is no output to look at after it.
            return result
        post = self.runner.run(POST_TOOL, judged, args, result=str(result))
        if post.decision == DENY:
            withheld = (
                f"[hooks: the tool ran, and the owner's hook {post.deciding_hook} withheld its "
                "output from the model.]"
            )
            said = _spoken(post.reasons, self.runner.taint)
            return f"{withheld}\n{said}" if said else withheld
        notes = _spoken([*pre.notes, *post.notes], self.runner.taint)
        return f"{result}\n\n{notes}" if notes else result

    def _refused(self, outcome: HookOutcome, what: str) -> str:
        head = (
            f"[hooks: the tool did NOT run — the owner's hook {outcome.deciding_hook} {what}.] "
            "Do not report this as done."
        )
        said = _spoken(outcome.reasons, self.runner.taint)
        # A Refusal by type either way, so the loop counts a call that did not run. The hook's own
        # words follow inside the data fence: `refusal` lets its text out of the ledger's fence, so
        # everything a command computed is fenced here, before it is joined to our sentence.
        return refusal(f"{head}\n{said}" if said else head)


def _ask_reason(outcome: HookOutcome) -> str:
    """Whose question a hook's ``ask`` is, for the card: a repository's hook says so and names its file."""
    if outcome.deciding_origin == "repo":
        where = Path(outcome.deciding_source).name or "settings.json"
        return (f"a project hook (.claude/{where}, third-party code) asks before this call: "
                f"{outcome.deciding_hook}")
    if outcome.deciding_origin == "user":
        return f"your hook from ~/.claude/settings.json asks before this call: {outcome.deciding_hook}"
    return f"the owner's hook {outcome.deciding_hook} asks before this call"


def hook_registry(
    registry: ToolRegistry,
    runner: HookRunner,
    *,
    approve: HookApprover | None = None,
    approver_name: str = "",
) -> ToolRegistry:
    """Every tool of ``registry`` with the owner's hooks around it."""
    hooked = ToolRegistry.like(registry)
    for tool in registry.tools():
        hooked.register(HookedTool(tool, runner, approve=approve, approver_name=approver_name))
    if any(h.protocol == CLAUDE_PROTOCOL for h in runner.config.hooks):
        hooked.lifecycle = ClaudeLifecycle(runner)
    return hooked


def hook_sandbox(settings: Any) -> Any:
    """The sandbox a hook runs in: the agent's own, EXCEPT that Docker gets no network even where the
    agent's sandbox is configured with one (``CHIMERA_SANDBOX_NETWORK=bridge``). The OS sandboxes
    already have none. A hook sees every call, so a hook with a network is a tap (A6)."""
    from chimera.sandbox import get_sandbox

    box = get_sandbox(settings)
    if hasattr(box, "network"):
        box.network = False
    return box


def hooks_wanted(settings: Any) -> bool:
    """Whether any hook source is switched on: the owner's file, or a Claude Code settings origin.

    Claude Code compatibility is ON by default (owner's decision, 2026-11-11), so this is true on a
    stock install; what it costs when no `.claude` file carries a hook is three failed file reads.
    """
    return bool(
        getattr(settings, "hooks", False)
        or getattr(settings, "claude_hooks_repo", True)
        or getattr(settings, "claude_hooks_user", True)
    )


def hooks_present(settings: Any, workspace: Path | None = None) -> bool:
    """Whether an assembly has anything to install: the owner's switch is on, or a Claude-format
    hook (or a broken settings file's refusal) was loaded. Lets callers skip building an approver for
    hooks that do not exist, which on a stock install (compatibility on) is the common case."""
    if getattr(settings, "hooks", False):
        return True
    config = load_all(settings, workspace)
    return bool(config.hooks or config.error)


def load_all(settings: Any, workspace: Path | None = None) -> HookConfig:
    """The owner's file (only while ``CHIMERA_HOOKS`` is on) plus the Claude-format hooks of every
    origin that is switched on, in one :class:`HookConfig`."""
    base = load_hooks(Path(settings.home)) if getattr(settings, "hooks", False) else HookConfig()
    ws = workspace or Path.cwd()
    hooks, sources = discover(
        ws,
        repo=bool(getattr(settings, "claude_hooks_repo", True)),
        user=bool(getattr(settings, "claude_hooks_user", True)),
        repo_trust=trust_state(Path(settings.home), ws),
    )
    return HookConfig(
        hooks=(*base.hooks, *hooks), sha256=base.sha256, error=base.error, path=base.path,
        sources=tuple(sources),
    )


def apply_hooks(
    registry: ToolRegistry,
    *,
    settings: Any,
    audit: AuditLog | None,
    approve: HookApprover | None,
    taint: TaintSink | None = None,
    sandbox: SandboxFactory | None = None,
    approver_name: str = "",
    workspace: Path | None = None,
) -> ToolRegistry:
    """The assembly's entry point: ``registry`` unchanged while no hook is loaded, wrapped when one is.

    No hooks returns the very same object — no wrapper, so the overhead of a feature nothing uses is
    zero by construction, not by measurement. ``workspace`` is the project whose ``.claude`` settings
    are read (default: the working directory).
    """
    if not hooks_wanted(settings):
        return registry
    config = load_all(settings, workspace)
    if not config.hooks and not config.error:
        if audit is not None and bool(getattr(settings, "hooks", False)):
            audit.record("hook", {"hook": "", "event": "load", "applied": NONE,
                                  "error": f"hooks are on and {config.path} has no hooks"})
        return registry
    if config.error and audit is not None:
        # A7: every call on this assembly is about to be refused for this reason, and the refusal
        # path writes nothing — so without this line the Security screen showed no hook activity
        # while every cron job and bot run was being refused. One line per assembly, like `load`.
        audit.record("hook", {"hook": "", "event": "load", "applied": DENY,
                              "error": config.error, "config_sha256": config.sha256})
    if audit is not None:
        noisy = [(x.path, x.error or "; ".join(x.skipped)) for x in config.sources
                 if x.error or x.skipped]
        if noisy:
            # A guard a settings file seems to promise and this loop dropped is said once per assembly.
            audit.record("hook", {"hook": "", "event": "load", "applied": NONE,
                                  "error": "; ".join(f"{p}: {m}" for p, m in noisy)})
    if sandbox is None:

        def sandbox() -> Any:
            return hook_sandbox(settings)

    runner = HookRunner(
        config, sandbox=sandbox, host_exec=bool(getattr(settings, "hooks_host_exec", False)),
        user_host_exec=bool(getattr(settings, "claude_user_hooks_host_exec", False)),
        audit=audit, taint=taint, workspace=workspace,
    )
    return hook_registry(registry, runner, approve=approve, approver_name=approver_name)
