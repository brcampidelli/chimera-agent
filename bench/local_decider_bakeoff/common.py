"""Shared pieces of the local bake-off — see PREREGISTRATION.md (written first).

Reading a Jev-shaped answer into the distribution JevBench scores, the validity guard (guard 4),
the GPU-alone check (guard 2), weight hashes (guard 1) and a server process that is always stopped.
Nothing here talks to a model by itself.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
TOLERANCE = 0.01


class GuardError(SystemExit):
    """A pre-registered guard failed: the apparatus, not the model, is in question."""

    def __init__(self, message: str, code: int = 4) -> None:
        print(f"GUARD FAILED: {message}", flush=True)
        super().__init__(code)


def check_distribution(probs: dict[str, float], labels: list[str], where: str) -> None:
    """Guard 4: finite, non-negative, exactly the expected labels, sums to 1 within 0.01."""
    if set(probs) != set(labels):
        raise GuardError(f"{where}: answer keys {sorted(probs)} != labels {sorted(labels)}")
    values = list(probs.values())
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in values):
        raise GuardError(f"{where}: non-finite or negative probability {probs}")
    if abs(sum(values) - 1.0) > TOLERANCE:
        raise GuardError(f"{where}: probabilities sum to {sum(values):.4f}")


def probs_of_answer(answer: dict[str, Any], kind: str, labels: list[str], where: str) -> dict[str, float]:
    """A Jev-shaped answer as a distribution keyed by the JevBench item's labels.

    ``noul``: the probability of true/yes is the ``noul`` field (Jev, Clef, Intern and our Eikos
    sidecar all return it); the item's labels are ``no``/``yes``. ``choice``: ``probabilities`` keyed
    by option id. ``score``: ``probabilities`` keyed by level, or a list indexed from 0.
    """
    if kind == "noul":
        p = answer.get("noul")
        if p is None:
            raw = answer.get("probabilities") or {}
            p = raw.get("yes", raw.get("true"))
        if p is None:
            raise GuardError(f"{where}: noul answer without a probability: {answer}")
        out = {"yes": float(p), "no": 1.0 - float(p)}
    else:
        raw = answer.get("probabilities")
        if isinstance(raw, list):
            raw = {str(i): v for i, v in enumerate(raw)}
        if not isinstance(raw, dict):
            raise GuardError(f"{where}: {kind} answer without probabilities: {answer}")
        out = {str(k): float(v) for k, v in raw.items()}
    check_distribution(out, labels, where)
    return out


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_hashes(manifest: Path, root: Path, prefix: str) -> int:
    """Guard 1: every line of ``weights.sha256`` under ``prefix`` matches the file on disk."""
    checked = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        expected, rel = line.split(maxsplit=1)
        if not rel.startswith(prefix):
            continue
        if sha256(root / rel) != expected:
            raise GuardError(f"{rel}: sha256 does not match weights.sha256")
        checked += 1
    if not checked:
        raise GuardError(f"no weight file listed under {prefix}")
    return checked


def _gpu_state() -> tuple[str, int]:
    apps = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    free = int(subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    ).stdout.split()[0])
    return apps, free


def gpu_alone(min_free_mib: int, wait_s: float = 900.0) -> dict[str, Any]:
    """Guard 2: no other compute process on the GPU and enough free memory. Exit 3 otherwise.

    It waits (up to ``wait_s``) for the GPU to empty — an idle Ollama unloads its model after its
    keep-alive — and never stops a process it did not start.
    """
    deadline = time.monotonic() + wait_s
    while True:
        apps, free = _gpu_state()
        if not apps and free >= min_free_mib:
            return {"free_mib_before": free}
        if time.monotonic() >= deadline:
            raise GuardError(
                f"GPU not free for a solo measurement (processes: {apps or 'none'}; free {free} MiB, need {min_free_mib})",
                code=3,
            )
        print(f"waiting for the GPU (processes: {apps or 'none'}; free {free} MiB)", flush=True)
        time.sleep(30.0)


def others_on_gpu(own_pids: set[int]) -> list[str]:
    """Compute processes on the GPU that this runner did not start (checked again after loading)."""
    apps, _ = _gpu_state()
    return [line for line in apps.splitlines() if line.strip() and int(line.split(",")[0]) not in own_pids]


def vram_used() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    ).stdout
    return int(out.split()[0])


class Server:
    """A child process this runner started, waited on until healthy, and always stopped."""

    def __init__(self, argv: list[str], health_url: str, log: Path, cwd: Path | None = None) -> None:
        self.argv, self.health_url, self.log, self.cwd = argv, health_url, log, cwd
        self.proc: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Server:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        fh = self.log.open("ab")
        print(f"starting: {' '.join(self.argv)}", flush=True)
        self.proc = subprocess.Popen(self.argv, stdout=fh, stderr=subprocess.STDOUT, cwd=self.cwd)
        return self

    def wait_healthy(self, timeout_s: float = 900.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise GuardError(f"server exited with {self.proc.returncode} before it was healthy; see {self.log}", code=5)
            try:
                r = httpx.get(self.health_url, timeout=5.0)
                if r.status_code == 200:
                    return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            except httpx.HTTPError:
                pass
            time.sleep(3.0)
        raise GuardError(f"server not healthy after {timeout_s:.0f}s; see {self.log}", code=5)

    def __exit__(self, *_exc: object) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=30)
        print(f"stopped server pid {self.proc.pid}", flush=True)
