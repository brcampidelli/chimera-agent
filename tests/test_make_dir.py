"""Creating a folder from the picker — the one WRITE on a browsing path made of reads.

The folder picker could only select, so starting a project meant leaving the app for Explorer, and
the folder people then picked was often the wrong one because the right one did not exist yet.

`browse_dirs` beside this is unscoped on purpose — it has to be, or it could not answer "which
workspace?" — and it is safe because it only enumerates names. This creates something, so the name
is treated as hostile: the caller is a text field, and a text field is where a path gets typed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.api.fs_api import make_dir


def test_it_creates_the_folder(tmp_path: Path) -> None:
    out = make_dir(str(tmp_path), "loja-nova")

    assert out["created"] is True
    assert (tmp_path / "loja-nova").is_dir()
    assert out["path"] == str((tmp_path / "loja-nova").resolve())


def test_an_existing_folder_is_the_answer_not_an_error(tmp_path: Path) -> None:
    """"Make me this folder" is already true. But the screen must not say it created one."""
    (tmp_path / "ja-existe").mkdir()

    out = make_dir(str(tmp_path), "ja-existe")

    assert out["created"] is False
    assert out["path"] == str((tmp_path / "ja-existe").resolve())


@pytest.mark.parametrize(
    "nome",
    [
        "../fora",
        "..\\fora",
        "sub/dentro",
        "sub\\dentro",
        "..",
        ".",
        "",
        "   ",
        "C:",
        "C:\\Windows",
        ".oculta",
    ],
)
def test_a_name_that_is_a_path_is_refused(tmp_path: Path, nome: str) -> None:
    """One segment, or nothing. Every one of these is a way to write outside the folder shown."""
    with pytest.raises(ValueError):
        make_dir(str(tmp_path), nome)

    # The refusal has to leave the disk alone, not merely return an error.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("nome", ["con", "PRN", "aux", "NUL", "com1", "lpt9"])
def test_reserved_device_names_are_refused_everywhere(tmp_path: Path, nome: str) -> None:
    """Windows refuses these whatever the extension, and fails in ways that do not read as a bad
    name. Checked on every platform: a folder made on Linux and synced to Windows is somebody's
    Monday morning."""
    with pytest.raises(ValueError):
        make_dir(str(tmp_path), nome)


def test_a_trailing_dot_or_space_cannot_smuggle_one_in(tmp_path: Path) -> None:
    """Windows silently strips them, so "con. " would arrive on disk as the reserved name."""
    with pytest.raises(ValueError):
        make_dir(str(tmp_path), "con. ")
    with pytest.raises(ValueError):
        make_dir(str(tmp_path), "..  ")


def test_the_parent_must_already_exist(tmp_path: Path) -> None:
    """This creates a folder, not a path. `mkdir`, never `mkdir -p`."""
    with pytest.raises(ValueError):
        make_dir(str(tmp_path / "nao-existe"), "filha")

    assert not (tmp_path / "nao-existe").exists()


def test_a_file_is_not_a_parent(tmp_path: Path) -> None:
    arquivo = tmp_path / "notas.md"
    arquivo.write_text("oi", encoding="utf-8")

    with pytest.raises(ValueError):
        make_dir(str(arquivo), "dentro")


def test_a_normal_name_with_dots_and_spaces_still_works(tmp_path: Path) -> None:
    """Guarding the guard. Refusing everything would pass every test above and ship a dead button —
    and real project folders are called things like "Loja v2.0"."""
    assert make_dir(str(tmp_path), "Loja v2.0")["created"] is True
    assert (tmp_path / "Loja v2.0").is_dir()
    assert make_dir(str(tmp_path), "projeto_final-2")["created"] is True
