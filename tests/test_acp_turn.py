"""Driving an external coding agent (:mod:`chimera.acp`), against a real conversation.

The agent under test is :mod:`tests.acp_fake_agent`, a scripted child that speaks the protocol for
real. Nothing here is mocked, because nothing here is a function call: every behaviour worth pinning
down is an interaction across a process boundary — a notification arriving while our own request is
outstanding, a callback the agent makes into us mid-turn, an adapter that dies without answering.

What these tests are for, in one line each:

* the translation is faithful — an ACP turn appears on the Code screen in the Code screen's words;
* the guarantees we DO have hold (the write region refuses, the workspace jail refuses);
* the guarantee we do NOT have is not quietly implied (a permission we grant is recorded as ours).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from chimera.acp.agents import CUSTOM, AcpAgentSpec, available_agents, child_env, is_installed
from chimera.acp.client import AcpError
from chimera.acp.turn import AcpTurn, unified_patch
from chimera.tools.write_region import WriteRegion

FAKE = Path(__file__).with_name("acp_fake_agent.py")


def _spec(script: list[dict]) -> AcpAgentSpec:
    """A spec that launches the fake agent with ``script`` baked into its environment."""
    os.environ["FAKE_ACP_SCRIPT"] = json.dumps(script)
    return AcpAgentSpec(
        key="fake",
        label="Fake agent",
        argv=[sys.executable, "-u", str(FAKE)],
        # The scenario travels in the environment, so it has to survive the secret scrubber that
        # `child_env` applies. It does — the name matches none of the secret markers, which is
        # exactly the property the passthrough list exists to handle for real keys.
        passthrough_env=("FAKE_ACP_SCRIPT",),
    )


def _turn(script: list[dict], workspace: Path, **kwargs: object) -> AcpTurn:
    return AcpTurn(_spec(script), workspace, **kwargs)  # type: ignore[arg-type]


def _text(chunk: str) -> dict:
    return {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": chunk}}


# --- the translation --------------------------------------------------------------------------


def test_the_answer_arrives_as_tokens_and_as_a_whole(tmp_path: Path) -> None:
    """The Code screen streams `token` and keeps the finished answer. Both come from the same frames,
    so a mismatch between them would show one thing while the transcript stored another."""
    tokens: list[str] = []
    with _turn([{"notify": _text("Hello, ")}, {"notify": _text("world.")}], tmp_path,
               on_token=tokens.append) as turn:
        result = turn.prompt("hi")

    assert "".join(tokens) == "Hello, world."
    assert result.answer == "Hello, world."
    assert result.stop_reason == "end_turn"


def test_a_stop_reason_is_reported_rather_than_normalised(tmp_path: Path) -> None:
    # "hit the token ceiling" and "finished" are different outcomes, and a turn that flattens them
    # tells the user their answer is complete when it was cut off.
    with _turn([{"stop": "max_tokens"}], tmp_path) as turn:
        assert turn.prompt("hi").stop_reason == "max_tokens"


def test_a_tool_is_reported_once_when_it_finishes(tmp_path: Path) -> None:
    """`chimera.core.events.tool` takes `ok` as a bool, so there is no shape for "still running".

    Emitting on the start frame too would show every call twice; emitting only on the start would
    report an outcome that nobody knows yet.
    """
    calls: list[tuple[str, bool, str]] = []
    script = [
        {"notify": {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Read main.py",
                    "kind": "read", "status": "pending"}},
        {"notify": {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress"}},
        {"notify": {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
                    "content": [{"type": "content", "content": {"type": "text", "text": "12 lines"}}]}},
    ]
    with _turn(script, tmp_path, on_tool=lambda n, _a, ok, obs: calls.append((n, ok, obs))) as turn:
        result = turn.prompt("read it")

    assert len(calls) == 1
    name, ok, observation = calls[0]
    assert "Read main.py" in name and "read" in name
    assert ok is True
    assert observation == "12 lines"
    assert result.tool_names == ["read"]


def test_a_failed_tool_is_reported_as_failed(tmp_path: Path) -> None:
    calls: list[bool] = []
    script = [
        {"notify": {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Run tests",
                    "kind": "execute", "status": "pending"}},
        {"notify": {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "failed"}},
    ]
    with _turn(script, tmp_path, on_tool=lambda _n, _a, ok, _o: calls.append(ok)) as turn:
        turn.prompt("test it")
    assert calls == [False]


def test_a_diff_becomes_the_patch_the_screen_already_renders(tmp_path: Path) -> None:
    """ACP sends oldText/newText; the `edit` event carries a unified diff, because that is what the
    transcript renders and what the revert understands."""
    edits: list[tuple[str, str]] = []
    script = [
        {"notify": {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Edit", "kind": "edit"}},
        {"notify": {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
                    "content": [{"type": "diff", "path": str(tmp_path / "app.py"),
                                 "oldText": "x = 1\n", "newText": "x = 2\n"}]}},
    ]
    with _turn(script, tmp_path, on_edit=lambda p, d: edits.append((p, d))) as turn:
        result = turn.prompt("change it")

    assert len(edits) == 1
    path, patch = edits[0]
    assert path == "app.py"  # workspace-relative, like every other edit on this screen
    assert "-x = 1" in patch and "+x = 2" in patch
    assert result.edited == ["app.py"]


def test_reasoning_stays_out_of_the_answer(tmp_path: Path) -> None:
    """`agent_thought_chunk` has no counterpart in this screen's vocabulary.

    Folding it into the token stream would corrupt the one artefact a user quotes and keeps. Dropped
    deliberately, and this test is what says so.
    """
    tokens: list[str] = []
    script = [
        {"notify": {"sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "Let me think..."}}},
        {"notify": _text("The answer is 4.")},
    ]
    with _turn(script, tmp_path, on_token=tokens.append) as turn:
        result = turn.prompt("2+2")

    assert "".join(tokens) == "The answer is 4."
    assert "think" not in result.answer


def test_an_unknown_update_kind_is_ignored_rather_than_fatal(tmp_path: Path) -> None:
    # The protocol gains update kinds. A client that dies on the first unfamiliar one turns every
    # upstream release into an outage.
    script = [{"notify": {"sessionUpdate": "something_invented_later", "data": 1}},
              {"notify": _text("still here")}]
    with _turn(script, tmp_path) as turn:
        assert turn.prompt("hi").answer == "still here"


def test_usage_is_carried_when_the_agent_reports_it(tmp_path: Path) -> None:
    script = [{"notify": {"sessionUpdate": "usage_update", "used": 1234, "size": 200000,
                          "cost": {"amount": 0.042, "currency": "USD"}}}]
    with _turn(script, tmp_path) as turn:
        result = turn.prompt("hi")
    assert result.completion_tokens == 1234
    assert result.usd == pytest.approx(0.042)


# --- what the agent asks of us ------------------------------------------------------------------


def test_the_agent_can_read_a_file_through_us(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("line one\nline two\n", encoding="utf-8")
    script = [
        {"call": {"method": "fs/read_text_file", "params": {"path": "notes.txt"}}},
        {"echo_answers": True},
    ]
    with _turn(script, tmp_path) as turn:
        answered = json.loads(turn.prompt("read it").answer)
    assert answered == [{"content": "line one\nline two\n"}]


def test_a_read_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    """The workspace jail applies to the agent's requests exactly as it does to the native tools."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "secret.txt").write_text("do not read me", encoding="utf-8")
    script = [
        {"call": {"method": "fs/read_text_file", "params": {"path": "../secret.txt"}}},
        {"echo_answers": True},
    ]
    with _turn(script, workspace) as turn:
        answered = json.loads(turn.prompt("read it").answer)

    assert "error" in answered[0]
    assert "do not read me" not in json.dumps(answered)


def test_a_write_through_us_lands_and_shows_up_as_an_edit(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    edits: list[str] = []
    script = [
        {"call": {"method": "fs/write_text_file",
                  "params": {"path": "app.py", "content": "x = 2\n"}}},
    ]
    with _turn(script, tmp_path, on_edit=lambda p, _d: edits.append(p)) as turn:
        result = turn.prompt("bump it")

    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "x = 2\n"
    assert edits == ["app.py"]
    assert result.edited == ["app.py"]


def test_a_write_outside_the_declared_region_is_refused_and_recorded(tmp_path: Path) -> None:
    """The write region is fail-closed for writes that come THROUGH us, and the refusal is kept.

    A refusal the user never sees is indistinguishable from a write that silently did not happen —
    and the difference matters, because one of them means the agent's work is incomplete.
    """
    (tmp_path / "src").mkdir()
    script = [
        {"call": {"method": "fs/write_text_file",
                  "params": {"path": "secrets.env", "content": "KEY=1"}}},
        {"echo_answers": True},
    ]
    region = WriteRegion(["src/**"], tmp_path)
    with _turn(script, tmp_path, write_region=region) as turn:
        result = turn.prompt("write it")

    assert not (tmp_path / "secrets.env").exists()
    assert result.refused == ["secrets.env"]
    answered = json.loads(result.answer)
    assert "write-region" in json.dumps(answered[0])


def test_a_permission_is_granted_and_recorded_as_ours(tmp_path: Path) -> None:
    """The honesty test.

    Refusing by default makes every turn useless. Prompting the user reads as a gate — and it is not
    one, because these agents have their own file and shell tools and did not have to ask. So the
    grant is automatic and it is RECORDED, and the posture note says the guarantee is the checkpoint
    rather than the prompt.
    """
    script = [
        {"call": {"method": "session/request_permission",
                  "params": {"sessionId": "sess_fake",
                             "toolCall": {"toolCallId": "t1", "title": "Delete build/"},
                             "options": [{"optionId": "no", "name": "Reject", "kind": "reject_once"},
                                         {"optionId": "yes", "name": "Allow", "kind": "allow_once"}]}}},
        {"echo_answers": True},
    ]
    with _turn(script, tmp_path) as turn:
        result = turn.prompt("clean up")

    answered = json.loads(result.answer)
    # `allow_once` chosen by kind and not by position — the options arrive in whatever order the
    # agent likes, and picking the first would have rejected this one.
    assert answered[0] == {"outcome": {"outcome": "selected", "optionId": "yes"}}
    assert result.auto_approved == ["Delete build/"]


def test_a_method_we_do_not_offer_is_answered_rather_than_ignored(tmp_path: Path) -> None:
    """We decline the terminal capability. An agent that asks anyway must get an ANSWER — silence is
    what makes a child hang forever waiting on a reply that is never coming."""
    script = [
        {"call": {"method": "terminal/create", "params": {"command": "rm -rf /"}}},
        {"echo_answers": True},
    ]
    with _turn(script, tmp_path) as turn:
        answered = json.loads(turn.prompt("run it").answer)
    assert answered[0]["error"]["code"] == -32601


# --- when it goes wrong --------------------------------------------------------------------------


def test_an_agent_that_dies_mid_turn_fails_the_turn(tmp_path: Path) -> None:
    """Rather than hanging until the turn timeout. The failure carries the child's exit code, which
    is the difference between "it crashed" and "it is slow"."""
    with _turn([{"exit": 9}], tmp_path) as turn, pytest.raises(AcpError, match="9"):
        turn.prompt("hi")


def test_an_agent_that_never_answers_times_out_as_a_failure(tmp_path: Path) -> None:
    with _turn([{"sleep": 30}], tmp_path, turn_timeout=1.0) as turn, pytest.raises(
        AcpError, match="within"
    ):
        turn.prompt("hi")


def test_a_missing_program_says_how_to_install_it(tmp_path: Path) -> None:
    # "FileNotFoundError: npx" helps nobody. The install line does.
    spec = AcpAgentSpec(key="ghost", label="Ghost", argv=["definitely-not-a-real-agent-xyz"],
                        install_hint="npm i -g ghost")
    with pytest.raises(AcpError, match="npm i -g ghost"):
        AcpTurn(spec, tmp_path).start()


def test_noise_on_stdout_does_not_break_the_conversation(tmp_path: Path) -> None:
    """npx prints notices before the adapter says a word. A client that treats the first non-JSON
    line as a protocol violation cannot talk to the adapter people actually install."""
    script = [{"noise": "npm warn exec The following package was not found"},
              {"notify": _text("fine")}]
    with _turn(script, tmp_path) as turn:
        assert turn.prompt("hi").answer == "fine"


def test_the_session_is_rooted_at_an_absolute_workspace(tmp_path: Path) -> None:
    # ACP requires an absolute `cwd`, and it is what every relative path in the session resolves
    # against — including the ones the agent hands back to us.
    relative = Path(os.path.relpath(tmp_path, Path.cwd())) if tmp_path.drive == Path.cwd().drive else tmp_path
    with _turn([], relative) as turn:
        assert turn.workspace.is_absolute()
        assert turn.workspace == tmp_path.resolve()


# --- the catalogue ---------------------------------------------------------------------------


def test_the_catalogue_reports_availability_per_agent() -> None:
    """`chimera doctor` shape: capability by capability, available true/false. A frozen sidecar built
    by CI on a machine nobody looked at is where "it should work" stops being evidence."""
    agents = available_agents()
    assert {a["key"] for a in agents} == {"claude", "gemini"}
    for agent in agents:
        assert isinstance(agent["available"], bool)
        assert agent["install_hint"]


def test_no_codex_entry_is_claimed() -> None:
    """Codex reaches ACP through third-party adapters this project has not run. Listing an
    unverified command would turn "we did not check" into "supported"."""
    assert {a["key"] for a in available_agents()}.isdisjoint({"codex"})


def test_a_custom_agent_with_no_command_is_not_installed() -> None:
    assert is_installed(CUSTOM) is False


def test_the_environment_keeps_the_scrubber_and_names_its_exceptions() -> None:
    """The trap this exists for: `_child_env` strips anything matching API_KEY/TOKEN/SECRET, and an
    ACP agent is a program whose entire job needs one. Launched with the scrubbed environment it
    fails as an authentication error from a provider the user is certain they configured."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-value"
    os.environ["UNRELATED_API_KEY"] = "sk-other-value"
    try:
        env = child_env(AcpAgentSpec(key="k", label="K", argv=["x"],
                                     passthrough_env=("ANTHROPIC_API_KEY",)))
        assert env["ANTHROPIC_API_KEY"] == "sk-test-value"
        # Named one at a time: passing the whole environment would be easier and would hand every
        # future adapter every key on the machine.
        assert "UNRELATED_API_KEY" not in env
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("UNRELATED_API_KEY", None)


# --- the patch builder ---------------------------------------------------------------------------


def test_a_patch_reads_as_a_unified_diff() -> None:
    patch = unified_patch("app.py", "a\nb\n", "a\nc\n")
    assert patch.startswith("--- a/app.py")
    assert "+++ b/app.py" in patch
    assert "-b" in patch and "+c" in patch


def test_an_unchanged_file_produces_an_empty_patch() -> None:
    assert unified_patch("app.py", "same\n", "same\n") == ""
