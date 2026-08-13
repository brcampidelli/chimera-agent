"""Machine telemetry (:mod:`chimera.core.resources`).

One rule holds this module together and every test here is a way of asking whether it still does:
**a measurement that could not be taken is absent, never zero.**

Zero VRAM reads as "the GPU is idle". Zero memory reads as "nothing is running". Both are claims
about a machine, and a laptop with an AMD card is a machine we are in no position to make claims
about — so the answer has to be "unavailable", and it has to say why.
"""

from __future__ import annotations

import subprocess
from typing import Any

from chimera.core.resources import Resources, snapshot


def test_a_snapshot_never_raises() -> None:
    """Whatever this machine is, telemetry answers. A panel that can crash the server is worse than
    a panel that says it does not know."""
    assert isinstance(snapshot(), Resources)


def test_cpu_count_is_always_there() -> None:
    # From the standard library, so it is the one field with no excuse on any machine that can run
    # this code at all.
    result = snapshot()
    assert result.cpu_count is not None and result.cpu_count >= 1


def test_the_dict_shape_keeps_the_gaps_as_gaps() -> None:
    data = snapshot().as_dict()
    assert set(data) == {"cpu_percent", "cpu_count", "memory", "process_mb", "gpus", "notes"}
    assert set(data["memory"]) == {"total_mb", "used_mb", "percent"}
    # Absent stays absent through serialisation. A JSON encoder that turned None into 0 here would
    # undo the entire point on the way to the screen.
    for key in ("cpu_percent", "process_mb"):
        assert data[key] is None or isinstance(data[key], (int, float))


def test_without_psutil_the_numbers_are_absent_and_the_note_says_what_to_install(
    monkeypatch: Any,
) -> None:
    """Not "0% CPU, 0 MB used". The note names the extra, because "unavailable" without a remedy is
    just a shrug."""
    monkeypatch.setattr("chimera.core.resources._psutil", lambda: None)

    result = snapshot()

    assert result.cpu_percent is None
    assert result.memory.total_mb is None
    assert result.memory.percent is None
    assert result.process_mb is None
    assert any("psutil" in note for note in result.notes)


def test_with_psutil_the_numbers_are_real(monkeypatch: Any) -> None:
    # The other direction: the guard above must not be so eager that a working machine reports
    # nothing. Real psutil when it is installed; skipped honestly when it is not.
    import importlib.util

    if importlib.util.find_spec("psutil") is None:
        import pytest

        pytest.skip("psutil not installed")

    result = snapshot()
    assert result.memory.total_mb and result.memory.total_mb > 0
    assert result.process_mb and result.process_mb > 0
    assert result.cpu_percent is not None


def test_the_first_reading_admits_it_measured_nothing(monkeypatch: Any) -> None:
    """`cpu_percent(interval=0)` averages since the PREVIOUS call, so the first one in a process has
    nothing to average and returns 0.0 by construction. Reported as 0% that is this module's own
    rule broken on its own first poll."""
    import chimera.core.resources as resources

    monkeypatch.setattr(resources, "_cpu_last", None)
    monkeypatch.setattr(resources, "_psutil", lambda: _FakePsutil(7.0))

    first = snapshot()

    assert first.cpu_percent is None
    assert any("second reading" in note for note in first.notes)


def test_two_readings_close_together_repeat_the_last_real_one(monkeypatch: Any) -> None:
    """Observed live: the panel's four-second poll plus one manual request produced 0%.

    Twenty milliseconds of a 24-core machine averages to zero whatever it is doing, and that zero
    looks exactly like an idle system. The most recent thing anybody measured is the honest answer.
    """
    import chimera.core.resources as resources

    monkeypatch.setattr(resources, "_cpu_last", (42.0, resources.time.monotonic()))
    monkeypatch.setattr(resources, "_psutil", lambda: _FakePsutil(0.0))

    assert snapshot().cpu_percent == 42.0


class _FakePsutil:
    """Just enough psutil to drive the CPU branch without depending on the real machine's load."""

    def __init__(self, value: float) -> None:
        self._value = value

    def cpu_percent(self, interval: float = 0.0) -> float:
        return self._value

    def virtual_memory(self) -> Any:
        class _V:
            total = 8 * 1_048_576
            available = 4 * 1_048_576
            percent = 50.0

        return _V()

    def Process(self) -> Any:  # noqa: N802 - mirrors psutil's own name
        class _P:
            def memory_info(self) -> Any:
                class _M:
                    rss = 1_048_576

                return _M()

        return _P()


def test_a_machine_without_nvidia_smi_says_unavailable_rather_than_zero(monkeypatch: Any) -> None:
    """The sentence this module exists for.

    An AMD card and an Apple GPU are real GPUs doing real work that `nvidia-smi` cannot see.
    Reporting 0 MB of VRAM in use would be a lie about hardware the user is looking at.
    """
    monkeypatch.setattr("chimera.core.resources.shutil.which", lambda _name: None)

    result = snapshot()

    assert result.gpus == []
    assert any("unavailable rather than zero" in note for note in result.notes)


def test_nvidia_output_is_parsed(monkeypatch: Any) -> None:
    monkeypatch.setattr("chimera.core.resources.shutil.which", lambda _n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        "chimera.core.resources.subprocess.run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="NVIDIA GeForce RTX 5070, 8188, 5312, 43\n", stderr=""
        ),
    )

    gpu = snapshot().gpus[0]

    assert gpu.name == "NVIDIA GeForce RTX 5070"
    assert (gpu.vram_total_mb, gpu.vram_used_mb, gpu.utilisation) == (8188, 5312, 43.0)


def test_a_value_the_driver_cannot_read_stays_absent(monkeypatch: Any) -> None:
    """`[N/A]` is what the driver answers for a laptop GPU in a low-power state or a virtualised
    card. Parsed to zero it would read as an idle GPU; it means the driver did not say."""
    monkeypatch.setattr("chimera.core.resources.shutil.which", lambda _n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        "chimera.core.resources.subprocess.run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Quadro P1000, 4096, [N/A], [N/A]\n", stderr=""
        ),
    )

    gpu = snapshot().gpus[0]

    assert gpu.vram_total_mb == 4096
    assert gpu.vram_used_mb is None
    assert gpu.utilisation is None


def test_two_cards_are_two_entries(monkeypatch: Any) -> None:
    monkeypatch.setattr("chimera.core.resources.shutil.which", lambda _n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        "chimera.core.resources.subprocess.run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="A, 1, 2, 3\nB, 4, 5, 6\n", stderr=""
        ),
    )
    assert [g.name for g in snapshot().gpus] == ["A", "B"]


def test_a_driver_that_errors_is_reported_and_not_silently_empty(monkeypatch: Any) -> None:
    # "No GPU" and "the driver is broken" look identical from an empty list, and only one of them
    # is something the user can act on.
    monkeypatch.setattr("chimera.core.resources.shutil.which", lambda _n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        "chimera.core.resources.subprocess.run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            args=[], returncode=9, stdout="", stderr="couldn't communicate with the driver\n"
        ),
    )

    result = snapshot()

    assert result.gpus == []
    assert any("driver" in note for note in result.notes)


def test_a_hung_nvidia_smi_does_not_hang_the_panel(monkeypatch: Any) -> None:
    """A wedged driver is exactly where a telemetry poll must not block: the panel is decoration and
    the server is not."""

    def explode(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=4)

    monkeypatch.setattr("chimera.core.resources.shutil.which", lambda _n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr("chimera.core.resources.subprocess.run", explode)

    result = snapshot()

    assert result.gpus == []
    assert any("did not answer" in note for note in result.notes)
