"""Where the CLI's source lives, for the tests that read it.

Until S30-70 every command was in ``chimera/cli/main.py``, and two dozen tests read that one file to
check what the commands do — that a surface passes the owner's profile, that no bare registry is
built, that an answer is escaped before Rich parses it. When the commands moved to
``chimera/cli/commands/``, a test still reading ``main.py`` would read a file of imports: the ones
asserting something is PRESENT fail, which is loud; the ones asserting something is ABSENT pass,
which is silent and means they stopped testing anything. Reading through here keeps both kinds
pointed at the code the commands actually run.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI_DIR = ROOT / "chimera" / "cli"


def cli_command_files() -> list[Path]:
    """``main.py`` and every area module the commands live in, in a stable order."""
    files = [CLI_DIR / "main.py", *sorted((CLI_DIR / "commands").glob("*.py"))]
    # A glob that matched nothing would make every caller vacuous, so it is refused here once.
    assert len(files) > 10, f"only {len(files)} CLI command files found — has the package moved?"
    return files


def cli_command_rel_paths() -> list[str]:
    """The same files, as repository-relative POSIX paths (``chimera/cli/commands/chat.py``)."""
    return [path.relative_to(ROOT).as_posix() for path in cli_command_files()]


def cli_source() -> str:
    """Every CLI command file's text, joined — for a substring check over all the commands."""
    return "\n".join(path.read_text(encoding="utf-8") for path in cli_command_files())
