"""The H2/H3 corpus: 140 existing tasks with hidden checkers, reused unchanged.

Registered with PREREGISTRATION.md, before any call. Nothing here is new text: every task comes from
two suites this project already authored and graded offline.

* ``bench/local_lift/tasks.py`` — 100 small Python tasks (15 original, 28 parsing, 29 algorithms,
  28 bug-fix), each with a strict pytest file written to fail a plausible naive shortcut.
* ``bench/learning_lift/tasks_hard_fix.py`` — 40 harder bug-fix tasks written so a correct-looking
  first patch fails a second clause of the contract.

What changes is only how the checker is used. Those runners wrote the test into the workspace and
handed it to ``solve`` as ``--verify``. Here the agent gets the task text and the starter files and
nothing else; the test is written AFTER the run, over whatever the agent left under that name, and
it is the checker. An unattended ``chimera solve "<task>"`` with no ``--verify`` is exactly this
situation: the only way to know the work is right is a check the agent writes and runs itself.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_BENCH = Path(__file__).resolve().parents[1]
for sub in ("local_lift", "learning_lift"):
    path = str(_BENCH / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

from tasks import TASKS as _LOCAL  # noqa: E402
from tasks_hard_fix import HARD_FIX_TASKS as _HARD  # noqa: E402


def _row(task: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "id": str(task["id"]),
        "source": source,
        "prompt": str(task["prompt"]),
        "files": {str(k): str(v) for k, v in dict(task.get("files") or {}).items()},
        "test": str(task["test"]),
        "test_src": str(task["test_src"]),
    }


#: The frozen order: local_lift in its own list order, then the hard-fix suite in its own order.
CORPUS: list[dict[str, Any]] = [_row(t, "local_lift") for t in _LOCAL] + [
    _row(t, "learning_lift_hard_fix") for t in _HARD
]

_ids = [t["id"] for t in CORPUS]
if len(_ids) != len(set(_ids)):
    raise AssertionError("duplicate task ids across the two suites")

#: The pilot: every tenth task of the frozen order (0, 10, …, 130) — fourteen tasks spread over all
#: five families. Registered before any call; its runs are never reused in the main run.
PILOT_IDS: list[str] = [CORPUS[i]["id"] for i in range(0, len(CORPUS), 10)]


def corpus_sha() -> str:
    """One hash over everything an arm or the checker reads, so the corpus cannot drift unseen."""
    blob = json.dumps(
        [[t["id"], t["prompt"], t["files"], t["test"], t["test_src"]] for t in CORPUS],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def by_id(task_id: str) -> dict[str, Any]:
    for task in CORPUS:
        if task["id"] == task_id:
            return task
    raise KeyError(task_id)
