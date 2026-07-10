"""Append-only log of (arm, proxy, reward) observations for PROBE best-arm selection (M18-5).

Closes the loop from the offline :class:`~chimera.eval.probe.ProbeBestArm` estimator to live runs: as
the agent works, each attempt records which **arm** ran (e.g. the base worker vs the escalated fusion
worker), the cheap **proxy** signal available at decision time (a manager self-judgment), and the
expensive **reward** (the verified outcome). Over many runs, ``chimera probe-select --from-log`` reads
the accumulated observations and reports the δ-confident best arm — deciding, from measured evidence,
whether an arm (e.g. escalation) actually pays.

JSONL, one observation per line: ``{"arm": str, "proxy": float, "reward": float|null}``. Never raises
on a malformed line (a corrupt record is skipped) — telemetry must not break a run.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.eval.probe import Observation
from chimera.telemetry import get_logger

_log = get_logger("fusion.probe_log")


class ProbeLog:
    """Append (arm, proxy, reward) observations; read them back grouped by arm."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def record(self, *, arm: str, proxy: float, reward: float | None) -> None:
        """Append one observation. Best-effort — a write failure is logged, never raised."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"arm": arm, "proxy": proxy, "reward": reward})
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:  # telemetry must never take a run down
            _log.warning("probe-log write failed (%s) — dropping observation", exc)

    def observations(self) -> dict[str, list[Observation]]:
        """Load the log grouped by arm: ``{arm: [(proxy, reward), ...]}`` for ProbeBestArm.select."""
        arms: dict[str, list[Observation]] = {}
        if not self.path.exists():
            return arms
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                arm = str(obj["arm"])
                proxy = float(obj["proxy"])
                reward = None if obj.get("reward") is None else float(obj["reward"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue  # skip a corrupt record, keep the rest
            arms.setdefault(arm, []).append((proxy, reward))
        return arms
