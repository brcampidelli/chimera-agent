"""The CI test jobs run the suite in shards, and together the shards run every test file once.

Windows runs it in three shards on one Python; Linux in two per Python version. Each OS is its own
partition: the shards of one job together run every file exactly once.

A split is only safe if it is a partition. The two ways it silently stops being one are both a
single character in a diff: the matrix listing ``[1, 2, 3]`` while each job is told it is "of 2"
(the third shard's files never run, and every job is green), or an assignment that depends on the
order pytest happened to collect in (two runners disagree, and some files run twice while others
run nowhere). Each is pinned below; `tests/shards.py` has the reason the split exists.
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests import shards

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.mark.parametrize(("spec", "expected"), [("1/3", (1, 3)), ("3/3", (3, 3)), (" 2/4 ", (2, 4))])
def test_a_well_formed_spec_is_read(spec: str, expected: tuple[int, int]) -> None:
    assert shards.parse(spec) == expected


@pytest.mark.parametrize("spec", ["", "3", "0/3", "4/3", "1/0", "a/b", "-1/3", "1/3/5"])
def test_a_malformed_spec_is_refused_rather_than_running_everything(spec: str) -> None:
    with pytest.raises(ValueError):
        shards.parse(spec)


def _counts() -> dict[str, int]:
    rng = random.Random(20261015)
    return {f"tests/test_{n:04d}.py": rng.randint(1, 60) for n in range(400)}


def test_every_file_lands_in_exactly_one_shard() -> None:
    counts = _counts()

    owner = shards.assign(counts, 3)

    assert set(owner) == set(counts)
    assert set(owner.values()) == {1, 2, 3}


def test_the_assignment_does_not_depend_on_collection_order() -> None:
    counts = _counts()
    shuffled = list(counts.items())
    random.Random(7).shuffle(shuffled)

    assert shards.assign(dict(shuffled), 3) == shards.assign(counts, 3)


def test_the_shards_are_balanced_to_within_one_file() -> None:
    counts = _counts()
    owner = shards.assign(counts, 3)
    loads = [sum(c for path, c in counts.items() if owner[path] == s) for s in (1, 2, 3)]

    assert max(loads) - min(loads) <= max(counts.values())


def test_one_shard_is_the_whole_suite() -> None:
    assert set(shards.assign(_counts(), 1).values()) == {1}


#: Each sharded job and how many shards it is meant to have. Changing a count is a decision, so it
#: is made here as well as in the workflow, not inferred from whatever the workflow says today.
SHARDED_JOBS = {"quality-windows": 3, "quality": 2}


def _workflow() -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return workflow


def _pytest_step(job: dict[str, Any]) -> dict[str, Any]:
    [step] = [s for s in job["steps"] if "pytest" in str(s.get("run", ""))]
    return step


@pytest.mark.parametrize(("job_id", "expected"), sorted(SHARDED_JOBS.items()))
def test_the_matrix_and_the_denominator_agree(job_id: str, expected: int) -> None:
    job = _workflow()["jobs"][job_id]
    listed = job["strategy"]["matrix"]["shard"]
    spec = _pytest_step(job)["env"][shards.ENV]
    match = re.fullmatch(r"\$\{\{ matrix\.shard \}\}/(\d+)", spec)

    assert match, f"{shards.ENV} is {spec!r}, not '${{{{ matrix.shard }}}}/<total>'"
    total = int(match.group(1))
    assert listed == list(range(1, total + 1)), (
        f"{job_id}: the matrix lists {listed} but tells each shard it is one of {total}: "
        "some files would run nowhere, or twice"
    )
    assert total == expected, f"{job_id} runs {total} shards; this test expects {expected}"
    assert job["strategy"].get("fail-fast") is False, "one red shard must not cancel the others"


def test_the_linux_shards_run_on_every_supported_python() -> None:
    matrix = _workflow()["jobs"]["quality"]["strategy"]["matrix"]

    assert matrix["python-version"] == ["3.11", "3.12", "3.13"]
    assert "include" not in matrix and "exclude" not in matrix, (
        "an include/exclude could drop one (python, shard) pair and leave its files unrun there"
    )


def test_lint_and_type_check_run_once_per_python_not_once_per_shard() -> None:
    steps = _workflow()["jobs"]["quality"]["steps"]
    for tool in ("ruff check", "mypy chimera"):
        [step] = [s for s in steps if tool in str(s.get("run", ""))]
        assert step.get("if") == "matrix.shard == 1", f"`{tool}` must run on shard 1 only"
    assert "if" not in _pytest_step(_workflow()["jobs"]["quality"]), "every shard runs its tests"


@pytest.mark.parametrize("job_id", sorted(SHARDED_JOBS))
def test_the_required_verdict_still_waits_for_every_shard(job_id: str) -> None:
    assert job_id in _workflow()["jobs"]["verdict"]["needs"]


#: Light files, so collecting them four times stays a few seconds on the slowest runner.
_SAMPLE = [
    "tests/test_the_ci_shards_cover_every_test_file_once.py",
    "tests/test_the_rust_audit_blocks_the_build.py",
    "tests/test_the_litellm_floor_is_real.py",
    "tests/test_a_release_is_not_latest_until_it_can_be_updated_to.py",
]


def _collect(shard: str | None) -> tuple[set[str], str]:
    env = dict(os.environ)
    env.pop(shards.ENV, None)
    if shard is not None:
        env[shards.ENV] = shard
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *_SAMPLE],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False,
    )
    assert done.returncode in (0, 5), done.stdout + done.stderr
    ids = {line for line in done.stdout.splitlines() if "::" in line}
    return ids, done.stdout


@pytest.mark.parametrize("total", sorted(set(SHARDED_JOBS.values())))
def test_the_real_hook_partitions_a_real_collection(total: int) -> None:
    everything, _ = _collect(None)
    parts = [_collect(f"{k}/{total}") for k in range(1, total + 1)]
    ids = [part for part, _ in parts]

    assert everything, "the sample collected nothing, so this proves nothing"
    assert set().union(*ids) == everything
    assert sum(len(part) for part in ids) == len(everything), "a test landed in two shards"
    for k, (_, out) in enumerate(parts, start=1):
        assert f"{shards.ENV}={k}/{total}:" in out, "a shard did not say what it ran"
