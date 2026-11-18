"""The corpus of `bench/proportionality` (PREREGISTRATION.md §3). Frozen before the first paid run.

Three families, all built from files that are already in this repository:

- **read** — twelve questions over three real modules (`read_fixture/src/`, byte copies of
  `decline_language.py`, `draft.py`, `checklist.py` as of origin/main 2026-11-16). Each has a
  checker written from the source, before any model saw the question.
- **edit** — eleven of the twelve tasks of `bench/edit_tools/tasks.py` (the twelfth was excluded by
  that bench's own pre-registration: the control arm failed it 3/3), verified by pytest on a test
  restored from pristine.
- **control** — the two requests the "bakery exception" in `DEFAULT_SYSTEM_PROMPT` is about.

Not mined from conversation history: those carry the owner's data, and the repository's own
fixtures do not. The questions are therefore short and written in one voice, which is a limit on
what this corpus can say about real conversations (PREREGISTRATION.md §7).
"""

from __future__ import annotations

import re
from pathlib import Path

from bench.edit_tools.tasks import TASKS as _EDIT_TASKS

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "read_fixture"

#: Each `must` is a list of regexes ALL of which have to match the final answer (case-insensitive).
READ: list[dict] = [
    {"id": "r01", "q": "In src/decline_language.py, what does `decline_language` return when both the owner language and the question are empty strings?",
     "must": [r"\ben\b|english"]},
    {"id": "r02", "q": "How many locales does the `DECLINES` table in src/decline_language.py hold?",
     "must": [r"\b10\b|\bten\b"]},
    {"id": "r03", "q": "What does `from_text` in src/decline_language.py return when two languages tie on the number of function words?",
     "must": [r"empty string|\"\"|''|``"]},
    {"id": "r04", "q": "What is the Portuguese decline sentence in src/decline_language.py?",
     "must": [r"As fontes fornecidas não cobrem isso"]},
    {"id": "r05", "q": "What are `MIN_REQUIREMENTS` and `MAX_REQUIREMENTS` in src/draft.py?",
     "must": [r"\b2\b", r"\b8\b"]},
    {"id": "r06", "q": "Which check types does `parse_draft` in src/draft.py accept, and which one does it refuse?",
     "must": [r"contains", r"defines", r"absent", r"command"]},
    {"id": "r07", "q": "What does `parse_draft` in src/draft.py raise when no requirement is marked required?",
     "must": [r"DraftError"]},
    {"id": "r08", "q": "What default temperature does `draft_spec` in src/draft.py use?",
     "must": [r"0\.2\b"]},
    {"id": "r09", "q": "What temperature does `RequirementChecklist.extract` in src/checklist.py call the backend with?",
     "must": [r"\b0\.0\b|\b0\b"]},
    {"id": "r10", "q": "What does `RequirementChecklist.grade` in src/checklist.py return when the grader abstains?",
     "must": [r"\bNone\b"]},
    {"id": "r11", "q": "What are the possible values of `RequirementKind` in src/checklist.py?",
     "must": [r"\bdo\b", r"\bavoid\b", r"\binclude\b"]},
    {"id": "r12", "q": "Which three exception types does `RequirementChecklist.extract` in src/checklist.py handle quietly, at debug level, before its catch-all?",
     "must": [r"JSONDecodeError", r"ValueError", r"ValidationError"]},
]

#: The twelfth edit task is excluded by `bench/edit_tools/run_ab.py` (EXCLUDED), for the reason it states.
EDIT: list[dict] = [t for t in _EDIT_TASKS if t["name"] != "import_module_moved"]

CONTROL: list[dict] = [
    {"id": "c01_bakery", "prompt": "faz um site pra minha padaria", "kind": "ask"},
    {"id": "c02_specific", "prompt": "Crie o arquivo `hello.py` na raiz do workspace; ele deve imprimir `ola mundo`.", "kind": "build"},
]


def read_answer_ok(item: dict, answer: str) -> bool:
    return all(re.search(p, answer or "", re.IGNORECASE) for p in item["must"])


def corpus_sizes() -> dict[str, int]:
    return {"read": len(READ), "edit": len(EDIT), "control": len(CONTROL)}
