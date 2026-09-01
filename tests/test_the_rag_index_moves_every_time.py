"""The RAG index was written to a filename that changed every time we looked for it.

`default_index_path` keyed the file by `hash()` of the resolved workspace path. `hash()` of a `str`
is salted per process (PEP 456, on by default since 3.3), so the digest was a different number in
every interpreter. Measured on this machine, the same string in three processes:

    9073428063654254822 · -1329371590271307703 · -8573617336570954535

The consequence is not a slow lookup — it is that the index is never found. Every `chimera find`
created a fresh empty database, re-embedded the corpus from zero, and left the previous one on disk
as a file nobody would ever open again. Nothing errored; the command worked, every single time, by
doing all of the work again.

That also makes every other claim about the RAG store untestable end to end: a stale vector cannot
be detected in a database that is never reopened.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from chimera.rag.store import default_index_path


def test_the_same_workspace_maps_to_the_same_file(tmp_path: Path) -> None:
    """The whole point: look twice, find the same file."""
    home, workspace = tmp_path / "home", tmp_path / "projeto"
    workspace.mkdir()

    assert default_index_path(home, workspace) == default_index_path(home, workspace)


def test_the_name_survives_a_new_interpreter(tmp_path: Path) -> None:
    """The assertion that would have caught this, and the only one that can.

    Two calls inside ONE process agree even with the salted builtin, because the salt is per
    process. The defect only exists across processes — which is exactly how the function is used,
    since each `chimera find` is a new one. Two real subprocesses, with hash randomisation left at
    its default rather than pinned, is the shape of the real failure.
    """
    workspace = tmp_path / "projeto"
    workspace.mkdir()
    program = (
        "from pathlib import Path;"
        "from chimera.rag.store import default_index_path;"
        f"print(default_index_path(Path({str(tmp_path / 'home')!r}), Path({str(workspace)!r})).name)"
    )
    seen = {
        subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(3)
    }

    assert len(seen) == 1, f"the index filename changed between processes: {sorted(seen)}"


def test_two_workspaces_with_the_same_name_do_not_collide(tmp_path: Path) -> None:
    """The digest has to carry the whole path, not just be stable.

    Written with two DIFFERENT basenames first, and a constant digest passed it — the filenames
    differed because the folder names did, and the digest was doing nothing. `~/projetos/foo` and
    `~/trabalho/foo` is the real case, and it is the one that decides whether this function needs a
    hash at all.
    """
    home = tmp_path / "home"
    (tmp_path / "projetos" / "foo").mkdir(parents=True)
    (tmp_path / "trabalho" / "foo").mkdir(parents=True)

    a = default_index_path(home, tmp_path / "projetos" / "foo")
    b = default_index_path(home, tmp_path / "trabalho" / "foo")

    assert a != b


def test_the_workspace_name_is_still_readable_in_the_filename(tmp_path: Path) -> None:
    """Kept from the original: a directory of opaque digests is a directory nobody can clean up."""
    workspace = tmp_path / "meu-projeto"
    workspace.mkdir()

    assert default_index_path(tmp_path / "home", workspace).name.startswith("meu-projeto-")


def test_the_index_stays_out_of_the_repository(tmp_path: Path) -> None:
    """The docstring's own promise — an index inside someone's repo is a diff they have to explain."""
    workspace = tmp_path / "projeto"
    workspace.mkdir()
    path = default_index_path(tmp_path / "home", workspace)

    assert workspace not in path.parents
    assert path.parent == tmp_path / "home" / "rag"
