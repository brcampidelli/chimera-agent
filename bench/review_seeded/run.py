"""S15: does `chimera review` find a defect seeded into a real diff, and what does its verifier keep?

    python bench/review_seeded/run.py --build DIR          # fixtures from git history; no calls
    python bench/review_seeded/run.py --check DIR          # the instrument check; no calls
    python bench/review_seeded/run.py --run DIR [--out results/run.json]
    python bench/review_seeded/run.py --report results/run.json

See PREREGISTRATION.md, registered before any call. Every item is rebuilt as a small git repository
(the commit's parent on ``main``, the commit's version, seeded or not, in the working tree), and
the product's own `collect` and `review` read it, so the bench measures the shipped path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from items import ITEMS  # noqa: E402

from chimera.providers.gateway import CompletionResult  # noqa: E402
from chimera.review import CautiousVerifier, KeepAll, ReviewerChoice, collect, review  # noqa: E402
from chimera.review.finder import FINDER_SYSTEM  # noqa: E402
from chimera.review.report import Finding  # noqa: E402
from chimera.review.verifier import VERIFIER_SYSTEM  # noqa: E402

AUTHOR = "openrouter/deepseek/deepseek-v4-flash-0731"  # the product default, and arm D's reviewer
ARMS = {"D": AUTHOR, "G": "openrouter/z-ai/glm-5.3"}  # G: the default config's reviewer
REPLICAS = {"D": 2, "G": 1}
PROVIDER = "DeepInfra"
#: US$ per million tokens, DeepInfra's route on OpenRouter, read from the public index 2026-09-25.
PRICE = {"D": (0.06, 0.18), "G": (0.5625, 2.50)}
TOL = 3  # a finding hits the seed at the seeded file and |line - seeded line| <= TOL
CAP_USD = 2.00
GUARD_USD = 1.60  # no new item starts past this, leaving room for the items in flight
ORDER_SEED = 20260925


# --- fixtures ----------------------------------------------------------------------------------


def _git_out(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def _show(rev: str, path: str) -> str | None:
    done = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=REPO, capture_output=True,
                          text=True, encoding="utf-8")
    return done.stdout if done.returncode == 0 else None


def _seed_line(after: str, old: str, new: str) -> int:
    """The 1-based line of the seeded file where the injected text first differs from the original."""
    start = after.index(new)
    same = 0
    for a, b in zip(old.splitlines(), new.splitlines(), strict=False):
        if a != b:
            break
        same += 1
    return after[:start].count("\n") + 1 + same


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for item in ITEMS:
        paths = [p for p in _git_out("diff-tree", "--no-commit-id", "--name-only", "-r", item.commit,
                                     "--", "chimera/*.py").splitlines() if p.strip()]
        files = {p: {"before": _show(f"{item.commit}^", p), "after": _show(item.commit, p)}
                 for p in paths}
        fixture: dict[str, Any] = {"commit": item.commit, "kind": item.kind, "files": files,
                                   "seed": None}
        if item.seed is not None:
            s = item.seed
            after = files[s.path]["after"]
            assert after is not None and after.count(s.old) == 1, (item.commit, after and after.count(s.old))
            seeded = after.replace(s.old, s.new)
            files[s.path]["after"] = seeded
            fixture["seed"] = {"path": s.path, "line": _seed_line(seeded, s.old, s.new),
                               "defect": s.defect, "category": s.category}
        (out / f"{item.commit}.json").write_text(json.dumps(fixture, ensure_ascii=False),
                                                 encoding="utf-8")
    print(f"built {len(ITEMS)} fixtures in {out}")


def make_repo(fixture: dict[str, Any], root: Path) -> Path:
    repo = root / fixture["commit"]
    repo.mkdir(parents=True)
    ident = ["-c", "user.email=bench@example.invalid", "-c", "user.name=bench"]
    _git_out("init", "-q", "-b", "main", cwd=repo)
    for path, versions in fixture["files"].items():
        if versions["before"] is not None:
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(versions["before"].encode("utf-8"))
    _git_out("add", "-A", cwd=repo)
    _git_out(*ident, "commit", "-q", "--allow-empty", "-m", "parent", cwd=repo)
    for path, versions in fixture["files"].items():
        target = repo / path
        if versions["after"] is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(versions["after"].encode("utf-8"))
    return repo


def _fixtures(folder: Path) -> list[dict[str, Any]]:
    return [json.loads((folder / f"{i.commit}.json").read_text(encoding="utf-8")) for i in ITEMS]


def hits(findings: list[Finding], seed: dict[str, Any] | None, tol: int = TOL) -> list[Finding]:
    if seed is None:
        return []
    return [f for f in findings if f.file == seed["path"] and abs(f.line - seed["line"]) <= tol]


# --- the instrument check (free) ---------------------------------------------------------------


class _Scripted:
    """A backend that answers the finder with a fixed reply and keeps everything at verification."""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, messages: list[Any], **_kw: Any) -> CompletionResult:
        system = messages[0].content
        text = self.reply if system == FINDER_SYSTEM else '{"reason": "", "verdict": "keep"}'
        return CompletionResult(content=text, model="scripted")


def check(folder: Path) -> None:
    """The grader runs first (bench/PROTOCOL.md): every seed sits on an added line of the product's
    own diff, an oracle finding at the seed scores 20/20, silence scores 0/20, and a finding ten
    lines off misses."""
    choice = ReviewerChoice("scripted", AUTHOR, "bench")
    oracle = far = silent = 0
    with tempfile.TemporaryDirectory(prefix="s15-check-") as tmp:
        for fx in _fixtures(folder):
            diff = collect(make_repo(fx, Path(tmp)))
            seed = fx["seed"]
            if seed is None:
                continue
            f = diff.file(seed["path"])
            assert f is not None, fx["commit"]
            added = {ln.new for h in f.hunks for ln in h.lines if ln.tag == "+"}
            assert seed["line"] in added, (fx["commit"], seed["line"])
            for line, counter in ((seed["line"], "oracle"), (seed["line"] + 10, "far")):
                reply = json.dumps({"findings": [{"file": seed["path"], "line": line,
                                                  "priority": "P1", "title": "x"}]})
                report = review(diff, _Scripted(reply), choice, KeepAll())
                got = hits(report.findings + report.dropped, seed)
                if counter == "oracle":
                    oracle += bool(got)
                else:
                    far += bool(got)
            report = review(diff, _Scripted('{"findings": []}'), choice, KeepAll())
            silent += bool(hits(report.findings, seed))
            assert report.status == "no_findings", (fx["commit"], report.status)
    print(f"oracle hits {oracle}/20 · ten lines off hits {far}/20 · silence hits {silent}/20")
    assert (oracle, far, silent) == (20, 0, 0), "the instrument cannot score this set"
    print("instrument check passed")


# --- the paid run ------------------------------------------------------------------------------


class _OverBudget(RuntimeError):
    """The guard is reached: this call is not made."""


class _Budget:
    """Every call is charged as it returns, and none starts once the guard is reached. The worst
    overshoot is one call per worker: 4 × about US$ 0.08 (a 32k-token reply on G) above the guard."""

    def __init__(self, guard: float) -> None:
        self.guard = guard
        self.usd = 0.0
        self.lock = threading.Lock()

    def charge(self, usd: float) -> None:
        with self.lock:
            self.usd += usd

    def exhausted(self) -> bool:
        with self.lock:
            return self.usd >= self.guard


class _Pinned:
    """The gateway pinned to one OpenRouter provider, no fallbacks, recording and charging every
    reply."""

    def __init__(self, gateway: Any, arm: str, budget: _Budget) -> None:
        self.gateway = gateway
        self.arm = arm
        self.budget = budget
        self.log: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        role = "finder" if messages[0].content == FINDER_SYSTEM else (
            "verifier" if messages[0].content == VERIFIER_SYSTEM else "?")
        if self.budget.exhausted():
            self.log.append({"role": role, "error": "budget guard"})
            raise _OverBudget("budget guard")
        kwargs["extra_body"] = {"provider": {"order": [PROVIDER], "allow_fallbacks": False}}
        started = time.time()
        try:
            result = self.gateway.complete(messages, **kwargs)
        except Exception as exc:
            self.log.append({"role": role, "error": f"{type(exc).__name__}: {exc}"[:300]})
            raise
        entry = {
            "role": role, "prompt": int(result.prompt_tokens or 0),
            "completion": int(result.completion_tokens or 0), "provider": result.provider,
            "finish": result.finish_reason, "seconds": round(time.time() - started, 1),
            "content": (result.content or "")[:20000],
        }
        self.log.append(entry)
        self.budget.charge(_usd(self.arm, [entry]))
        return result


def _usd(arm: str, log: list[dict[str, Any]]) -> float:
    pin, pout = PRICE[arm]
    return sum(e.get("prompt", 0) * pin + e.get("completion", 0) * pout for e in log) / 1e6


def _brief(f: Finding) -> dict[str, Any]:
    return {"file": f.file, "line": f.line, "priority": f.priority, "confidence": f.confidence,
            "title": f.title, "evidence": f.evidence[:400], "consequence": f.consequence[:400],
            "anchor": "dropped" if f.verdict and f.verdict.stage == "anchor" else "kept"}


def _turn(gateway: Any, fx: dict[str, Any], arm: str, root: Path, budget: _Budget) -> dict[str, Any]:
    model = ARMS[arm]
    pinned = _Pinned(gateway, arm, budget)
    empty: dict[str, Any] = {"findings": [], "hits": [], "verified": [], "log": pinned.log}
    if budget.exhausted():
        return {**empty, "status": "budget", "error": None, "usd": 0.0}
    repo = make_repo(fx, root)
    try:
        diff = collect(repo)
        report = review(diff, pinned, ReviewerChoice(model, AUTHOR, "bench"), KeepAll())
        if any(e.get("error") == "budget guard" for e in pinned.log):
            return {**empty, "status": "budget", "error": None, "usd": _usd(arm, pinned.log)}
        located = report.findings + report.dropped
        seed = fx["seed"]
        # Registered subset: the seed's hits on a seeded diff, every anchored finding on a clean one.
        chosen = hits(report.findings, seed) if seed else list(report.findings)
        verifier = CautiousVerifier(pinned, model)
        checked = []
        for f in chosen:
            file = diff.file(f.file)
            assert file is not None
            if budget.exhausted():
                checked.append({**_brief(f), "verdict": "not run", "label": "budget", "reason": ""})
                continue
            v = verifier.check(f, file)
            label = "budget" if v.reason == "budget guard" else v.label
            checked.append({**_brief(f), "verdict": v.state, "label": label, "reason": v.reason})
        return {
            "status": report.status, "findings": [_brief(f) for f in located],
            "hits": [_brief(f) for f in hits(located, seed)], "verified": checked,
            "residual_risks": report.residual_risks, "untested_paths": report.untested_paths,
            "log": pinned.log, "usd": _usd(arm, pinned.log), "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — counted by the stop rule, never raised
        return {**empty, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:300],
                "usd": _usd(arm, pinned.log)}
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def run(folder: Path, out: Path, workers: int) -> None:
    from chimera.providers.gateway import LLMGateway

    gateway = LLMGateway()
    fixtures = _fixtures(folder)
    order = list(range(len(fixtures)))
    random.Random(ORDER_SEED).shuffle(order)
    budget = _Budget(GUARD_USD)
    lock = threading.Lock()
    failed = {"D": 0, "G": 0}
    turns = {"D": 0, "G": 0}
    stop = {"why": ""}
    rows: dict[int, dict[str, Any]] = {}

    def item(index: int) -> dict[str, Any] | None:
        with lock:
            if stop["why"]:
                return None
        if budget.exhausted():
            with lock:
                stop["why"] = stop["why"] or f"budget guard at US$ {budget.usd:.3f}"
            return None
        fx = fixtures[index]
        row: dict[str, Any] = {"commit": fx["commit"], "kind": fx["kind"], "seed": fx["seed"],
                               "runs": {"D": [], "G": []}}
        with tempfile.TemporaryDirectory(prefix="s15-") as tmp:
            for arm in ("D", "G", "D"):  # registered order: D1 G1 D2
                got = _turn(gateway, fx, arm, Path(tmp) / f"{arm}{len(row['runs'][arm])}", budget)
                row["runs"][arm].append(got)
                if got["status"] == "budget":
                    continue
                with lock:
                    turns[arm] += 1
                    failed[arm] += int(got["status"] in ("error", "incomplete"))
                    if turns[arm] >= 10 and failed[arm] / turns[arm] > 0.10:
                        stop["why"] = f"stop rule: arm {arm} failed {failed[arm]}/{turns[arm]}"
        return row

    def mark(run: dict[str, Any]) -> str:
        if run["status"] == "budget":
            return "$"
        return "H" if run["hits"] else ("E" if run["status"] in ("error", "incomplete") else ".")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(item, i): i for i in order}
        for n, future in enumerate(as_completed(futures), 1):
            row = future.result()
            if row is None:
                continue
            rows[futures[future]] = row
            marks = " ".join(f"{a}={''.join(mark(r) for r in row['runs'][a])}" for a in ("D", "G"))
            print(f"  [{n:>2}/{len(order)}] {row['kind']:6} {row['commit']} {marks}  "
                  f"US$ {budget.usd:.3f}", flush=True)
    payload = {
        "arms": ARMS, "replicas": REPLICAS, "provider": PROVIDER, "price_per_m": PRICE, "tol": TOL,
        "order": [fixtures[i]["commit"] for i in order], "stopped": stop["why"],
        "usd": round(budget.usd, 4), "finder_prompt_sha": _sha(FINDER_SYSTEM),
        "verifier_prompt_sha": _sha(VERIFIER_SYSTEM),
        "fixtures_sha": _sha("".join(json.dumps(f, sort_keys=True) for f in fixtures)),
        "rows": [rows[i] for i in sorted(rows)],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                   newline="\n")
    print(f"\nwrote {out}  US$ {budget.usd:.4f}  stopped: {stop['why'] or 'no'}")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# --- the report --------------------------------------------------------------------------------


def _wilson(k: int, n: int) -> str:
    from chimera.eval.anytime import wilson_bounds

    lo, hi = wilson_bounds(k, n)
    return f"{k}/{n} = {k / n:.1%} [{lo:.1%}, {hi:.1%}]" if n else "0/0"


def _mcnemar(b: int, c: int) -> float:
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2**n)


def _hit_at(run: dict[str, Any], seed: dict[str, Any], tol: int) -> bool:
    return any(f["file"] == seed["path"] and abs(f["line"] - seed["line"]) <= tol
               for f in run["findings"])


#: A turn that did not produce a review: it leaves every denominator (PROTOCOL §2).
HALTS = ("error", "incomplete", "budget")
#: A verification that did not run is not a judgement, kept or dropped.
UNJUDGED = ("budget", "call failed")


def _cell(
    rows: list[dict[str, Any]], arm: str, rep: int
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(row, run) for every row where this arm/replica ran and did not halt."""
    out = []
    for row in rows:
        runs = row["runs"][arm]
        if len(runs) > rep and runs[rep]["status"] not in HALTS:
            out.append((row, runs[rep]))
    return out


def report(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"]
    print(f"arms {data['arms']} via {data['provider']} · US$ {data['usd']} · stopped: "
          f"{data['stopped'] or 'no'} · rows {len(rows)}")
    cells = {"D1": ("D", 0), "D2": ("D", 1), "G": ("G", 0)}
    pooled_kept = pooled_hits = 0
    for name, (arm, rep) in cells.items():
        got = _cell(rows, arm, rep)
        seeded = [(r, x) for r, x in got if r["seed"]]
        clean = [(r, x) for r, x in got if not r["seed"]]
        halted = sum(1 for r in rows if len(r["runs"][arm]) > rep
                     and r["runs"][arm][rep]["status"] in HALTS)
        found = sum(1 for _, x in seeded if x["hits"])
        everything = [v for _, x in seeded for v in x["verified"]]
        verified = [v for v in everything if v["label"] not in UNJUDGED]
        kept = sum(1 for v in verified if v["verdict"] != "dropped")
        e2e = sum(1 for _, x in seeded if any(v["verdict"] != "dropped" for v in x["verified"]))
        if name in ("D1", "G"):
            pooled_kept += kept
            pooled_hits += len(verified)
        unjudged = len(everything) - len(verified)
        print(f"\n[{name}] {ARMS[arm]}  halted {halted}  unjudged checks {unjudged}")
        print(f"  finder recall (±{data['tol']} lines)   {_wilson(found, len(seeded))}")
        for tol in (0, 10):
            k = sum(1 for r, x in seeded if _hit_at(x, r["seed"], tol))
            print(f"    at ±{tol:<2} lines                 {k}/{len(seeded)}")
        print(f"  verifier keeps, of hit findings    {_wilson(kept, len(verified))}")
        print(f"  end-to-end recall (a hit kept)     {_wilson(e2e, len(seeded))}")
        anchored = [f for _, x in clean for f in x["findings"] if f["anchor"] == "kept"]
        judged = [v for _, x in clean for v in x["verified"] if v["label"] not in UNJUDGED]
        dropped = [v for v in judged if v["verdict"] == "dropped"]
        after = [v for _, x in clean for v in x["verified"] if v["verdict"] != "dropped"]
        with_any = sum(1 for _, x in clean if any(f["anchor"] == "kept" for f in x["findings"]))
        with_any_after = sum(1 for _, x in clean if any(v["verdict"] != "dropped" for v in x["verified"]))
        print(f"  clean diffs: findings {len(anchored)} → {len(after)} after the verifier, over "
              f"{len(clean)} diffs; diffs with any {with_any} → {with_any_after}")
        for p in ("P0", "P1"):
            before_p = sum(1 for f in anchored if f["priority"] == p)
            after_p = sum(1 for v in after if v["priority"] == p)
            print(f"    {p}: {before_p} → {after_p}")
        if clean:
            print(f"  verifier drops, of clean findings  {_wilson(len(dropped), len(judged))}")
        other = sum(sum(f["anchor"] == "kept" for f in x["findings"]) - len(x["hits"])
                    for _, x in seeded)
        print(f"  seeded diffs: {other} other anchored findings (truth unknown), "
              f"{other / max(1, len(seeded)):.1f} per diff")
        usd = sum(x["usd"] for r in rows for x in r["runs"][arm][rep:rep + 1])
        calls = sum(len(x["log"]) for r in rows for x in r["runs"][arm][rep:rep + 1])
        secs = [e["seconds"] for r in rows for x in r["runs"][arm][rep:rep + 1] for e in x["log"]
                if e.get("role") == "finder" and "seconds" in e]
        med = sorted(secs)[len(secs) // 2] if secs else 0
        print(f"  cost US$ {usd:.4f} · calls {calls} · finder call median {med}s")
    print(f"\nPOOLED D1+G verifier keeps {_wilson(pooled_kept, pooled_hits)} of hit findings")
    b = c = n = 0
    for row in rows:
        if not row["seed"] or not row["runs"]["D"] or not row["runs"]["G"]:
            continue
        d1, g = row["runs"]["D"][0], row["runs"]["G"][0]
        if {d1["status"], g["status"]} & set(HALTS):
            continue
        n += 1
        b += int(bool(d1["hits"]) and not g["hits"])
        c += int(bool(g["hits"]) and not d1["hits"])
    print(f"PAIRED D1 vs G on {n} seeded items: only D {b}, only G {c}, exact McNemar p = {_mcnemar(b, c):.3g}")
    dis = m = 0
    for row in rows:
        runs = row["runs"]["D"]
        if row["seed"] and len(runs) == 2 and not {runs[0]["status"], runs[1]["status"]} & set(HALTS):
            m += 1
            dis += int(bool(runs[0]["hits"]) != bool(runs[1]["hits"]))
    print(f"FLOOR D replica disagreement on a hit: {dis}/{m}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", type=Path)
    ap.add_argument("--check", type=Path)
    ap.add_argument("--run", type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--out", type=Path, default=HERE / "results" / "run.json")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    if args.build:
        build(args.build)
    elif args.check:
        check(args.check)
    elif args.run:
        run(args.run, args.out, args.workers)
    elif args.report:
        report(args.report)
    else:
        ap.error("pass --build, --check, --run or --report")


if __name__ == "__main__":
    main()
