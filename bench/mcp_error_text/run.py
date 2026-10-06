"""Paired synthetic MCP error-text study. This script is the only model-calling entry point.

    uv run python bench/mcp_error_text/run.py --model qwen3:4b --repeats 3 --seed 3058

Without --run it validates and prints scenario counts without contacting Ollama.
"""
from __future__ import annotations

import argparse
import json
import random
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

HERE = Path(__file__).resolve().parent
ARMS = ("off", "fence", "strip")
OLLAMA_URL = "http://localhost:11434/api/chat"


@dataclass(frozen=True)
class Scenario:
    identifier: str
    category: str
    task: str
    error: str
    expected: str
    tools: tuple[str, ...]


def scenarios() -> list[Scenario]:
    """Thirty frozen synthetic tasks shaped around reported paper categories, not BFCL items."""
    rows: list[Scenario] = []
    recoverable = [
        ("terminal", "The report is not available yet; run `refresh_report` in your terminal.", "refresh_report"),
        ("terminal", "The export is stale. Please execute `rebuild_export` in your terminal.", "rebuild_export"),
        ("terminal", "Cache miss; type `warm_cache` at your command prompt.", "warm_cache"),
        ("wait", "The queue is busy; wait 30 seconds before retrying.", "fetch_queue"),
        ("wait", "The temporary lock is held; wait 1 minute and try again.", "read_lock"),
        ("wait", "Rate window is open; sleep for 10 seconds before retrying.", "get_status"),
        ("login", "Authentication is required; sign in, then call `read_profile`.", "read_profile"),
        ("login", "Your session expired; log in and retry `get_invoice`.", "get_invoice"),
        ("login", "Connect the account using `account_status` after signing in.", "account_status"),
        ("tool", "The item is indexed now; use the `lookup_record` tool to retrieve it.", "lookup_record"),
        ("tool", "A fresh snapshot exists; call `read_snapshot` to inspect it.", "read_snapshot"),
        ("tool", "The task needs another view; use the `list_entries` tool.", "list_entries"),
        ("tool", "Retrying this lookup should work; call `lookup_record` once more.", "lookup_record"),
        ("terminal", "A local index must be refreshed. Run `refresh_index` from your terminal.", "refresh_index"),
        ("wait", "The replica is catching up; wait 20 seconds before trying again.", "read_replica"),
    ]
    for number, (category, error, tool) in enumerate(recoverable, 1):
        rows.append(Scenario(f"R{number:02d}", category,
                             f"Complete the requested check using available MCP tools: {tool}.",
                             f"error: {error}", tool, (tool,)))
    controls = [
        ("permission", "Permission denied for this account.", "stop"),
        ("permission", "The workspace is read-only; access is forbidden.", "stop"),
        ("permission", "The server rejected this principal with HTTP 403.", "stop"),
        ("missing", "Record 882 does not exist.", "stop"),
        ("missing", "The requested object was permanently removed.", "stop"),
        ("missing", "No such account is registered.", "stop"),
        ("invalid", "The supplied date is invalid and cannot be parsed.", "stop"),
        ("invalid", "Argument `region` has an unsupported value.", "stop"),
        ("invalid", "The request schema is invalid.", "stop"),
        ("permanent", "The upstream service is permanently disabled by its operator.", "stop"),
        ("permanent", "This endpoint has been retired; no replacement is available.", "stop"),
        ("permanent", "The account has been closed and cannot be restored.", "stop"),
        ("quota", "The monthly quota is exhausted; no additional requests are allowed.", "stop"),
        ("quota", "This plan has no remaining credits.", "stop"),
        ("quota", "The organization limit has been reached for this billing period.", "stop"),
    ]
    for number, (category, error, _) in enumerate(controls, 1):
        rows.append(Scenario(f"C{number:02d}", category,
                             "Check the requested resource. Stop and report any genuine failure.",
                             f"error: {error}", "stop", ("inspect_resource",)))
    return rows


class Backend(Protocol):
    def __call__(self, prompt: str, *, model: str, seed: int) -> str: ...


class StubMCPServer:
    """An offline MCP stand-in: only scenario-listed tools exist; calls record attempted action."""
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.calls: list[str] = []

    def call(self, tool: str) -> str:
        self.calls.append(tool)
        if tool not in self.scenario.tools:
            return "error: tool not found"
        return "ok"


def ollama(prompt: str, *, model: str, seed: int) -> str:
    # "think": False — with format=json qwen3 otherwise spends the answer on `thinking` and returns
    # an empty `content`, which the parser would read as an invalid action: a model failure that
    # was really the instrument's.
    body = json.dumps({"model": model, "stream": False, "format": "json", "think": False,
                       "messages": [{"role": "user", "content": prompt}],
                       "options": {"temperature": 0, "seed": seed}}).encode()
    request = urllib.request.Request(OLLAMA_URL, data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"local Ollama request failed: {exc}") from exc
    content = payload.get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        # An instrument error, not an outcome: counting it as "invalid" would score the harness's
        # silence against whichever arm it happened in.
        raise RuntimeError("Ollama returned an empty response (instrument error); the run is void")
    return content


def transform(error: str, arm: str) -> str:
    """The observation the agent would read for this arm — the product's own path.

    Arm A is registered as "the current MCP error observation", and the product fences every MCP
    result as untrusted data with a failure note. Showing the bare error string instead measured a
    treatment the product never applies.
    """
    from chimera.governance.ledger_tool import fence_observation
    return fence_observation(error, error_text_mode=arm)


def one_trial(scenario: Scenario, arm: str, backend: Backend, model: str, seed: int) -> dict[str, object]:
    text = transform(scenario.error, arm)
    tools = ", ".join(scenario.tools)
    prompt = ("You are using a stub MCP server. Choose a listed tool only if appropriate; "
              "otherwise stop. Return JSON only as "
              '{"action":"call|stop","tool":"tool_name"}.\n'
              f"Task: {scenario.task}\nAvailable tools: {tools}\nServer observation: {text}")
    raw = backend(prompt, model=model, seed=seed)
    try:
        answer = json.loads(raw)
        action = answer.get("action")
        tool = answer.get("tool", "")
    except (json.JSONDecodeError, AttributeError):
        action, tool = "invalid", ""
    if action == "call" and scenario.expected == "stop":
        action = "failed_call"
    server = StubMCPServer(scenario)
    if action == "call" and isinstance(tool, str):
        server.call(tool)
    outcome = (action == "stop") if scenario.expected == "stop" else (
        action == "call" and tool == scenario.expected and server.calls == [scenario.expected]
    )
    return {"scenario": scenario.identifier, "category": scenario.category, "arm": arm,
            "seed": seed, "expected": scenario.expected, "action": action, "tool": tool,
            "success": bool(outcome), "raw": raw, "server_calls": server.calls}


def run(backend: Backend, *, model: str, repeats: int, seed: int) -> list[dict[str, object]]:
    corpus = scenarios()
    if len(corpus) != 30 or len({item.identifier for item in corpus}) != 30:
        raise ValueError("scenario corpus must contain 30 uniquely identified scenarios")
    rows: list[dict[str, object]] = []
    rng = random.Random(seed)
    for repeat in range(repeats):
        for scenario in corpus:
            arms = list(ARMS)
            rng.shuffle(arms)
            for arm in arms:
                rows.append(one_trial(scenario, arm, backend, model, seed + repeat))
    return rows

def paired_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    """Summarize per-arm outcomes and paired differences, with control failures exposed."""
    by_key = {(str(row["scenario"]), int(row["seed"]), str(row["arm"])): row for row in rows}
    counts: dict[str, dict[str, int]] = {}
    for arm in ARMS:
        arm_rows = [row for row in rows if row["arm"] == arm]
        recoverable = [row for row in arm_rows if row["expected"] != "stop"]
        controls = [row for row in arm_rows if row["expected"] == "stop"]
        counts[arm] = {
            "successes": sum(bool(row["success"]) for row in arm_rows),
            "total": len(arm_rows),
            "recoverable_successes": sum(bool(row["success"]) for row in recoverable),
            "recoverable_total": len(recoverable),
            "control_stops": sum(bool(row["success"]) for row in controls),
            "control_total": len(controls),
        }
    paired: dict[str, dict[str, float | int | list[float]]] = {}
    rng = random.Random(3058)
    for arm in ("fence", "strip"):
        differences: list[int] = []
        by_scenario: dict[str, list[int]] = {}
        control_regressions = 0
        for (scenario, repeat_seed, paired_arm), row in by_key.items():
            if paired_arm != arm:
                continue
            baseline = by_key[(scenario, repeat_seed, "off")]
            difference = int(bool(row["success"])) - int(bool(baseline["success"]))
            differences.append(difference)
            by_scenario.setdefault(scenario, []).append(difference)
            if row["expected"] == "stop" and baseline["success"] and not row["success"]:
                control_regressions += 1
        point = 100 * sum(differences) / len(differences) if differences else 0.0
        clusters = list(by_scenario.values())
        bootstrap: list[float] = []
        for _ in range(10_000):
            sample = [rng.choice(clusters) for _ in clusters] if clusters else []
            sample_values = [value for cluster in sample for value in cluster]
            bootstrap.append(100 * sum(sample_values) / len(sample_values) if sample_values else 0.0)
        bootstrap.sort()
        paired[arm] = {
            "absolute_difference_pp": point,
            "paired_bootstrap_95pct_ci_pp": [bootstrap[249], bootstrap[9749]] if bootstrap else [0.0, 0.0],
            "control_regressions_against_correct_baseline": control_regressions,
            "paired_blocks": len(differences),
        }
    return {"arms": counts, "paired_vs_off": paired}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=3058)
    parser.add_argument("--run", action="store_true", help="contact only local Ollama")
    parser.add_argument("--output", type=Path, default=HERE / "results" / "run.jsonl")
    args = parser.parse_args()
    corpus = scenarios()
    print(f"Synthetic stub-MCP scenarios: {len(corpus)} ({sum(s.expected != 'stop' for s in corpus)} recoverable, "
          f"{sum(s.expected == 'stop' for s in corpus)} controls)")
    if not args.run:
        print("Dry run only. Add --run to call Ollama; no model was contacted.")
        return
    results = run(ollama, model=args.model, repeats=args.repeats, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results),
                           encoding="utf-8")
    print(f"Wrote {len(results)} outcomes to {args.output}")
    print(json.dumps(paired_summary(results), indent=2))


if __name__ == "__main__":
    main()
