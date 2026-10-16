"""The Linux installers are built on the oldest runner that can build them.

An AppImage or a .deb needs, at least, the C library of the machine that built it. Built on
`ubuntu-latest` (24.04) they required glibc 2.39 and did not start on Ubuntu 22.04, Debian 12 or
Mint 21. The AppImage catalog's automatic test said so (AppImage/appimage.github.io#7006); nothing
of ours did, because every machine that ran the AppImage here was new.

Pinned here so the floor only moves on purpose: the Linux build runs on a named, old Ubuntu, and
no step is gated on a runner NAME — a literal `matrix.os == 'ubuntu-latest'` skipped the system
libraries silently the moment the name changed.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "desktop-release.yml"

#: The newest Ubuntu whose glibc (2.35) the installers may require. 22.04 is also the oldest
#: GitHub runner with webkit2gtk-4.1, which Tauri 2 needs; raising it raises every user's floor.
OLDEST = "ubuntu-22.04"


def _linux_matrix_os(text: str) -> list[str]:
    return re.findall(r"^\s*-\s*os:\s*(ubuntu[\w.-]*)\s*$", text, re.MULTILINE)


def test_the_linux_installers_are_built_on_ubuntu_22_04() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    found = _linux_matrix_os(text)
    assert found == [OLDEST], f"the Linux installers build on {found}, not {OLDEST}"


def test_no_step_is_gated_on_a_runner_name() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    gated = re.findall(r"if:\s*matrix\.os\s*==\s*'ubuntu[^']*'", text)
    assert not gated, f"a step gated on the Ubuntu runner's name is skipped when the name changes: {gated}"


def test_the_check_reads_the_matrix_it_is_about() -> None:
    # The regex above returns nothing on a file it cannot read, and an empty list would pass the
    # first test's comparison only by accident; this keeps it honest about what it parsed.
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "bundles: appimage,deb" in text
    assert _linux_matrix_os(text.replace(OLDEST, "ubuntu-latest")) == ["ubuntu-latest"]
