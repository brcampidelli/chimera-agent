"""A reopened conversation keeps its receipts.

The turn receipt is the best thing this application produces. It says "price unknown" rather than
zero, it separates "recalled 0 facts" from "we did not look", and it puts the stopping reason first
because that decides how to read the rest. It existed only in the live stream: reopen the
conversation and the words were there and the accounting was gone, along with the verification
verdict on whatever the turn wrote.

**The pairing is the whole risk.** Attributing one turn's receipt to another would report somebody
else's cost, taint and verdict as this turn's, with nothing on screen to suggest anything is wrong
— worse than showing none. Both lists are cut at the FRONT as a conversation grows, so the pairing
is anchored at the END, where the newest turn and the newest receipt always meet.
"""

from __future__ import annotations

from typing import Any

from chimera.api.code_replay import attach_receipts, exchanges_from_messages


def _msgs(*questions: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for q in questions:
        out.append({"role": "user", "content": q})
        out.append({"role": "assistant", "content": f"answered {q}"})
    return out


def _receipt(usd: float | None, **extra: Any) -> dict[str, Any]:
    return {"usd": usd, "steps": 1, "stopped_reason": "final", **extra}


def test_each_turn_gets_its_own_receipt() -> None:
    exchanges = exchanges_from_messages(_msgs("um", "dois", "tres"))
    out = attach_receipts(exchanges, [_receipt(0.1), _receipt(0.2), _receipt(0.3)])

    assert [e["done"]["usd"] for e in out] == [0.1, 0.2, 0.3]


def test_a_trimmed_conversation_pairs_from_the_end() -> None:
    """The case the whole design turns on. Messages are cut at the front as a conversation grows,
    so the surviving exchanges are the LAST ones — and counting receipts forward from zero would
    hand turn three's cost to turn one, silently, on every long conversation."""
    exchanges = exchanges_from_messages(_msgs("quatro", "cinco"))  # one and two were trimmed away
    out = attach_receipts(
        exchanges, [_receipt(0.1), _receipt(0.2), _receipt(0.3), _receipt(0.4), _receipt(0.5)]
    )

    assert [e["done"]["usd"] for e in out] == [0.4, 0.5]


def test_more_turns_than_receipts_leaves_the_oldest_without_one() -> None:
    """A conversation older than receipts, or one whose receipts were capped. The oldest turns get
    `None`, which the screen already renders as "no accounting for this one" — the honest answer,
    and not the same as a receipt full of zeroes."""
    exchanges = exchanges_from_messages(_msgs("um", "dois", "tres"))
    out = attach_receipts(exchanges, [_receipt(0.9)])

    assert [e["done"] for e in out[:2]] == [None, None]
    assert out[2]["done"]["usd"] == 0.9


def test_a_conversation_stored_before_receipts_existed_shows_none() -> None:
    out = attach_receipts(exchanges_from_messages(_msgs("um", "dois")), [])
    assert [e["done"] for e in out] == [None, None]
    assert [e["verified"] for e in out] == [None, None]


def test_the_verdict_travels_beside_the_receipt_not_inside_it() -> None:
    """The screen renders the two in different places. Folding the verdict into the receipt would
    make "the tests failed" look like a cost line."""
    out = attach_receipts(
        exchanges_from_messages(_msgs("um")),
        [_receipt(0.1, verified={"command": "pytest -q", "source": "found", "state": "failed"})],
    )

    assert out[0]["verified"]["state"] == "failed"
    assert "verified" not in out[0]["done"]


def test_price_unknown_survives_as_unknown() -> None:
    """`usd: null` is the receipt's most careful claim — a model whose price nobody published. If a
    replay turned it into 0.0 it would report a free turn, which is a different and false fact."""
    out = attach_receipts(exchanges_from_messages(_msgs("um")), [_receipt(None)])
    assert out[0]["done"]["usd"] is None


def test_the_input_is_not_mutated() -> None:
    """The exchanges come from a parser that other callers share. A function that writes into its
    argument makes the second caller's data depend on whether the first one ran."""
    exchanges = exchanges_from_messages(_msgs("um"))
    attach_receipts(exchanges, [_receipt(0.1)])
    assert "done" not in exchanges[0]


class _Agent:
    def run(self, task: str, **kwargs: Any) -> Any:  # pragma: no cover - never called
        raise AssertionError("no turn is run in these tests")


def test_a_session_stores_and_reloads_its_receipts(tmp_path: Any) -> None:
    from chimera.core.code_session import CodeSession, CodeSessionStore

    store = CodeSessionStore(tmp_path)
    session = CodeSession(_Agent(), session_id="s1", messages=[{"role": "user", "content": "oi"}])
    session.remember_receipt(_receipt(0.42))
    store.save(session)

    assert store.load("s1", _Agent()).receipts == [_receipt(0.42)]


def test_a_receipt_that_will_not_serialise_is_dropped_rather_than_thrown(tmp_path: Any) -> None:
    """A receipt is a record ABOUT a turn that already ran and was already paid for. Failing to
    store one must not be able to fail the turn it describes."""
    from chimera.core.code_session import CodeSession

    session = CodeSession(_Agent(), session_id="s1")
    session.remember_receipt({"usd": object()})  # type: ignore[dict-item]

    assert session.receipts == []


def test_receipts_are_bounded(tmp_path: Any) -> None:
    """A conversation runs for as long as somebody keeps typing; a file that grows per turn without
    a ceiling is a file that eventually stops loading."""
    from chimera.core.code_session import MAX_RECEIPTS, CodeSession

    session = CodeSession(_Agent(), session_id="s1")
    for i in range(MAX_RECEIPTS + 25):
        session.remember_receipt(_receipt(float(i)))

    assert len(session.receipts) == MAX_RECEIPTS
    # The NEWEST are the ones kept: tail-pairing means dropping from the tail would leave the most
    # recent turns — the ones anybody is actually looking at — without their accounting.
    assert session.receipts[-1]["usd"] == float(MAX_RECEIPTS + 24)


def test_an_old_session_file_without_receipts_loads(tmp_path: Any) -> None:
    import json

    from chimera.core.code_session import CodeSessionStore

    (tmp_path / "old.json").write_text(
        json.dumps({"session_id": "old", "workspace": "", "messages": []}), encoding="utf-8"
    )

    assert CodeSessionStore(tmp_path).load("old", _Agent()).receipts == []
