"""Durable inbox behavior for multi-surface Code turns."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from chimera.api.code_session_queue import (
    MAX_PENDING_PER_SESSION,
    CodeTurnQueue,
    QueueDrainer,
    QueueFullError,
)


def test_queue_is_idempotent_and_reports_position(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    first, created = queue.enqueue("session-1", "client-a", {"message": "one"})
    second, duplicate_created = queue.enqueue("session-1", "client-b", {"message": "two"})
    duplicate, duplicate_created_again = queue.enqueue(
        "session-1", "client-a", {"message": "one"}, source="app"
    )
    with pytest.raises(ValueError, match="different queued request"):
        queue.enqueue("session-1", "client-a", {"message": "different"}, source="app")
    with pytest.raises(ValueError, match="invalid queue source"):
        queue.enqueue("session-1", "client-c", {"message": "third"}, source="unknown")

    assert created and duplicate_created and not duplicate_created_again
    assert first.position == 1
    assert second.position == 2
    assert duplicate.id == first.id
    assert duplicate.request == {"message": "one"}
    assert duplicate.source == "app"
    claimed = queue.claim_next("session-1")
    assert claimed is not None and claimed.id == first.id
    queue.finish("session-1", first.id)
    terminal_retry, terminal_created = queue.enqueue("session-1", "client-a", {"message": "one"})
    assert not terminal_created
    assert terminal_retry.id == first.id and terminal_retry.state == "done"
    # The retry claimed nothing new: the next claim is the OTHER client's message. (This line
    # asserted None, which was false — client-b was still waiting — and was never run before.)
    next_claim = queue.claim_next("session-1")
    assert next_claim is not None and next_claim.id == second.id
    assert queue.claim_next("session-1") is None


def test_queue_claims_in_order_and_persists_terminal_states(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    first, _ = queue.enqueue("session-1", "a", {"message": "first"})
    second, _ = queue.enqueue("session-1", "b", {"message": "second"})

    claimed = queue.claim_next("session-1")
    assert claimed is not None and claimed.id == first.id and claimed.state == "running"
    assert queue.claim_next("session-1") is None  # there is one executor per session
    assert queue.finish("session-1", first.id)
    claimed_second = queue.claim_next("session-1")
    assert claimed_second is not None and claimed_second.id == second.id
    assert queue.fail("session-1", second.id)

    reopened = CodeTurnQueue(tmp_path)
    states = {item.id: item.state for item in reopened.list("session-1")}
    assert states == {first.id: "done", second.id: "failed"}


def test_cancel_only_removes_pending_items_and_torn_tail_is_ignored(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    item, _ = queue.enqueue("session-1", "a", {"message": "hello"})
    assert queue.cancel("session-1", item.id)
    assert not queue.cancel("session-1", item.id)

    path = tmp_path / "session-1.queue.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"id":"partial"')
    reopened = CodeTurnQueue(tmp_path)
    assert [(entry.id, entry.state) for entry in reopened.list("session-1")] == [
        (item.id, "cancelled")
    ]


def test_restart_marks_running_interrupted_without_replaying(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    item, _ = queue.enqueue("session-1", "client", {"message": "do not repeat"})
    assert queue.claim_next("session-1") is not None
    before = (tmp_path / "session-1.queue.jsonl").read_text(encoding="utf-8").splitlines()

    recovered = CodeTurnQueue(tmp_path)
    current = recovered.get("session-1", item.id)
    after = (tmp_path / "session-1.queue.jsonl").read_text(encoding="utf-8").splitlines()
    assert current is not None and current.state == "interrupted"
    # Durable, not just in memory: the file says interrupted. The quiet queue is then compacted
    # (every item terminal), so the superseded ``running`` line is gone rather than appended past.
    assert before and json.loads(before[-1])["state"] == "running"
    assert [json.loads(line)["state"] for line in after] == ["interrupted"]
    assert recovered.claim_next("session-1") is None


def test_caller_posture_and_source_are_kept_in_the_durable_item(tmp_path: Path) -> None:
    # Rewritten: the first version stored a guest's identity INSIDE the request ("author": "Guest")
    # with source "guest", which is the defect the review found. The identity is the queue's own
    # ``author`` field, set by the server; the request is only what the screen asked for.
    queue = CodeTurnQueue(tmp_path)
    item, _ = queue.enqueue(
        "session-1",
        "guest-msg-1",
        {"message": "change a file", "posture": {"reach": "read_only", "approval": "always"}},
        source="guest",
        author="Guest",
    )
    reopened = CodeTurnQueue(tmp_path).get("session-1", item.id)

    assert reopened is not None
    assert reopened.source == "guest"
    assert reopened.author == "Guest"
    assert reopened.request["posture"]["reach"] == "read_only"
    assert reopened.request["posture"]["approval"] == "always"


def test_a_label_is_not_an_identity(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    terminal, _ = queue.enqueue("session-1", "t", {"message": "x"}, source="terminal")
    bridge, _ = queue.enqueue("session-1", "b", {"message": "y"}, source="bridge")
    assert terminal.author == "" and bridge.author == ""  # the owner's own screens run as the owner
    with pytest.raises(ValueError, match="only a guest"):
        queue.enqueue("session-1", "g", {"message": "z"}, source="guest")  # a label with no guest
    with pytest.raises(ValueError, match="only a guest"):
        queue.enqueue("session-1", "a", {"message": "z"}, source="app", author="Mallory")


def test_an_append_after_a_torn_tail_lands_on_its_own_line(tmp_path: Path) -> None:
    """A crash mid-append left bytes with no newline; the next snapshot used to glue onto them."""
    queue = CodeTurnQueue(tmp_path)
    first, _ = queue.enqueue("session-1", "a", {"message": "first"})
    second, _ = queue.enqueue("session-1", "b", {"message": "second"})
    assert queue.claim_next("session-1") is not None
    with (tmp_path / "session-1.queue.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"id":"torn","sta')

    assert queue.finish("session-1", first.id)
    current = queue.get("session-1", first.id)
    assert current is not None and current.state == "done", "the finish snapshot was swallowed"
    claimed = queue.claim_next("session-1")
    assert claimed is not None and claimed.id == second.id, "the conversation's queue was stuck"


def test_a_conversation_holds_a_bounded_number_of_waiting_messages(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    for index in range(MAX_PENDING_PER_SESSION):
        queue.enqueue("session-1", f"c{index}", {"message": str(index)})
    with pytest.raises(QueueFullError):
        queue.enqueue("session-1", "one-too-many", {"message": "x"})
    retry, created = queue.enqueue("session-1", "c0", {"message": "0"})  # a retry is not a new one
    assert not created and retry.position == 1
    assert queue.claim_next("session-1") is not None
    _, created = queue.enqueue("session-1", "one-too-many", {"message": "x"})
    assert created, "a slot freed by a claimed item stayed taken"


def test_a_quiet_queue_is_compacted_to_one_line_per_item(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    ids = [queue.enqueue("session-1", f"c{i}", {"message": str(i)})[0].id for i in range(3)]
    for _ in ids:
        claimed = queue.claim_next("session-1")
        assert claimed is not None
        queue.finish("session-1", claimed.id)
    lines = (tmp_path / "session-1.queue.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, "terminal snapshots kept piling up"
    assert {json.loads(line)["state"] for line in lines} == {"done"}
    # The client ids stay reserved after compaction: a late retry observes completion.
    retry, created = queue.enqueue("session-1", "c1", {"message": "1"})
    assert not created and retry.state == "done"


def test_a_claimed_item_refused_by_a_full_conversation_goes_back_to_waiting(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    item, _ = queue.enqueue("session-1", "a", {"message": "x"})
    assert queue.claim_next("session-1") is not None
    assert queue.requeue("session-1", item.id)
    current = queue.get("session-1", item.id)
    assert current is not None and current.state == "pending" and current.position == 1


def test_startup_reports_what_it_interrupted(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    item, _ = queue.enqueue("session-1", "a", {"message": "x"})
    queue.enqueue("session-1", "b", {"message": "y"})
    assert queue.claim_next("session-1") is not None
    recovered = CodeTurnQueue(tmp_path)
    assert [entry.id for entry in recovered.recovered] == [item.id]
    assert recovered.sessions_with_pending() == ["session-1"]
    assert CodeTurnQueue(tmp_path).recovered == []  # once, not on every start


def test_a_drain_requested_while_another_holds_the_lock_is_not_lost() -> None:
    """The ending turn's request arrives while a drain is busy and has already looked."""
    looked = threading.Event()
    proceed = threading.Event()
    calls: list[str] = []

    def drain(session_id: str) -> None:
        calls.append(session_id)
        if len(calls) == 1:
            looked.set()  # the holder has read the queue and found nothing
            assert proceed.wait(5)

    drainer = QueueDrainer(drain)
    holder = threading.Thread(target=drainer.request, args=("session-1",))
    holder.start()
    assert looked.wait(5)
    drainer.request("session-1")  # cannot take the lock; returns at once
    assert calls == ["session-1"]
    proceed.set()
    holder.join(5)
    assert calls == ["session-1", "session-1"], "the second request was dropped"


def test_cancelled_item_is_removed_with_its_conversation(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    item, _ = queue.enqueue("session-1", "client", {"message": "hello"})
    queue.forget("session-1")

    assert queue.get("session-1", item.id) is None
    assert not (tmp_path / "session-1.queue.jsonl").exists()
