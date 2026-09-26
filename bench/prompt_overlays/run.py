"""H4 and H5 on SWE-bench Verified (django). See PREREGISTRATION.md, committed before any paid call.

    python run.py slice <gold_report.json>            # freeze the slice from the gold validation
    python run.py pilot                               # arm A on the first 10 slice items
    python run.py main --c-items M [--workers W]      # A,B on every item, C on the first M; resumable
    python run.py predictions <pilot|main> <dir>      # harness input, one file per arm
    python run.py report                              # the registered analysis

Solves run as subprocesses (`solve_one.py`) under a 1800 s wall clock. Every solve appends one JSON
line, flushed, so a block that the tool's clock kills loses only the solves in flight; a relaunch
skips every (item, arm) already on file.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# In WSL the code runs from a /tmp copy that does not survive the VM, so results go straight to the
# Windows worktree through H45_RESULTS.
RESULTS = Path(os.environ.get("H45_RESULTS") or (HERE / "results"))
POOL = RESULTS / "pool_django.jsonl"
SLICE = RESULTS / "slice.jsonl"
PILOT_ITEMS = RESULTS / "pilot_items.jsonl"
PILOT = RESULTS / "pilot_solves.jsonl"
MAIN = RESULTS / "main_solves.jsonl"
GRADES = RESULTS / "grades"
STRATA = ("<15 min fix", "15 min - 1 hour")

PILOT_N = 10
TIMEOUT_S = 1800
#: DeepInfra's published price for this endpoint (OpenRouter endpoints API, read 2026-09-25), USD/token.
PRICE = {"prompt": 0.06e-6, "completion": 0.18e-6, "cache_read": 0.015e-6}
#: The owner's cap is US$ 25.00 for H4 and H5 together, pilot included. The driver stops submitting at
#: this computed spend, leaving room for the solves in flight and for any gap between the token price
#: and the bill.
STOP_AT_USD = 22.0
_LOCK = threading.Lock()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def solve_cost(row: dict[str, Any]) -> float:
    usd = 0.0
    for c in row.get("calls") or []:
        prompt, done, cached = c.get("prompt") or 0, c.get("completion") or 0, c.get("cache_read") or 0
        usd += (prompt - cached) * PRICE["prompt"] + cached * PRICE["cache_read"] + done * PRICE["completion"]
    return usd


def spent() -> float:
    """Everything paid for, retried halts included."""
    return sum(solve_cost(r) + solve_cost({"calls": r.get("retry_calls") or []})
               for path in (RESULTS / "pilot0_solves.jsonl", PILOT, MAIN) for r in load_jsonl(path))


# ---------------------------------------------------------------- slice
def build_slice(gold_report: Path, head: int = 0) -> None:
    """The slice from a gold report. With ``head`` (Amendment 1), only the first ``head`` candidates
    in the registered order are read, and the pilot's items are written instead of the slice."""
    report = json.loads(gold_report.read_text(encoding="utf-8"))
    resolved = set(report.get("resolved_ids", []))
    pool = load_jsonl(POOL)
    cand = [r for r in pool if r["difficulty"] in STRATA]
    if head:
        cand = cand[:head]
    kept = [r for r in cand if r["instance_id"] in resolved]
    dropped = [r["instance_id"] for r in cand if r["instance_id"] not in resolved]
    out, rows = (PILOT_ITEMS, kept[:PILOT_N]) if head else (SLICE, kept)
    if head and len(rows) < PILOT_N:
        raise SystemExit(f"only {len(rows)} of the first {head} candidates resolved; widen the head run")
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"candidates {len(cand)}  gold-resolved {len(kept)}  dropped {len(dropped)}: {dropped}")
    print(f"wrote {out} ({len(rows)} rows)")
    if not head and PILOT_ITEMS.exists():
        pilot_ids = [r["instance_id"] for r in load_jsonl(PILOT_ITEMS)]
        same = pilot_ids == [r["instance_id"] for r in kept[:PILOT_N]]
        print(f"pilot items are the slice's first {PILOT_N}: {same}")


# ---------------------------------------------------------------- solving
#: Amendment 2, the network wall. Every process of a solve (the agent and every command it runs)
#: sends HTTP(S) through a proxy that does not exist, except to the model endpoint. The first pilot
#: showed why: one solve in ten curl'd django's stable/4.1.x and main branches from GitHub — the
#: fixed file — and searched GitHub's commits for the fix.
DEAD_PROXY = "http://127.0.0.1:9"
MODEL_HOSTS = "openrouter.ai,localhost,127.0.0.1"
_PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def _child_env(home: Path) -> dict[str, str]:
    """The solve's environment. The venv is left off PATH and HOME is per solve, so a `pip install`
    the agent runs cannot change the interpreter the harness runs on or leak into another solve.
    TMPDIR is per solve too (Amendment 2); only an explicit `/tmp/...` path is still shared."""
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "CHIMERA_TEMPERATURE",
                                                             "CHIMERA_FALLBACK_MODELS", "CHIMERA_PROVIDER_ORDER")}
    venv_bin = str(Path(sys.executable).parent)
    env["PATH"] = ":".join(p for p in env.get("PATH", "").split(":") if p and p != venv_bin)
    env["HOME"] = str(home)
    tmp = home.parent / "tmp"
    tmp.mkdir(exist_ok=True)
    env["TMPDIR"] = str(tmp)
    env["PYTHONUNBUFFERED"] = "1"
    for var in _PROXY_VARS:
        env[var] = DEAD_PROXY
    env["no_proxy"] = env["NO_PROXY"] = MODEL_HOSTS
    return env


def probe_wall() -> None:
    """PROTOCOL §1, the probe that tries: from a solve's environment, three ways out must fail and
    one model call must succeed. Costs one short model call."""
    work = Path(os.environ["H45_WORK"])
    scratch = Path(tempfile.mkdtemp(prefix="h45-probe-", dir=str(work)))
    home = scratch / "home"
    home.mkdir()
    env = _child_env(home)
    tries = {
        "curl_github": "curl -sS -m 15 -o /dev/null -w '%{http_code}' https://raw.githubusercontent.com/django/django/main/README.rst",
        "pip_download": f"python3 -m pip download --no-deps -q -d {scratch} asgiref",
        "git_ls_remote": "timeout 20 git ls-remote https://github.com/django/django HEAD",
        "python_urllib": "python3 -c \"import urllib.request;print(urllib.request.urlopen('https://pypi.org/simple/django/',timeout=15).status)\"",
    }
    out: dict[str, Any] = {}
    for name, cmd in tries.items():
        r = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=120, check=False)
        blocked = r.returncode != 0 or "200" not in (r.stdout or "")
        out[name] = {"blocked": blocked, "rc": r.returncode, "tail": ((r.stdout or "") + (r.stderr or ""))[-160:]}
        print(f"{name:<14} blocked={blocked} rc={r.returncode}")
    code = (
        f"import sys; sys.path.insert(0, {str(HERE)!r}); from solve_one import Recorder; r = Recorder(None);"
        "res = r.complete([{'role': 'user', 'content': 'Reply with the word OK.'}], "
        "model='openrouter/deepseek/deepseek-v4-flash-0731', temperature=0.2, max_tokens=400);"
        "print('MODEL', repr((res.content or '')[:40]), r.calls[0]['provider'])"
    )
    model = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                           timeout=180, check=False)
    ok = model.returncode == 0 and "MODEL" in model.stdout
    out["model_call"] = {"ok": ok, "tail": (model.stdout + model.stderr)[-300:]}
    print(f"model_call     ok={ok} {model.stdout.strip()[-120:]}")
    out["wall_holds"] = ok and all(v["blocked"] for k, v in out.items() if k != "model_call" and isinstance(v, dict))
    (RESULTS / "wall_probe.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("WALL HOLDS" if out["wall_holds"] else "WALL DOES NOT HOLD")
    subprocess.run(["rm", "-rf", str(scratch)], check=False)


def run_solve(inst: dict[str, Any], arm: str) -> dict[str, Any]:
    work = Path(os.environ["H45_WORK"])
    scratch = Path(tempfile.mkdtemp(prefix=f"h45-{arm}-", dir=str(work)))
    inst_path, out_path = scratch / "instance.json", scratch / "out.json"
    home = scratch / "home"
    home.mkdir()
    inst_path.write_text(json.dumps(inst), encoding="utf-8")
    ws = work / f"{arm}__{inst['instance_id']}"
    started = time.monotonic()
    proc = subprocess.Popen([sys.executable, str(HERE / "solve_one.py"), str(inst_path), arm, str(out_path)],
                            env=_child_env(home), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)
    timed_out = False
    try:
        _, err = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGKILL)
        _, err = proc.communicate()
    if out_path.exists() and not timed_out:
        row = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        row = {"instance_id": inst["instance_id"], "arm": arm, "calls": [],
               "halted": "timeout" if timed_out else f"no output (exit {proc.returncode}): {(err or b'')[-300:]!r}"}
        if ws.exists():
            diff = subprocess.run(["git", "-C", str(ws), "diff", inst["base_commit"]], capture_output=True,
                                  text=True, errors="replace", check=False)
            row["patch"] = diff.stdout if diff.returncode == 0 else ""
        row["seconds"] = round(time.monotonic() - started, 1)
    row["usd"] = round(solve_cost(row), 6)
    row["difficulty"] = inst.get("difficulty")
    for path in (ws, scratch):
        s = str(path)
        if s.startswith(str(work) + "/") and len(s) > len(str(work)) + 1:
            subprocess.run(["rm", "-rf", s], check=False)
    return row


def _append(path: Path, row: dict[str, Any]) -> None:
    with _LOCK, path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()


def solve_with_retry(inst: dict[str, Any], arm: str) -> dict[str, Any]:
    """A halt that is not the wall clock (a provider or harness error) is re-run once, fresh."""
    row = run_solve(inst, arm)
    if row.get("halted") and row["halted"] != "timeout":
        first = row
        row = run_solve(inst, arm)
        row["retried_after"] = first["halted"]
        row["retry_calls"] = first.get("calls") or []
    return row


def arm_order(index: int, with_c: bool) -> list[str]:
    arms = ["A", "B", "C"] if with_c else ["A", "B"]
    k = index % len(arms)
    return arms[k:] + arms[:k]


def ensure_reference() -> None:
    """Clone django once into the persistent reference every template is cloned from."""
    ref = Path(os.environ["H45_DJANGO_REF"])
    if (ref / ".git").exists():
        return
    ref.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "clone", "-q", "https://github.com/django/django", str(ref)], check=False,
                       capture_output=True, text=True, timeout=3000)
    if r.returncode != 0:
        raise SystemExit(f"reference clone failed: {r.stderr[-400:]}")


def run_items(items: list[tuple[int, dict[str, Any], list[str]]], out: Path, workers: int) -> None:
    from arms import assert_frozen
    from solve_one import build_template, template_path

    assert_frozen()
    ensure_reference()
    done = {(r["instance_id"], r["arm"]) for r in load_jsonl(out)}
    state: dict[str, Any] = {"stopped": "", "usd": spent()}
    counts: dict[str, list[int]] = {}
    print(f"spent so far US${state['usd']:.3f}; {len(done)} solves on file; workers {workers}", flush=True)

    def work(idx: int, inst: dict[str, Any], arms: list[str]) -> str:
        try:
            return _work(idx, inst, arms)
        finally:
            tpl = template_path(inst)
            if str(tpl).startswith(os.environ["H45_WORK"] + "/templates/") and tpl.name == inst["instance_id"]:
                subprocess.run(["rm", "-rf", str(tpl)], check=False)

    def _work(idx: int, inst: dict[str, Any], arms: list[str]) -> str:
        marks = []
        if any((inst["instance_id"], arm) not in done for arm in arms) and not state["stopped"]:
            try:
                build_template(inst)
            except Exception as exc:  # noqa: BLE001 — solve_one retries it and records the halt
                print(f"  template for {inst['instance_id']} failed here: {exc}", flush=True)
        for arm in arms:
            if (inst["instance_id"], arm) in done:
                marks.append(f"{arm}=")
                continue
            if state["stopped"]:
                marks.append(f"{arm}-")
                continue
            if state["usd"] >= STOP_AT_USD:
                state["stopped"] = f"budget: computed spend reached US${STOP_AT_USD}"
                marks.append(f"{arm}$")
                continue
            row = solve_with_retry(inst, arm)
            row["item_index"] = idx
            _append(out, row)
            with _LOCK:
                state["usd"] += row["usd"] + solve_cost({"calls": row.get("retry_calls") or []})
                tally = counts.setdefault(arm, [0, 0])
                tally[0] += 1
                tally[1] += int(bool(row.get("halted")) and row["halted"] != "timeout")
                if tally[0] >= 20 and tally[1] / tally[0] > 0.10 and not state["stopped"]:
                    state["stopped"] = f"stop rule: arm {arm} halted on {tally[1]}/{tally[0]} solves"
            flag = "H" if row.get("halted") else ("E" if not (row.get("patch") or "").strip() else "p")
            marks.append(f"{arm}{flag}{row.get('steps', '-')}")
        return " ".join(marks)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, idx, inst, arms): (idx, inst) for idx, inst, arms in items}
        for n, fut in enumerate(as_completed(futures), 1):
            idx, inst = futures[fut]
            print(f"  [{n:>3}/{len(items)}] #{idx:<3} {inst['instance_id']:<24} {fut.result():<22} "
                  f"US${state['usd']:.3f}", flush=True)
    if state["stopped"]:
        print(f"STOPPED: {state['stopped']}", flush=True)


def pilot(workers: int) -> None:
    items = [(i, inst, ["A"]) for i, inst in enumerate(load_jsonl(PILOT_ITEMS)[:PILOT_N])]
    run_items(items, PILOT, workers)


def main_run(c_items: int, workers: int) -> None:
    items = [(i, inst, arm_order(i, i < c_items)) for i, inst in enumerate(load_jsonl(SLICE))]
    run_items(items, MAIN, workers)


def dry() -> None:
    """US$ 0: build one item's template and workspace, and compose each arm's system message without
    calling a model. Shows the wall assertion, the tool list and that the arms differ where registered."""
    import hashlib

    from arms import ARMS, assert_frozen
    from solve_one import CODING_TOOLS, INSTRUCTION, prepare_workspace

    from chimera.core.agent import Agent, AgentConfig
    from chimera.governance.allowlist import restrict_registry
    from chimera.providers import LLMGateway
    from chimera.tools.builtin import default_registry

    assert_frozen()
    ensure_reference()
    inst = (load_jsonl(SLICE) or load_jsonl(POOL))[0]
    print("fallback models:", LLMGateway().settings.fallback_models or "none")
    for name, arm in ARMS.items():
        ws = prepare_workspace(inst, name)
        tools = restrict_registry(default_registry(ws, host_exec_confirm=None), allow=CODING_TOOLS)
        agent = Agent(object(), tools, AgentConfig(system_prompt=arm.system, temperature=arm.temperature,  # type: ignore[arg-type]
                                                   max_steps=30, project_root=ws, turn_context=True, prefix_nonce=""))
        system = agent.compose_system_prompt(INSTRUCTION.format(problem=inst["problem_statement"]))
        extra = system[len(arm.system):]
        print(f"arm {name}: T={arm.temperature} top_p={arm.top_p} sha {hashlib.sha256(system.encode()).hexdigest()[:12]} "
              f"starts_with_arm={system.startswith(arm.system)} tail_after_arm={extra[:120]!r}")
        print(f"   tools: {sorted(t.name for t in tools.tools())}")
        subprocess.run(["rm", "-rf", str(ws)], check=False)


# ---------------------------------------------------------------- grading input
def predictions(phase: str, outdir: Path) -> None:
    rows = load_jsonl(PILOT if phase == "pilot" else MAIN)
    outdir.mkdir(parents=True, exist_ok=True)
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("halted") and r["halted"] != "timeout":
            continue  # a halt is not graded (PROTOCOL §2); a timeout is graded for the sensitivity reading
        by_arm.setdefault(r["arm"], []).append(
            {"instance_id": r["instance_id"], "model_name_or_path": f"h45-{phase}-{r['arm']}",
             "model_patch": r.get("patch") or ""})
    for arm, preds in by_arm.items():
        path = outdir / f"predictions_{phase}_{arm}.jsonl"
        path.write_text("".join(json.dumps(p) + "\n" for p in preds), encoding="utf-8", newline="\n")
        print(f"{path}: {len(preds)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("slice")
    s.add_argument("gold", type=Path)
    s.add_argument("--head", type=int, default=0)
    p = sub.add_parser("pilot")
    p.add_argument("--workers", type=int, default=10)
    m = sub.add_parser("main")
    m.add_argument("--c-items", type=int, required=True)
    m.add_argument("--workers", type=int, default=12)
    pr = sub.add_parser("predictions")
    pr.add_argument("phase", choices=("pilot", "main"))
    pr.add_argument("outdir", type=Path)
    sub.add_parser("report")
    sub.add_parser("pilotreport")
    sub.add_parser("dry")
    sub.add_parser("probe")
    args = ap.parse_args()
    if args.cmd == "probe":
        probe_wall()
        sys.exit(0)
    if args.cmd == "pilotreport":
        from report import arm_table, grades

        print(json.dumps(arm_table(load_jsonl(PILOT), grades("pilot", "A")), indent=2))
    elif args.cmd == "dry":
        dry()
    elif args.cmd == "slice":
        build_slice(args.gold, args.head)
    elif args.cmd == "pilot":
        pilot(args.workers)
    elif args.cmd == "main":
        main_run(args.c_items, args.workers)
    elif args.cmd == "predictions":
        predictions(args.phase, args.outdir)
    elif args.cmd == "report":
        from report import report

        report()
