"""Precision gate for the deterministic success-claim vocabulary."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from chimera.core.claims import success_claims


def test_claims_corpus_precision_and_confusion_matrix() -> None:
    corpus_path = Path(__file__).parent / "fixtures" / "claims_corpus.jsonl"
    rows = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines()]
    source_counts: dict[str, Counter[tuple[bool, bool]]] = {
        source: Counter() for source in ("corpus", "audit1", "audit2")
    }
    for row in rows:
        source = str(row.get("source", "corpus"))
        assert source in source_counts
        source_counts[source][
            (bool(row["claim"]), bool(success_claims(str(row["text"]))))
        ] += 1
    corpus_counts = source_counts["corpus"]
    corpus_false_positives = corpus_counts[(False, True)]
    corpus_negatives = corpus_counts[(False, False)] + corpus_false_positives

    trigger_counts: Counter[str] = Counter()
    trigger_tp: Counter[str] = Counter()
    trigger_fn: Counter[str] = Counter()
    trigger_fp: Counter[str] = Counter()
    for row in rows:
        if bool(row["claim"]):
            continue
        for extracted in success_claims(str(row["text"])):
            trigger_fp[re.sub(r"\s+", " ", extracted.phrase.casefold())] += 1
    for row in rows:
        if not bool(row["claim"]):
            continue
        extracted = success_claims(str(row["text"]))
        trigger = extracted[0].phrase.casefold() if extracted else "missed"
        trigger_counts[trigger] += 1
        if extracted:
            trigger_tp[trigger] += 1
        else:
            trigger_fn[trigger] += 1
    print(
        "claims by trigger (precision/recall): "
        + ", ".join(
            f"{term}:P={trigger_tp[term] / max(1, trigger_tp[term] + trigger_fp[term]):.3f}/R="
            f"{trigger_tp[term] / trigger_counts[term]:.3f}"
            for term in sorted(trigger_counts)
        )
    )
    for source in ("corpus", "audit1", "audit2"):
        source_matrix = source_counts[source]
        source_tp = source_matrix[(True, True)]
        source_fp = source_matrix[(False, True)]
        source_fn = source_matrix[(True, False)]
        source_tn = source_matrix[(False, False)]
        source_precision = source_tp / max(1, source_tp + source_fp)
        source_recall = source_tp / max(1, source_tp + source_fn)
        print(
            f"claims source={source}: TN={source_tn} FP={source_fp} "
            f"FN={source_fn} TP={source_tp} precision={source_precision:.3f} "
            f"recall={source_recall:.3f}"
        )
    assert corpus_false_positives / max(1, corpus_negatives) <= 0.05
    assert {str(row.get("source", "corpus")) for row in rows} >= {"corpus", "audit1", "audit2"}
    assert sum(row.get("source") == "audit1" for row in rows) == 30
    assert sum(row.get("source") == "audit2" for row in rows) == 44
    assert len(rows) >= 60
    assert sum(bool(row["claim"]) for row in rows) >= 20
    assert sum(not bool(row["claim"]) for row in rows) >= 20


def test_claim_extractor_preserves_exact_claim_and_rejects_negation_question() -> None:
    assert [claim.phrase for claim in success_claims("Tests passed; build ok.")] == [
        "Tests passed", "build ok",
    ]
    assert success_claims("Tests did not pass.") == []
    assert success_claims("Did the tests pass?") == []
    assert success_claims("We wonder if it is done.") == []
    assert success_claims("Please check whether the tests passed.") == []
    assert success_claims("Ainda não pronto.") == []
