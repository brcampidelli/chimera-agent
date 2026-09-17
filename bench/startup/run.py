"""What Chimera itself costs to start and to run a turn, outside the model. Registered in
`PREREGISTRATION.md`.

Deterministic apart from the clock, no model, US$ 0. Four numbers: cold start to bound and to a
health answer, where the start goes (importtime), harness overhead per turn against an instant
agent, and idle RSS/CPU.

    python bench/startup/run.py                  # prints; writes results/table.json (before/after copies kept by hand)
"""

from __future__ import annotations

import contextlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

PY = sys.executable
RUNS = 5
TURNS = 20


def _wait_for(path: Path, seconds: float) -> float | None:
    began = time.perf_counter()
    while time.perf_counter() - began < seconds:
        if path.is_file() and path.read_text(encoding="utf-8").strip():
            return time.perf_counter() - began
        time.sleep(0.005)
    return None


def _health(url: str, seconds: float) -> float | None:
    began = time.perf_counter()
    while time.perf_counter() - began < seconds:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1) as resp:  # noqa: S310
                if resp.status == 200:
                    return time.perf_counter() - began
        except Exception:  # noqa: BLE001 — not bound yet
            time.sleep(0.005)
    return None


def cold_start(home: Path, runs: int) -> list[dict[str, Any]]:
    """Spawn the app `runs` times; each row is (spawn→bound, bound→health, rss after health)."""
    out: list[dict[str, Any]] = []
    env = {**os.environ, "CHIMERA_HOME": str(home), "CHIMERA_APP_CRON": "false", "PYTHONUTF8": "1"}
    env.pop("CHIMERA_MEMORY_BACKEND", None)
    for i in range(runs):
        port_file = home / f"port.{i}"
        port_file.unlink(missing_ok=True)
        began = time.perf_counter()
        proc = subprocess.Popen(
            [PY, "-c", "from chimera.cli.main import app; app()",
             "app", "--no-open", "--port", "0", "--emit-port-file", str(port_file)],
            cwd=str(REPO), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            bound = _wait_for(port_file, 60)
            url = port_file.read_text(encoding="utf-8").strip() if bound is not None else ""
            healthy = _health(url, 30) if url else None
            # The venv's python.exe on Windows is a trampoline that spawns the real interpreter;
            # the server is the whole tree, and so is the kill below.
            rss = max(p.memory_info().rss for p in _tree(proc.pid))
            out.append({
                "spawn_to_bound_s": round(bound, 3) if bound is not None else None,
                "bound_to_health_s": round(healthy, 3) if healthy is not None else None,
                "spawn_to_health_s": (
                    round(bound + healthy, 3) if bound is not None and healthy is not None else None
                ),
                "rss_mb_after_boot": round(rss / 1e6, 1),
            })
            _ = began
        finally:
            _kill_tree(proc.pid)
            proc.wait(timeout=10)
    return out


def _tree(pid: int) -> list[Any]:
    import psutil

    root = psutil.Process(pid)
    return [root, *root.children(recursive=True)]


def _kill_tree(pid: int) -> None:
    import psutil

    for p in reversed(_tree(pid)):
        with contextlib.suppress(psutil.NoSuchProcess):
            p.kill()


def importtime_of_boot(home: Path, top: int = 15) -> list[tuple[int, str]]:
    """The `top` largest cumulative imports of the app's ACTUAL boot, in microseconds.

    Not `import chimera.cli.main` alone: the app imports its server, its routes and its agent
    lazily inside the command, and a profile of the module import would miss all of them.
    """
    port_file = home / "port.importtime"
    port_file.unlink(missing_ok=True)
    env = {**os.environ, "CHIMERA_HOME": str(home), "CHIMERA_APP_CRON": "false", "PYTHONUTF8": "1"}
    env.pop("CHIMERA_MEMORY_BACKEND", None)
    proc = subprocess.Popen(
        [PY, "-X", "importtime", "-c", "from chimera.cli.main import app; app()",
         "app", "--no-open", "--port", "0", "--emit-port-file", str(port_file)],
        cwd=str(REPO), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    # Read stderr on a thread: importtime writes a line per import and a full pipe would block
    # the boot this is timing.
    import threading

    lines: list[str] = []

    def _drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            lines.append(line)

    drain = threading.Thread(target=_drain, daemon=True)
    drain.start()
    _wait_for(port_file, 60)
    url = port_file.read_text(encoding="utf-8").strip() if port_file.is_file() else ""
    if url:
        _health(url, 30)
    _kill_tree(proc.pid)
    proc.wait(timeout=30)
    drain.join(timeout=10)
    rows: list[tuple[int, str]] = []
    for line in lines:
        if not line.startswith("import time:") or "cumulative" in line:
            continue
        parts = line.split("|")
        try:
            cumulative = int(parts[1].strip())
        except (IndexError, ValueError):
            continue
        rows.append((cumulative, parts[2].rstrip(chr(10))))
    # Top-level modules only — importtime indents a nested import two spaces per level under the
    # statement that pulled it in, and a top-level one carries exactly one leading space. Nested
    # entries are already inside their parent's cumulative total; counting them too (the first
    # draft stripped the indentation first and did) reports one cost several times.
    top_level = [(c, n.strip()) for c, n in rows if n.startswith(" ") and not n.startswith("  ")]
    total = sum(c for c, _ in top_level)
    top_level.sort(reverse=True)
    return [(total, "<every top-level import, summed>"), *top_level[:top]]


def turn_overhead(home: Path, turns: int) -> dict[str, Any]:
    """Median wall time of `POST /api/code/turn` against an agent that answers at once, in-process."""
    import psutil
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings
    from chimera.core.agent import AgentResult
    from chimera.interface import ChatSession

    class _Instant:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def run(self, task: str, **kw: Any) -> AgentResult:
            history = list(kw.get("history") or [])
            return AgentResult(
                answer="ok", steps=1, stopped_reason="final",
                transcript=[*history, {"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
                model="test/model",
            )

    os.environ["CHIMERA_HOME"] = str(home)
    os.environ.pop("CHIMERA_MEMORY_BACKEND", None)
    get_settings.cache_clear()
    chimera.core.Agent = _Instant  # type: ignore[assignment,misc]
    ws = home / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(build_api_app(lambda: ChatSession(_Instant()), workspace=ws, settings=settings))
    # Warm-up: the first turn pays one-time imports and the memory graph.
    first = client.post("/api/code/turn", json={"message": "warm up"})
    session_id = ""
    for line in first.text.splitlines():
        if line.startswith("data: ") and '"session_id"' in line:
            session_id = json.loads(line[6:])["session_id"]
            break
    times: list[float] = []
    for i in range(turns):
        began = time.perf_counter()
        client.post("/api/code/turn", json={"message": f"turn {i}", "session_id": session_id})
        times.append(time.perf_counter() - began)
    times.sort()
    me = psutil.Process()
    cpu_before = me.cpu_times()
    time.sleep(10)
    cpu_after = me.cpu_times()
    idle_cpu = (cpu_after.user - cpu_before.user) + (cpu_after.system - cpu_before.system)
    return {
        "turns": turns,
        "median_ms": round(1000 * statistics.median(times), 1),
        "p95_ms": round(1000 * times[int(0.95 * (len(times) - 1))], 1),
        "min_ms": round(1000 * times[0], 1),
        "rss_mb_after_turns": round(me.memory_info().rss / 1e6, 1),
        "idle_cpu_s_over_10s": round(idle_cpu, 3),
    }


def main() -> None:
    table: dict[str, Any] = {"python": sys.version.split()[0], "platform": sys.platform}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        print("== cold start (spawn -> bound -> health), warm OS cache after run 1")
        starts = cold_start(home, RUNS)
        for i, row in enumerate(starts, 1):
            print(
                f"  run {i}: bound {row['spawn_to_bound_s']} s, +health {row['bound_to_health_s']} s"
                f" = {row['spawn_to_health_s']} s, rss {row['rss_mb_after_boot']} MB"
            )
        bounds = [r["spawn_to_bound_s"] for r in starts[1:] if r["spawn_to_bound_s"] is not None]
        healths = [r["spawn_to_health_s"] for r in starts[1:] if r["spawn_to_health_s"] is not None]
        table["cold_start"] = {
            "runs": starts,
            "first_run_bound_s": starts[0]["spawn_to_bound_s"],
            "first_run_health_s": starts[0]["spawn_to_health_s"],
            "warm_median_bound_s": round(statistics.median(bounds), 3) if bounds else None,
            "warm_spread_bound_s": [min(bounds), max(bounds)] if bounds else None,
            "warm_median_health_s": round(statistics.median(healths), 3) if healths else None,
        }
        print(
            f"  warm median: to bound {table['cold_start']['warm_median_bound_s']} s, "
            f"to health {table['cold_start']['warm_median_health_s']} s"
        )

        print("\n== where the start goes (importtime of the real boot, cumulative, top-level)")
        rows = importtime_of_boot(home)
        table["importtime"] = [{"cumulative_ms": round(c / 1000, 1), "module": n} for c, n in rows]
        for c, n in rows:
            print(f"    {c / 1000:8.1f} ms  {n}")

        print("\n== harness overhead per turn (instant agent, in-process)")
        overhead = turn_overhead(home / "turns", TURNS)
        table["turn_overhead"] = overhead
        print(f"  median {overhead['median_ms']} ms, p95 {overhead['p95_ms']} ms, min {overhead['min_ms']} ms")
        print(f"  rss after turns {overhead['rss_mb_after_turns']} MB, idle cpu over 10 s {overhead['idle_cpu_s_over_10s']} s")

    out = REPO / "bench" / "startup" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "table.json").write_text(json.dumps(table, indent=2), encoding="utf-8")
    print(f"\nwrote {out / 'table.json'}")


if __name__ == "__main__":
    main()
