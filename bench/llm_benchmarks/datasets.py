"""Fetch HumanEval and GSM8K from their canonical sources.

The datasets are downloaded on first use and cached under ``bench/llm_benchmarks/data/`` (gitignored):
they carry their own licences and are large enough that vendoring them into the repo would be both a
licensing question and a diff-noise problem. The canonical URLs are pinned here so a run is
reproducible and nobody has to guess which copy of "HumanEval" was used — there are several forks with
silently different task sets.
"""

from __future__ import annotations

import gzip
import json
import urllib.request
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"

# openai/human-eval @ master — the original 164 problems.
HUMANEVAL_URL = "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
# openai/grade-school-math @ master — the GSM8K test split (1319 problems).
GSM8K_URL = (
    "https://raw.githubusercontent.com/openai/grade-school-math/master/"
    "grade_school_math/data/test.jsonl"
)


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 — pinned https URLs above
        dest.write_bytes(resp.read())
    return dest


def load_humaneval() -> list[dict[str, Any]]:
    """The 164 HumanEval problems: ``task_id``, ``prompt``, ``entry_point``, ``test``."""
    path = _download(HUMANEVAL_URL, DATA_DIR / "HumanEval.jsonl.gz")
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_gsm8k() -> list[dict[str, Any]]:
    """The GSM8K test split: ``question`` and ``answer`` (reference ends with ``#### <number>``)."""
    path = _download(GSM8K_URL, DATA_DIR / "gsm8k_test.jsonl")
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def gsm8k_reference(answer: str) -> str:
    """The gold numeric answer — the text after ``####``, commas stripped."""
    tail = answer.rsplit("####", 1)[-1]
    return tail.strip().replace(",", "").replace("$", "")
