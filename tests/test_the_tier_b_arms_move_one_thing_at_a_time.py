"""The Tier B arms of study 21, pinned: the instrument variants move the state and the question, never
each other, and the arms that must spend nothing still spend nothing.

`bench/jev_decisions/PREREGISTRATION-tier-b.md` registered four arms before any of them ran. What is
held here is what has to stay true for the run to mean anything:

* **B1(a) reverses the OPTIONS, not the question.** The `danger` Noul is byte-identical to the
  registered one — a yes/no question has no order to reverse, and a Noul that changed with the
  Choice's order would mean the arm moved two things at once.
* **B1(b) sends ANOTHER item's state, and never its own.** A derangement is a permutation with no
  fixed point; a pairing that left an item with its own state would silently measure the baseline.
* **B3 puts the sentence in the QUESTION, never the state.** The state is byte-identical to the
  registered run's — that is what makes the A/B an A/B, and a sentence that reached the state would
  change the command the kernel renders.
* **B2 batches STATES, one question per slot**, with an explicit separator, and the state text of
  each slot survives byte-for-byte inside the joined request.
* **The corpus is not invented.** `claims.load()` reads the local run; where it is absent the arm
  reports no rows rather than making them up.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import bench.jev_decisions.run as run  # noqa: E402


def test_b1a_reverses_the_options_and_leaves_the_noul_alone() -> None:
    """One variable moved. The Noul is the registered one, object for object."""
    assert run.QUESTIONS_REVERSED["danger"] is run.QUESTIONS["danger"]
    assert run.LABELS_REVERSED == ("ALLOW", "REVIEW", "BLOCK")
    assert list(run.QUESTIONS["verdict"]["criteria"]) == ["BLOCK", "REVIEW", "ALLOW"]
    assert list(run.QUESTIONS_REVERSED["verdict"]["criteria"]) == ["ALLOW", "REVIEW", "BLOCK"]
    # Same three meanings, same instructions — only the order differs.
    assert run.QUESTIONS_REVERSED["verdict"]["criteria"] == {
        k: run.QUESTIONS["verdict"]["criteria"][k] for k in ("ALLOW", "REVIEW", "BLOCK")
    }
    assert run.QUESTIONS_REVERSED["verdict"]["instructions"] == run.QUESTIONS["verdict"]["instructions"]


def test_b1b_is_a_derangement_so_no_item_is_paired_with_itself() -> None:
    items = [{"id": f"i{i}", "state": f"state {i}"} for i in range(6)]
    pairing = run._derangement(items)
    assert set(pairing) == {i["id"] for i in items}
    for item in items:
        assert pairing[item["id"]] != item["state"], f"{item['id']} was paired with its own state"
    # A permutation, not a collapse onto one state: every state is used exactly once.
    assert sorted(pairing.values()) == sorted(i["state"] for i in items)


def test_b3_puts_the_sentence_in_the_question_and_never_in_the_state() -> None:
    questions = run.boundary_questions()
    for key, q in questions.items():
        assert q["instructions"].startswith(run.BOUNDARY_SENTENCE)
        assert run.QUESTIONS[key]["instructions"] in q["instructions"]
    # The state a boundary task carries is the registered state, byte for byte.
    tasks = run.tier_b_tasks("Lb")
    by_id = {i["id"]: i["state"] for i in run.two_sided_items()}
    assert tasks
    for task in tasks:
        assert task["variant"] == "boundary"
        assert task["state"] == by_id[task["item"]["id"]]
        assert run.BOUNDARY_SENTENCE not in task["state"]
        assert task["system"].startswith(run.BOUNDARY_SENTENCE)


def test_the_tier_b_arms_carry_the_variant_they_claim() -> None:
    tasks = run.tier_b_tasks("Lr,Ls,Lb,Jr,Js,Jb,Jbatch")
    variants = {t["arm"]: t["variant"] for t in tasks}
    assert variants == {
        "Lr": "reversed", "Jr": "reversed", "Ls": "shuffled", "Js": "shuffled",
        "Lb": "boundary", "Jb": "boundary", "Jbatch": "batch",
    }
    # The reversed arms carry the reversed labels; the others carry the registered ones.
    for task in tasks:
        if task["arm"] in ("Lr", "Jr"):
            assert task["labels"] == run.LABELS_REVERSED
            assert task["qs"] is run.QUESTIONS_REVERSED
        elif task["arm"] in ("Ls", "Js"):
            assert task["qs"] is None


def test_the_local_reversed_arm_reads_the_reversed_label_set() -> None:
    """`local` must read the labels it was handed — the reader is what B1(a) is about.

    Without this, reversing the options would change the request and not the reading, and the arm
    would measure nothing while looking like it ran.
    """
    import inspect

    source = inspect.getsource(run.local)
    assert "labels: tuple[str, ...] = LABELS" in source
    assert "list(labels)" in source, "the reader was left hard-coded to the registered labels"


def test_b2_splits_the_fifty_five_items_into_groups_of_ten_and_keeps_every_state_intact() -> None:
    items = run.two_sided_items()
    groups = run._batches(items, 10)
    assert [len(g) for g in groups] == [10, 10, 10, 10, 10, 5]
    assert sum(len(g) for g in groups) == len(items)
    # Every state survives the join byte-for-byte — a slot boundary is never a silent concatenation.
    joined = "\n\n---\n\n".join(i["state"] for g in groups for i in g)
    for item in items:
        assert item["state"] in joined


def test_a_run_with_no_matching_arms_refuses_instead_of_writing_an_empty_meta(tmp_path: Path) -> None:
    """The defect this arm actually had: `run()` was called without `tier_b`, the arms matched
    nothing, and a meta line was written as though the run had happened."""
    import httpx

    with pytest.raises(SystemExit, match="no requests registered"):
        run.run(tmp_path / "empty.jsonl", httpx.Client(), None, arms="Lr,Ls,Lb", tier_b=False)
    assert not (tmp_path / "empty.jsonl").exists()


def test_the_claim_noul_state_carries_no_task_identity() -> None:
    """PROTOCOL §7 — the arm cannot tell which task it is looking at, or the leak-free claim dies."""
    sys.path.insert(0, str(REPO / "bench" / "claim_vs_diff"))
    import claims  # noqa: E402

    from bench.jev_decisions.run_claim_noul import state_of  # noqa: E402

    solve = claims.Solve(
        task="oc-bench-v2-011-code-debug", hid="arm-000-r0", claim="I fixed parser.py",
        self_report=True, oracle=1.0, patch_text="--- a/parser.py\n+++ b/parser.py\n",
    )
    state = state_of(solve)
    assert "parser.py" in state and "I fixed parser.py" in state
    assert solve.task not in state, "the task id rode into the state"
    assert "arm-000-r0" not in state, "the arm id rode into the state"


def test_a_long_diff_is_truncated_from_the_end_not_the_head() -> None:
    """The head of a patch carries the file headers a summary talks about; a truncated tail is the
    smaller distortion."""
    sys.path.insert(0, str(REPO / "bench" / "claim_vs_diff"))
    import claims  # noqa: E402

    from bench.jev_decisions.run_claim_noul import state_of  # noqa: E402

    solve = claims.Solve(
        task="t", hid="h", claim="c", self_report=True, oracle=1.0,
        patch_text="--- a/first.py\n" + ("x" * 20000) + "\n--- a/last.py\n",
    )
    state = state_of(solve, max_diff_chars=1000)
    assert "first.py" in state and "last.py" not in state
    assert state.endswith("… (diff truncated)")