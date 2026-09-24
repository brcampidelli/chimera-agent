"""Static checks on a question before it is ever asked — the failure shapes that were measured.

Each rule is a way a typed question was shown to answer something other than what it asked, with no
error anywhere (the family of `~/.claude/rules/bee-pretreino-licoes.md` §2g: a difference that the
instrument made). ``error`` findings are shapes with a measured cost; ``warn`` findings are
tokenizer- or context-dependent and need a reader.

* **compound** (error, Noul) — "is it A and B?" has no single yes. TypeSafe's jaggedness notes and
  our own claim-vs-diff Noul (AUROC 0.537, the shuffled control) both point the same way: ask A and
  B as two Nouls and combine them in code (study 22, I6).
* **negated** (warn, Noul) — "is it not X?" flips the label the model has to produce against the
  rubric it reads; affirmative phrasing is the documented form.
* **polar_label** (error, Choice/Score) — an option named ``yes``/``safe``/``pass``/``answer``… is
  read as the word, not the rubric: arXiv 2609.26758 swapped rubrics under yes/no labels and changed
  76.9% of answers, 6.5% under neutral ones (I3). A Noul is yes/no by contract and is exempt; its
  remedy is :meth:`Choice.neutral` on the Choice it reduces to, benched first.
* **numeric_levels** (error, Score) — levels ``1``…``5`` **without a meaning each** carry nothing the
  model can read (with one each — the SDK's list shape — they are neutral identifiers, study 22 I3);
  name what each level is.
* **prefix** (error) — an option that is a prefix of another (``RE`` and ``REVIEW``): the label
  token cannot say which one was meant (study 21 A4; phase 0 now refuses that read at run time).
* **shared_start** (warn) — two options that begin with the same two characters may share their
  first token (``REVIEW``/``REFUSE`` → ``RE``) depending on the tokenizer; phase 0 gives no ``p``
  then, so the question silently stops producing numbers.
* **criteria_key** (error) — a criterion for an option the question does not have is rubric the
  model reads and no answer can pick.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from chimera.decisions.contract import Choice, Noul, Question, Score, as_choice

Severity = Literal["error", "warn"]

POLAR_LABELS = frozenset({
    "yes", "no", "true", "false", "safe", "unsafe", "ok", "okay", "answer", "done", "stop", "pass",
    "fail", "correct", "incorrect", "good", "bad", "sim", "não", "nao", "certo", "errado",
})
_COMPOUND = re.compile(r"\b(?:and|or|e|ou)\b", re.IGNORECASE)
_NEGATED = re.compile(r"\b(?:not|never|no|n't|não|nao|nunca)\b|n't\b", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str


def _last_sentence(text: str) -> str:
    """The question itself — the last sentence of the instructions; earlier sentences are framing."""
    parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+", text.strip()) if p.strip()]
    return parts[-1] if parts else ""


def lint(question: Question) -> list[Finding]:
    """Every finding for one question, errors first."""
    found: list[Finding] = []
    if isinstance(question, Noul):
        asked = _last_sentence(question.instructions)
        if _COMPOUND.search(asked):
            found.append(Finding("error", "compound", f"{question.key}: {asked!r} joins two conditions — ask two Nouls"))
        if _NEGATED.search(asked):
            found.append(Finding("warn", "negated", f"{question.key}: {asked!r} is phrased in the negative"))
    choice: Choice = as_choice(question)
    if not isinstance(question, Noul):
        polar = [o for o in choice.options if o.strip().casefold() in POLAR_LABELS]
        if polar:
            found.append(Finding("error", "polar_label", f"{choice.key}: options {polar} are read as words, not rubric"))
    if (
        isinstance(question, Score)
        and all(level.strip().replace(".", "", 1).isdigit() for level in question.levels)
        and not all(question.criteria.get(level) for level in question.levels)
    ):
        # Numbers with a meaning each are neutral identifiers (I3) — the SDK's own Score shape, levels
        # "0", "1", ... with the meanings in criteria. Numbers with nothing behind them name nothing.
        found.append(Finding("error", "numeric_levels", f"{choice.key}: levels {list(question.levels)} name nothing"))
    folded = [(o, o.strip().casefold()) for o in choice.options]
    for a, fa in folded:
        for b, fb in folded:
            if a != b and fb.startswith(fa):
                found.append(Finding("error", "prefix", f"{choice.key}: {a!r} is a prefix of {b!r}"))
    seen: set[tuple[str, str]] = set()
    for i, (a, fa) in enumerate(folded):
        for b, fb in folded[i + 1:]:
            if len(fa) >= 2 and len(fb) >= 2 and fa[:2] == fb[:2] and not (fb.startswith(fa) or fa.startswith(fb)):
                pair = (a, b)
                if pair not in seen:
                    seen.add(pair)
                    found.append(Finding("warn", "shared_start", f"{choice.key}: {a!r} and {b!r} may share a first token"))
    stray = [k for k in choice.criteria if k not in choice.options]
    if stray:
        found.append(Finding("error", "criteria_key", f"{choice.key}: criteria for options it does not have: {stray}"))
    return sorted(found, key=lambda f: f.severity != "error")


def errors(question: Question) -> list[Finding]:
    return [f for f in lint(question) if f.severity == "error"]
