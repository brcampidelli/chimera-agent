"""Which model from another family should `chimera review` pick by default?

    python bench/review_reviewer/run.py --check DIR                    # S15's instrument check
    python bench/review_reviewer/run.py --probe                        # one priced call per route
    python bench/review_reviewer/run.py --pilot DIR [--out results/pilot.json]
    python bench/review_reviewer/run.py --run DIR [--out results/run.json] [--replicas 2]
    python bench/review_reviewer/run.py --report results/run.json
    python bench/review_reviewer/run.py --hits results/run.json ARM    # every hit, for the hand read

See PREREGISTRATION.md, registered before any call. The items, the fixtures, the scoring and the
product path are `bench/review_seeded`'s (S15), imported rather than copied, so a number here and a
number there come from one harness: the fixtures hash (`b2f9e29a5fd3`) and both prompt hashes are
checked against S15's before anything is spent.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load_s15() -> Any:
    spec = importlib.util.spec_from_file_location("s15_run", REPO / "bench/review_seeded/run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


s15 = _load_s15()  # also puts REPO on sys.path, and reconfigures stdout to UTF-8

from chimera.providers.gateway import CompletionResult  # noqa: E402
from chimera.review import CautiousVerifier, KeepAll, ReviewerChoice, collect, review  # noqa: E402
from chimera.review.finder import FINDER_SYSTEM  # noqa: E402
from chimera.review.verifier import VERIFIER_SYSTEM  # noqa: E402

AUTHOR = "openrouter/deepseek/deepseek-v4-flash-0731"  # the product default model


@dataclass(frozen=True)
class Arm:
    model: str
    provider: str  # the one OpenRouter provider this arm is pinned to, fallbacks off
    price: tuple[float, float]  # US$ per million tokens (input, output) on that provider
    guard: float  # no call of this arm starts once its running cost reaches this


#: Frozen in PREREGISTRATION.md. Prices are the pinned route's, read from OpenRouter's public
#: endpoints listing on 2026-09-26 (`/api/v1/models/<slug>/endpoints`); `--probe` checks them
#: against the cost OpenRouter itself reports before the run.
ARMS: dict[str, Arm] = {
    "D": Arm(AUTHOR, "DeepInfra", (0.06, 0.18), 0.15),
    "L": Arm("openrouter/openai/gpt-6-luna", "OpenAI", (0.10, 0.50), 0.75),
    "Q": Arm("openrouter/qwen/qwen3.7-flash", "Alibaba", (0.03, 0.13), 0.25),
    # Amendment 1: DeepInfra serves at most 16,384 completion tokens and the product asks for its
    # 32,000-token ceiling, so OpenRouter removes that route before pinning is applied. Parasail
    # is the one route that accepts the product's request, and so the one the product reaches.
    "M": Arm("openrouter/mistralai/mistral-small-3.2-24b-instruct", "Parasail", (0.09, 0.30),
             0.10),
}
ORDER = ("D", "L", "Q", "M")  # within an item, replica 1 of every arm, then replica 2
CANDIDATES = ("L", "Q", "M")
S15_FIXTURES_SHA = "b2f9e29a5fd3"
S15_PROMPT_SHAS = ("3a480dab0478", "ee9d780a9f4b")
PILOT_ITEMS = 3  # the first three of the registered order: two seeded, one clean
#: The frozen selection rule (PREREGISTRATION.md).
RECALL_MARGIN = 0.10
INCOMPLETE_MAX = 0.05
CLEAN_MARGIN = 2.0
#: Positive control: D's replica-1 finder recall reproduces S15's published 17/20 within 3 items.
CONTROL_RANGE = (14, 20)
#: An arm is measured when it was not stopped and replica 1 covered at least this many items.
MIN_SEEDED = 16
MIN_CLEAN = 8
#: Stop rule: an arm whose finder calls raise a transport error on more than this share of its
#: turns, after at least ten turns, starts no new turn.
TRANSPORT_STOP = 0.10


# --- the pinned backend ------------------------------------------------------------------------


class _Pinned:
    """The gateway pinned to one OpenRouter provider with fallbacks off, charging every reply."""

    def __init__(self, gateway: Any, arm: str, budget: Any) -> None:
        self.gateway = gateway
        self.arm = arm
        self.budget = budget
        self.log: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        role = "finder" if messages[0].content == FINDER_SYSTEM else (
            "verifier" if messages[0].content == VERIFIER_SYSTEM else "?")
        if self.budget.exhausted():
            self.log.append({"role": role, "error": "budget guard"})
            raise s15._OverBudget("budget guard")
        kwargs["extra_body"] = {
            "provider": {"order": [ARMS[self.arm].provider], "allow_fallbacks": False}
        }
        started = time.time()
        try:
            result = self.gateway.complete(messages, **kwargs)
        except Exception as exc:
            self.log.append({"role": role, "error": f"{type(exc).__name__}: {exc}"[:300],
                             "seconds": round(time.time() - started, 1)})
            raise
        entry = {
            "role": role, "prompt": int(result.prompt_tokens or 0),
            "completion": int(result.completion_tokens or 0),
            "cache_read": int(result.cache_read_tokens or 0), "provider": result.provider,
            "finish": result.finish_reason, "seconds": round(time.time() - started, 1),
            "content": (result.content or "")[:20000],
        }
        self.log.append(entry)
        self.budget.charge(_usd(self.arm, [entry]))
        return result


def _usd(arm: str, log: list[dict[str, Any]]) -> float:
    pin, pout = ARMS[arm].price
    return sum(e.get("prompt", 0) * pin + e.get("completion", 0) * pout for e in log) / 1e6


def _turn(gateway: Any, fx: dict[str, Any], arm: str, root: Path, budget: Any) -> dict[str, Any]:
    """One review of one item by one arm: S15's `_turn`, with the arm's own model and route."""
    model = ARMS[arm].model
    pinned = _Pinned(gateway, arm, budget)
    empty: dict[str, Any] = {"findings": [], "hits": [], "verified": [], "log": pinned.log}
    if budget.exhausted():
        return {**empty, "status": "budget", "error": None, "usd": 0.0}
    repo = s15.make_repo(fx, root)
    try:
        diff = collect(repo)
        report = review(diff, pinned, ReviewerChoice(model, AUTHOR, "bench"), KeepAll())
        if any(e.get("error") == "budget guard" for e in pinned.log):
            return {**empty, "status": "budget", "error": None, "usd": _usd(arm, pinned.log)}
        located = report.findings + report.dropped
        seed = fx["seed"]
        # S15's registered subset: the seed's hits on a seeded diff, every anchored finding on a
        # clean one, each checked by the product's verifier on the arm's own model.
        chosen = s15.hits(report.findings, seed) if seed else list(report.findings)
        verifier = CautiousVerifier(pinned, model)
        checked = []
        for f in chosen:
            file = diff.file(f.file)
            assert file is not None
            if budget.exhausted():
                checked.append({**s15._brief(f), "verdict": "not run", "label": "budget",
                                "reason": ""})
                continue
            v = verifier.check(f, file)
            label = "budget" if v.reason == "budget guard" else v.label
            checked.append({**s15._brief(f), "verdict": v.state, "label": label,
                            "reason": v.reason})
        return {
            "status": report.status, "findings": [s15._brief(f) for f in located],
            "hits": [s15._brief(f) for f in s15.hits(located, seed)], "verified": checked,
            "log": pinned.log, "usd": _usd(arm, pinned.log), "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — a harness failure is a halt, never raised
        return {**empty, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:300],
                "usd": _usd(arm, pinned.log)}
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def _transport(turn: dict[str, Any]) -> bool:
    """The finder call raised something other than a timeout: the pinned route failed, not the
    model. A timeout (600 s) is the model's, like a reply cut at the ceiling."""
    for e in turn["log"]:
        if e.get("role") == "finder" and "error" in e and e["error"] != "budget guard":
            return "timeout" not in e["error"].lower()
    return False


def _cause(turn: dict[str, Any]) -> str:
    """Why an incomplete review is incomplete, read from the finder's log entry."""
    for e in turn["log"]:
        if e.get("role") != "finder":
            continue
        if "error" in e:
            return "timeout" if "timeout" in e["error"].lower() else "transport"
        if e.get("finish") == "length":
            return "ceiling"
        if not (e.get("content") or "").strip():
            return "empty"
        return "unreadable"
    return "no finder call"


# --- checks before spending --------------------------------------------------------------------


def _hashes(folder: Path) -> tuple[str, str, str]:
    fixtures = s15._fixtures(folder)
    blob = "".join(json.dumps(f, sort_keys=True) for f in fixtures)
    return s15._sha(blob), s15._sha(FINDER_SYSTEM), s15._sha(VERIFIER_SYSTEM)


def check(folder: Path) -> None:
    """S15's instrument check, then the hashes that make this S15's harness and items."""
    s15.check(folder)
    got = _hashes(folder)
    print(f"fixtures {got[0]} · finder {got[1]} · verifier {got[2]}")
    assert got == (S15_FIXTURES_SHA, *S15_PROMPT_SHAS), "not S15's items or prompts"
    print("same items and prompts as S15")


def probe() -> None:
    """One small call per route, straight to OpenRouter with its own cost accounting on: does the
    pinned provider answer, and does tokens × the registered price match what OpenRouter bills?"""
    key = os.environ["OPENROUTER_API_KEY"]
    for name, arm in ARMS.items():
        body = {
            "model": arm.model.removeprefix("openrouter/"),
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
            "max_tokens": 400, "usage": {"include": True},
            "provider": {"order": [arm.provider], "allow_fallbacks": False},
        }
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read())
        except Exception as exc:  # noqa: BLE001 — reported, the run does not start on it
            print(f"{name} {arm.model}: FAILED {type(exc).__name__}: {str(exc)[:200]}")
            continue
        usage = data.get("usage") or {}
        billed = float(usage.get("cost") or 0.0)
        ours = _usd(name, [{"prompt": usage.get("prompt_tokens", 0),
                            "completion": usage.get("completion_tokens", 0)}])
        ratio = billed / ours if ours else float("nan")
        print(f"{name} {arm.model} via {data.get('provider')}: tokens {usage.get('prompt_tokens')}"
              f"/{usage.get('completion_tokens')} billed US$ {billed:.8f} ours US$ {ours:.8f} "
              f"ratio {ratio:.3f}")


# --- the paid run ------------------------------------------------------------------------------


def run(
    folder: Path, out: Path, workers: int, replicas: int, limit: int | None,
    arms: tuple[str, ...] = ORDER,
) -> None:
    from chimera.providers.gateway import LLMGateway

    got = _hashes(folder)
    assert got == (S15_FIXTURES_SHA, *S15_PROMPT_SHAS), f"not S15's items or prompts: {got}"
    gateway = LLMGateway()
    fixtures = s15._fixtures(folder)
    order = s15.registered_order(fixtures)[:limit]
    budgets = {a: s15._Budget(arm.guard) for a, arm in ARMS.items()}
    lock = threading.Lock()
    transport = dict.fromkeys(ARMS, 0)
    turns = dict.fromkeys(ARMS, 0)
    stopped: dict[str, str] = {}
    rows: dict[int, dict[str, Any]] = {}

    def item(index: int) -> dict[str, Any]:
        fx = fixtures[index]
        row: dict[str, Any] = {"commit": fx["commit"], "kind": fx["kind"], "seed": fx["seed"],
                               "runs": {a: [] for a in ARMS}}
        with tempfile.TemporaryDirectory(prefix="rv-") as tmp:
            for rep in range(replicas):
                for arm in arms:
                    with lock:
                        halted = stopped.get(arm)
                    if halted:
                        row["runs"][arm].append({"status": "stopped", "findings": [], "hits": [],
                                                 "verified": [], "log": [], "usd": 0.0,
                                                 "error": halted})
                        continue
                    got = _turn(gateway, fx, arm, Path(tmp) / f"{arm}{rep}", budgets[arm])
                    row["runs"][arm].append(got)
                    if got["status"] == "budget":
                        continue
                    with lock:
                        turns[arm] += 1
                        transport[arm] += int(_transport(got))
                        if turns[arm] >= 10 and transport[arm] / turns[arm] > TRANSPORT_STOP:
                            stopped.setdefault(
                                arm, f"stop rule: {transport[arm]}/{turns[arm]} transport errors")
        return row

    def mark(turn: dict[str, Any]) -> str:
        status = turn["status"]
        if status in ("budget", "stopped", "error"):
            return {"budget": "$", "stopped": "S", "error": "X"}[status]
        if status == "incomplete":
            return "T" if _transport(turn) else "E"
        return "H" if turn["hits"] else "."

    # Each finished row is also appended here as it lands, so a WSL VM that dies mid-run (it has,
    # on this machine) loses the rows in flight and not the money already spent on the others.
    partial = out.with_suffix(".partial.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    partial.unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(item, i): i for i in order}
        for n, future in enumerate(as_completed(futures), 1):
            row = future.result()
            rows[futures[future]] = row
            with partial.open("a", encoding="utf-8", newline="\n") as sink:
                sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            marks = " ".join(f"{a}={''.join(mark(r) for r in row['runs'][a])}" for a in ORDER)
            spent = sum(b.usd for b in budgets.values())
            print(f"  [{n:>2}/{len(order)}] {row['kind']:6} {row['commit']} {marks}  "
                  f"US$ {spent:.3f}", flush=True)
    payload = {
        "arms": {a: {"model": x.model, "provider": x.provider, "price_per_m": x.price,
                     "guard": x.guard} for a, x in ARMS.items()},
        "replicas": replicas, "tol": s15.TOL, "order": [fixtures[i]["commit"] for i in order],
        "stopped": stopped, "usd": round(sum(b.usd for b in budgets.values()), 4),
        "usd_by_arm": {a: round(b.usd, 4) for a, b in budgets.items()},
        "fixtures_sha": got[0], "finder_prompt_sha": got[1], "verifier_prompt_sha": got[2],
        "rows": [rows[i] for i in order if i in rows],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                   newline="\n")
    print(f"\nwrote {out}  US$ {payload['usd']:.4f}  stopped: {stopped or 'no'}")


def raw(path: Path, chars: int) -> None:
    """The first finder reply of each arm on each row, printed for reading by eye (§2e)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for row in data["rows"]:
        for arm in ORDER:
            for turn in row["runs"][arm][:1]:
                finder = next((e for e in turn["log"] if e.get("role") == "finder"), {})
                print(f"\n===== {arm} {row['commit']} ({row['kind']}) status={turn['status']} "
                      f"finish={finder.get('finish')} tokens={finder.get('prompt')}/"
                      f"{finder.get('completion')} {finder.get('seconds')}s via "
                      f"{finder.get('provider')} US$ {turn['usd']:.5f}")
                print(finder.get("error") or (finder.get("content") or "")[:chars])


# --- the report --------------------------------------------------------------------------------


HALTS = ("budget", "stopped", "error")


def _done(turn: dict[str, Any]) -> bool:
    """A completed review: it ran, and its status is not incomplete."""
    return turn["status"] not in (*HALTS, "incomplete")


def _counted(turn: dict[str, Any]) -> bool:
    """A review that enters the incomplete rate: it ran and did not fail on the route."""
    return turn["status"] not in HALTS and not (turn["status"] == "incomplete" and _transport(turn))


def _e2e(turn: dict[str, Any]) -> bool:
    """A hit that the verifier did not drop: what `chimera review` shows by default."""
    return any(v["verdict"] != "dropped" for v in turn["verified"])


def _shown(turn: dict[str, Any]) -> int:
    """Findings a clean review shows after the verifier."""
    return sum(1 for v in turn["verified"] if v["verdict"] != "dropped")


def _arm_stats(rows: list[dict[str, Any]], arm: str, replicas: int) -> dict[str, Any]:
    turns = [(row, t) for row in rows for t in row["runs"][arm][:replicas]]
    counted = [(r, t) for r, t in turns if _counted(t)]
    done = [(r, t) for r, t in turns if _done(t)]
    seeded = [(r, t) for r, t in done if r["seed"]]
    clean_by_rep = []
    for rep in range(replicas):
        cells = [row["runs"][arm][rep] for row in rows if not row["seed"]
                 and len(row["runs"][arm]) > rep and _done(row["runs"][arm][rep])]
        if cells:
            clean_by_rep.append((sum(_shown(t) for t in cells), sum(
                sum(f["anchor"] == "kept" for f in t["findings"]) for t in cells), len(cells)))
    incomplete = [t for _, t in counted if t["status"] == "incomplete"]
    ran = [t for _, t in turns if t["status"] not in ("budget", "stopped")]
    usd = sum(t["usd"] for t in ran)
    secs = sorted(e["seconds"] for t in ran for e in t["log"]
                  if e.get("role") == "finder" and "seconds" in e and "error" not in e)
    seeded_counted = [(r, t) for r, t in counted if r["seed"]]
    first = [(row, row["runs"][arm][0]) for row in rows if row["runs"][arm]]
    return {
        # Coverage of replica 1: reviews that ran and did not fail on the route.
        "r1_seeded": sum(1 for r, t in first if r["seed"] and _counted(t)),
        "r1_clean": sum(1 for r, t in first if not r["seed"] and _counted(t)),
        "turns": len(turns), "ran": len(ran), "counted": len(counted),
        "transport": sum(1 for _, t in turns if t["status"] == "incomplete" and _transport(t)),
        "halted": sum(1 for _, t in turns if t["status"] in HALTS),
        "incomplete": len(incomplete),
        "causes": {c: sum(1 for t in incomplete if _cause(t) == c)
                   for c in sorted({_cause(t) for t in incomplete})},
        "seeded": len(seeded), "e2e": sum(1 for _, t in seeded if _e2e(t)),
        "finder": sum(1 for _, t in seeded if t["hits"]),
        "seeded_counted": len(seeded_counted),
        "e2e_as_miss": sum(1 for _, t in seeded_counted if _done(t) and _e2e(t)),
        "clean_reps": clean_by_rep,
        # Findings shown on the ten clean diffs, per replica scaled to ten diffs, then averaged.
        "clean_per10": (sum(10 * s / n for s, _, n in clean_by_rep) / len(clean_by_rep)
                        if clean_by_rep else None),
        "clean_before_per10": (sum(10 * b / n for _, b, n in clean_by_rep) / len(clean_by_rep)
                               if clean_by_rep else None),
        "usd": usd, "usd_per_review": usd / len(ran) if ran else None,
        "finder_median_s": secs[len(secs) // 2] if secs else None,
        "cache_read": sum(e.get("cache_read", 0) for t in ran for e in t["log"]),
        "prompt": sum(e.get("prompt", 0) for t in ran for e in t["log"]),
        "completion": sum(e.get("completion", 0) for t in ran for e in t["log"]),
    }


def _rate(k: int, n: int) -> float | None:
    return k / n if n else None


def _measured(s: dict[str, Any], stopped: str | None) -> str:
    """Empty when the arm counts as measured; otherwise why not (PREREGISTRATION.md)."""
    if stopped:
        return stopped
    if s["r1_seeded"] < MIN_SEEDED or s["r1_clean"] < MIN_CLEAN:
        return (f"replica 1 covered {s['r1_seeded']}/20 seeded and {s['r1_clean']}/10 clean, "
                f"under {MIN_SEEDED} and {MIN_CLEAN}")
    return ""


def decide(
    stats: dict[str, dict[str, Any]], stopped: dict[str, str]
) -> tuple[str, list[str]]:
    """The frozen selection rule, applied mechanically. Returns the chosen arm and the reasons."""
    d = stats["D"]
    if _measured(d, stopped.get("D")):
        return "", [f"  D, the reference, is not measured ({_measured(d, stopped.get('D'))}): "
                    "no decision"]
    d_recall = _rate(d["e2e"], d["seeded"])
    lines = []
    qualifying = []
    for arm in CANDIDATES:
        s = stats[arm]
        recall = _rate(s["e2e"], s["seeded"])
        incomplete = _rate(s["incomplete"], s["counted"])
        why = []
        missing = _measured(s, stopped.get(arm))
        if missing or recall is None or d_recall is None or incomplete is None                 or s["clean_per10"] is None:
            why.append(f"not measured: {missing or 'no completed review'}")
        else:
            if recall < d_recall - RECALL_MARGIN - 1e-9:
                why.append(f"recall {recall:.1%} < D's {d_recall:.1%} - 10 pp")
            if incomplete > INCOMPLETE_MAX + 1e-9:
                why.append(f"incomplete {incomplete:.1%} > 5%")
            if s["clean_per10"] > d["clean_per10"] + CLEAN_MARGIN + 1e-9:
                why.append(f"clean findings {s['clean_per10']:.1f} > D's "
                           f"{d['clean_per10']:.1f} + 2")
        if why:
            lines.append(f"  {arm}: does not qualify ({'; '.join(why)})")
        else:
            qualifying.append(arm)
            lines.append(f"  {arm}: qualifies, US$ {s['usd_per_review']:.5f} per review")
    if not qualifying:
        lines.append("  no candidate qualifies: the default reviewer falls back to D's family")
        return "D", lines
    chosen = min(qualifying, key=lambda a: stats[a]["usd_per_review"])
    lines.append(f"  chosen: {chosen} ({ARMS[chosen].model}), the cheapest qualifying")
    return chosen, lines


def _wilson(k: int, n: int) -> str:
    return s15._wilson(k, n)


def _replace_arm(data: dict[str, Any], arm: str, other: Path) -> None:
    """Amendment 3: one arm's cells from a later run of that arm alone, matched by commit."""
    rerun = json.loads(other.read_text(encoding="utf-8"))
    by_commit = {row["commit"]: row["runs"][arm] for row in rerun["rows"]}
    for row in data["rows"]:
        row["runs"][arm] = by_commit.get(row["commit"], [])
    data["stopped"] = {a: why for a, why in data["stopped"].items() if a != arm}
    if arm in rerun["stopped"]:
        data["stopped"][arm] = rerun["stopped"][arm]
    data["usd"] = round(data["usd"] - data["usd_by_arm"][arm] + rerun["usd_by_arm"][arm], 4)
    data["usd_by_arm"][arm] = rerun["usd_by_arm"][arm]
    print(f"arm {arm} read from {other.name} (Amendment 3)")


def report(path: Path, replace: tuple[str, Path] | None = None) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if replace is not None:
        _replace_arm(data, *replace)
    rows, replicas = data["rows"], data["replicas"]
    print(f"rows {len(rows)} · replicas {replicas} · US$ {data['usd']} · stopped: "
          f"{data['stopped'] or 'no'} · fixtures {data['fixtures_sha']}")
    stats = {arm: _arm_stats(rows, arm, replicas) for arm in ARMS}
    for arm, s in stats.items():
        x = ARMS[arm]
        print(f"\n[{arm}] {x.model} via {x.provider}  turns {s['turns']}, ran {s['ran']}, "
              f"halted {s['halted']}, transport failures {s['transport']}")
        print(f"  incomplete reviews               {_wilson(s['incomplete'], s['counted'])}  "
              f"causes {s['causes']}")
        print(f"  recall, a hit shown (±{data['tol']})       {_wilson(s['e2e'], s['seeded'])}")
        print(f"    finder recall, before verifier {_wilson(s['finder'], s['seeded'])}")
        print(f"    incomplete counted as a miss   {_wilson(s['e2e_as_miss'], s['seeded_counted'])}")
        for rep in range(replicas):
            cell = [row["runs"][arm][rep] for row in rows if row["seed"]
                    and len(row["runs"][arm]) > rep and _done(row["runs"][arm][rep])]
            print(f"    replica {rep + 1}: {sum(_e2e(t) for t in cell)}/{len(cell)} shown, "
                  f"{sum(bool(t['hits']) for t in cell)}/{len(cell)} located")
        reps = ", ".join(f"{sh} shown of {b} anchored over {n}" for sh, b, n in s["clean_reps"])
        per10 = s["clean_per10"]
        print(f"  clean diffs: {reps}; per 10 diffs after the verifier "
              f"{per10 if per10 is None else round(per10, 2)} "
              f"(before {s['clean_before_per10'] and round(s['clean_before_per10'], 2)})")
        per = s["usd_per_review"]
        print(f"  cost US$ {s['usd']:.4f}, per review US$ {per if per is None else round(per, 6)}"
              f" · tokens {s['prompt']}/{s['completion']} · cache read {s['cache_read']}"
              f" · finder median {s['finder_median_s']}s")
    d1 = [row["runs"]["D"][0] for row in rows if row["seed"] and row["runs"]["D"]
          and _done(row["runs"]["D"][0])]
    located = sum(bool(t["hits"]) for t in d1)
    lo, hi = CONTROL_RANGE
    verdict = "reproduced" if lo <= located <= hi and len(d1) == 20 else "NOT REPRODUCED"
    print(f"\nPOSITIVE CONTROL D replica 1 finder recall {located}/{len(d1)} against S15's 17/20: "
          f"{verdict}")
    print("FLOOR replica disagreement on a shown hit, seeded items with every replica done:")
    for arm in ARMS:
        dis = m = 0
        for row in rows:
            runs = row["runs"][arm][:replicas]
            if row["seed"] and len(runs) == replicas and replicas > 1 and all(map(_done, runs)):
                m += 1
                dis += int(len({_e2e(t) for t in runs}) > 1)
        print(f"  {arm}: {dis}/{m}")
    print("PAIRED against D, replica 1, a shown hit (reported, not decided on):")
    for arm in CANDIDATES:
        b = c = n = 0
        for row in rows:
            if not row["seed"] or not row["runs"]["D"] or not row["runs"][arm]:
                continue
            dt, ct = row["runs"]["D"][0], row["runs"][arm][0]
            if not (_done(dt) and _done(ct)):
                continue
            n += 1
            b += int(_e2e(dt) and not _e2e(ct))
            c += int(_e2e(ct) and not _e2e(dt))
        print(f"  {arm} on {n}: only D {b}, only {arm} {c}, exact McNemar p = "
              f"{s15._mcnemar(b, c):.3g}")
    chosen, lines = decide(stats, data["stopped"])
    print("\nDECISION (frozen rule)")
    print("\n".join(lines))


def show_hits(path: Path, arm: str) -> None:
    """Every hit an arm located on a seeded diff, beside the seeded defect, for the hand read."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for row in data["rows"]:
        if not row["seed"]:
            continue
        for rep, turn in enumerate(row["runs"][arm]):
            if not _done(turn):
                continue
            print(f"\n--- {row['commit']} r{rep + 1} seed L{row['seed']['line']}: "
                  f"{row['seed']['defect']}")
            if not turn["hits"]:
                print("    (no hit)")
            for f in turn["hits"]:
                verdict = next((v["verdict"] for v in turn["verified"]
                                if v["line"] == f["line"] and v["title"] == f["title"]), "?")
                print(f"    L{f['line']} {f['priority']} [{verdict}] {f['title']}")
                print(f"      {f['consequence'][:300]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", type=Path)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--pilot", type=Path)
    ap.add_argument("--run", type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--hits", nargs=2, metavar=("RUN_JSON", "ARM"))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--replicas", type=int, default=2)
    ap.add_argument("--arms", default="".join(ORDER), help="a subset of the arms, e.g. M")
    ap.add_argument("--arm-from", help="report: ARM=run.json, that arm's cells from another run")
    args = ap.parse_args()
    if args.check:
        check(args.check)
    elif args.probe:
        probe()
    elif args.pilot:
        run(args.pilot, args.out or HERE / "results" / "pilot.json", args.workers, 1, PILOT_ITEMS,
            tuple(a for a in ORDER if a in args.arms))
    elif args.run:
        run(args.run, args.out or HERE / "results" / "run.json", args.workers, args.replicas, None,
            tuple(a for a in ORDER if a in args.arms))
    elif args.report:
        arm, _, other = (args.arm_from or "").partition("=")
        report(args.report, (arm, Path(other)) if other else None)
    elif args.raw:
        raw(args.raw, 1500)
    elif args.hits:
        show_hits(Path(args.hits[0]), args.hits[1])
    else:
        ap.error("pass --check, --probe, --pilot, --run, --report, --raw or --hits")


if __name__ == "__main__":
    main()
