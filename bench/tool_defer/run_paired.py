"""Paired A/B of `CHIMERA_DEFER_TOOLS`, per `PREREGISTRATION.md`.

    python bench/tool_defer/run_paired.py --repeats 3

Arm A declares every tool; arm B declares the core and puts the rest behind
`tool_list`/`tool_describe`/`tool_call`. Same tasks, same model, same fixtures, within-task
comparison.

**The primary metric is whether the task completed**, judged by the task's own shell verifier. Token
counts are secondary here and deliberately so: the token half is settled by arithmetic in the
pre-registration and needs no spend to answer. What arithmetic cannot answer is whether an agent
that has to look a tool up still finds it.

`reached` — whether the turn actually called one of the three proxies — is reported on its own and
subtracted from the primary rate. `run_shell` is in the core in both arms, so a B run can shell out
to Python and pass without ever touching the proxy; counting that as a success would report the
bench measuring something it did not exercise.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from tasks import TASKS  # noqa: E402

PROXIES = {"tool_list", "tool_describe", "tool_call"}


def _dotenv() -> dict[str, str]:
    """The repo `.env`, so a run from a temp cwd still finds the provider key."""
    arquivo = REPO_ROOT / ".env"
    saida: dict[str, str] = {}
    if not arquivo.exists():
        return saida
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        saida[chave.strip()] = valor.strip().strip('"').strip("'")
    return saida


def _materialise(task: dict, root: Path) -> None:
    for rel, corpo in task["files"].items():
        caminho = root / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(corpo, encoding="utf-8")


def _solve(task: dict, ws: Path, home: Path, timeout: int, defer: bool) -> tuple[int, str]:
    """One run. `home` isolates this run's `runs.jsonl` so "the last line" means this run."""
    argv = [
        sys.executable, "-m", "chimera.cli.main", "solve", task["prompt"],
        "--workspace", str(ws),
    ]
    ambiente = {**os.environ, **_dotenv(), "CHIMERA_HOME": str(home)}
    # "0", not "": an inherited value from the developer's shell would make both arms the same arm
    # and the bench would report a null result it never measured — but the first version wrote "" and
    # every one of arm A's five pilot runs died in under a second, because an empty boolean raised
    # out of `Settings`. That defect is fixed now (`tests/test_an_empty_variable_stopped_the_app.py`)
    # and "0" is still the right value here: it says off, rather than relying on empty meaning off.
    ambiente["CHIMERA_DEFER_TOOLS"] = "1" if defer else "0"
    try:
        proc = subprocess.run(
            argv, cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False, env=ambiente,
        )
        return proc.returncode, ((proc.stdout or "")[-400:] + (proc.stderr or "")[-400:])
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def _verdict(task: dict, ws: Path) -> bool:
    """The task's own shell verifier, in the workspace. No model judges anything."""
    proc = subprocess.run(
        task["verify"], cwd=str(ws), shell=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return proc.returncode == 0


def _receipt(home: Path) -> dict:
    runs = home / "runs.jsonl"
    if not runs.exists():
        return {}
    linhas = [ln for ln in runs.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return json.loads(linhas[-1]) if linhas else {}


def _measure(recibo: dict) -> tuple[int, list[str], int]:
    """(steps, tool names, prompt tokens) summed over the run's attempts."""
    tentativas = recibo.get("attempts") or []
    nomes = [n for a in tentativas for n in (a.get("tool_names") or [])]
    tokens = sum(int(a.get("prompt_tokens") or 0) for a in tentativas)
    return len(nomes), nomes, tokens


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--offline-only", action="store_true", help="skip the network tasks")
    ap.add_argument("--out", type=Path, default=HERE / "results.jsonl")
    args = ap.parse_args()

    tarefas = [t for t in TASKS if not (args.offline_only and t["network"])]
    total = len(tarefas) * args.repeats * 2
    print(f"{len(tarefas)} tarefas x {args.repeats} repeticoes x 2 bracos = {total} execucoes\n")

    with args.out.open("w", encoding="utf-8") as saida:
        for i in range(args.repeats):
            for task in tarefas:
                for arm, defer in (("A", False), ("B", True)):
                    with tempfile.TemporaryDirectory() as tmp:
                        raiz = Path(tmp)
                        ws, home = raiz / "ws", raiz / "home"
                        ws.mkdir()
                        _materialise(task, ws)
                        t0 = time.time()
                        codigo, cauda = _solve(task, ws, home, args.timeout, defer)
                        passou = _verdict(task, ws)
                        passos, nomes, tokens = _measure(_receipt(home))
                        linha = {
                            "repeat": i,
                            "task": task["name"],
                            "needs": task["needs"],
                            "network": task["network"],
                            "arm": arm,
                            "completed": passou,
                            "steps": passos,
                            "tools": nomes,
                            "reached": bool(PROXIES & set(nomes)),
                            "prompt_tokens": tokens,
                            "exit": codigo,
                            # A timeout kills the process before the receipt is written, so steps
                            # and tokens read as zero while the task may well have been done. Marked,
                            # because "0 steps" and "we stopped watching" are different facts and the
                            # pilot produced one run that completed with both.
                            "timed_out": codigo == 124,
                            "seconds": round(time.time() - t0, 1),
                            "tail": cauda if not passou else "",
                        }
                        saida.write(json.dumps(linha, ensure_ascii=False) + "\n")
                        saida.flush()
                        marca = "ok " if passou else "FAIL"
                        via = " via-proxy" if linha["reached"] else ""
                        print(
                            f"  [{i}] {task['name']:14s} {arm}  {marca}  "
                            f"{passos:2d} passos  {tokens:6,} tok{via}"
                        )

    print(f"\nescrito em {args.out}")


if __name__ == "__main__":
    main()
