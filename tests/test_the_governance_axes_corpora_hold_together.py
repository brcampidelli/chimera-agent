"""The corpora of `bench/governance_axes` are what their pre-registration says (study 30, S30-36).

The representation corpus is only an instrument if each representation carries exactly the channels
it claims — a flattener that kept a comment, or a snapshot that kept a hidden div, would make the
comparison measure the flattener. Checked here, before any model sees a page, and against the
corpus's own `instrument()` so a change to either side is caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench" / "governance_axes"))

import preemption  # noqa: E402
import representation  # noqa: E402


def test_every_representation_carries_exactly_the_channels_it_claims() -> None:
    assert representation.instrument() == []
    pages = representation.pages()
    assert len(pages) == 100 and len({p.id for p in pages}) == 100
    assert len({p.canary for p in pages}) == 100  # one canary per page, so a hit names its page


def test_the_instrument_sees_a_flattener_that_keeps_a_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    claimed = frozenset({"comment", "hidden_div", "zero_font", "aria_hidden"})
    monkeypatch.setitem(representation.SURVIVES, "text", claimed)
    assert any("comment text" in problem for problem in representation.instrument())


def test_the_injection_is_absent_from_the_article_text_of_every_snapshot_control() -> None:
    for page in representation.pages():
        if page.channel not in representation.SURVIVES["snapshot"]:
            assert page.canary not in representation.represent(page, "snapshot")


def test_the_preemption_pairs_differ_only_in_whose_resource_they_touch() -> None:
    pairs = preemption.PAIRS
    assert len(pairs) == 16 and len({p.id for p in pairs}) == 16
    for pair in pairs:
        assert pair.attack != pair.twin
        assert "run-b" not in pair.twin  # a twin never reaches the other run
