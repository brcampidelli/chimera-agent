"""The item → question mapping and the distribution handed to JevBench, without a model."""

from __future__ import annotations

from bench.jevbench_local.run import KEY, NUM_CTX, body_of, probs_of, question_of
from chimera.decisions import Choice, Noul, Score, as_choice
from chimera.decisions.local import LocalLogprobBackend


def _item(kind: str, labels: list[str], criteria: object) -> dict[str, object]:
    return {"id": f"x-{kind}", "labels": labels, "question": {"type": kind, "instructions": "Decide.", "criteria": criteria}}


def test_a_noul_goes_through_the_product_noul() -> None:
    q = question_of(_item("noul", ["no", "yes"], {"true": "it holds", "false": "it does not"}))
    assert isinstance(q, Noul)
    c = as_choice(q)
    assert c.options == ("yes", "no") and c.criteria == {"yes": "it holds", "no": "it does not"} and c.key == KEY


def test_a_choice_keeps_the_items_label_order_not_the_criteria_order() -> None:
    q = question_of(_item("choice", ["b", "a"], {"a": "first", "b": "second"}))
    assert isinstance(q, Choice) and q.options == ("b", "a") and q.criteria == {"a": "first", "b": "second"}


def test_a_score_attaches_its_criteria_level_by_level() -> None:
    q = question_of(_item("score", ["0", "1", "2"], ["none", "some", "all"]))
    assert isinstance(q, Score) and q.levels == ("0", "1", "2") and q.criteria == {"0": "none", "1": "some", "2": "all"}


def test_no_reading_is_no_distribution_and_a_reading_covers_every_label() -> None:
    assert probs_of(None, ["no", "yes"]) is None
    assert probs_of({"yes": 0.8, "no": 0.2}, ["no", "yes"]) == {"no": 0.2, "yes": 0.8}


def test_only_the_ctx_arm_changes_the_body() -> None:
    backend = LocalLogprobBackend("http://127.0.0.1:1")
    c = as_choice(question_of(_item("noul", ["no", "yes"], {"true": "t", "false": "f"})))
    assert body_of(backend, "s", c, "ctx")["options"]["num_ctx"] == NUM_CTX
    ship = body_of(backend, "s", c, "ship")
    assert "num_ctx" not in ship["options"] and ship == backend.body("s", c)
