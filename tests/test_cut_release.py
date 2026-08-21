"""Cutting a release — the two spellings, and the files it must not touch.

The script exists because a release is six files and two version formats, and both are the kind of
thing that is right until one day it is not. So the tests are about the parts that would be wrong
quietly: a version converted into the wrong SemVer, and a substitution that hits more than the
line it was aimed at.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import cut_release  # noqa: E402


@pytest.mark.parametrize(
    ("pep440", "expected"),
    [
        ("0.48.0", "0.48.0"),
        ("0.48.0rc9", "0.48.0-rc.9"),
        ("0.48.0rc10", "0.48.0-rc.10"),
        ("1.2.3a1", "1.2.3-alpha.1"),
        ("1.2.3b4", "1.2.3-beta.4"),
    ],
)
def test_the_two_spellings_of_one_release(pep440: str, expected: str) -> None:
    # `0.48.0rc9` and `0.48.0-rc.9` are the same release written for two ecosystems, and keeping
    # them in step by hand across pyproject.toml, Cargo.toml and tauri.conf.json is the
    # transcription this script was written to stop doing.
    assert cut_release.semver(pep440) == expected


@pytest.mark.parametrize("bad", ["0.48.0.post1", "0.48.0dev1", "v1.2.3", "1.2", "", "latest"])
def test_a_version_with_no_clean_semver_is_refused_not_guessed(bad: str) -> None:
    # A post-release has no agreed SemVer spelling. Inventing one puts a version on the desktop
    # shell that no updater will ever match against the wheel.
    with pytest.raises(SystemExit):
        cut_release.semver(bad)


def test_the_bump_hits_the_version_line_and_nothing_else(tmp_path: Path, monkeypatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "chimera-agent"\nversion = "0.48.0rc9"\n'
        'dependencies = ["httpx>=0.27.0", "pydantic>=2"]\n'
        '\n[tool.thing]\nversion = "not-the-project-version"\n',
        encoding="utf-8",
    )
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text('[package]\nname = "chimera"\nversion = "0.48.0-rc.9"\n', encoding="utf-8")
    tauri = tmp_path / "tauri.conf.json"
    tauri.write_text(json.dumps({"version": "0.48.0-rc.9", "productName": "Chimera"}), encoding="utf-8")

    monkeypatch.setattr(cut_release, "PYPROJECT", pyproject)
    monkeypatch.setattr(cut_release, "CARGO", cargo)
    monkeypatch.setattr(cut_release, "TAURI", tauri)

    cut_release.write_versions("0.48.0rc10")

    text = pyproject.read_text(encoding="utf-8")
    assert 'version = "0.48.0rc10"' in text
    # count=1 and an anchored pattern, because a blanket replace would rewrite the first other
    # `version = ` line it met — here a tool's setting, elsewhere a dependency pin.
    assert 'version = "not-the-project-version"' in text
    assert '"httpx>=0.27.0"' in text

    # And the desktop shell gets the OTHER spelling, which is the whole point.
    assert 'version = "0.48.0-rc.10"' in cargo.read_text(encoding="utf-8")
    assert json.loads(tauri.read_text(encoding="utf-8"))["version"] == "0.48.0-rc.10"
    assert json.loads(tauri.read_text(encoding="utf-8"))["productName"] == "Chimera"


def test_a_stale_install_stops_the_release_rather_than_stamping_the_old_version(monkeypatch) -> None:
    monkeypatch.setattr(cut_release, "installed_version", lambda: "0.48.0rc9")
    # Stubbed, or this "unit" test reinstalls the package into the developer's environment —
    # which it did, and the only sign was the runtime going from one second to five.
    tried: list[bool] = []
    monkeypatch.setattr(cut_release, "refresh_install", lambda: tried.append(True))

    with pytest.raises(SystemExit) as refused:
        cut_release.regenerate_snapshots("0.48.0rc10")

    assert tried == [True], "it must try to refresh before giving up"

    # The snapshots read `chimera.__version__`, which is the INSTALLED metadata, not pyproject.
    # Regenerating against a stale install produces three files stamped for the previous release —
    # a commit that looks complete, passes locally, and is wrong. The message says what to run.
    assert "0.48.0rc9" in str(refused.value)
    assert "pip install -e ." in str(refused.value)
