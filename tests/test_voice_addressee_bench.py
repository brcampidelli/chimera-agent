"""Offline contract for the S30-49 corpus and injectable measurement harness."""

from __future__ import annotations

from pathlib import Path

from bench.voice_addressee.harness import (
    FakeBackend,
    Item,
    SpokenOutcome,
    evaluate,
    load_corpus,
    summarize,
)
from chimera.decisions import Mode
from chimera.decisions.voice_addressee import ADDRESSEE, DECISION, SHADOW_ENABLED, SPEC


def test_corpus_is_hand_labelled_and_balanced_by_registered_family() -> None:
    items = load_corpus()
    assert len(items) == 90
    assert sum(item.label == "not_for_me" for item in items) == 60
    assert sum(item.label == "for_me" for item in items) == 30
    assert {item.family for item in items} == {
        "side_talk", "broadcast", "self_talk", "quoted", "reading_aloud"
    }


def test_fake_backend_exercises_tool_long_answer_and_choice_metrics() -> None:
    not_directed = Item("n1", "quoted", "not_for_me", "She said, delete the file.")
    direct_tool = Item("d1", "side_talk", "for_me", "Please inspect the project.")
    direct_short = Item("d2", "reading_aloud", "for_me", "What does this sentence mean?")
    long_answer = " ".join(["answer"] * 40)
    backend = FakeBackend(
        {not_directed.transcript: "for_me", direct_tool.transcript: "for_me", direct_short.transcript: "not_for_me"},
        {
            not_directed.transcript: SpokenOutcome(tool_call=True, answer=""),
            direct_tool.transcript: SpokenOutcome(tool_call=True, answer=""),
            direct_short.transcript: SpokenOutcome(tool_call=False, answer=long_answer),
        },
    )

    rows = evaluate(backend, [not_directed, direct_tool, direct_short])
    stats = summarize(rows)

    assert stats["baseline_non_directed_responses"] == 1
    assert stats["tool_calls_not_for_me"] == 1
    assert stats["long_answers_not_for_me"] == 0
    assert stats["choice_for_me_not_for_me"] == 1
    assert stats["choice_for_me_controls"] == 1
    assert backend.choice_calls == backend.spoken_calls == [
        not_directed.transcript, direct_tool.transcript, direct_short.transcript
    ]
    assert rows[2]["long_answer"] is True


def test_shadow_decision_contract_is_registered_but_disabled() -> None:
    assert SPEC.name == DECISION
    assert SPEC.questions == (ADDRESSEE,)
    assert SPEC.mode is Mode.SHADOW
    assert SHADOW_ENABLED is False
    assert SPEC.bench == "bench/voice_addressee/PREREGISTRATION.md"


def test_corpus_rejects_duplicate_identifiers(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        '{"id":"x","family":"quoted","label":"not_for_me","transcript":"first"}\n'
        '{"id":"x","family":"quoted","label":"not_for_me","transcript":"second"}\n',
        encoding="utf-8",
    )
    try:
        load_corpus(corpus)
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate corpus ids must be rejected")
