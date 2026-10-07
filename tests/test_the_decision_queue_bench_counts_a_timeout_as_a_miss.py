"""`bench/decision_queue`'s report, pinned on synthetic runs before it reads a real one.

Three ways the consolidator could print a latency it never measured, each without an error:

* **dropping the calls that timed out.** A call that hit the backend's 30 s timeout is the slowest
  call, and leaving it out reports the latency of the calls fast enough to answer. It is a censored
  latency and a miss at every deadline (the smoke on a busy GPU timed out 12 of 14 calls);
* **reading one sweep.** PROTOCOL §15: a level counts as supported only if both sweeps say so; the
  two disagreeing is "not robust", never a pass;
* **reading a run whose control failed** — a busy GPU, an incomplete run, the c = 1 floor drifting
  between the sweeps.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bench.decision_queue import run as bench  # noqa: E402

Latency = Callable[[str, int, int], float]


def _write(tmp_path: Path, latency: Latency, *, idle: bool = True, drop: int = 0, timeouts: Callable[[str, int, int], bool] | None = None) -> Path:
    """A complete registered run: 2 sweeps x 4 levels x 110 calls. ``latency(sweep, c, k)`` gives the
    k-th call's seconds; ``timeouts`` marks calls that halted on the backend's timeout."""
    items = bench.items()
    lines: list[dict[str, Any]] = [{"kind": "meta"}]
    for i, (sweep, levels) in enumerate(bench.SWEEPS):
        lines.append({"kind": "gpu", "sweep": sweep, "idle": idle})
        for c in levels:
            work = bench.order(items, i, c)
            total = 0.0
            for k, (pass_, item) in enumerate(work):
                seconds = latency(sweep, c, k)
                halted = bool(timeouts and timeouts(sweep, c, k))
                total += seconds
                lines.append({
                    "kind": "measured", "sweep": sweep, "c": c, "pass_": pass_, "id": item["id"],
                    "label": item["label"], "p": None if halted else 0.9 if item["label"] == "attack" else 0.1,
                    "calibrated": not halted, "build": "" if halted else bench.BUILD,
                    "halt": "ReadTimeout: timed out" if halted else None,
                    "seconds": 30.0 if halted else seconds,
                })
            # A serialised server: the wall time is the sum of the service times, whatever c is.
            lines.append({"kind": "cell", "sweep": sweep, "c": c, "calls": len(work), "wall": total})
    if drop:
        lines = lines[:-drop]
    path = tmp_path / "run.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    return path


def test_a_fast_decider_supports_every_level(tmp_path: Path) -> None:
    result = bench.report(_write(tmp_path, lambda s, c, k: 0.2))
    assert result["control"]["ok"]
    assert "supported concurrency at 0.5s: c=8" in result["decision"]
    assert "supported concurrency at 1s: c=8" in result["decision"]
    assert result["cells"]["A/c=1"]["phi_1"] == 1.0 and result["cells"]["A/c=1"]["timeouts"] == 0


def test_a_timeout_is_a_miss_and_is_never_dropped(tmp_path: Path) -> None:
    # 10 of 110 calls at c = 1 time out; every answered call is fast. Dropping the timeouts would read
    # phi_1 = 1.0 and "supported"; counted, phi_1 = 100/110 < 0.95.
    path = _write(tmp_path, lambda s, c, k: 0.2, timeouts=lambda s, c, k: c == 1 and k < 10)
    result = bench.report(path)
    cell = result["cells"]["A/c=1"]
    assert cell["timeouts"] == 10 and cell["n"] == 110 and cell["phi_1"] < 0.95
    assert result["control"]["bad_rows"] == 0  # a timeout is censored latency, not a broken instrument
    assert "missed by more than 5% of calls even with one caller" in result["decision"]


def test_the_two_sweeps_must_agree_or_the_level_is_not_robust(tmp_path: Path) -> None:
    # At c = 4 sweep A is fast and sweep B is slow: c = 4 is neither supported nor dropped silently.
    result = bench.report(_write(tmp_path, lambda s, c, k: 2.0 if (c == 4 and s == "B") or c == 8 else 0.2))
    assert "supported concurrency at 1s: c=2" in result["decision"]
    assert "not robust at 1s (the sweeps disagree): c in [4]" in result["decision"]


def test_a_failed_control_reads_nothing(tmp_path: Path) -> None:
    for path in (
        _write(tmp_path, lambda s, c, k: 0.2, idle=False),  # the GPU was busy
        _write(tmp_path, lambda s, c, k: 0.2, drop=5),  # the run died part-way
        _write(tmp_path, lambda s, c, k: 0.2 if s == "A" else 0.4),  # the c = 1 floor drifted 100%
    ):
        result = bench.report(path)
        assert not result["control"]["ok"] and result["decision"].startswith("UNREADABLE")
        assert "supported" not in result["decision"]


def test_the_quantiles_are_nearest_rank_with_an_order_statistic_interval() -> None:
    xs = [float(i) for i in range(1, 101)]
    assert bench.nearest_rank(xs, 0.50) == 50.0 and bench.nearest_rank(xs, 0.99) == 99.0
    lo, hi = bench.quantile_interval(xs, 0.50)  # type: ignore[misc]
    assert lo < 50.0 < hi
    assert bench.quantile_interval(xs[:5], 0.99) is None  # five calls cannot bound a p99
