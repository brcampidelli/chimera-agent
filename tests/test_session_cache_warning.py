"""A conversation whose cache rate falls under 80% is told so once, with the cause that cost the most.

The rate is over the whole conversation, not a turn: one cold turn after a pause is the cache doing
its job. These tests pin the places where a naive version warns wrongly: on the opening turn, on a
route that never reported a cache, on every turn once it is already under, and at exactly 80%.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.cache_miss import CacheTurn, session_cache_warning
from chimera.core.code_session import CodeSession


def _t(prompt: int, read: int | None, cause: str = "none", first: bool = False) -> CacheTurn:
    return CacheTurn(prompt_tokens=prompt, cache_read_tokens=read, cause=cause, first_turn=first)


FIRST = _t(1000, 0, "first_turn", first=True)


def test_sixty_percent_warns_and_names_the_cause() -> None:
    warning = session_cache_warning([FIRST, _t(1000, 600, "ttl_expired")])
    assert warning is not None
    assert warning.rate == 0.6 and warning.turns == 1
    assert warning.cause == "ttl_expired"
    assert warning.threshold == 0.80


def test_eighty_five_percent_does_not_warn() -> None:
    assert session_cache_warning([FIRST, _t(1000, 850)]) is None


def test_exactly_eighty_percent_is_not_under_the_threshold() -> None:
    assert session_cache_warning([FIRST, _t(1000, 800, "ttl_expired")]) is None


def test_a_route_that_reports_no_cache_never_warns() -> None:
    assert session_cache_warning([_t(1000, None, "unknown", first=True), _t(1000, None, "unknown")]) is None
    assert session_cache_warning([_t(1000, None, "unknown")] * 5) is None


def test_the_opening_turn_is_outside_the_rate() -> None:
    assert session_cache_warning([FIRST]) is None
    # Its zero would drag 2 turns of 90% down to 60%; left out, the conversation is fine.
    assert session_cache_warning([FIRST, _t(1000, 900), _t(1000, 900)]) is None


def test_it_warns_on_the_crossing_and_not_again_while_it_stays_under() -> None:
    turns = [FIRST, _t(1000, 900), _t(1000, 100, "prefix_changed")]
    assert session_cache_warning(turns) is not None
    still_under = [*turns, _t(1000, 100, "prefix_changed")]
    assert session_cache_warning(still_under) is None


def test_it_rearms_once_the_rate_recovers() -> None:
    turns = [FIRST, _t(1000, 900), _t(1000, 100, "prefix_changed")]
    recovered = [*turns, *[_t(1000, 1000)] * 20]
    assert session_cache_warning(recovered) is None
    # Recovered above the line, then a bad stretch takes it under again: that crossing warns.
    again = [*recovered, *[_t(1000, 0, "tools_changed")] * 5]
    assert session_cache_warning(again[:-1]) is None  # four cold turns on the recovery: still over
    assert session_cache_warning(again) is not None


def test_the_cause_is_the_one_that_cost_the_most_tokens() -> None:
    turns = [
        FIRST,
        _t(10000, 10000),
        _t(1000, 0, "tools_changed"),
        _t(500, 0, "tools_changed"),
        _t(4000, 0, "ttl_expired"),
    ]
    warning = session_cache_warning(turns)
    assert warning is not None and warning.cause == "ttl_expired"


def test_a_partial_hit_has_no_rule_behind_it_so_it_counts_as_unknown() -> None:
    warning = session_cache_warning([FIRST, _t(10000, 5000, "none")])
    assert warning is not None and warning.cause == "unknown"


def test_a_read_larger_than_the_prompt_cannot_lift_the_rate_past_the_turn() -> None:
    # Reported reads can exceed the prompt of a step; the rate is capped per turn.
    assert session_cache_warning([FIRST, _t(1000, 5000), _t(1000, 0, "unknown")]) is not None


def test_the_window_survives_a_save_and_a_reload(tmp_path: Path) -> None:
    from chimera.core.code_session import CodeSessionStore

    store = CodeSessionStore(tmp_path)
    session = store.load("s1", agent=_Agent())  # type: ignore[arg-type]
    assert session.record_cache_turn(FIRST) is None
    assert session.record_cache_turn(_t(1000, 900)) is None
    store.save(session)
    again = store.load("s1", agent=_Agent())  # type: ignore[arg-type]
    warning = again.record_cache_turn(_t(1000, 0, "ttl_expired"))
    assert warning is not None and warning.cause == "ttl_expired"
    # And the next turn, still under, does not repeat it.
    assert again.record_cache_turn(_t(1000, 0, "ttl_expired")) is None


class _Agent:
    def run(self, *_a: object, **_k: object) -> object:  # pragma: no cover - never run here
        raise AssertionError


def test_an_old_file_without_the_window_reads_as_no_turns(tmp_path: Path) -> None:
    session = CodeSession(session_id="x", agent=_Agent())  # type: ignore[arg-type]
    assert session.cache_turns == []
    assert "cache_turns" not in session.to_dict()


def test_the_turn_receipt_carries_the_warning_once_the_conversation_crosses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_the_turn_receipt_explains_the_cache_miss import _client, _done, _log

    # Two turns of the SAME conversation, each reporting a cache read of zero. The first is the
    # opening turn and is outside the rate; the second is a miss with nothing cached, so the rate
    # is 0% for the first time and the receipt says so, with the cause that turn was classified as.
    client = _client(tmp_path, monkeypatch, _log(cached=0))
    first = _done(client.post("/api/code/turn", json={"message": "oi", "session_id": "w1"}))
    assert first["cache_warning"] is None
    second = _done(client.post("/api/code/turn", json={"message": "de novo", "session_id": "w1"}))
    warning = second["cache_warning"]
    assert warning is not None
    assert warning["rate"] == 0.0 and warning["threshold"] == 0.8 and warning["turns"] == 1
    third = _done(client.post("/api/code/turn", json={"message": "e mais", "session_id": "w1"}))
    assert third["cache_warning"] is None


def test_a_silent_route_has_no_warning_on_the_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_the_turn_receipt_explains_the_cache_miss import _client, _done, _log

    client = _client(tmp_path, monkeypatch, _log(cached=None))
    for message in ("a", "b", "c"):
        done = _done(client.post("/api/code/turn", json={"message": message, "session_id": "w2"}))
        assert done["cache_warning"] is None


def test_a_crossing_that_depends_on_the_oldest_turn_is_seen_at_the_cap() -> None:
    from chimera.core.cache_miss import CACHE_WINDOW_TURNS

    session = CodeSession(session_id="cap", agent=_Agent())  # type: ignore[arg-type]
    # One large, fully cached turn at the back of the window, then small cold ones: 200 turns in all.
    session.cache_turns = [_t(10000, 10000).to_record()] + [
        _t(10, 0, "ttl_expired").to_record() for _ in range(CACHE_WINDOW_TURNS - 1)
    ]
    assert len(session.cache_turns) == CACHE_WINDOW_TURNS
    # Over the whole window the rate is 10000/11990 (over 0.8). The new cold turn takes it under, but
    # only if the oldest turn is still counted in the "before" rate: truncating first would lose it.
    warning = session.record_cache_turn(_t(1000, 0, "ttl_expired"))
    assert warning is not None and warning.cause == "ttl_expired"
    assert len(session.cache_turns) == CACHE_WINDOW_TURNS


def _full_window(first_read: int, middle_read: int) -> CodeSession:
    from chimera.core.cache_miss import CACHE_WINDOW_TURNS

    session = CodeSession(session_id="cap", agent=_Agent())  # type: ignore[arg-type]
    session.cache_turns = [_t(1000, first_read, "ttl_expired").to_record()] + [
        _t(1000, middle_read, "ttl_expired").to_record() for _ in range(CACHE_WINDOW_TURNS - 1)
    ]
    assert len(session.cache_turns) == CACHE_WINDOW_TURNS
    return session


def test_the_retained_window_falling_under_is_seen_when_the_oldest_turn_leaves() -> None:
    # Held: (1000 + 799*199) / 200000 = 80.0005%. Held after: (799*199 + 799) / 200000 = 79.9%.
    session = _full_window(1000, 799)
    warning = session.record_cache_turn(_t(1000, 799, "ttl_expired"))
    assert warning is not None and warning.rate < 0.8


def test_a_rate_that_rises_in_the_retained_window_gives_no_false_warning() -> None:
    # Held: (0 + 805*199) / 200000 = 80.0975%. Held after: (805*199 + 400) / 200000 = 80.2975%.
    # A 201-turn comparison read 79.898% and warned although the window the person has went UP.
    session = _full_window(0, 805)
    assert session.record_cache_turn(_t(1000, 400, "ttl_expired")) is None
