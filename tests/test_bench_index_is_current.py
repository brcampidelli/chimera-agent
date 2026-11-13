"""bench/INDEX.md lists every bench directory and matches what the generator writes now."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
INDEX = BENCH / "INDEX.md"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_bench_index", ROOT / "scripts" / "gen_bench_index.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_bench_index"] = mod
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()
PATHS = gen.tracked_bench_files()
pytestmark = pytest.mark.skipif(PATHS is None, reason="not a git checkout: the index is built from git ls-files")


def _ignored(path: Path) -> bool:
    proc = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(path)], capture_output=True)
    return proc.returncode == 0


def test_index_matches_generator_output() -> None:
    assert PATHS is not None
    current = INDEX.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert current == gen.render(PATHS), "bench/INDEX.md is stale: run python scripts/gen_bench_index.py"


def test_every_bench_directory_is_indexed() -> None:
    assert PATHS is not None
    text = INDEX.read_text(encoding="utf-8")
    indexed = set(gen.bench_dirs(PATHS))
    on_disk = {
        p.name
        for p in BENCH.iterdir()
        if p.is_dir() and p.name != "__pycache__" and not _ignored(p)
    }
    missing = sorted(on_disk - indexed)
    assert not missing, f"bench directories missing from the index: {missing}"
    for name in on_disk:
        assert f"[`{name}`]({name}/)" in text, f"{name} has no line in bench/INDEX.md"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["PREREGISTRATION.md", "RESULTS.md", "run.py"], "with result"),
        (["PREREGISTRATION.md", "run.py", "results/rows.json"], "preregistered, no result"),
        (["README.md", "run.py"], "wrapper"),
        (["notes.txt", "run.py"], "neither"),
        (["results.json", "run.py"], "neither"),
    ],
)
def test_status_is_read_from_file_names(files: list[str], expected: str) -> None:
    assert gen.classify(files)[0] == expected

def test_frozen_corpora_are_searchable() -> None:
    # _ignored checks Git's ignore rules; ripgrep must evaluate .rgignore itself.
    try:
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--", "bench"],
            capture_output=True,
            check=True,
        )
        searchable = subprocess.run(
            ["rg", "--files", "--hidden", "--no-ignore", "--ignore-file", ".rgignore", "-0", "bench"],
            cwd=ROOT,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git or ripgrep unavailable: cannot check .rgignore")
    assert searchable.returncode in (0, 1), searchable.stderr.decode("utf-8", errors="replace")
    corpora = {
        Path(name.decode("utf-8"))
        for name in tracked.stdout.split(b"\0")
        if name
        and Path(name.decode("utf-8")).name.startswith(("items", "corpus", "heldout"))
        and name.endswith(b".jsonl")
    }
    visible = {Path(name.decode("utf-8")) for name in searchable.stdout.split(b"\0") if name}
    hidden = sorted(str(path) for path in corpora - visible)
    assert not hidden, f"frozen bench corpora hidden by .rgignore: {hidden}"
