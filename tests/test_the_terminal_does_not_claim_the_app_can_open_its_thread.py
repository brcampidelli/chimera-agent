"""The terminal says where it saved a thread, and never that another program can open it.

`chat` used to print "saved as you go, and open in the app" and its docstrings promised "the same
store the desktop app reads". No line of `apps/desktop/src` fetches `/api/sessions` — the app reads
`<home>/code_sessions` — so the claim was false the day it was written (the app deleted its chat
screens on 2026-08-07; this command learned to save on 2026-08-09). The docstrings were corrected in
#401; this pins the third place the sentence lived, the screen, and the one that a user actually reads.
"""

from __future__ import annotations

import pathlib

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "chimera" / "cli" / "main.py"

#: Phrases that assert another program can open a terminal thread. A comment may quote them while
#: explaining the correction, so only executable lines are searched.
FALSE_CLAIMS = ("open in the app", "One store, two front ends", "the same ones the desktop app shows")


def _executable_lines() -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate(SOURCE.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append((n, line))
    return out


def test_no_executable_line_says_the_app_can_open_a_terminal_thread() -> None:
    guilty = [
        f"{n}: {line.strip()[:100]}"
        for n, line in _executable_lines()
        for claim in FALSE_CLAIMS
        if claim in line
    ]
    assert not guilty, (
        "these lines tell the user the desktop app can open a thread the terminal saved, and it "
        f"cannot — it reads <home>/code_sessions: {guilty}"
    )


def test_the_session_banner_names_the_directory_it_writes_to() -> None:
    """Saying *where* is the honest replacement: a path the reader can go and look at."""
    text = SOURCE.read_text(encoding="utf-8")
    assert 'store_label = str(settings.home / "sessions")' in text, (
        "the banner's label must come from the store the command builds, not from a literal that "
        "can drift away from it"
    )
    assert "saved as you go, under {store_label}" in text
