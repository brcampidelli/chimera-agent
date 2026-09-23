"""The ``decide`` tool — typed questions the agent asks over text it already has.

Study 22, phase 4 (`bench/PLAN-study22-system-one.md`, Architecture B). The agent classifies, filters
or triages many items without writing prose it then has to parse: each question comes back as a
probability (``noul``), a distribution with the chosen option (``choice``) or an expected level
(``score``), through the same function as ``chimera decide`` and ``POST /api/decide``
(`chimera/decisions/interface.py`).

**What it is not.** Nothing in the run reads these answers but the agent: no gate, no approval, no
verification is decided on them (study 22, I1 — the B4 router, which could, made every executor worse).
The numbers are uncalibrated unless a map exists for exactly the question, and the tool says so.

**Off by default** (``CHIMERA_DECIDE_TOOL``): a tool schema is paid in every prompt of every step, the
charge this repository measured on ``edit_batch`` and ``todo_write`` before either was switched on.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from chimera.tools.base import Tool

MAX_STATES = 50
"""States per call. A thousand is a job for ``chimera decide --jsonl``, not for one tool call that
holds the step for minutes."""


def _build_decider() -> Any:
    from chimera.config import get_settings
    from chimera.decisions.factory import build_decider

    return build_decider(get_settings())


class DecideTool(Tool):
    name = "decide"
    description = (
        "Ask typed questions about text and get probabilities back instead of prose. Each question is "
        "'noul' (yes/no -> P(yes)), 'choice' (pick one option -> probabilities) or 'score' (ordered "
        "levels, lowest first -> expected level). Give 'state' for one text or 'states' for up to 50. "
        "Ask one condition per question (no 'A and B'); put the meaning of each option in its criteria. "
        "Answers are uncalibrated estimates for your own use — they decide nothing by themselves."
    )
    parameters = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "object",
                "description": (
                    "key -> {type: noul|choice|score, instructions: str, criteria: {...}}. noul criteria: "
                    "{true, false}; choice: option -> meaning; score: level -> meaning, lowest first."
                ),
            },
            "state": {"type": "string", "description": "The text to ask about."},
            "states": {"type": "array", "items": {"type": "string"}, "description": f"Up to {MAX_STATES} texts."},
        },
        "required": ["questions"],
    }

    def __init__(self, decider: Any | None = None) -> None:
        self._decider = decider
        self._lock = threading.Lock()

    def _get_decider(self) -> Any:
        with self._lock:
            if self._decider is None:
                self._decider = _build_decider()
            return self._decider

    def run(self, **kwargs: Any) -> str:
        from chimera.decisions.interface import RequestError, decide

        questions = kwargs.get("questions")
        state = kwargs.get("state")
        states = kwargs.get("states")
        texts: list[str]
        if isinstance(states, list) and states:
            texts = [str(s) for s in states]
        elif isinstance(state, str) and state.strip():
            texts = [state]
        else:
            return "error: give 'state' (one text) or 'states' (a list of texts)"
        if len(texts) > MAX_STATES:
            return f"error: at most {MAX_STATES} states per call (got {len(texts)}); use `chimera decide --jsonl` for more"
        results: list[dict[str, Any]] = []
        for i, text in enumerate(texts):
            try:
                out = decide(self._get_decider(), {"state": text, "questions": questions})
            except RequestError as exc:
                return f"error: {exc}"
            calibrated = {k: bool(r.get("calibrated")) for k, r in out["receipts"].items()}
            results.append({"index": i, "answers": out["answers"], "calibrated": calibrated})
        if len(results) == 1:
            return json.dumps(results[0], ensure_ascii=False)
        return json.dumps({"results": results}, ensure_ascii=False)


def make_decide(decider: Any | None = None) -> Any:
    """The MCP bridge's ``decide`` callable: a request dict in, the interface's JSON out."""
    tool = DecideTool(decider)

    def _call(arguments: dict[str, Any]) -> str:
        return tool.run(**arguments)

    return _call
