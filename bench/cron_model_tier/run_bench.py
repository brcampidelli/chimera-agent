"""Cron model-tier bench (P1-03 acceptance). Design and adoption rule: PREREGISTRATION.md.

    python bench/cron_model_tier/run_bench.py --selftest   # no network, no cost
    python bench/cron_model_tier/run_bench.py --pilot      # 1 job x 2 arms x 1 seed, to price the run
    python bench/cron_model_tier/run_bench.py              # the registered run (resumable)
    python bench/cron_model_tier/run_bench.py --report     # RESULTS tables from the journal

The call is a single completion per cell: the scheduled-run system note chimera itself uses
(`chimera/scheduler/surface.py`, read from the file, not retyped), the job prompt, and the frozen
source inlined as if read from a file. No tool loop (a stated limit, PREREGISTRATION section 7).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
import jobs as J  # noqa: E402

JOURNAL = HERE / "results" / "journal.jsonl"
ARMS = {"current": "deepseek/deepseek-chat-v3.1", "cheap": "deepseek/deepseek-v4-flash-0731"}
SEEDS = (42, 43, 44)
TEMPERATURE = 0.7
MAX_TOKENS = 1500
CEILING_USD = 2.50  # the item's cap is 3.00; this stops the loop with margin


def _surface_constants() -> tuple[str, str]:
    """SCHEDULED_RUN_NOTE and NOTHING_NEW read from surface.py by AST: importing the package needs
    the whole dependency tree, and retyping the note would let the bench drift from what ships."""
    tree = ast.parse((REPO / "chimera" / "scheduler" / "surface.py").read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("SCHEDULED_RUN_NOTE", "NOTHING_NEW"):
                found[node.targets[0].id] = ast.literal_eval(node.value)
    return found["SCHEDULED_RUN_NOTE"], found["NOTHING_NEW"]


NOTE, NOTHING_NEW = _surface_constants()


def is_nothing_new(answer: str) -> bool:
    return answer.strip().strip("`").strip() == NOTHING_NEW  # same rule as surface.is_nothing_new


def system_prompt(job: J.Job) -> str:
    return NOTE.format(name=job.name, destination="the job's result log, which the owner reads later",
                       sentinel=NOTHING_NEW)


# ---------------------------------------------------------------------------------------- scoring

_NUM = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:[.,]\d+)?")


def _first_number(text: str) -> float | None:
    m = _NUM.search(text)
    if not m:
        return None
    tok = m.group(0)
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", tok):
        tok = tok.replace(",", "")
    else:
        tok = tok.replace(",", ".")
    return float(tok)


def _clean(text: str) -> str:
    return re.sub(r"[*`_]", "", text).strip()


def _parse_lines(answer: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in answer.splitlines():
        line = _clean(re.sub(r"^[\s>\-*•#]+", "", raw))
        label, sep, value = line.partition(":")
        if sep:
            out.setdefault(label.strip().lower(), value.strip())
    return out


def _check(kind: str, got: str, want: object) -> bool:
    got = _clean(got)
    if kind == "str":
        return got.lower() == str(want).lower()
    if kind == "set":
        names = {p.strip().lower() for p in re.split(r",|\band\b", got) if p.strip()}
        return names == {str(w).lower() for w in want}  # type: ignore[union-attr]
    n = _first_number(got)
    if n is None:
        return False
    tol = {"int": 0.0, "dec1": 0.0501, "money": 0.0101}[kind]
    return abs(n - float(want)) <= tol  # type: ignore[arg-type]


def score(job: J.Job, answer: str) -> dict:
    """Per-field correctness. ``ok`` is the registered criterion: EVERY field right (or, for the
    quiet job, the reply is exactly NOTHING_NEW)."""
    if job.nothing_new:
        return {"ok": is_nothing_new(answer), "fields": {"NOTHING_NEW": is_nothing_new(answer)}}
    if is_nothing_new(answer):
        return {"ok": False, "fields": {"NOTHING_NEW (wrongly)": False}}
    parsed = _parse_lines(answer)
    fields = {}
    for label, kind in job.template:
        got = parsed.get(label.lower())
        fields[label] = got is not None and _check(kind, got, job.expected[label])
    return {"ok": all(fields.values()), "fields": fields}


def perfect_answer(job: J.Job) -> str:
    if job.nothing_new:
        return NOTHING_NEW
    return "\n".join(
        f"{label}: {', '.join(sorted(job.expected[label])) if kind == 'set' else job.expected[label]}"
        for label, kind in job.template)


def selftest() -> int:
    """The gabarito must pass itself, and a corrupted copy must fail (a guard that never fired
    may be inert: it is tested against the broken state too)."""
    bad = 0
    for job in J.all_jobs():
        if not score(job, perfect_answer(job))["ok"]:
            print("REGUA QUEBRADA: gabarito reprova a si mesmo em", job.name)
            bad += 1
        if job.nothing_new:
            wrong = "all services look fine"
        else:
            first = job.template[0][0]
            wrong = perfect_answer(job).replace(f"{first}: ", f"{first}: 9", 1)  # prefix a digit
        if score(job, wrong)["ok"]:
            print("REGUA INERTE: resposta errada passou em", job.name)
            bad += 1
    j = J.all_jobs()[0]
    rate = str(j.expected["Activation rate %"]).replace(".", ",")
    md = "- **Total signups**: %d\n* Activation rate %%: %s%%" % (j.expected["Total signups"], rate)
    f = score(j, md)["fields"]
    if not (f["Total signups"] and f["Activation rate %"]):
        print("PARSER: markdown / decimal-comma not handled", f)
        bad += 1
    if _first_number("1,803 users") != 1803.0:
        print("PARSER: thousands separator")
        bad += 1
    print("selftest:", "FALHOU" if bad else "ok", f"({len(J.all_jobs())} jobs)")
    return 1 if bad else 0


# -------------------------------------------------------------------------------------- API calls


def _key() -> str:
    for p in (REPO / ".env", REPO.parent / ".env"):
        if p.is_file():
            m = re.search(r"^OPENROUTER_API_KEY=(.*)$", p.read_text(encoding="utf-8"), re.M)
            if m:
                return m.group(1).strip().strip("\"'")
    raise SystemExit("OPENROUTER_API_KEY not found")


def _prices() -> dict[str, tuple[float, float]]:
    req = urllib.request.Request("https://openrouter.ai/api/v1/models")
    data = json.load(urllib.request.urlopen(req, timeout=30))["data"]
    p = {m["id"]: m["pricing"] for m in data}
    return {a: (float(p[s]["prompt"]), float(p[s]["completion"])) for a, s in ARMS.items()}


def call(model: str, system: str, user: str, seed: int, key: str) -> dict:
    body = json.dumps({"model": model, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS, "seed": seed,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                       "usage": {"include": True}}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    t0 = time.time()
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=120))
            ch = r["choices"][0]
            return {"answer": ch["message"].get("content") or "", "finish": ch.get("finish_reason"),
                    "usage": r.get("usage", {}), "seconds": round(time.time() - t0, 2)}
        except Exception as exc:  # noqa: BLE001 - retried, then surfaced as a failed cell
            last = exc
            time.sleep(2 * (attempt + 1))
    return {"answer": "", "finish": "error", "error": str(last)[:200], "usage": {}, "seconds": 0}


def _read_journal() -> list[dict]:
    if not JOURNAL.is_file():
        return []
    return [json.loads(x) for x in JOURNAL.read_text(encoding="utf-8").splitlines() if x.strip()]


def run(only_pilot: bool) -> None:
    JOURNAL.parent.mkdir(exist_ok=True)
    key = _key()
    prices = _prices()
    done = {(r["job"], r["arm"], r["seed"]) for r in _read_journal()}
    spent = sum(r["usd"] for r in _read_journal())
    pre = hashlib.sha256((HERE / "PREREGISTRATION.md").read_bytes()).hexdigest()[:12]
    cells = [(j, a, s) for j in J.all_jobs() for s in SEEDS for a in ARMS]
    if only_pilot:
        cells = [(j, a, 42) for j in J.all_jobs()[:1] for a in ARMS]
    for job, arm, seed in cells:
        if (job.name, arm, seed) in done:
            continue
        if spent >= CEILING_USD:
            print(f"STOP: ${spent:.4f} reached the ${CEILING_USD} ceiling")
            return
        out = call(ARMS[arm], system_prompt(job), J.user_message(job), seed, key)
        u = out["usage"]
        pin, pout = prices[arm]
        usd = float(u.get("cost") or (u.get("prompt_tokens", 0) * pin + u.get("completion_tokens", 0) * pout))
        spent += usd
        sc = score(job, out["answer"]) if out["finish"] != "error" else {"ok": False, "fields": {}}
        row = {"job": job.name, "arm": arm, "seed": seed, "model": ARMS[arm], "ok": sc["ok"],
               "fields": sc["fields"], "answer": out["answer"], "finish": out["finish"],
               "prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0),
               "usd": usd, "seconds": out["seconds"], "prereg": pre, "error": out.get("error")}
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{job.name:22} {arm:8} s{seed} ok={sc['ok']!s:5} ${usd:.5f}  (total ${spent:.4f})")


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (c - m) / d


def report() -> None:
    rows = _read_journal()
    names = [j.name for j in J.all_jobs()]
    print("| job | current | cheap | cheap fails (fields) | adopt cheap? |\n|---|---|---|---|---|")
    adopted = 0
    for n in names:
        cell = {a: [r for r in rows if r["job"] == n and r["arm"] == a] for a in ARMS}
        cur = f"{sum(r['ok'] for r in cell['current'])}/{len(cell['current'])}"
        ch = f"{sum(r['ok'] for r in cell['cheap'])}/{len(cell['cheap'])}"
        fails = sorted({k for r in cell["cheap"] for k, v in r["fields"].items() if not v})
        ok = len(cell["cheap"]) == len(SEEDS) and all(r["ok"] for r in cell["cheap"])
        adopted += ok
        print(f"| {n} | {cur} | {ch} | {', '.join(fails) or '-'} | {'YES' if ok else 'no'} |")
    print(f"\nadopted: {adopted}/{len(names)}")
    for a in ARMS:
        rs = [r for r in rows if r["arm"] == a]
        usd = sum(r["usd"] for r in rs)
        k = sum(r["ok"] for r in rs)
        n = len(rs)
        print(f"{a}: {k}/{n} cells fully correct, ${usd:.4f}, Wilson lower {wilson_lower(k, n):.3f}" if n else a)
    print(f"total spend ${sum(r['usd'] for r in rows):.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.report:
        report()
    else:
        if selftest():
            raise SystemExit("selftest failed; not spending")
        run(a.pilot)
