"""The acceptance ledger — the thing that would have to exist before any claim about quality.

These are small on purpose. The value is not in the arithmetic; it is in the one behaviour that
would let the feature overstate itself, which is reporting a rate before anyone has answered.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.complete.outcomes import acceptance, record_outcome, record_shown


def test_a_rate_with_no_answers_is_null_not_zero(tmp_path: Path) -> None:
    """Zero is a claim — "nobody wants these". Null is the truth: nobody has said yet."""
    record_shown(tmp_path, ms=120, chars=14, model="m")

    found = acceptance(tmp_path)

    assert found["shown"] == 1
    assert found["rate"] is None
    assert found["note"]


def test_the_rate_is_over_what_was_answered(tmp_path: Path) -> None:
    """Denominator = accepted + dismissed, NOT shown.

    Over `shown` it would drift towards zero every time a suggestion scrolled off unanswered, which
    reads as the model getting worse when what changed is that people stopped replying.
    """
    for _ in range(4):
        record_shown(tmp_path, ms=100, chars=10, model="m")
    record_outcome(tmp_path, ident="a", accepted=True)
    record_outcome(tmp_path, ident="b", accepted=False)

    found = acceptance(tmp_path)

    assert found["shown"] == 4
    assert found["rate"] == 0.5
    assert found["note"] == ""


def test_the_ledger_holds_no_code(tmp_path: Path) -> None:
    """An acceptance rate does not need to know what you were writing.

    A local file that quietly accumulated your source under a name nobody expects would be a copy
    of the repository in a place no backup policy covers.
    """
    record_shown(tmp_path, ms=90, chars=12, model="m")

    rows = [
        json.loads(line)
        for line in (tmp_path / "completion_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert set(rows[0]) == {"id", "event", "ms", "chars", "model"}


def test_a_corrupt_line_does_not_lose_the_rest(tmp_path: Path) -> None:
    # Append-only files get truncated by crashes. Losing the tally to one bad line would be a poor
    # trade for a statistic whose only job is to accumulate.
    record_shown(tmp_path, ms=100, chars=10, model="m")
    with (tmp_path / "completion_outcomes.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"event": "sho\n')
    record_outcome(tmp_path, ident="a", accepted=True)

    found = acceptance(tmp_path)

    assert found["shown"] == 1 and found["accepted"] == 1
