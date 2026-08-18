"""The container had one limit, and no way to set it.

`DockerSandbox` capped memory at 512m and stopped there. Worse, `network` and `memory` were
constructor parameters that `get_sandbox` never passed — accepted, documented, and unreachable, so
the container was hard-wired regardless of what the settings said.

WHAT WAS NOT MEASURED, said here rather than in a commit message nobody re-reads: the roadmap asked
for a fork bomb and a CPU loop run under the sandbox with and without these flags. That did not
happen — Docker is not running on the machine this was written on, and CI has no daemon either. So
what is verified below is that the flags reach the command line, which is a weaker claim than "the
fork bomb was contained" and is stated as the weaker one.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from chimera.config import Settings
from chimera.sandbox import get_sandbox
from chimera.sandbox.docker import DockerSandbox


def argv_of(sandbox: DockerSandbox) -> list[str]:
    return sandbox._argv("test", "echo hi", Path.cwd(), [])


def flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_the_memory_cap_does_not_leak_into_swap() -> None:
    """`--memory` alone is roughly decorative.

    Docker grants swap equal to the memory limit ON TOP of it unless `--memory-swap` says otherwise,
    so a container capped at 512m can touch a gigabyte. Pinning swap to the same value is what makes
    the number mean what it reads as.
    """
    argv = argv_of(DockerSandbox(memory="256m"))
    assert flag_value(argv, "--memory") == "256m"
    assert flag_value(argv, "--memory-swap") == "256m"


def test_a_fork_bomb_cannot_exhaust_the_host_process_table() -> None:
    """PIDs are not namespaced by default, so a fork bomb in the container takes the HOST down.

    There is a `fork_bomb` lexical rule, but it only fires under governance, which is off by
    default — and a limit that holds without governance is the point of having a sandbox at all.
    """
    assert flag_value(argv_of(DockerSandbox(pids_limit=64)), "--pids-limit") == "64"


def test_a_busy_loop_cannot_take_the_whole_machine() -> None:
    assert flag_value(argv_of(DockerSandbox(cpus="1.5")), "--cpus") == "1.5"


def test_the_disk_limit_is_deliberately_absent() -> None:
    """`--storage-opt size=` is the obvious fourth limit and it is the wrong one to add.

    Docker refuses it outside xfs with pquota. The common case — overlay2 on ext4, every default
    laptop install — would turn a working sandbox into a hard failure at the first command. A limit
    that only works on one filesystem is not a limit; it is an outage waiting for the wrong host.
    """
    assert "--storage-opt" not in argv_of(DockerSandbox())


def test_the_network_is_off_unless_asked() -> None:
    assert flag_value(argv_of(DockerSandbox()), "--network") == "none"
    assert flag_value(argv_of(DockerSandbox(network=True)), "--network") == "bridge"


@pytest.mark.parametrize(
    "env,expected",
    [({}, "none"), ({"CHIMERA_SANDBOX_NETWORK": "bridge"}, "bridge"), ({"CHIMERA_SANDBOX_NETWORK": "none"}, "none")],
)
def test_the_factory_actually_passes_the_settings(env: dict[str, str], expected: str) -> None:
    """The half that was broken.

    Every limit above is reachable from a unit test because a unit test constructs the sandbox
    directly. Nothing in the product does — it goes through `get_sandbox`, which built a
    `DockerSandbox()` with the image and the runtime and dropped the rest on the floor. A knob the
    factory does not forward is a knob that does not exist.
    """
    # Constructed by ALIAS, not by field name: every field here declares a `validation_alias`, and
    # `Settings(sandbox="docker")` silently yields the default instead of raising — which is how the
    # first version of this test read a LocalSandbox and blamed the factory.
    settings = Settings(CHIMERA_SANDBOX="docker", **env)  # type: ignore[arg-type]
    sandbox = get_sandbox(settings)
    assert isinstance(sandbox, DockerSandbox)
    assert flag_value(argv_of(sandbox), "--network") == expected


def test_the_factory_forwards_the_limits_too() -> None:
    settings = Settings(
        **{  # type: ignore[arg-type]
            "CHIMERA_SANDBOX": "docker",
            "CHIMERA_SANDBOX_MEMORY": "1g",
            "CHIMERA_SANDBOX_CPUS": "0.5",
            "CHIMERA_SANDBOX_PIDS": "32",
        }
    )
    argv = argv_of(get_sandbox(settings))  # type: ignore[arg-type]
    assert flag_value(argv, "--memory") == "1g"
    assert flag_value(argv, "--memory-swap") == "1g"
    assert flag_value(argv, "--cpus") == "0.5"
    assert flag_value(argv, "--pids-limit") == "32"


def _docker_up() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "version"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _docker_up(), reason="no docker daemon — the argv tests above are what runs")
def test_the_pid_limit_actually_holds_a_fork_bomb() -> None:
    """The measurement the roadmap asked for, for whoever has a daemon.

    Skipped where this was written and skipped in CI, which is exactly why it is written down rather
    than assumed: a `--pids-limit` that reaches the command line and is silently ignored by the
    runtime would pass every other test in this file.
    """
    sandbox = DockerSandbox(image="python:3.12-slim", pids_limit=32)
    # Bounded on purpose: a real `:(){ :|:& };:` would not stop when the limit is hit, and a test
    # that needs a `docker kill` to finish is a test that wedges someone's laptop.
    result = sandbox.run("for i in $(seq 1 200); do sleep 5 & done; wait", timeout=30)
    assert result.exit_code != 0 or "fork" in (result.stderr or "").lower(), (
        "200 processes started under a 32-PID limit — the limit is not being enforced"
    )
