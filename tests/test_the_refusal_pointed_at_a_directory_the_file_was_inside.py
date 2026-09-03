"""A write-region given an absolute directory refused every write and explained none of it.

Measured on a live run: the region was declared as ``["C:/Users/.../sonda"]`` and the agent asked to
write ``sonda/ZAP2.txt``. Patterns are matched against the workspace-RELATIVE path, so an absolute
one matches nothing, ever — but the refusal read:

    error: write to 'ZAP2.txt' is outside the declared write-region
           (C:/Users/brcam/Desktop/chimera-teste-0492/sonda) - refused

which names a directory the file is plainly inside. The agent did the only thing that message
invites: it retried the same write four ways — bare name, absolute forward-slash, ``./`` prefix,
absolute backslash — burned its whole retry budget across three attempts and about twelve minutes,
and reported an environment fault. The identical run with ``["**"]`` wrote the file on the first
try in 57 seconds.

The refusal itself was right. What was missing was any way to tell an out-of-region write from a
region that cannot match anything at all, and those need different remedies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.tools.write_region import ALWAYS_DENIED, WriteRegion


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path


def test_an_absolute_pattern_still_refuses(ws: Path) -> None:
    """The behaviour does not change — only what the refusal says about it."""
    regiao = WriteRegion([str(ws / "sonda")], ws)
    assert regiao.check(ws / "sonda" / "ZAP.txt") is not None


def test_the_refusal_names_the_path_as_compared(ws: Path) -> None:
    regiao = WriteRegion(["src/**"], ws)
    recado = regiao.check(ws / "docs" / "nota.md")
    assert recado is not None
    assert "'docs/nota.md'" in recado, (
        "the caller passed an absolute path; the comparison used the relative one, and quoting the "
        "bare filename is what made a file inside the named directory look like it was outside"
    )


def test_the_refusal_says_the_region_is_globs(ws: Path) -> None:
    regiao = WriteRegion(["src/**"], ws)
    recado = regiao.check(ws / "docs" / "nota.md")
    assert recado is not None
    assert "glob" in recado, "reading the region as a directory is the mistake being corrected"


@pytest.mark.parametrize(
    "padrao",
    ["C:/Users/x/proj", "/home/x/proj", "//servidor/compartilhado/proj", "D:/dados"],
)
def test_an_absolute_pattern_is_named_as_the_thing_that_cannot_match(ws: Path, padrao: str) -> None:
    regiao = WriteRegion([padrao], ws)
    recado = regiao.check(ws / "arquivo.txt")
    assert recado is not None
    assert "absolute" in recado
    assert "'**'" in recado, "name the remedy, not just the fault"
    assert padrao in recado, "name WHICH pattern, so a caller with several can find it"


def test_a_relative_region_that_simply_does_not_match_says_nothing_about_absolutes(ws: Path) -> None:
    """The absolute hint must not fire on an ordinary miss, or it becomes noise on every refusal."""
    regiao = WriteRegion(["src/**"], ws)
    recado = regiao.check(ws / "docs" / "nota.md")
    assert recado is not None
    assert "absolute" not in recado


def test_a_matching_write_is_still_allowed(ws: Path) -> None:
    regiao = WriteRegion(["**"], ws)
    assert regiao.check(ws / "qualquer" / "coisa.txt") is None


def test_outside_the_workspace_is_its_own_message(ws: Path, tmp_path_factory) -> None:
    fora = tmp_path_factory.mktemp("fora")
    regiao = WriteRegion(["**"], ws)
    recado = regiao.check(fora / "x.txt")
    assert recado is not None
    assert "outside the workspace" in recado, (
        "a path that never reached the region check must not be reported as a region miss"
    )


@pytest.mark.parametrize("negado", ALWAYS_DENIED)
def test_the_never_writable_set_does_not_blame_the_region(ws: Path, negado: str) -> None:
    """No region can grant these, so pointing at the region would send the reader nowhere."""
    regiao = WriteRegion(["**"], ws)
    recado = regiao.check(ws / negado / "algo")
    assert recado is not None
    assert "never permitted" in recado
    assert "does not match the declared" not in recado


def test_looks_absolute_only_flags_the_absolute_ones(ws: Path) -> None:
    regiao = WriteRegion(["src/**", "C:/proj", "*.py", "/etc/x"], ws)
    assert regiao.looks_absolute() == ["C:/proj", "/etc/x"]
