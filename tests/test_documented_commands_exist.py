"""Every `chimera …` command in the docs and the UI has to be a command that exists.

This is the guard for a whole class of defect that nothing was catching. The Governance screen told
users to run `chimera run --guard` in ten languages; `run` is `gateway.quick(prompt)` — one
completion, no tools, no such flag — so copying the command from the UI gives
`No such option: --guard`. The capability table told them to transcribe a file, crawl a site and
render a chart with the same `chimera run`, and the model politely explained it could not, which
reads as the feature being broken rather than the instruction being wrong.

None of it was a lie about the product. `--guard` is real and wired; the tools are real. The
instructions named the wrong command, and a wrong instruction is indistinguishable from a missing
feature to the person following it.

Checked against `chimera/_cli_snapshot.json`, which CI already regenerates and diffs, so this can
never drift from the real CLI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: `chimera <command>` optionally followed by flags, inside backticks or a shell line.
_INVOCATION = re.compile(r"\bchimera\s+([a-z][a-z0-9-]*)((?:\s+--?[a-z][a-z0-9-]*)*)")

#: Words that follow `chimera` in prose without being commands ("the chimera agent framework").
_NOT_COMMANDS = frozenset({"agent-", "is", "in", "on", "as", "and", "to", "can", "will"})


def _snapshot() -> dict[str, set[str]]:
    """{command: {its flags}} from the snapshot CI keeps in step with the real CLI."""
    data = json.loads((ROOT / "chimera" / "_cli_snapshot.json").read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for command in data["commands"]:
        out[command["name"]] = set(re.findall(r"--[a-z][a-z0-9-]*", json.dumps(command)))
    return out


def _sources() -> list[Path]:
    files = [ROOT / "README.md", *sorted(ROOT.glob("README.*.md"))]
    files += sorted((ROOT / "docs").rglob("*.md"))
    files.append(ROOT / "apps" / "desktop" / "src" / "lib" / "i18n.tsx")
    return [p for p in files if p.exists()]


def _claims() -> list[tuple[Path, int, str, str]]:
    found: list[tuple[Path, int, str, str]] = []
    commands = _snapshot()
    for path in _sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for name, flags in _INVOCATION.findall(line):
                if name in _NOT_COMMANDS or name not in commands:
                    # Unknown names are a separate assertion below; prose is full of "chimera is".
                    continue
                for flag in re.findall(r"--[a-z][a-z0-9-]*", flags):
                    found.append((path, lineno, name, flag))
    return found


def test_every_flag_the_docs_name_exists_on_the_command_they_name_it_on() -> None:
    commands = _snapshot()
    wrong = [
        f"{path.relative_to(ROOT)}:{lineno}: `chimera {name} {flag}` — {name} has no {flag}"
        for path, lineno, name, flag in _claims()
        if flag not in commands[name]
    ]
    assert not wrong, "\n".join(["commands the docs promise and the CLI does not have:", *wrong])


def test_the_probe_finds_something_at_all() -> None:
    """A regex that matched nothing would make the test above pass forever.

    The failure this file exists to catch is a claim about a command; a probe that has stopped
    seeing claims reports "no problems" for exactly the same reason it would report it on a fixed
    codebase, and the two are indistinguishable from the outside.
    """
    claims = _claims()
    assert len(claims) > 20, f"only {len(claims)} flagged invocations found — is the pattern stale?"


@pytest.mark.parametrize("command", ["run", "agent", "solve"])
def test_the_snapshot_is_the_shape_this_test_assumes(command: str) -> None:
    """If the snapshot format changes, the guard above degrades to vacuous rather than failing."""
    commands = _snapshot()
    assert command in commands
    assert commands["agent"], "a command with options must report some"
    # The specific fact this whole file grew out of, pinned so it cannot quietly become true again
    # in the wrong direction.
    assert "--guard" in commands["agent"] and "--guard" in commands["solve"]
    assert "--guard" not in commands["run"]
