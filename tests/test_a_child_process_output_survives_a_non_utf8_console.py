"""A command's output arrives as the child wrote it, on a machine whose console is not UTF-8.

The defect was silent and had a catastrophic tail. ``text=True`` with no ``encoding=`` decodes with
the **ANSI** code page while a Windows console program writes the **OEM** one — cp1252 against cp850
on the machine this project is developed on. Ordinary output came back mangled; output containing
any of the five bytes cp1252 leaves undefined came back as ``None`` **with a zero exit code**,
because ``UnicodeDecodeError`` is raised on ``subprocess``'s reader thread and ``communicate``
returns the process as successful. ``chimera/core/worktree.py`` already carried that lesson for git;
these are the sites that run arbitrary commands.

The last test is the ratchet, and it is the one that matters in a year: every ``text=True`` in the
package names its encoding, so a new subprocess call cannot inherit the machine's locale by default.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

from chimera.proc import decode
from chimera.proc.decode import console_encoding, console_line, console_text

PACKAGE = pathlib.Path(decode.__file__).resolve().parents[1]

#: What a child writes, and what it means. `\x82 \xa4` is `café ñ` in cp850; the five-byte string is
#: exactly the set cp1252 has no mapping for, which is what used to make output vanish.
CP850_ACCENTS = "café ñ".encode("cp850")
CP1252_UNDEFINED = bytes([0x81, 0x8D, 0x8F, 0x90, 0x9D])
UTF8_EMOJI = "check ✓ fogo \U0001f525".encode()


@pytest.fixture
def oem(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the console page to cp850 so the decision is tested, not the CI runner's locale.

    Without this the whole file would pass vacuously on Linux, where `console_encoding()` is utf-8
    and the fallback can never differ from the first attempt — a test that cannot fail.
    """
    monkeypatch.setattr(decode, "console_encoding", lambda: "cp850")


def test_utf8_output_is_returned_exactly(oem: None) -> None:
    assert console_text(UTF8_EMOJI) == "check ✓ fogo \U0001f525"


def test_output_in_the_console_page_is_read_in_the_console_page(oem: None) -> None:
    """The row that decides the design: cmd.exe's own builtins write this, not UTF-8."""
    assert console_text(CP850_ACCENTS) == "café ñ"


def test_output_that_used_to_vanish_now_arrives(oem: None) -> None:
    """The catastrophic case. Any string at all beats `None` on a process that reported success."""
    got = console_text(CP1252_UNDEFINED)
    assert isinstance(got, str)
    assert got != ""


def test_a_child_cut_off_mid_character_keeps_everything_before_it(oem: None) -> None:
    """A timeout kills the child mid-stream, so this is the normal case here, not a corner one.

    The head must stay UTF-8: falling back to the console page over one incomplete trailing
    character would mangle an entire log because it was interrupted.
    """
    assert console_text("café ✓".encode()[:-1]) == "café �"


def test_a_long_utf8_stream_is_not_decided_by_its_last_byte(oem: None) -> None:
    """The sibling of the test above, and the reason there is no distance-from-the-end constant.

    A first draft accepted any error starting within three bytes of the end. On six bytes of cp850
    that window covered half the string, and `café ñ` went down the truncation path and came out as
    replacement characters — the exact output the fix exists to prevent.
    """
    assert console_text("olá".encode() + b"\xc3") == "olá�"


def test_a_line_that_needed_no_help_is_returned_unchanged(oem: None) -> None:
    assert console_line("check ✓") == "check ✓"


def test_a_streamed_line_recovers_the_bytes_its_reader_could_not_decode(oem: None) -> None:
    """`console_line`'s contract: `surrogateescape` is lossless, so the original bytes come back.

    The streaming reader cannot hand over bytes — `bufsize=1` is line buffering and Python honours
    it in text mode only — so this is how the same decision reaches it.
    """
    as_the_reader_saw_it = CP850_ACCENTS.decode("utf-8", "surrogateescape")
    assert as_the_reader_saw_it != "café ñ"  # the reader really could not read it
    assert console_line(as_the_reader_saw_it) == "café ñ"


def test_empty_and_missing_output_are_the_same_thing(oem: None) -> None:
    """Callers pass `proc.stdout` straight in; a pipe that was never opened is empty, not missing."""
    assert console_text(None) == ""
    assert console_text(b"") == ""


def test_the_console_encoding_is_never_a_codec_python_cannot_load() -> None:
    """Whatever this machine answers, it has to be usable — the fallback must not raise its own error."""
    b"x".decode(console_encoding())


# --------------------------------------------------------------------------- through a real child


@pytest.mark.skipif(sys.platform != "win32", reason="the two code pages only differ on Windows")
def test_the_sandbox_returns_what_the_child_actually_wrote() -> None:
    """The wiring, not the helper. A unit test of `console_text` passes with the sandbox untouched."""
    from chimera.sandbox.local import LocalSandbox

    writer = f'"{sys.executable}" -c "import sys;sys.stdout.buffer.write({UTF8_EMOJI!r})"'
    assert LocalSandbox().run(writer, timeout=60).stdout.strip() == "check ✓ fogo \U0001f525"


@pytest.mark.skipif(sys.platform != "win32", reason="the two code pages only differ on Windows")
def test_output_the_ansi_page_cannot_read_no_longer_disappears() -> None:
    """The end-to-end of the catastrophic case, through the sandbox that runs the model's commands."""
    from chimera.sandbox.local import LocalSandbox

    writer = f'"{sys.executable}" -c "import sys;sys.stdout.buffer.write({CP1252_UNDEFINED!r})"'
    result = LocalSandbox().run(writer, timeout=60)
    assert result.exit_code == 0
    assert result.stdout != "", "the process succeeded and its output was thrown away"


# --------------------------------------------------------------------------- the ratchet


def _text_true_calls_without_an_encoding() -> list[str]:
    """Every `subprocess` call in the package that asks for text and does not say in which encoding."""
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            asks_for_text = any(
                kw.arg in ("text", "universal_newlines")
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            if asks_for_text and "encoding" not in kwargs:
                offenders.append(f"{path.relative_to(PACKAGE.parent)}:{node.lineno}")
    return offenders


def test_no_subprocess_reads_text_without_naming_its_encoding() -> None:
    """The guard, and the only part of this file that still applies once everyone has forgotten why.

    Measured when it was written: eight call sites asked for text, three named an encoding, five took
    the machine's locale. Naming it once per site is the only way to be sure it is named at all —
    the same argument `chimera/core/worktree.py` makes for its own helper.
    """
    assert _text_true_calls_without_an_encoding() == []


def test_the_ratchet_can_actually_fail() -> None:
    """A guard nobody has seen go red is a guard that might be inert. This one is checked against
    the shape it exists to reject, so the test above means what it says."""
    tree = ast.parse("subprocess.run(argv, capture_output=True, text=True)")
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    kwargs = {kw.arg for kw in call.keywords if kw.arg}
    assert "text" in kwargs and "encoding" not in kwargs


def test_the_git_helper_that_taught_this_still_names_its_encoding() -> None:
    """`_git` is where the lesson was learned; a refactor that dropped its encoding would be a
    regression this file is in the best position to notice."""
    source = (PACKAGE / "core" / "worktree.py").read_text(encoding="utf-8")
    assert 'encoding="utf-8"' in source
