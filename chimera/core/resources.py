"""What this machine is spending while the agent works.

Three numbers, and one rule that governs all of them: **a measurement that could not be taken is
reported as absent, never as zero.** Zero VRAM reads as "the GPU is idle" and zero memory reads as
"nothing is running"; both are claims about the machine, and neither is one we are in a position to
make on a laptop whose GPU we cannot see.

That rule is why every field here is `| None` and why the answer carries a `note` saying which tool
was missing. A dashboard showing 0% on an AMD card is worse than a dashboard showing "unavailable",
because the first one gets believed.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("core.resources")

#: Seconds to wait for `nvidia-smi`. It is a fast query against a driver that is either there or not;
#: a longer wait would only be spent on a machine where the driver is wedged, which is exactly where
#: a telemetry panel must not block.
_SMI_TIMEOUT = 4.0

#: The last CPU reading and when it was taken. See :func:`snapshot` for why both are needed.
_cpu_last: tuple[float, float] | None = None

#: Shortest gap that makes a non-blocking CPU reading mean anything. `psutil.cpu_percent(interval=0)`
#: averages over the time since the PREVIOUS call, so two calls 20 ms apart measure 20 ms of a
#: 24-core machine and answer 0.0 — a number that looks like an idle system and is really an
#: unmeasured one. Observed live: the panel's 4-second poll plus one manual request produced exactly
#: that. Below this gap the previous reading is repeated, which is the most recent thing actually
#: measured.
_CPU_MIN_GAP = 0.5


@dataclass
class Memory:
    """RAM, in megabytes. None throughout when psutil is not installed."""

    total_mb: int | None = None
    used_mb: int | None = None
    percent: float | None = None


@dataclass
class Gpu:
    """One GPU's memory and utilisation, as its own driver reports them."""

    name: str
    vram_total_mb: int | None = None
    vram_used_mb: int | None = None
    utilisation: float | None = None


@dataclass
class Resources:
    """A snapshot, with every gap named."""

    cpu_percent: float | None = None
    #: Logical cores. Available from the standard library, so it is the one field that is never None
    #: on any machine that can run Python.
    cpu_count: int | None = None
    memory: Memory = field(default_factory=Memory)
    #: Resident set of THIS process — the sidecar. Separate from system memory because "the agent is
    #: using 12 GB" and "the machine is using 12 GB" are different sentences and only one is ours.
    process_mb: int | None = None
    gpus: list[Gpu] = field(default_factory=list)
    #: Why something is missing, in the words of what to install. Empty when nothing is missing.
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cpu_percent": self.cpu_percent,
            "cpu_count": self.cpu_count,
            "memory": {
                "total_mb": self.memory.total_mb,
                "used_mb": self.memory.used_mb,
                "percent": self.memory.percent,
            },
            "process_mb": self.process_mb,
            "gpus": [
                {
                    "name": g.name,
                    "vram_total_mb": g.vram_total_mb,
                    "vram_used_mb": g.vram_used_mb,
                    "utilisation": g.utilisation,
                }
                for g in self.gpus
            ],
            "notes": list(self.notes),
        }


def _psutil() -> Any | None:
    """psutil if it is installed, else None.

    An optional dependency on purpose. It is a compiled package, and making the sidecar refuse to
    start without it would trade a telemetry panel for the whole product on any machine where the
    wheel does not build.
    """
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def _nvidia_gpus() -> tuple[list[Gpu], str]:
    """GPUs as `nvidia-smi` reports them, plus a note when it could not be asked.

    `nvidia-smi` rather than a Python binding: the binding is another optional dependency wrapping
    the same driver, and this needs four numbers. The CSV query mode is stable across driver
    versions in a way parsing the human table is not.
    """
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return [], ""
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, never a shell string
            [
                smi,
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=_SMI_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"nvidia-smi did not answer ({type(exc).__name__})"
    if proc.returncode != 0:
        return [], (proc.stderr or "").strip()[:200] or "nvidia-smi reported an error"

    gpus: list[Gpu] = []
    for line in proc.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        name, total, used, util = parts[0], parts[1], parts[2], parts[3]

        def number(raw: str) -> float | None:
            # "[N/A]" is what the driver says for a value it cannot read — a laptop GPU in a power
            # state, a virtualised card. Passed through as None rather than parsed to zero.
            try:
                return float(raw)
            except ValueError:
                return None

        total_v, used_v, util_v = number(total), number(used), number(util)
        gpus.append(
            Gpu(
                name=name or "GPU",
                vram_total_mb=int(total_v) if total_v is not None else None,
                vram_used_mb=int(used_v) if used_v is not None else None,
                utilisation=util_v,
            )
        )
    return gpus, ""


def _cpu_reading(psutil: Any, cpu_interval: float, notes: list[str]) -> float | None:
    """CPU load, or None when nothing has actually been measured yet.

    Two ways to get a number that is not one, both of which produce 0.0 and both of which read as
    "the machine is idle":

    * the FIRST non-blocking call in a process has no earlier sample to average against;
    * two calls close together average over the gap between them, and a 20 ms window on a 24-core
      machine is 0.0 whatever the machine is doing.

    Neither is a measurement, so neither is reported as one. The first primes and says so; the
    second repeats the last real reading, which is the most recent thing anybody measured.

    A blocking sample would sidestep both and cost the server that many seconds on every poll of a
    number nobody is watching that closely.
    """
    global _cpu_last
    if cpu_interval > 0:
        return float(psutil.cpu_percent(interval=cpu_interval))

    now = time.monotonic()
    if _cpu_last is not None and now - _cpu_last[1] < _CPU_MIN_GAP:
        return _cpu_last[0]

    measured = float(psutil.cpu_percent(interval=0))
    if _cpu_last is None:
        # Primed. The value psutil just returned is 0.0 by construction, not by observation.
        _cpu_last = (measured, now)
        notes.append("CPU needs a second reading to compare against")
        return None
    _cpu_last = (measured, now)
    return measured


def snapshot(*, cpu_interval: float = 0.0) -> Resources:
    """Take a reading. Never raises; every failure becomes a note and a None.

    ``cpu_interval`` is passed to psutil. Zero means "since the last call", which is what a polling
    panel wants — a blocking sample would make every poll cost that many seconds of server time for
    a number nobody is watching that closely.
    """
    out = Resources(cpu_count=os.cpu_count())

    psutil = _psutil()
    if psutil is None:
        out.notes.append("install the 'desktop' extra for CPU and memory (psutil)")
    else:
        try:
            out.cpu_percent = _cpu_reading(psutil, cpu_interval, out.notes)
            virtual = psutil.virtual_memory()
            out.memory = Memory(
                total_mb=int(virtual.total / 1_048_576),
                used_mb=int((virtual.total - virtual.available) / 1_048_576),
                percent=float(virtual.percent),
            )
            out.process_mb = int(psutil.Process().memory_info().rss / 1_048_576)
        except Exception as exc:  # noqa: BLE001 — telemetry must not take the server with it
            _log.debug("psutil reading failed: %s", exc)
            out.notes.append("the system reading failed")

    gpus, gpu_note = _nvidia_gpus()
    out.gpus = gpus
    if gpu_note:
        out.notes.append(gpu_note)
    elif not gpus:
        # The honest sentence for every machine that is not NVIDIA, and the reason this module
        # exists in the shape it does. An AMD card and an Apple GPU are both real GPUs doing real
        # work that `nvidia-smi` cannot see — reporting 0 MB of VRAM in use would be a lie about
        # hardware the user is looking at.
        out.notes.append(
            "GPU memory is read through nvidia-smi; on "
            f"{platform.system() or 'this system'} without it, it is unavailable rather than zero"
        )
    return out
