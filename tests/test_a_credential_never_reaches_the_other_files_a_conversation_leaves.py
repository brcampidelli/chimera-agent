"""The rest of what a conversation writes: frames, the queue, titles and the completion cache.

The chat transcript was masked and these were not: a key pasted into a turn survived in
`frames.jsonl`, in a queued request kept after it ran, in a renamed title and in the opt-in
completion cache.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.code_session_queue import CodeTurnQueue
from chimera.core.code_session_marks import CodeSessionMarks
from chimera.orchestration import runlog
from chimera.providers.cache import CompletionCache

SECRET = "opaque_" + "0123456789abcdef-pasted"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    # `_KEY` only: the name the old marker list did not cover.
    monkeypatch.setenv("STRIPE_KEY", SECRET)


def _everything_under(root: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*") if p.is_file())


def test_a_run_frame_is_masked_in_the_file(tmp_path: Path) -> None:
    runlog.append(tmp_path, "r1", "tool_result", {"text": f"the key is {SECRET}", "n": 3, "seq": 1})
    assert SECRET not in _everything_under(tmp_path)
    assert runlog.frames(tmp_path, "r1")[0]["n"] == 3


def test_a_queued_request_is_masked_and_a_retry_is_still_the_same_request(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    request = {"message": f"use {SECRET} to deploy"}
    first = queue.enqueue("s1", "c1", request)
    assert SECRET not in _everything_under(tmp_path)
    # The same client id and the same request, retried, must not read as "a different request".
    again = queue.enqueue("s1", "c1", request)
    assert again[0].id == first[0].id


def test_a_renamed_title_is_masked(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "marks.json")
    marks.rename("s1", f"deploy with {SECRET}")
    marks.stamp_title("s2", f"deploy with {SECRET}", source="caller")
    assert SECRET not in _everything_under(tmp_path)


def test_a_completion_with_a_secret_is_not_cached(tmp_path: Path) -> None:
    cache = CompletionCache(tmp_path / "cache.json")
    cache.put("k1", {"content": f"your key is {SECRET}"})
    cache.put("k2", {"content": "fine"})
    assert SECRET not in _everything_under(tmp_path)
    assert cache.get("k1") is None
    assert cache.get("k2") == {"content": "fine"}


def test_a_queued_turn_runs_with_the_secret_while_the_disk_keeps_the_mask(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    queue.enqueue("s1", "c1", {"message": f"deploy with {SECRET}"})

    claimed = queue.claim_next("s1")

    assert claimed is not None
    assert SECRET in claimed.request["message"]  # what the turn runs from: the original
    assert not claimed.ran_without_secret
    assert SECRET not in _everything_under(tmp_path)  # what the disk keeps: the mask


def test_two_requests_that_differ_only_in_the_secret_are_not_the_same_retry(tmp_path: Path) -> None:
    queue = CodeTurnQueue(tmp_path)
    queue.enqueue("s1", "c1", {"message": f"use {SECRET}-one"})
    with pytest.raises(ValueError, match="different queued request"):
        queue.enqueue("s1", "c1", {"message": f"use {SECRET}-two"})


def test_after_a_restart_the_item_runs_masked_and_says_so(tmp_path: Path) -> None:
    from chimera.api import code_session_queue

    CodeTurnQueue(tmp_path).enqueue("s1", "c1", {"message": f"deploy with {SECRET}"})
    code_session_queue._ORIGINAL_REQUESTS.clear()  # the process went away; the file stayed

    claimed = CodeTurnQueue(tmp_path).claim_next("s1")

    assert claimed is not None
    assert claimed.ran_without_secret
    assert SECRET not in claimed.request["message"]
    # A message with nothing to mask is not reported as having lost anything.
    CodeTurnQueue(tmp_path).enqueue("s2", "c2", {"message": "plain"})
    code_session_queue._ORIGINAL_REQUESTS.clear()
    plain = CodeTurnQueue(tmp_path).claim_next("s2")
    assert plain is not None and not plain.ran_without_secret
