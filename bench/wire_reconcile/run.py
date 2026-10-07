"""Deterministic synthetic fault-injection exercise for wire reconciliation."""

from __future__ import annotations

import json
import random
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from chimera.config import Settings
from chimera.governance.reconcile import reconcile
from chimera.providers.gateway import LLMGateway, Message


def wilson(successes: int, trials: int) -> tuple[float, float]:
    """Two-sided 95% Wilson score interval for a binomial proportion."""
    if trials == 0:
        return 0.0, 1.0
    z = 1.959963984540054
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    margin = z * ((proportion * (1 - proportion) / trials + z * z / (4 * trials * trials)) ** 0.5) / denominator
    return center - margin, center + margin


def _rate(successes: int, trials: int) -> str:
    low, high = wilson(successes, trials)
    return f"{successes}/{trials} ({successes / trials:.1%}; {low:.1%}–{high:.1%})"


def _trial(root: Path, fault: str | None, rng: random.Random) -> bool:
    root.mkdir(parents=True)
    wire_path = root / "wire.jsonl"
    step_path = root / "steps.jsonl"
    steps: list[dict[str, Any]] = []
    call_count = 0
    lock = threading.Lock()

    def fake_completion(**_: Any) -> SimpleNamespace:
        nonlocal call_count
        with lock:
            call_count += 1
            current = call_count
        message = SimpleNamespace(content=f"synthetic response {current}", tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
            id=f"synthetic-{current}",
        )

    settings = Settings(_env_file=None, CHIMERA_HOME=str(root), CHIMERA_WIRE_LOG=True,
                        default_model="ollama_chat/synthetic")
    assert settings.wire_log
    gateway = LLMGateway(settings=settings)
    with patch("litellm.completion", side_effect=fake_completion):
        for index in range(5):
            result = gateway.complete(
                [Message(role="user", content=f"synthetic request {index}")],
                model="ollama_chat/synthetic",
            )
            steps.append({
                "index": index,
                "wire_id": result.wire_id,
                "request_digest": result.request_digest,
                "response_digest": result.response_digest,
            })

    if fault == "omission":
        del steps[rng.randrange(len(steps))]
    elif fault == "fabrication":
        steps.append({"index": 99, "wire_id": "fabricated-" + str(rng.randrange(1_000_000)),
                      "request_digest": "fabricated-request", "response_digest": "fabricated-response"})
    elif fault == "altered copy":
        steps[rng.randrange(len(steps))]["response_digest"] = "altered-copy"

    step_path.write_text(json.dumps({"steps": steps}) + "\n", encoding="utf-8")
    audit = reconcile(wire_path, step_path)
    return not audit["clean"]


def main() -> None:
    rng = random.Random(3061)
    counts: dict[str, tuple[int, int]] = {}
    with tempfile.TemporaryDirectory(prefix="wire-reconcile-") as temporary:
        base = Path(temporary)
        for fault in ("omission", "fabrication", "altered copy"):
            detected = 0
            for trial in range(30):
                detected += _trial(base / f"{fault}-{trial}", fault, rng)
            counts[fault] = (detected, 30)
        false_positives = sum(
            _trial(base / f"clean-{trial}", None, rng) for trial in range(30)
        )
    print("Synthetic fake-backend gateway calls; five exchanges per trial; seed=3061")
    for fault, (detected, trials) in counts.items():
        print(f"{fault}: {_rate(detected, trials)} detected")
    total_detected = sum(value[0] for value in counts.values())
    print(f"pooled faults: {_rate(total_detected, 90)} detected")
    print(f"clean controls: {_rate(false_positives, 30)} false positives")


if __name__ == "__main__":
    main()
