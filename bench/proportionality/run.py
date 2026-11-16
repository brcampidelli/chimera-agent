"""Run `bench/proportionality` (PREREGISTRATION.md §4). Paid.

One run = one `Agent` in-process, assembled as `chimera agent` assembles it, in a fresh workspace and a
fresh `CHIMERA_HOME`. The only difference between the arms is `AgentConfig.proportionality`.

Resumable: one line per finished run in ``<out>/runs.jsonl``. Stops before starting a pair once the
tally of ``AgentResult.usd`` reaches ``--stop-usd`` (the pre-registered 4.50). The key is read by the
product's settings from the ``.env`` of the working directory and is never printed.

    cd <repo with the .env> && python <worktree>/bench/proportionality/run.py --out <dir> --limit 2
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
MODEL = "openrouter/deepseek/deepseek-chat-v3.1"
SEED = 20261116
MAX_STEPS = 25
#: The five switches whose product default changed on 2026-11-10 (`bench/_legacy_defaults.py`). This
#: bench measures the block on the product AS IT SHIPS, so it does NOT pin them back to the legacy
#: values: the effective value of each is recorded in `switches.json` beside the results instead.
SWITCHES = (
    "CHIMERA_DEFER_TOOLS",
    "CHIMERA_SKILL_CARDS",
    "CHIMERA_CODE_CLOSE_LOOP",
    "CHIMERA_PULL_REQUESTS",
    "CHIMERA_CHAT_INBOUND_MEDIA",
)


def _record_switches(out: Path) -> None:
    from chimera.config import Settings, get_settings

    settings = get_settings()
    found = {}
    for name, field in Settings.model_fields.items():
        if field.validation_alias in SWITCHES:
            found[str(field.validation_alias)] = getattr(settings, name)
    (out / "switches.json").write_text(json.dumps(found, indent=2, sort_keys=True) + chr(10), encoding="utf-8")


def _pairs(replicas: int) -> list[dict]:
    import corpus

    rng = random.Random(SEED)
    items = (
        [{"family": "read", "id": it["id"], "item": it} for it in corpus.READ]
        + [{"family": "edit", "id": it["name"], "item": it} for it in corpus.EDIT]
        + [{"family": "control", "id": it["id"], "item": it} for it in corpus.CONTROL]
    )
    pairs = [{**it, "rep": r, "first": rng.choice(["off", "on"])} for it in items for r in range(replicas)]
    rng.shuffle(pairs)
    return pairs


def _materialise(pair: dict, ws: Path) -> None:
    import corpus

    fam, item = pair["family"], pair["item"]
    if fam == "read":
        shutil.copytree(corpus.FIXTURE / "src", ws / "src")
    elif fam == "edit":
        for rel, body in item["files"].items():
            path = ws / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")


def _prompt(pair: dict) -> str:
    fam, item = pair["family"], pair["item"]
    return item["q"] if fam == "read" else item["prompt"]


def _snapshot(ws: Path) -> dict[str, str]:
    return {p.relative_to(ws).as_posix(): p.read_text(encoding="utf-8", errors="replace")
            for p in ws.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def _verdict(pair: dict, ws: Path, answer: str, before: dict[str, str]) -> dict:
    import corpus

    fam, item = pair["family"], pair["item"]
    after = _snapshot(ws)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if fam == "read":
        return {"ok": corpus.read_answer_ok(item, answer), "files_changed": len(changed)}
    if fam == "edit":
        # The authoritative verdict: OUR pytest, on a test restored from pristine.
        (ws / item["test"]).write_text(item["files"][item["test"]], encoding="utf-8")
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", item["test"]], cwd=str(ws),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        return {"ok": proc.returncode == 0, "files_changed": len(changed)}
    if item["kind"] == "ask":
        return {"ok": ("?" in answer) and not changed, "files_changed": len(changed), "asked": "?" in answer}
    built = (ws / "hello.py").exists() and "ola mundo" in (ws / "hello.py").read_text(encoding="utf-8", errors="replace")
    return {"ok": built, "files_changed": len(changed)}


def _run_one(pair: dict, arm: str, out: Path) -> dict:
    from chimera.cli.right_hand import build_right_hand
    from chimera.config import get_settings
    from chimera.core import Agent, AgentConfig
    from chimera.core.agent import attended
    from chimera.providers import LLMGateway

    work = Path(tempfile.mkdtemp(prefix=f"prop-{arm}-{pair['id']}-"))
    ws = work / "ws"
    ws.mkdir()
    _materialise(pair, ws)
    before = _snapshot(ws)
    settings = get_settings()
    hand = build_right_hand(ws, settings=settings, surface="agent", allow=None, deny=None, mode="")
    hand.begin_turn(_prompt(pair))
    cfg = attended(AgentConfig(model=MODEL, max_steps=MAX_STEPS, project_root=ws, turn_context=True,
                               proportionality=(arm == "on")))
    started = time.time()
    error = ""
    try:
        res = Agent(LLMGateway(), hand.registry, cfg).run(_prompt(pair))
        row = {"steps": res.steps, "completion_tokens": res.completion_tokens, "prompt_tokens": res.prompt_tokens,
               "tool_calls": res.tool_calls_made, "tools": res.tool_names, "usd": res.usd or 0.0,
               "stopped": str(res.stopped_reason), "answer": str(res.answer)[:1200]}
    except Exception as exc:  # a run that errors is recorded, never retried: no re-rolls
        error = f"{type(exc).__name__}: {exc}"[:300]
        row = {"steps": 0, "completion_tokens": 0, "prompt_tokens": 0, "tool_calls": 0, "tools": [],
               "usd": 0.0, "stopped": "error", "answer": ""}
    verdict = _verdict(pair, ws, row["answer"], before)
    shutil.rmtree(work, ignore_errors=True)
    return {"family": pair["family"], "id": pair["id"], "rep": pair["rep"], "arm": arm, **row, **verdict,
            "error": error, "seconds": round(time.time() - started, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--replicas", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="only the first N pairs of the shuffled order")
    ap.add_argument("--stop-usd", type=float, default=4.5)
    args = ap.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["CHIMERA_HOME"] = str(out / "home")  # never the owner's ~/.chimera
    sys.path.insert(0, str(HERE.parents[1]))

    _record_switches(out)
    log = out / "runs.jsonl"
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.exists() else []
    done = {(r["family"], r["id"], r["rep"], r["arm"]) for r in rows}
    spent = sum(r["usd"] for r in rows)
    pairs = _pairs(args.replicas)
    if args.limit:
        pairs = pairs[: args.limit]
    for pair in pairs:
        if spent >= args.stop_usd:
            print(f"admission stop: US$ {spent:.4f} spent")
            break
        order = [pair["first"], "on" if pair["first"] == "off" else "off"]
        for arm in order:
            if (pair["family"], pair["id"], pair["rep"], arm) in done:
                continue
            row = _run_one(pair, arm, out)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            spent += row["usd"]
            print(f"{pair['family']:7} {pair['id']:30} r{pair['rep']} {arm:3} ok={row['ok']!s:5} steps={row['steps']:<3} "
                  f"out={row['completion_tokens']:<5} US$ {spent:.4f} {row['error']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
