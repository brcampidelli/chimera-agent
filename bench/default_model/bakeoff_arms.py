"""The four arms of the default-model bake-off, frozen in PREREGISTRATION.md before any paid call.

    python bench/default_model/bakeoff_arms.py      # prints the arms and the frozen hash

Only the model and the provider it is pinned to differ between arms. Everything else is the loop
of `chimera solve`'s worker as `bench/prompt_overlays` runs it (arm A there): the product's
`DEFAULT_SYSTEM_PROMPT`, the loop's default temperature, 30 steps, the coding tools, the network
wall. Prices are each pinned endpoint's published USD per token, read from OpenRouter's endpoints
API on 2026-09-26 (`results/endpoints_2026-09-26.json`), and are what the driver's budget and the
cost figures are computed from.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


@dataclass(frozen=True)
class Arm:
    name: str
    model: str
    provider: str
    """OpenRouter provider slug for `provider.order`, sent with `allow_fallbacks: false`."""
    prompt: float
    completion: float
    cache_read: float
    """USD per token at the pinned endpoint: uncached prompt, completion, cache-read prompt."""


_M = 1e-6
ARMS: dict[str, Arm] = {
    # The product default (`Settings.default_model`), on the endpoint bench/prompt_overlays pins.
    "A": Arm("A", "openrouter/deepseek/deepseek-v4-flash-0731", "DeepInfra", 0.06 * _M, 0.18 * _M, 0.015 * _M),
    # The next DeepSeek flash generation, on the same provider as A, so the route is held fixed.
    "D": Arm("D", "openrouter/deepseek/deepseek-v4.1-flash", "DeepInfra", 0.14 * _M, 0.42 * _M, 0.0042 * _M),
    # OpenAI's small GPT-6, on OpenAI's standard endpoint (not flex, not fast).
    "G": Arm("G", "openrouter/openai/gpt-6-luna", "OpenAI", 0.10 * _M, 0.50 * _M, 0.01 * _M),
    # Qwen 3.7 Flash; Alibaba is its only endpoint.
    "Q": Arm("Q", "openrouter/qwen/qwen3.7-flash", "Alibaba", 0.03 * _M, 0.13 * _M, 0.006 * _M),
}
ORDER = ("A", "D", "G", "Q")

#: The loop's knobs, identical in every arm (bench/prompt_overlays arm A, which is `chimera solve`'s
#: worker with the closed SWE-bench phase's 30-step budget).
MAX_STEPS = 30

#: sha256 of the arms table above as registered. A changed slug, pin or price refuses to run.
REGISTERED_ARMS_SHA256 = "07bb8079377e29437bece153780bc95ed34b7216739702bea61826fd1d7f548f"


def arms_sha256() -> str:
    table = [asdict(ARMS[k]) for k in ORDER]
    return hashlib.sha256(json.dumps(table, sort_keys=True).encode("utf-8")).hexdigest()


def assert_frozen() -> None:
    """Refuse to run if an arm changed after registration, or if the product's system prompt did."""
    from chimera.core.agent import DEFAULT_SYSTEM_PROMPT

    got = arms_sha256()
    if got != REGISTERED_ARMS_SHA256:
        raise SystemExit(f"arms changed since registration ({got[:12]} != {REGISTERED_ARMS_SHA256[:12]})")
    # The same L0 that bench/prompt_overlays registered for its arm A (and that this bench's A is).
    sha = hashlib.sha256(DEFAULT_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    if sha != "66299259ecf8e09b5072f5fb7abea3b93927b35d13b620ae68b1413ea19a1810":
        raise SystemExit(f"DEFAULT_SYSTEM_PROMPT changed since registration ({sha[:12]})")


def cost(calls: list[dict[str, object]], arm: Arm) -> float:
    """Computed USD of a solve's calls at the arm's pinned price (cache reads at the read rate)."""
    usd = 0.0
    for c in calls:
        prompt = int(c.get("prompt") or 0)  # type: ignore[call-overload]
        done = int(c.get("completion") or 0)  # type: ignore[call-overload]
        cached = int(c.get("cache_read") or 0)  # type: ignore[call-overload]
        usd += (prompt - cached) * arm.prompt + cached * arm.cache_read + done * arm.completion
    return usd


if __name__ == "__main__":
    for k in ORDER:
        a = ARMS[k]
        print(f"{k}  {a.model:<46} @{a.provider:<10} in {a.prompt / _M:.4f}  out {a.completion / _M:.4f}"
              f"  cache {a.cache_read / _M:.4f}  per M")
    print("arms sha256:", arms_sha256())
