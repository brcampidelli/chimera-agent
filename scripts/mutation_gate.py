"""The mutation gate: fail if any mutant survived outside the allowlist.

`mutmut run` reports survivors but always exits 0, so on its own it cannot gate anything. This
script turns its results into a pass/fail decision:

  * every SURVIVED / NO-TESTS mutant must be listed in ``scripts/mutation_allowlist.toml``;
  * every allowlist entry must still be a survivor — a stale entry (the mutant is now killed, or was
    renamed by an edit to the module) is itself a failure, so the allowlist cannot quietly rot into
    a blanket exemption.

Run `mutmut run` first, then this. See MUTATION.md.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

_ALLOWLIST = Path(__file__).with_name("mutation_allowlist.toml")

# `mutmut results` prints "    <mutant name>: <status>" under a per-file heading.
_RESULT_LINE = re.compile(r"^\s+(?P<name>[\w.ǁ]+):\s+(?P<status>survived|no tests)\s*$")


def _collect_survivors() -> dict[str, str]:
    """Run ``mutmut results`` and return ``{mutant_name: status}`` for everything still alive."""
    proc = subprocess.run(
        [sys.executable, "-m", "mutmut", "results"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"`mutmut results` failed with exit code {proc.returncode}")
    survivors: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        match = _RESULT_LINE.match(line)
        if match:
            survivors[match.group("name")] = match.group("status")
    return survivors


def _load_allowlist() -> dict[str, str]:
    if not _ALLOWLIST.exists():
        return {}
    data = tomllib.loads(_ALLOWLIST.read_text(encoding="utf-8"))
    allowed = data.get("allowed", {})
    return {str(k): str(v) for k, v in allowed.items()}


def main() -> int:
    survivors = _collect_survivors()
    allowlist = _load_allowlist()

    unexpected = sorted(set(survivors) - set(allowlist))
    stale = sorted(set(allowlist) - set(survivors))

    print(f"mutation gate: {len(survivors)} alive, {len(allowlist)} allowlisted")

    if unexpected:
        print(f"\n::error::{len(unexpected)} mutant(s) survived outside the allowlist:\n")
        for name in unexpected:
            print(f"  [{survivors[name]}] {name}")
            print(f"      inspect with: mutmut show {name}")
        print(
            "\nEach of these is a change to a module that the tests did NOT notice. Write the test "
            "that kills it. Only if the mutant is genuinely equivalent or unreachable, add it to "
            f"{_ALLOWLIST.name} WITH a one-line reason."
        )

    if stale:
        print(f"\n::error::{len(stale)} allowlist entry/entries no longer survive:\n")
        for name in stale:
            print(f"  {name}")
        print(
            "\nThese are killed now (or the mutant was renamed by an edit to the module). Remove "
            "them — a stale allowlist silently grows into a blanket exemption."
        )

    if unexpected or stale:
        return 1

    print("mutation gate: OK — every surviving mutant is a justified allowlist entry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
