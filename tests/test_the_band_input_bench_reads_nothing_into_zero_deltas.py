"""The S30-31 bench's report, checked before it has ever read a real run (§2ae, §2aa).

`bench/band_input/run.py --report` turns 275 local answers into "this arm moves p" or not. Two ways a
consolidator lies without an error: a verdict reachable with every delta at zero, and arms read
against a control that did not reproduce the rows the map was fitted on. Both are pinned here on
synthetic rows built from those fitted rows, so no model is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bench.band_input import run as bench  # noqa: E402
from chimera.decisions.maps import SHIPPED_MAPS  # noqa: E402


def _fitted() -> dict[str, float]:
    out = {}
    for line in bench.FITTED_ROWS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("arm") == "L" and row.get("wrapper") is None and row.get("rep") == 0 and row.get("p") is not None:
                out[row["id"]] = float(row["p"])
    return out


def _rows(tmp_path: Path, *, arm_raw: Any = None, build: str = bench.BUILD) -> Path:
    """Every arm answers the fitted raw p, unless ``arm_raw(arm, item)`` says otherwise."""
    fitted, fmap = _fitted(), SHIPPED_MAPS[0]
    lines = []
    for item in bench.items():
        for arm in bench.ARMS:
            raw = fitted[item["id"]]
            if arm_raw is not None:
                raw = arm_raw(arm, item, raw)
            lines.append(json.dumps({
                **item, "arm": arm, "raw_p": raw, "p": fmap.apply(raw), "calibrated": True,
                "choice": "ALLOW" if raw < 0.5 else "BLOCK", "build": build, "halt": None,
            }))
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_the_offline_check_holds_on_the_registered_templates() -> None:
    rows = bench.check()
    assert len(rows) == 55


def test_zero_deltas_read_as_a_null_and_never_as_a_movement(tmp_path: Path) -> None:
    result = bench.report(_rows(tmp_path))
    assert result["control"]["ok"]
    assert result["floor"]["flips"] == 0
    assert not any(arm["moves_p"] for arm in result["arms"].values())
    assert "neither moves p" in result["decision"]


def test_a_decider_captured_by_the_forged_turn_reads_as_moving(tmp_path: Path) -> None:
    def captured(arm: str, item: dict[str, Any], raw: float) -> float:
        return 0.01 if arm == "F" and item["label"] == "attack" else raw

    result = bench.report(_rows(tmp_path, arm_raw=captured))
    assert result["arms"]["F"]["moves_p"]
    assert result["arms"]["F"]["forged_signature"] == 24
    assert not result["arms"]["E"]["moves_p"]
    assert "Fs restores it" in result["decision"]


def test_arms_are_not_read_when_the_control_did_not_reproduce_the_fitted_rows(tmp_path: Path) -> None:
    result = bench.report(_rows(tmp_path, build="qwen3:4b@Q8_0"))
    assert not result["control"]["ok"]
    assert result["decision"].startswith("UNREADABLE")
    assert "arms" not in result
