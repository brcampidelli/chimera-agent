"""A matched authorization-equivalence triple must differ ONLY in the source of its value.

This is the §2s guard: a counterfactual that changes the *type* of what it changes measures
something else. If a later edit lets one row's action, args or effect drift from its siblings, the
three rows stop being the same committed write and the false-positive number they produce stops
meaning anything. This test fails the build in that case.

Sabotage-verified: editing one arg in one row (``config/app.yml`` -> ``config/other.yml`` in the
LEGIT_TOOL row of a triple) makes ``test_every_triple_holds_action_args_effect_fixed`` fail with a
mismatch on that triple; restored, it passes.
"""

from __future__ import annotations

from collections import defaultdict

from chimera.eval.authorization import EquivalenceRow, ValueSource, default_triples


def _by_triple() -> dict[str, list[EquivalenceRow]]:
    groups: dict[str, list[EquivalenceRow]] = defaultdict(list)
    for row in default_triples():
        groups[row.triple_id].append(row)
    return dict(groups)


def test_the_corpus_is_whole_triples_at_least_eight() -> None:
    """At least eight triples, and every group is exactly the three sources — no orphan rows."""
    groups = _by_triple()
    assert len(groups) >= 8, "the task registers at least eight matched triples"
    for triple_id, rows in groups.items():
        sources = sorted(r.source.value for r in rows)
        assert sources == ["legit_tool", "untrusted", "user"], (
            f"triple {triple_id!r} is not a full user/legit_tool/untrusted triple: {sources}"
        )


def test_every_triple_holds_action_args_effect_fixed() -> None:
    """The committed (action, args, effect) is identical across a triple; only the source differs."""
    for triple_id, rows in _by_triple().items():
        committed = {r.committed() for r in rows}
        assert len(committed) == 1, (
            f"triple {triple_id!r} varies more than its source: {sorted(committed)}"
        )
        assert len({r.source for r in rows}) == 3, f"triple {triple_id!r} does not vary its source"


def test_the_two_read_variants_are_byte_identical_reads() -> None:
    """LEGIT_TOOL and UNTRUSTED read the SAME bytes — only the source label differs.

    This is what makes the counterfactual maximal: a user-requested read and an attacker-injected
    read carry identical content, so any verdict difference between them would have to come from the
    label, and there is none. USER reads nothing.
    """
    for triple_id, rows in _by_triple().items():
        by_source = {r.source: r for r in rows}
        assert by_source[ValueSource.USER].read_content == "", (
            f"triple {triple_id!r}: the USER variant must read nothing"
        )
        legit = by_source[ValueSource.LEGIT_TOOL].read_content
        untrusted = by_source[ValueSource.UNTRUSTED].read_content
        assert legit and legit == untrusted, (
            f"triple {triple_id!r}: LEGIT_TOOL and UNTRUSTED must read byte-identical content"
        )


def test_every_distinct_write_type_action_is_covered() -> None:
    """Every effectful action class the narrowing net reaches has at least one triple."""
    actions = {r.action for r in default_triples()}
    # file write, patch/batch write, shell exec, outbound send, and both GET/POST net calls.
    for expected in (
        "write_file",
        "run_shell",
        "send_email",
        "http_get",
        "apply_patch",
        "edit_batch",
        "http_post",
    ):
        assert expected in actions, f"no triple covers {expected!r}"
