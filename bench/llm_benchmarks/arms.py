"""The two arms, and the token accounting that enforces the budget cap.

* ``baseline`` — one completion, no tools, no loop. The model answering once, which is how HumanEval
  and GSM8K are normally scored.
* ``chimera`` — the full loop via the ``chimera solve`` CLI in a workspace (plan, tools,
  verify-or-revert, retry), the same invocation shape ``bench/local_lift`` uses.

Both arms see the identical problem statement. The chimera arm's extra power is the loop, never extra
information — in particular it never sees the grading tests (see :mod:`humaneval`).

Token accounting: the baseline's usage is exact (the gateway reports it). The chimera arm runs in a
subprocess, so its usage is **measured** from the run's receipts when available and otherwise
**estimated conservatively** (over-, never under-estimating), because a budget guard that undercounts
is worse than no guard.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


@dataclass
class Spend:
    """Running token/cost total, so the runner can stop before crossing the budget."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    estimated_calls: int = 0  # calls whose cost we estimated rather than measured
    by_arm: dict[str, float] = field(default_factory=dict)

    def add(self, arm: str, prompt: int, completion: int, usd: float, *, estimated: bool) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.usd += usd
        self.by_arm[arm] = self.by_arm.get(arm, 0.0) + usd
        if estimated:
            self.estimated_calls += 1


def extract_code(text: str) -> str:
    """Pull Python out of a model response: fenced block if present, else the raw text."""
    blocks = _FENCE.findall(text or "")
    if blocks:
        return max(blocks, key=len).strip()
    return (text or "").strip()


def _price(model: str) -> tuple[float, float]:
    """(input, output) US$ per million tokens for ``model``; (0,0) when unknown.

    Unknown prices yield zero *for the guard's arithmetic only* — and every such call is counted in
    ``Spend.estimated_calls`` so the report can say the total is a floor, not a fact.
    """
    from chimera.providers.catalog import CATALOG

    for entry in CATALOG:
        if entry.slug == model:
            return (entry.input_per_m or 0.0, entry.output_per_m or 0.0)
    return (0.0, 0.0)


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp, out = _price(model)
    return (prompt_tokens / 1_000_000) * inp + (completion_tokens / 1_000_000) * out


def run_baseline(prompt: str, *, model: str, spend: Spend, max_tokens: int = 1024) -> str:
    """One completion, no tools. Returns the raw response text."""
    from chimera.providers.gateway import LLMGateway

    gateway = LLMGateway()
    result = gateway.complete(
        [{"role": "user", "content": prompt}],
        model=model,
        temperature=0.0,
        max_tokens=max_tokens,
    )
    prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(result, "completion_tokens", 0) or 0)
    usd = getattr(result, "usd", None)
    measured = usd is not None
    spend.add(
        "baseline",
        prompt_tokens,
        completion_tokens,
        float(usd) if measured else cost_usd(model, prompt_tokens, completion_tokens),
        estimated=not measured,
    )
    return str(getattr(result, "content", "") or "")


# A deliberately generous per-task estimate for the loop, used only when the subprocess did not
# report usage. Over-estimating makes the budget guard stop EARLY, which is the safe direction.
_LOOP_PROMPT_EST = 14_000
_LOOP_COMPLETION_EST = 2_500


def run_chimera_solve(
    task: str,
    *,
    workspace: Path,
    model: str,
    verify: str | None,
    spend: Spend,
    timeout: int = 300,
    max_attempts: int = 3,
) -> bool:
    """Run the full loop in ``workspace``. Returns whether the CLI reported success.

    That return value is NOT the benchmark grade — grading happens separately, against the hidden
    tests, in a workspace the agent never touched. It is used only for diagnostics.
    """
    cmd = [
        sys.executable,
        "-m",
        "chimera.cli.main",
        "solve",
        task,
        "--workspace",
        str(workspace),
        "--model",
        model,
        "--max-attempts",
        str(max_attempts),
        "--repo-map",
        "--progress-ledger",
        "--checklist",
        "--replan",
        # Hygiene: no cross-task learning, so task N+1 is not made easier by task N. Without this the
        # suite would measure "the loop plus memory of this exact benchmark", which is not the claim.
        "--no-remember",
        "--no-collect",
        "--no-evolve-skills",
    ]
    if verify:
        cmd += ["--verify", verify]

    env = dict(os.environ)
    env.setdefault("CHIMERA_HOST_EXEC", "allow")  # headless bench: the loop must be able to run code
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            cmd, cwd=str(workspace), capture_output=True, text=True, timeout=timeout, env=env
        )
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False  # an honest FAIL, never an exclusion

    spend.add(
        "chimera",
        _LOOP_PROMPT_EST,
        _LOOP_COMPLETION_EST,
        cost_usd(model, _LOOP_PROMPT_EST, _LOOP_COMPLETION_EST),
        estimated=True,
    )
    return ok
