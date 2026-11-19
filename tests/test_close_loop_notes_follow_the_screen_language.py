"""Presentation follows payload metadata; it never grants a capability."""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from chimera.api import code_close_loop as wiring
from chimera.core import close_loop as core


@pytest.mark.parametrize(("verified", "clean", "incomplete", "code"), [
    (False, True, "", "verification_failed"),
    (True, False, "", "self_check_failed"),
    (True, True, "bounded listing", "incomplete_changes"),
])
def test_gate_codes_come_from_the_decision(
    tmp_path: Path, verified: bool, clean: bool, incomplete: str, code: str,
) -> None:
    loop = wiring.CloseLoopWiring(
        enabled=lambda: True, amend=lambda *_: 0, enqueue=lambda *_: False,
        locked=lambda _: nullcontext(),
    )
    result = loop.at_turn_end(
        session_id="s", turn_id="t", ws=tmp_path, requested=None, via_screen=True,
        origin_ok=True, message="Update the label", measure=lambda: (["label.txt"], incomplete),
        verified=verified, self_check_clean=clean, follow=None,
    )
    assert result is not None
    assert result.to_payload()["skipped_code"] == code
    assert result.note() is not None  # English remains available to other channels.


def test_screen_note_does_not_modify_the_canonical_answer() -> None:
    state = core.CloseLoop(skipped_reason="verification did not pass for this turn")
    payload: dict[str, Any] = {"answer": "The label changed."}
    note = wiring.present_close_loop(payload, state, desktop=True)
    assert note is None
    assert payload["answer"] == "The label changed."
    assert payload["close_loop_note"] == "payload"
    assert payload["close_loop"]["skipped_reason"] == state.skipped_reason


def test_other_channels_keep_the_english_note() -> None:
    state = core.CloseLoop(skipped_reason="verification did not pass for this turn")
    payload: dict[str, Any] = {"answer": "The label changed."}
    note = wiring.present_close_loop(payload, state, desktop=False)
    assert note == state.note()
    assert payload["answer"] == "The label changed.\n\n" + str(state.note())
    assert "close_loop_note" not in payload


def test_unknown_and_legacy_codes_round_trip_without_prose_classification() -> None:
    original = {"skipped_reason": "future reason", "skipped_code": "future_v2",
                "merge_blocked_code": "future_merge"}
    state = core.CloseLoop.from_payload(original)
    assert state.to_payload()["skipped_code"] == "future_v2"
    assert state.to_payload()["merge_blocked_code"] == "future_merge"
    legacy = core.CloseLoop.from_payload({"skipped_reason": "verification did not pass for this turn"})
    assert legacy.to_payload()["skipped_code"] == ""


@pytest.mark.parametrize(("branch", "code"), [("", "detached"), ("main", "default_branch")])
def test_branch_refusals_get_codes_without_matching_the_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str, code: str,
) -> None:
    monkeypatch.setattr(core, "is_git_repo", lambda _: True)
    monkeypatch.setattr(core, "branch_refusal", lambda _: (branch, "a diagnostic from the decision"))
    state = core.close_at_turn_end(tmp_path, turn_id="t", message="label", paths=["label.txt"], hooks=core.Hooks())
    assert state.skipped_code == code
    assert state.skipped_reason == "a diagnostic from the decision"


@pytest.mark.parametrize("code", ["no_origin", "gh_signed_out", "push_elsewhere", "remote_unreachable"])
def test_readiness_codes_survive_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str,
) -> None:
    from types import SimpleNamespace
    monkeypatch.setattr(core, "is_git_repo", lambda _: True)
    monkeypatch.setattr(core, "branch_refusal", lambda _: ("work", ""))
    monkeypatch.setattr(core, "readiness", lambda *_a, **_k: SimpleNamespace(reason=code))
    state = core.close_at_turn_end(
        tmp_path, turn_id="t", message="label", paths=["label.txt"], hooks=core.Hooks(gh=["unused"]),
    )
    assert state.skipped_code == code


@pytest.mark.parametrize(("allowed", "diff", "rc", "code"), [
    (False, "", 0, "merging_off"),
    (True, None, 0, "unreadable_diff"),
    (True, "", 1, "merge_refused"),
    (True, "", 0, ""),
])
def test_merge_decisions_emit_codes_and_preserve_the_english_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    allowed: bool, diff: str | None, rc: int, code: str,
) -> None:
    from subprocess import CompletedProcess
    monkeypatch.setattr(core, "_pr_diff", lambda *_: diff)
    state = core.CloseLoop(pr_url="https://example.com/org/repo/pull/1", ci="passed")
    hooks = core.Hooks(merge_allowed=lambda: allowed,
                       run=lambda argv, _: CompletedProcess(argv, rc, "", "remote diagnostic"))
    core._merge(tmp_path, state, hooks, ["unused"], "abc")
    assert state.merge_blocked_code == code
    assert bool(state.merge_blocked_reason) == bool(code)
    assert state.merged is (not code)
    assert "CI passed" in str(state.note())


def test_follow_up_keeps_presentation_without_screen_authority(tmp_path: Path) -> None:
    queued: list[dict[str, Any]] = []
    loop = wiring.CloseLoopWiring(
        enabled=lambda: True, amend=lambda *_: 0,
        enqueue=lambda _s, _c, payload: (queued.append(payload) or True),
        locked=lambda _: nullcontext(),
    )
    state = core.CloseLoop(turn_id="original")
    assert loop._ask_fix("s", tmp_path, state, "one benign failed check", 1, desktop=True)
    follow = queued[0]["close_loop_follow"]
    assert wiring.desktop_close_loop_note(False, follow)
    # The presentation marker cannot authorize a continuation, even if it survived in an internal message.
    denied = loop.at_turn_end(
        session_id="s", turn_id="fix", ws=tmp_path, requested=None, via_screen=False,
        origin_ok=False, continuation_ok=False, message="fix label", measure=lambda: ([], ""),
        verified=True, self_check_clean=True, follow=follow,
    )
    assert denied is not None and denied.ci == "failed"
    assert "owner's own conversation" in denied.error


def test_watcher_updates_only_payload_and_preserves_marked_and_legacy_answers(tmp_path: Path) -> None:
    import json

    from chimera.core.code_session import CodeSessionStore
    store = CodeSessionStore(tmp_path / "sessions")
    path = store._path("s")
    path.parent.mkdir(parents=True, exist_ok=True)
    receipts = [
        {"answer": "The label changed.", "close_loop_note": "payload",
         "close_loop": {"turn_id": "original", "ci": "pending"}},
        {"answer": "The old answer.\n\nClose loop: CI pending.",
         "close_loop": {"turn_id": "original", "ci": "pending"}},
    ]
    path.write_text(json.dumps({"messages": [], "receipts": receipts}), encoding="utf-8")
    loop = wiring.CloseLoopWiring(
        enabled=lambda: True, amend=store.amend_close_loop,
        enqueue=lambda *_: False, locked=lambda _: nullcontext(),
    )
    loop._record("s", core.CloseLoop(turn_id="original", ci="passed", merged=True))
    stored = json.loads(path.read_text(encoding="utf-8"))["receipts"]
    for before, after in zip(receipts, stored, strict=True):
        assert before["answer"] == after["answer"]
        assert before.get("close_loop_note") == after.get("close_loop_note")
        assert after["close_loop"]["merged"] is True


@pytest.mark.parametrize("desktop", [False, True])
def test_guest_projection_still_shows_only_the_sealed_final_answer(desktop: bool) -> None:
    from chimera.api.guest_projection import project_frame
    payload: dict[str, Any] = {"answer": "canonical", "final_answer": "sealed", "final_sealed": True}
    wiring.present_close_loop(payload, core.CloseLoop(skipped_reason="raw owner detail"), desktop=desktop)
    frame = project_frame({"event": "done", "payload": payload})
    assert frame is not None
    assert frame["payload"]["answer"] == "sealed"
    assert "close_loop" not in frame["payload"]
    assert "close_loop_note" not in frame["payload"]


@pytest.mark.parametrize(("repo", "paths", "gh", "code"), [
    (False, ["label.txt"], ["unused"], "not_repo"),
    (True, [], ["unused"], "no_changes"),
    (True, ["label.txt"], None, "no_gh"),
])
def test_early_skips_have_codes_without_starting_an_external_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    repo: bool, paths: list[str], gh: list[str] | None, code: str,
) -> None:
    monkeypatch.setattr(core, "is_git_repo", lambda _: repo)
    monkeypatch.setattr(core, "branch_refusal", lambda _: ("work", ""))
    monkeypatch.setattr(core, "_gh", lambda _: gh)
    state = core.close_at_turn_end(tmp_path, turn_id="t", message="label", paths=paths, hooks=core.Hooks())
    assert state.skipped_code == code


def test_exception_code_preserves_the_complete_english_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff = "diff --git a/billing.py b/billing.py\n@@ -1 +1 @@\n-label = 1\n+label = 2\n"
    monkeypatch.setattr(core, "_pr_diff", lambda *_: diff)
    state = core.CloseLoop(pr_url="https://example.com/org/repo/pull/1", ci="passed")
    core._merge(tmp_path, state, core.Hooks(merge_allowed=lambda: True), ["unused"], "abc")
    assert state.merge_blocked_code == "exceptions"
    assert "touches billing (billing.py)" in state.merge_blocked_reason
    assert "left open for the owner" in str(state.note())


@pytest.mark.parametrize("origin", ["bridge", "channel", "guest", "background", "provider", "fused"])
def test_presentation_never_widens_who_can_start_a_loop(origin: str) -> None:
    fields: dict[str, Any] = dict(
        via_screen=True, via_bridge=False, channel_origin="", author="", background=False,
        provider=None, fused=False,
    )
    field, value = {
        "bridge": ("via_bridge", True), "channel": ("channel_origin", "discord"),
        "guest": ("author", "Visitor"), "background": ("background", True),
        "provider": ("provider", "external"), "fused": ("fused", True),
    }[origin]
    fields[field] = value
    assert wiring.desktop_close_loop_note(True, None)
    assert not core.may_start_loop(**fields)


@pytest.mark.parametrize("loop_enabled", [True, False])
@pytest.mark.parametrize("tool_frames", [False, True])
@pytest.mark.parametrize("verify_retry", [False, True])
def test_verified_owner_answer_survives_refresh_separately_from_the_guest_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loop_enabled: bool, tool_frames: bool, verify_retry: bool,
) -> None:
    from test_a_finished_turn_closes_its_own_loop import _EditingAgent, _turn

    import chimera.providers
    from chimera.api import code_api
    from chimera.config import get_settings
    from chimera.core.diff_self_check import SelfCheck

    class _NoModel:
        def complete(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("the fixture must not ask a model")

    monkeypatch.setattr(chimera.providers, "LLMGateway", lambda *_a, **_k: _NoModel())
    get_settings.cache_clear()
    ws = tmp_path / "project"
    ws.mkdir()
    monkeypatch.setattr("chimera.api.attachments.vision_support", lambda _: "no")
    monkeypatch.setattr(code_api, "_self_check_of_turn", lambda *_a, **_k: SelfCheck())
    monkeypatch.setattr(code_api, "_measured_turn", lambda *_: (["label.txt"], ""))
    monkeypatch.setattr(wiring, "close_at_turn_end", lambda _ws, **kw: core.CloseLoop(
        turn_id=kw["turn_id"], skipped_code="not_repo", skipped_reason="the workspace is not a git repository",
    ))
    class _ToolAgent(_EditingAgent):
        def run(self, task: str, **kwargs: Any) -> Any:
            result = super().run(task, **kwargs)
            if tool_frames:
                import json

                from chimera.core.agent import ToolActivity

                args = {"path": "label.txt"}
                final = result.transcript[-1]
                result.transcript = [*result.transcript[:-1], {
                    "role": "assistant", "content": "", "tool_calls": [{
                        "id": "read-label", "type": "function", "function": {
                            "name": "read_file", "arguments": json.dumps(args),
                        },
                    }],
                }, {"role": "tool", "tool_call_id": "read-label", "content": "label = 2"}, final]
                result.tool_names = ["read_file"]
                result.steps = 2
                if kwargs.get("on_tool"):
                    kwargs["on_tool"](ToolActivity("read_file", args, True, "label = 2"))
            self.last_transcript = result.transcript
            return result

    agent = _ToolAgent(ws, {"label.txt": "label = 2\n"}, "The label changed.")
    try:
        client, done, sid = _turn(
            tmp_path, monkeypatch, ws, agent, message="Update label.txt", setting=loop_enabled,
            verify="unused verifier" if verify_retry else None,
            body={"verify_retry": verify_retry, "plan_gate": False},
        )
        assert done["self_check"]["clean"] is True
        if verify_retry:
            assert done["turn_summary"] in done["answer"]
        assert (done.get("close_loop_note") == "payload") is loop_enabled
        if loop_enabled and verify_retry:
            assert done["close_loop"]["skipped_code"] == "not_repo"
        stored = client.get(f"/api/code/sessions/{sid}").json()["exchanges"][-1]
        if verify_retry:
            assert stored["verified"]["state"] == "passed"
        if tool_frames:
            assert [m["role"] for m in agent.last_transcript[-4:]] == ["user", "assistant", "tool", "assistant"]
            assert agent.last_transcript[-3]["tool_calls"]
            assert stored["tools"][0]["name"] == "read_file"
        assert stored["answer"] == done["answer"]
        assert stored["done"].get("close_loop_note") == done.get("close_loop_note")
        shared = client.post(f"/api/code/sessions/{sid}/share")
        assert shared.status_code == 200, shared.text
        guest = client.get("/guest/api/session", headers={"Authorization": f"Bearer {shared.json()['token']}"})
        assert guest.status_code == 200
        final = guest.json()["exchanges"][-1]
        assert final["answer"] == "The label changed."
        assert "close_loop" not in final.get("done", {})
        assert "close_loop_note" not in final.get("done", {})
        if verify_retry:
            assert done["turn_summary"] not in final["answer"]
    finally:
        get_settings.cache_clear()


def test_passed_is_published_only_after_the_final_merge_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    states: list[core.CloseLoop] = []
    state = core.CloseLoop(turn_id="t", commit="head", ci="pending",
                           merge_blocked_reason="preliminary category (old.py)", merge_blocked_code="exceptions")
    monkeypatch.setattr(core, "_pr_view", lambda *_: {
        "state": "OPEN", "headRefOid": "head", "statusCheckRollup": [
            {"status": "COMPLETED", "conclusion": "SUCCESS"},
        ],
    })

    def merge(*_args: Any) -> None:
        state.merge_blocked_reason = "final category (current.py)"
        state.merge_blocked_code = "exceptions"

    monkeypatch.setattr(core, "_merge", merge)
    hooks = core.Hooks(sleep=lambda _: None, clock=lambda: 0,
                       report=lambda current: states.append(core.CloseLoop.from_payload(current.to_payload())))
    core.follow_ci(tmp_path, state, hooks, head="head", gh=["unused"])
    assert len(states) == 1
    assert states[0].ci == "passed"
    assert states[0].merge_blocked_reason == "final category (current.py)"


def test_failed_is_published_once_with_the_enqueue_failure_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    states: list[core.CloseLoop] = []
    monkeypatch.setattr(core, "_pr_view", lambda *_: {
        "state": "OPEN", "headRefOid": "head", "statusCheckRollup": [
            {"status": "COMPLETED", "conclusion": "FAILURE"},
        ],
    })
    monkeypatch.setattr(core, "_failed_log", lambda *_: "one benign failed check")
    state = core.CloseLoop(turn_id="t", commit="head", ci="pending")
    hooks = core.Hooks(sleep=lambda _: None, clock=lambda: 0, ask_fix=lambda *_: False,
                       report=lambda current: states.append(core.CloseLoop.from_payload(current.to_payload())))
    core.follow_ci(tmp_path, state, hooks, head="head", gh=["unused"])
    assert len(states) == 1
    assert states[0].ci == "failed"
    assert states[0].error == "could not start the fix turn"



def test_fast_enqueue_drain_advances_the_head_and_refuses_the_old_watch_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from chimera.core.code_session import CodeSessionStore

    store = CodeSessionStore(tmp_path / "sessions")
    path = store._path("s")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = core.CloseLoop(turn_id="original", committed=True, commit="old-head", ci="pending")
    path.write_text(json.dumps({"messages": [], "receipts": [
        {"close_loop_note": "payload", "close_loop": original.to_payload()},
    ]}), encoding="utf-8")
    loop: wiring.CloseLoopWiring

    def enqueue(_session: str, _id: str, payload: dict[str, Any]) -> bool:
        follow = payload["close_loop_follow"]
        result = loop.at_turn_end(
            session_id="s", turn_id="fix", ws=tmp_path, requested=None, via_screen=False,
            origin_ok=False, continuation_ok=True, message="Update label.txt",
            measure=lambda: (["label.txt"], ""), verified=True, self_check_clean=True, follow=follow,
        )
        assert result is not None and result.commit == "new-head"
        pending = json.loads(path.read_text(encoding="utf-8"))["receipts"][0]["close_loop"]
        assert pending["commit"] == "new-head" and pending["ci"] == "pending"
        # A fast new watch finishes before the old watch returns from this enqueue call.
        result.ci, result.merge_blocked_reason, result.merge_blocked_code = "passed", "final reason (label.txt)", "exceptions"
        loop._record("s", result)
        return True

    loop = wiring.CloseLoopWiring(
        enabled=lambda: True, amend=store.amend_close_loop, enqueue=enqueue,
        locked=lambda _: nullcontext(), sleep=lambda _: None,
    )
    monkeypatch.setattr(loop, "_start_watch", lambda *_a, **_k: None)

    def follow_up(_ws: Path, *, state: core.CloseLoop, **_kw: Any) -> core.CloseLoop:
        state.commit, state.ci, state.error = "new-head", "pending", ""
        return state

    monkeypatch.setattr(wiring, "follow_up", follow_up)
    monkeypatch.setattr(core, "_pr_view", lambda *_: {
        "state": "OPEN", "headRefOid": "old-head", "statusCheckRollup": [
            {"status": "COMPLETED", "conclusion": "FAILURE"},
        ],
    })
    monkeypatch.setattr(core, "_failed_log", lambda *_: "one benign failed check")
    hooks = core.Hooks(
        sleep=lambda _: None, clock=lambda: 0,
        ask_fix=lambda log, attempt: loop._ask_fix("s", tmp_path, original, log, attempt, desktop=True),
        report=lambda state: loop._record("s", state),
    )
    core.follow_ci(tmp_path, original, hooks, head="old-head", gh=["unused"])
    final = json.loads(path.read_text(encoding="utf-8"))["receipts"][0]["close_loop"]
    assert original.ci == "failed"  # This is the old watch's terminal report, now stale.
    assert final["commit"] == "new-head" and final["ci"] == "passed"
    assert final["merge_blocked_reason"] == "final reason (label.txt)"


def test_unqualified_answer_anchor_does_not_erase_an_existing_final_seal() -> None:
    from types import SimpleNamespace

    from chimera.core.code_session import CodeSession

    session = CodeSession(SimpleNamespace(), messages=[
        {"role": "user", "content": "original"}, {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "later"}, {"role": "assistant", "content": "later answer"},
    ])
    assert session.seal_final_at(3, "later model final")
    before = session.to_dict()["messages"]
    assert not session.replace_turn_answer_at(0, 3, before[0], before[3], "wrong answer")
    assert session.to_dict()["messages"] == before



def test_a_fix_may_advance_the_head_after_the_previous_failure_was_already_recorded(tmp_path: Path) -> None:
    import json

    from chimera.core.code_session import CodeSessionStore

    store = CodeSessionStore(tmp_path / "sessions")
    path = store._path("s")
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = core.CloseLoop(turn_id="t", commit="old-head", ci="failed", fix_attempts=1)
    path.write_text(json.dumps({"messages": [], "receipts": [
        {"close_loop": previous.to_payload()},
    ]}), encoding="utf-8")
    current = core.CloseLoop(turn_id="t", commit="new-head", ci="pending", fix_attempts=1)
    assert store.amend_close_loop("s", "t", current.to_payload()) == 1
    current.ci, current.merged = "passed", True
    assert store.amend_close_loop("s", "t", current.to_payload()) == 1
    assert store.amend_close_loop("s", "t", previous.to_payload()) == 0
    stale_pending = core.CloseLoop(turn_id="t", commit="old-head", ci="pending", fix_attempts=0)
    assert store.amend_close_loop("s", "t", stale_pending.to_payload()) == 0
    stale_error = core.CloseLoop(turn_id="t", commit="old-head", ci="pending", fix_attempts=1,
                                 error="the old watch stopped")
    assert store.amend_close_loop("s", "t", stale_error.to_payload()) == 0
    final = json.loads(path.read_text(encoding="utf-8"))["receipts"][0]["close_loop"]
    assert final["commit"] == "new-head" and final["merged"] is True



def _offline_final_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: Any, ws: Path, message: str,
) -> tuple[Any, dict[str, Any], str]:
    from test_a_finished_turn_closes_its_own_loop import _turn

    import chimera.providers
    from chimera.api import code_api
    from chimera.config import get_settings
    from chimera.core.diff_self_check import SelfCheck

    class _NoModel:
        def complete(self, *_a: Any, **_k: Any) -> Any:
            raise AssertionError("the fixture must not ask an external model")

    get_settings.cache_clear()
    monkeypatch.setattr(chimera.providers, "LLMGateway", lambda *_a, **_k: _NoModel())
    monkeypatch.setattr("chimera.api.attachments.vision_support", lambda _: "yes")
    monkeypatch.setattr(code_api, "_self_check_of_turn", lambda *_a, **_k: SelfCheck())
    monkeypatch.setattr(code_api, "_measured_turn", lambda *_: (["label.txt"], ""))
    monkeypatch.setattr(wiring, "close_at_turn_end", lambda _ws, **kw: core.CloseLoop(
        turn_id=kw["turn_id"], skipped_code="not_repo", skipped_reason="the workspace is not a git repository",
    ))
    return _turn(tmp_path, monkeypatch, ws, agent, message=message, setting=True,
                 verify="unused verifier", body={"verify_retry": True, "plan_gate": False})


@pytest.mark.parametrize("redacted_part", ["opener", "answer"])
def test_redacted_persisted_anchors_keep_verdict_and_guest_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redacted_part: str,
) -> None:
    import json

    from test_a_finished_turn_closes_its_own_loop import _EditingAgent

    from chimera.core.redact import redact, redact_json

    ws = tmp_path / "project"
    ws.mkdir()
    fixture = "Authorization: Bearer " + "fixture-only-not-a-credential"
    assert redact(fixture) != fixture, "precondition: effective masking, without a real credential"
    question = "Update label.txt" + ("; fixture note " + fixture if redacted_part == "opener" else "")
    answer = "The label changed." + (" Fixture note " + fixture if redacted_part == "answer" else "")
    client, done, sid = _offline_final_turn(
        tmp_path, monkeypatch, _EditingAgent(ws, {"label.txt": "label = 2\n"}, answer), ws, question,
    )
    stored = client.get(f"/api/code/sessions/{sid}").json()["exchanges"][-1]
    assert stored["verified"]["state"] == "passed"
    assert stored["answer"] == redact_json(done["answer"])
    assert done["turn_summary"] in stored["answer"]
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    guest = client.get("/guest/api/session", headers={"Authorization": f"Bearer {token}"})
    assert guest.status_code == 200
    projected = guest.json()["exchanges"][-1]
    assert projected["answer"] == redact(answer)
    assert fixture not in guest.text
    assert "Turn receipt:" not in projected["answer"]
    records = list((tmp_path / "home").rglob(f"{sid}.json"))
    assert records
    for record in records:
        assert fixture not in json.dumps(json.loads(record.read_text(encoding="utf-8")))


def test_real_image_tool_frame_keeps_one_human_turn_and_the_guest_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from test_image_tools_safety_controls import PNG

    from chimera.core.agent import Agent, AgentConfig
    from chimera.core.guest_history import project_history
    from chimera.providers import CompletionResult, ToolCall
    from chimera.providers.gateway import _to_message_dicts
    from chimera.tools.base import Tool
    from chimera.tools.registry import ToolRegistry

    ws = tmp_path / "project"
    ws.mkdir()
    picture = ws / "label.png"
    picture.write_bytes(PNG)

    class _ImageTool(Tool):
        name = "view_image"
        description = "Offline fixture image observation"
        parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

        def run(self, **_kwargs: Any) -> str:
            return "offline image observation"

    class _Backend:
        def __init__(self) -> None:
            self.calls: list[Any] = []
            self.wire: list[Any] = []

        def complete(self, messages: list[Any], **_kw: Any) -> CompletionResult:
            self.calls.append(list(messages))
            self.wire.append(_to_message_dicts(messages))
            if len(self.calls) == 1:
                return CompletionResult(content="", model="fake/model", usd=0.0, tool_calls=[
                    ToolCall(id="picture", name="view_image", arguments={"path": "label.png"}),
                ])
            assert len(self.calls) == 2
            return CompletionResult(content="The label is blue.", model="fake/model", usd=0.0)

    registry = ToolRegistry()
    registry.register(_ImageTool())
    backend = _Backend()
    agent = Agent(backend, registry, config=AgentConfig(model="fake/model", max_steps=3, project_root=ws))
    question = "Inspect label.png"
    client, done, sid = _offline_final_turn(tmp_path, monkeypatch, agent, ws, question)
    assert len(backend.calls) == 2
    wire_frame = next(row for row in backend.wire[1] if isinstance(row.get("content"), list))
    assert any(part.get("type") == "image_url" for part in wire_frame["content"])
    from chimera.core.guest_history import WORKSPACE_IMAGE_KEY, WorkspaceImageFrame

    image_frame = next(row for row in backend.calls[1] if row.get("images"))
    assert isinstance(image_frame, WorkspaceImageFrame)
    assert image_frame[WORKSPACE_IMAGE_KEY] == {"tool_call_id": "picture"}
    assert WORKSPACE_IMAGE_KEY not in wire_frame and "origin" not in wire_frame
    replay = client.get(f"/api/code/sessions/{sid}").json()["exchanges"]
    assert len(replay) == 1, "the synthetic image frame is part of the human's turn"
    assert replay[0]["you"] == question
    assert replay[0]["answer"] == done["answer"]
    assert done["turn_summary"] in replay[0]["answer"]
    token = client.post(f"/api/code/sessions/{sid}/share").json()["token"]
    guest = client.get("/guest/api/session", headers={"Authorization": f"Bearer {token}"})
    assert guest.status_code == 200
    assert len(guest.json()["exchanges"]) == 1
    assert guest.json()["exchanges"][0]["you"] == question
    assert guest.json()["exchanges"][0]["answer"] == "The label is blue."
    assert "Inspect this workspace image" not in guest.text and str(picture) not in guest.text
    record = next((tmp_path / "home").rglob(f"{sid}.json"))
    messages = json.loads(record.read_text(encoding="utf-8"))["messages"]
    projected = project_history(messages, "another-guest")
    assert [m["role"] for m in projected] == ["user", "assistant"]
    assert projected[0]["content"] == question and projected[1]["content"] == "The label is blue."
    assert str(picture) not in json.dumps(projected)



@pytest.mark.parametrize("marked", [False, True])
def test_a_real_user_between_anchors_is_refused_even_with_images_and_a_copied_marker(marked: bool) -> None:
    from types import SimpleNamespace

    from chimera.core.code_session import CodeSession, TurnAnswerContext
    from chimera.core.guest_history import WORKSPACE_IMAGE_KEY

    human = {"role": "user", "content": "Inspect this workspace image and answer the user's task.", "images": ["label.png"]}
    if marked:
        human[WORKSPACE_IMAGE_KEY] = True
    session = CodeSession(SimpleNamespace(), messages=[
        {"role": "user", "content": "original"}, human,
        {"role": "assistant", "content": "already saved"},
    ])
    assert session.seal_final_at(2, "already sealed model answer")
    saved = session.to_dict()["messages"]
    # Matching persisted data is insufficient: no runtime image identity was captured for this user.
    context = TurnAnswerContext(tuple(saved), frozenset())
    assert not session.replace_turn_answer_at(0, 2, saved[0], saved[2], "wrong", context=context)
    assert session.to_dict()["messages"] == saved


def test_a_real_multimodal_opener_is_still_a_human_turn() -> None:
    from types import SimpleNamespace

    from chimera.core.code_session import CodeSession
    from chimera.core.guest_history import HUMAN, is_turn_opener

    opener = {"role": "user", "content": "Inspect my image", "images": ["label.png"], "origin": HUMAN}
    session = CodeSession(SimpleNamespace(), messages=[opener, {"role": "assistant", "content": "Blue."}])
    saved = session.to_dict()["messages"]
    assert is_turn_opener(opener)
    assert session.replace_turn_answer_at(0, 1, saved[0], saved[1], "Blue. Verified.")


def test_request_fields_cannot_create_an_image_identity_or_answer_context() -> None:
    from chimera.api.code_api import CodeTurnRequest
    from chimera.core.guest_history import WORKSPACE_IMAGE_KEY

    request = CodeTurnRequest.model_validate({"message": "Inspect my image", WORKSPACE_IMAGE_KEY: True,
                                              "turn_answer_context": {"workspace_images": [1]}})
    assert WORKSPACE_IMAGE_KEY not in request.model_dump()
    assert "turn_answer_context" not in request.model_dump()


def test_persisted_image_mark_is_filtered_from_wire_even_without_images() -> None:
    from chimera.core.guest_history import WORKSPACE_IMAGE_KEY
    from chimera.providers.gateway import _to_message_dicts

    sent = _to_message_dicts([{"role": "user", "content": "continuation", WORKSPACE_IMAGE_KEY: True, "images": []}])
    assert sent == [{"role": "user", "content": "continuation"}]



def test_captured_image_context_is_required_after_refresh_and_refuses_a_changed_span() -> None:
    import json
    from types import SimpleNamespace

    from chimera.core.code_session import CodeSession, TurnAnswerContext
    from chimera.core.guest_history import WorkspaceImageFrame
    from chimera.core.redact import redact_json

    live = CodeSession(SimpleNamespace(), messages=[
        {"role": "user", "content": "Inspect the label"}, WorkspaceImageFrame("label.png", "picture"),
        {"role": "assistant", "content": "Blue."},
    ])
    saved = redact_json(live.to_dict()["messages"])
    context = TurnAnswerContext(tuple(saved), frozenset({1}))
    loaded = CodeSession(SimpleNamespace(), messages=json.loads(json.dumps(saved)))
    assert not isinstance(loaded.messages[1], WorkspaceImageFrame)
    assert not loaded.replace_turn_answer_at(0, 2, saved[0], saved[2], "Blue. Verified.")
    assert loaded.replace_turn_answer_at(0, 2, saved[0], saved[2], "Blue. Verified.", context=context)
    changed = CodeSession(SimpleNamespace(), messages=json.loads(json.dumps(saved)))
    changed.messages[1] = {**changed.messages[1], "content": "A real follow-up from the owner"}
    before = changed.to_dict()["messages"]
    assert not changed.replace_turn_answer_at(0, 2, saved[0], saved[2], "wrong", context=context)
    assert changed.to_dict()["messages"] == before



@pytest.mark.parametrize("actor", ["owner", "other_guest", "ambiguous"])
@pytest.mark.parametrize("marker", [True, {"tool_call_id": "picture"}])
@pytest.mark.parametrize("after_tool", [False, True])
def test_a_copied_persisted_marker_cannot_merge_the_next_actor_into_a_guests_turn(
    actor: str, marker: Any, after_tool: bool,
) -> None:
    import json

    from chimera.api.code_replay import exchanges_from_messages
    from chimera.core.guest_history import (
        AUTO_PLACEHOLDER,
        HUMAN,
        WORKSPACE_IMAGE_KEY,
        project_history,
    )

    first = {"role": "user", "content": "visitor question", "origin": HUMAN, "human_text": "visitor question",
             "author": "Visitor one", "guest_id": "first"}
    second: dict[str, Any] = {"role": "user", "content": "next question", "images": ["label.png"], WORKSPACE_IMAGE_KEY: marker}
    if actor != "ambiguous":
        second.update(origin=HUMAN, human_text="next question")
    if actor == "other_guest":
        second.update(author="Visitor two", guest_id="second")
    messages: list[Any] = [first]
    if after_tool:
        messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": "picture", "type": "function",
                        "function": {"name": "view_image", "arguments": "{}"}}]},
                     {"role": "tool", "tool_call_id": "picture", "content": "visitor observation"}]
    else:
        messages += [{"role": "assistant", "content": "visitor final", "final_seal": True, "model_answer": "visitor final"}]
    messages += [second, {"role": "assistant", "content": "", "tool_calls": [{"id": "owner-read", "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"}}]},
                 {"role": "tool", "tool_call_id": "owner-read", "content": "owner-only fixture observation"},
                 {"role": "assistant", "content": "next final", "final_seal": True, "model_answer": "next final"}]
    replay = exchanges_from_messages(messages)
    assert len(replay) == 2
    assert replay[1]["you"] == "next question"
    projected = project_history(messages, "first")
    assert "owner-only fixture observation" not in json.dumps(projected)
    assert projected[-2]["role"] == "user"
    assert projected[-2]["content"] == (AUTO_PLACEHOLDER if actor == "ambiguous" else "next question")
    assert projected[-1] == {"role": "assistant", "content": "next final"}


def test_internal_provenance_without_a_view_image_trail_is_still_a_separator() -> None:
    from chimera.api.code_replay import exchanges_from_messages
    from chimera.core.guest_history import HUMAN, WORKSPACE_IMAGE_KEY, project_history

    ambiguous = {"role": "user", "content": "Inspect this workspace image and answer the user's task.",
                 "images": ["label.png"], "origin": "auto:workspace_image", WORKSPACE_IMAGE_KEY: {"tool_call_id": "picture"}}
    messages = [
        {"role": "user", "content": "first", "origin": HUMAN, "human_text": "first", "guest_id": "first"},
        {"role": "assistant", "content": "first final", "final_seal": True}, ambiguous,
        {"role": "assistant", "content": "next final", "final_seal": True},
    ]
    assert len(exchanges_from_messages(messages)) == 2
    projected = project_history(messages, "first")
    assert len([m for m in projected if m["role"] == "user"]) == 2


@pytest.mark.parametrize("marker", [True, {"tool_call_id": "picture"}])
def test_live_opener_marking_uses_runtime_identity_not_a_copied_json_marker(marker: Any) -> None:
    from types import SimpleNamespace

    from chimera.core.code_session import CodeSession
    from chimera.core.guest_history import HUMAN, WORKSPACE_IMAGE_KEY

    opener = {"role": "user", "content": "a real request", "images": ["label.png"], WORKSPACE_IMAGE_KEY: marker}
    session = CodeSession(SimpleNamespace(), messages=[opener, {"role": "assistant", "content": "final"}])
    session._mark_opener("a real request", origin=HUMAN, author="Visitor", human_text="a real request", guest_id="visitor")
    assert opener["origin"] == HUMAN and opener["human_text"] == "a real request"
    assert opener["author"] == "Visitor" and opener["guest_id"] == "visitor"



@pytest.mark.parametrize("opening_field", ["author", "human_text", "guest_id", "guidance", "agent_batch"])
def test_internal_image_metadata_with_an_opening_field_is_an_ambiguous_separator(opening_field: str) -> None:
    import json

    from chimera.api.code_replay import exchanges_from_messages
    from chimera.core.guest_history import HUMAN, WorkspaceImageFrame, project_history

    copied = dict(WorkspaceImageFrame("label.png", "picture"))
    copied[opening_field] = {"results": []} if opening_field == "agent_batch" else True if opening_field == "guidance" else "another actor"
    messages = [
        {"role": "user", "content": "first", "origin": HUMAN, "human_text": "first", "guest_id": "first"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "picture", "type": "function",
            "function": {"name": "view_image", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "picture", "content": "first observation"}, copied,
        {"role": "assistant", "content": "", "tool_calls": [{"id": "next", "type": "function",
            "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "next", "content": "other actor fixture observation"},
        {"role": "assistant", "content": "safe final", "final_seal": True},
    ]
    assert len(exchanges_from_messages(messages)) == 2
    projected = project_history(messages, "first")
    assert "other actor fixture observation" not in json.dumps(projected)
    assert len([m for m in projected if m["role"] == "user"]) == 2


def test_the_persisted_image_must_match_the_immediately_preceding_tool_id() -> None:
    from chimera.core.guest_history import WorkspaceImageFrame, workspace_image_positions

    messages = [
        {"role": "assistant", "tool_calls": [{"id": "picture", "function": {"name": "view_image"}}]},
        {"role": "tool", "tool_call_id": "another", "content": "another observation"},
        dict(WorkspaceImageFrame("label.png", "picture")),
    ]
    assert workspace_image_positions(messages) == frozenset()



def test_classifying_image_provenance_does_not_read_unrelated_model_images() -> None:
    from types import SimpleNamespace

    from chimera.core.code_session import CodeSession
    from chimera.providers.gateway import Message

    session = CodeSession(SimpleNamespace(), messages=[
        {"role": "user", "content": "original question"},
        Message(role="user", content="another multimodal question", images=["fixture-image-does-not-exist.png"]),
        {"role": "assistant", "content": "final"},
    ])
    assert session.title() == "original question"



@pytest.mark.parametrize("origin", ["human", "auto", None])
def test_only_the_dedicated_image_origin_may_join_a_matching_tool_trace(origin: str | None) -> None:
    import json

    from chimera.api.code_replay import exchanges_from_messages
    from chimera.core.guest_history import HUMAN, WorkspaceImageFrame, project_history

    next_opener = dict(WorkspaceImageFrame("label.png", "picture"))
    next_opener["origin"] = origin
    messages = [
        {"role": "user", "content": "visitor", "origin": HUMAN, "human_text": "visitor", "guest_id": "visitor"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "picture", "function": {"name": "view_image"}}]},
        {"role": "tool", "tool_call_id": "picture", "content": "visitor observation"}, next_opener,
        {"role": "assistant", "content": "", "tool_calls": [{"id": "next", "function": {"name": "read_file"}}]},
        {"role": "tool", "tool_call_id": "next", "content": "next actor fixture observation"},
        {"role": "assistant", "content": "safe final", "final_seal": True},
    ]
    assert len(exchanges_from_messages(messages)) == 2
    assert "next actor fixture observation" not in json.dumps(project_history(messages, "visitor"))
