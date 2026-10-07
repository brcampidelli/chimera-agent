"""Receipt-only reporting for a claim that the task's checker is defective."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from chimera.tools.base import Tool


class ReportDefectTool(Tool):
    """Record, without altering run state, the agent's claim that a checker is defective."""

    name = "report_defect"
    description = (
        "Report evidence that a task's test or checker is defective or impossible to satisfy "
        "honestly. This records your claim on the run receipt only: it does not mark work blocked, "
        "stop or park the run, or change any files. Give a concise explanation and evidence."
    )
    parameters = {
        "type": "object",
        "properties": {
            "claim": {"type": "string", "description": "Why the checker appears defective."},
            "evidence": {"type": "string", "description": "Relevant test, contract, or contradiction."},
        },
        "required": ["claim", "evidence"],
    }

    def __init__(self) -> None:
        self._local = threading.local()

    def bind(self, on_report: Callable[[dict[str, str]], None] | None = None) -> None:
        """Bind this shared registry tool to the current run's receipt collector."""
        self._local.on_report = on_report
        self._local.claims = []

    @property
    def claims(self) -> list[dict[str, str]]:
        return list(getattr(self._local, "claims", []))

    def run(self, **kwargs: Any) -> str:
        claim = str(kwargs.get("claim") or "").strip()
        evidence = str(kwargs.get("evidence") or "").strip()
        if not claim or not evidence:
            return "error: both 'claim' and 'evidence' are required"
        record = {"claim": claim, "evidence": evidence}
        claims = list(getattr(self._local, "claims", []))
        claims.append(record)
        self._local.claims = claims
        callback = getattr(self._local, "on_report", None)
        if callback is not None:
            callback(dict(record))
        return "Defect claim recorded on the run receipt; execution continues unchanged."

    @staticmethod
    def render(claims: list[dict[str, str]]) -> str:
        """Serialize claims in a stable form suitable for a run receipt."""
        return json.dumps(claims, ensure_ascii=False, sort_keys=True)
