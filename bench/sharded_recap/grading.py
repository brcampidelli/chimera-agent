"""Extract the code of a final reply, grade it against a task's hidden tests, and read its recap.

All deterministic; no model is involved anywhere in grading.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tasks import Task

HERE = Path(__file__).resolve().parent
CHILD = HERE / "grader_child.py"
#: Whole-process ceiling. Each block and each test also has its own 5 s limit (on Linux, where the
#: paid runs are graded); this only catches a solution that defeats those.
GRADE_TIMEOUT_S = 240

_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
#: A list item: "-", "*", "•" or "1." / "1)" at the start of a line, followed by text.
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S")


def code_blocks(reply: str) -> list[str]:
    """Every fenced block of the reply, in order. Unterminated fences are not blocks."""
    return [m.group(1) for m in _FENCE.finditer(reply)]


def recap_items(reply: str) -> int:
    """List items written before the first code fence (the recap the arm-B sentence asks for)."""
    head = reply.split("```", 1)[0]
    return sum(1 for line in head.splitlines() if _ITEM.match(line))


def grade_blocks(task: Task, blocks: list[str]) -> dict[str, Any]:
    """Run ``blocks`` against every test of ``task`` in a child process."""
    with tempfile.TemporaryDirectory(prefix="h7-grade-") as tmp:
        payload = Path(tmp) / "payload.json"
        out = Path(tmp) / "out.json"
        payload.write_text(
            json.dumps({"blocks": blocks, "names": list(task.names), "setup": task.setup,
                        "tests": [src for _shard, src in task.tests]}),
            encoding="utf-8",
        )
        try:
            subprocess.run(
                [sys.executable, str(CHILD), str(payload), str(out)],
                cwd=tmp, stdin=subprocess.DEVNULL, capture_output=True, timeout=GRADE_TIMEOUT_S,
                check=False,
            )
            got = json.loads(out.read_text(encoding="utf-8"))
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            got = {"missing": list(task.names), "load_errors": [f"grader: {type(exc).__name__}"],
                   "results": [[False, "grader failed"] for _ in task.tests]}
    results = got["results"]
    failed_shards = sorted({task.tests[i][0] for i, (ok, _msg) in enumerate(results) if not ok})
    return {
        "passed": bool(results) and all(ok for ok, _msg in results) and not got["missing"],
        "n_tests": len(results),
        "n_passed": sum(1 for ok, _msg in results if ok),
        "failed_shards": failed_shards,
        "missing": got["missing"],
        "load_errors": got["load_errors"][:5],
        "failures": [
            {"test": task.tests[i][1][:160], "shard": task.tests[i][0], "error": msg}
            for i, (ok, msg) in enumerate(results) if not ok
        ][:12],
    }


def grade_reply(task: Task, reply: str) -> dict[str, Any]:
    """Grade the final reply of a conversation. No block that defines the names is a fail, not a halt."""
    blocks = code_blocks(reply)
    graded = grade_blocks(task, blocks)
    graded["n_blocks"] = len(blocks)
    graded["no_code"] = bool(graded["missing"])
    graded["recap_items"] = recap_items(reply)
    return graded
