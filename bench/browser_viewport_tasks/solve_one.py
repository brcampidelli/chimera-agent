"""One paid solve: one task, one arm, one replica, in its own process. See PREREGISTRATION.md.

    python -m bench.browser_viewport_tasks.solve_one --task W1 --arm today --replica 0 --out FILE

Run by `run.py`, never by hand in the measurement. Its own process gives each solve its own local
server, its own Chromium and its own CHIMERA_HOME (a fresh temporary one: no memory, no skill bundles,
no settings file from this machine's home), so no two solves share state. The agent gets ONE tool —
the browser — so the only way to the answer is the listing under test. The record is written whether
the solve succeeded, failed or errored; `error` is set only when the apparatus or the provider broke,
never for a wrong answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
MAX_STEPS = 20
PER_SOLVE_USD = 0.25
"""The agent's own spend ceiling per solve, checked before each call against the RECEIPT's price."""


def worst_price(model: str) -> tuple[float, float, float]:
    """(input, output) per 1M at the dearest rate the catalogue has seen for this slug, and how much
    dearer that is than the receipt's rate at worst.

    A solve runs in a fresh CHIMERA_HOME with no cached price index, so its receipt is priced at the
    catalogue row — 0.04/0.08 for the registered model — while OpenRouter has also served the slug at
    0.065/0.18. A cap summed from receipts could undercount real spend by up to 2.25x; the runner
    therefore charges every solve at the dearest rate and reserves each one in flight at its ceiling
    times that factor."""
    from chimera.providers.catalog import CATALOG

    entry = next((e for e in CATALOG if e.slug == model), None)
    if entry is None:
        raise ValueError(f"{model!r} is not in the catalogue: the cap has no dearest rate to charge at")
    list_in, list_out = entry.input_per_m or 0.0, entry.output_per_m or 0.0
    worst_in = max([list_in] + [p[0] for p in entry.also_seen])
    worst_out = max([list_out] + [p[1] for p in entry.also_seen])
    factor = max(worst_in / list_in if list_in else 1.0, worst_out / list_out if list_out else 1.0)
    return worst_in, worst_out, factor


def cap_charge(model: str, prompt_tokens: int, completion_tokens: int, receipt: float | None) -> float:
    """What the runner's cap counts for one solve: the receipt, or the tokens at the dearest seen
    rate, whichever is larger (cache reads charged at the full rate — an overcount, on purpose)."""
    worst_in, worst_out, _ = worst_price(model)
    return max(receipt or 0.0, (prompt_tokens * worst_in + completion_tokens * worst_out) / 1e6)


def ruler() -> dict[str, str]:
    """What this solve was measured with: the site's bytes, the tasks' source, the tool's source."""
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    import chimera
    from chimera.tools import browser

    return {
        "site_manifest": sha(HERE / "pages" / "MANIFEST.json"),
        "tasks": sha(HERE / "tasks.py"),
        "browser_tool": sha(Path(browser.__file__)),
        "chimera": str(Path(chimera.__file__).resolve().parent),
        "commit": os.environ.get("M7B_COMMIT", ""),
    }


def solve(task_id: str, arm: str, replica: int, model: str, backend: Any = None) -> dict[str, Any]:
    """One solve. ``backend`` is None in the measurement (the product's gateway); the dry-run passes a
    scripted stand-in to exercise this exact path at US$ 0."""
    from bench.browser_viewport_tasks.harness import browser_for
    from bench.browser_viewport_tasks.server import SiteServer
    from bench.browser_viewport_tasks.tasks import BY_ID
    from chimera.config import get_settings
    from chimera.core import Agent, AgentConfig
    from chimera.core.agent import partial_spend
    from chimera.providers.gateway import LLMGateway
    from chimera.tools import ToolRegistry

    task = BY_ID[task_id]
    record: dict[str, Any] = {
        "task": task.id, "arm": arm, "replica": replica, "model": model, "stratum": task.stratum,
        "hurt_prone": task.hurt_prone, "ruler": ruler(), "error": "",
    }
    started = time.monotonic()
    server = SiteServer()
    try:
        with server as origin, browser_for(origin, arm) as (tool, _driver):
            registry = ToolRegistry()
            registry.register(tool)
            config = AgentConfig(model=model, max_steps=MAX_STEPS, max_usd=PER_SOLVE_USD)
            agent = Agent(backend if backend is not None else LLMGateway(get_settings()), registry, config)
            record["agent_config"] = {
                "temperature": config.temperature, "compact_schemas": config.compact_schemas,
                "detect_tool_loops": config.detect_tool_loops, "inject_skill_context": config.inject_skill_context,
                "prefix_nonce_set": bool(config.prefix_nonce), "max_steps": config.max_steps,
                "max_usd": config.max_usd,
            }
            try:
                result = agent.run(task.prompt(origin))
            except Exception as exc:  # noqa: BLE001 — the provider's failure is the apparatus's, not the arm's
                spend = partial_spend(exc)
                record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                record["usd"] = spend.usd if spend else None
                record["prompt_tokens"] = spend.prompt_tokens if spend else 0
                record["completion_tokens"] = spend.completion_tokens if spend else 0
                # Unknown spend on a failed call is charged at the ceiling: the cap never assumes zero.
                record["cap_usd"] = (
                    cap_charge(model, spend.prompt_tokens, spend.completion_tokens, spend.usd) if spend
                    else PER_SOLVE_USD * worst_price(model)[2]
                )
                return record
            # First, before anything else can raise: what the run cost.
            record["cap_usd"] = cap_charge(model, result.prompt_tokens, result.completion_tokens, result.usd)
            steps = list(getattr(result.steplog, "steps", []) or [])
            peak = getattr(result.steplog, "context_peak_tokens", 0)
            record.update({
                "answer": (result.answer or "")[:2000],
                "success": task.check.passes(result.answer or ""),
                "stopped_reason": result.stopped_reason,
                "steps": result.steps,
                "tool_calls": result.tool_calls_made,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "cache_read_tokens": result.cache_read_tokens,
                "cache_write_tokens": result.cache_write_tokens,
                "usd": result.usd,
                "answered_by": result.model,
                "providers": [str(getattr(s, "provider", "") or "") for s in steps],
                "context_peak_tokens": peak() if callable(peak) else peak,
                "observation_chars": tool.log.chars,
                "browser_actions": tool.log.actions(),
                "browser_calls": tool.log.calls,
            })
            # A solve whose cost cannot be priced is a receipt missing, not a free solve (Bee §2z).
            if result.usd is None:
                record["error"] = "unpriced: the receipt has no usd"
    except Exception as exc:  # noqa: BLE001 — Chromium, the server: the apparatus broke
        record["error"] = record["error"] or f"{type(exc).__name__}: {str(exc)[:300]}"
        # `cap_usd` is set as soon as a model call has happened; unset here means none did.
        record.setdefault("cap_usd", 0.0)
    finally:
        record["seconds"] = round(time.monotonic() - started, 1)
        record["requested_paths"] = server.requested[:200]
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--arm", required=True, choices=["today", "viewport"])
    ap.add_argument("--replica", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()

    # Before anything reads the settings: the CODE's defaults, not this machine's. A `.env` loaded by
    # `bench/_with_env.py` carries the owner's CHIMERA_* choices (temperature, schema compaction, …),
    # and they would reach the agent silently. Credential pools (`CHIMERA_*_KEYS`) stay; the arm comes
    # only from the constructor; the home is a fresh one.
    stripped = sorted(n for n in os.environ if n.upper().startswith("CHIMERA_") and not n.upper().endswith("_KEYS"))
    for name in stripped:
        del os.environ[name]
    os.environ["CHIMERA_HOME"] = tempfile.mkdtemp(prefix="m7b-home-")
    record = solve(args.task, args.arm, args.replica, args.model)
    record["stripped_env"] = stripped  # names only, never values
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)  # a half-written record never looks finished
    return 1 if record.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
