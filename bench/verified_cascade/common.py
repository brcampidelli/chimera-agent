"""Shared, offline helpers of the verified-cascade bench: text normalisation, the deterministic number
check (PREREGISTRATION.md §5.2), and nearest-excerpt retrieval for premise questions. Stdlib only.

Everything here is used by the freeze (``build_items.py freeze``), the validator (``check_items.py``),
the harness (``run.py``) and the report (``report.py``), so the four read the same rule the same way.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

WORD = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)
DIGITS = re.compile(r"\d+")
LIST_MARKER = re.compile(r"^\s*\d+[.)]", re.M)
CITATION = re.compile(r"\[[1-4]\]")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(text: str) -> str:
    """Case-folded, NFC, whitespace collapsed: the 'normalized substring' of §3.3's checks."""
    text = unicodedata.normalize("NFC", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def contains(haystack: str, needle: str) -> bool:
    return norm(needle) in norm(haystack)


def norm_question(text: str) -> str:
    """For duplicate detection: letters and digits only."""
    return " ".join(w.casefold() for w in WORD.findall(unicodedata.normalize("NFC", text)))


def digit_runs(text: str) -> set[str]:
    return set(DIGITS.findall(text))


def answer_digit_runs(answer: str) -> list[str]:
    """Maximal digit runs of an answer after stripping list markers and [1]..[4] citations."""
    stripped = CITATION.sub(" ", LIST_MARKER.sub(" ", answer))
    return DIGITS.findall(stripped)


def number_check_fires(answer: str, excerpts: Sequence[str], question: str) -> bool:
    """True when the answer states a digit run that is in neither the excerpts nor the question."""
    allowed = digit_runs(question)
    for text in excerpts:
        allowed |= digit_runs(text)
    return any(run not in allowed for run in answer_digit_runs(answer))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def jsonl_text(rows: Iterable[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def tokens(text: str) -> Counter[str]:
    return Counter(w.lower() for w in WORD.findall(text) if len(w) > 2)


class TfIdf:
    """The builder's TF-IDF (build_items.tfidf), fitted on one language's pool, able to embed a query."""

    def __init__(self, texts: Sequence[str]) -> None:
        toks = [tokens(t) for t in texts]
        self.df: Counter[str] = Counter(t for c in toks for t in c)
        self.n = len(texts)
        self.vecs = [self.embed_counts(c) for c in toks]

    def embed_counts(self, c: Counter[str]) -> dict[str, float]:
        v = {
            t: (1 + math.log(f)) * math.log(self.n / self.df[t])
            for t, f in c.items()
            if self.df.get(t)
        }
        n = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / n for t, x in v.items()}

    def embed(self, text: str) -> dict[str, float]:
        return self.embed_counts(tokens(text))


def cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def nearest_for_question(
    question: str, doc: str, rows: Sequence[dict[str, Any]], model: TfIdf, k: int = 4
) -> list[str]:
    """The k chunks nearest to a question text, same document first (the ANS/NCR rule, §3.2)."""
    q = model.embed(question)
    scored = sorted(
        (0 if r["doc"] == doc else 1, -cos(q, model.vecs[i]), r["id"]) for i, r in enumerate(rows)
    )
    return [rid for _, _, rid in scored[:k]]
