"""A 0% bias number is also what a judge that stops choosing produces (arXiv 2609.12439: tie rates
rose from under 1% to 31% under debiasing while the bias metric improved). `bench/judge_blind_prose`
reported 0/180 propagation and nothing about resolution; `corpus.resolution` is the same partition
read as whether the final COMMITTED — and it has to tell a hedge from a choice, or the resolution
table beside the bias table is decoration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CORPUS = Path(__file__).resolve().parent.parent / "bench" / "judge_blind_prose" / "corpus.py"


def _corpus():
    spec = importlib.util.spec_from_file_location("judge_blind_prose_corpus", _CORPUS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["judge_blind_prose_corpus"] = module
    spec.loader.exec_module(module)
    return module


def _item(corpus):
    return corpus.Item(
        id="datastore", passage="The service stores sessions in Postgres.", instruction="Summarise.",
        right="Postgres", wrong="Redis", faithful_a="Sessions live in Postgres.",
        faithful_b="Postgres holds the sessions.", flawed="Sessions live in Redis.",
        right_aliases=("PostgreSQL",),
    )


def test_a_final_that_carries_only_the_source_token_is_resolved():
    corpus = _corpus()
    assert corpus.resolution("Sessions are kept in Postgres.", _item(corpus)) == "resolved"
    # The alias counts as the source's token, exactly as `grade` counts it (the §2l/§2t lesson).
    assert corpus.resolution("Sessions are kept in PostgreSQL.", _item(corpus)) == "resolved"


def test_a_final_that_carries_both_tokens_is_a_hedge_not_a_choice():
    corpus = _corpus()
    final = "Some candidates say Postgres and one says Redis; the source is not explicit."
    assert corpus.resolution(final, _item(corpus)) == "hedged"
    # `grade` still calls it propagated: the contradiction reached the output. The two readers agree
    # on the row and disagree only on what the row is evidence of.
    assert corpus.grade(final, _item(corpus)) == "propagated"


def test_the_wrong_token_alone_is_a_choice_of_the_flaw_and_neither_is_an_abstention():
    corpus = _corpus()
    assert corpus.resolution("Sessions are kept in Redis.", _item(corpus)) == "flaw"
    assert corpus.resolution("Sessions are kept in a database.", _item(corpus)) == "abstained"


def test_the_partition_is_exactly_the_grade_partition_refined():
    """Every `resolution` class maps onto one `grade` class and back: resolved→kept, hedged and
    flaw→propagated, abstained→omitted. A drift between the two would let the resolution table
    and the bias table describe different runs."""
    corpus = _corpus()
    item = _item(corpus)
    onto = {"resolved": "kept", "hedged": "propagated", "flaw": "propagated", "abstained": "omitted"}
    for final in (
        "Postgres.", "PostgreSQL.", "Redis.", "Postgres or Redis.", "PostgreSQL, or maybe Redis.", "a database",
    ):
        assert corpus.grade(final, item) == onto[corpus.resolution(final, item)], final


def test_the_hedge_phrases_are_the_registered_list_and_match_case_insensitively():
    corpus = _corpus()
    assert corpus.hedges("The candidates disagree; it is UNCLEAR.") == ["disagree", "unclear", "the candidates"]
    assert corpus.hedges("The Pro tier is $49 per month.") == []
    # Substring matching is what the registration fixed, and it has a known false positive the
    # RESULTS quote rather than edit away: `neither` contains `either`.
    assert corpus.hedges("neither is included") == ["either"]
