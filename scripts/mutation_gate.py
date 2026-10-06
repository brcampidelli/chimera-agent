"""The mutation gate: fail if any mutant survived outside the allowlist.

`mutmut run` reports survivors but always exits 0, so on its own it cannot gate anything. This
script turns its results into a pass/fail decision:

  * every SURVIVED / NO-TESTS mutant must be listed in ``scripts/mutation_allowlist.toml``;
  * every allowlist entry must still be a survivor — a stale entry (the mutant is now killed, or was
    renamed by an edit to the module) is itself a failure, so the allowlist cannot quietly rot into
    a blanket exemption.

An allowlisted mutant that TIMED OUT is neither: a timeout says the run was slow, not that a test
caught the change. On a loaded runner a handful of the slower equivalents time out instead of
surviving, and reading that as "stale" failed the job on noise. Those entries are re-run alone; one
that still times out is reported as inconclusive (a warning), and only one that is now killed is
stale.

Run `mutmut run` first, then this. See MUTATION.md.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

_ALLOWLIST = Path(__file__).with_name("mutation_allowlist.toml")

# `mutmut results --all true` prints "    <mutant name>: <status>" for every mutant.
_RESULT_LINE = re.compile(r"^\s+(?P<name>[\w.ǁ]+):\s+(?P<status>[a-z][a-z ]*?)\s*$")

_ALIVE = frozenset({"survived", "no tests"})
#: Statuses that say nothing about whether a test notices the mutant.
_INCONCLUSIVE = frozenset({"timeout"})


@dataclass(frozen=True)
class Verdict:
    unexpected: list[str]
    stale: list[str]
    inconclusive: list[str]
    statuses: dict[str, str]  # after any re-run

    @property
    def failed(self) -> bool:
        return bool(self.unexpected or self.stale)


def parse_results(text: str) -> dict[str, str]:
    """``{mutant_name: status}`` from the output of ``mutmut results --all true``."""
    statuses: dict[str, str] = {}
    for line in text.splitlines():
        match = _RESULT_LINE.match(line)
        if match:
            statuses[match.group("name")] = match.group("status")
    return statuses


def judge(
    statuses: dict[str, str],
    allowlist: Iterable[str],
    rerun: Callable[[list[str]], dict[str, str]] | None = None,
) -> Verdict:
    """Decide the gate. ``rerun`` re-runs the named mutants alone and returns their new statuses."""
    allowed = set(allowlist)
    survivors = {name for name, status in statuses.items() if status in _ALIVE}
    unexpected = sorted(survivors - allowed)
    not_alive = sorted(allowed - survivors)
    timed_out = [name for name in not_alive if statuses.get(name) in _INCONCLUSIVE]
    if timed_out and rerun is not None:
        retried = rerun(timed_out)
        # Only the names that were re-run. A filtered `mutmut run` marks every OTHER mutant "not
        # checked" on disk (measured, mutmut 3.6), so anything else it reports is not a result.
        statuses = {**statuses, **{n: retried[n] for n in timed_out if n in retried}}
    stale: list[str] = []
    inconclusive: list[str] = []
    for name in not_alive:
        status = statuses.get(name)
        if status in _ALIVE:
            continue  # survived on the re-run: the entry is still true
        if status in _INCONCLUSIVE:
            inconclusive.append(name)
        else:
            stale.append(name)
    return Verdict(unexpected=unexpected, stale=stale, inconclusive=inconclusive, statuses=statuses)


def _mutmut(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "mutmut", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"`mutmut {' '.join(args)}` failed with exit code {proc.returncode}")
    return proc.stdout


def _collect_statuses() -> dict[str, str]:
    return parse_results(_mutmut("results", "--all", "true"))


def _rerun_alone(names: list[str]) -> dict[str, str]:
    """Re-run only these mutants (nothing else competes for the CPU), then read their statuses."""
    print(f"mutation gate: re-running {len(names)} allowlisted mutant(s) that timed out")
    _mutmut("run", *names)
    fresh = _collect_statuses()
    return {name: fresh[name] for name in names if name in fresh}


def _load_allowlist() -> dict[str, str]:
    if not _ALLOWLIST.exists():
        return {}
    data = tomllib.loads(_ALLOWLIST.read_text(encoding="utf-8"))
    allowed = data.get("allowed", {})
    return {str(k): str(v) for k, v in allowed.items()}


def report(verdict: Verdict, allowlist: dict[str, str]) -> int:
    statuses = verdict.statuses
    alive = sum(1 for status in statuses.values() if status in _ALIVE)
    print(f"mutation gate: {alive} alive, {len(allowlist)} allowlisted")

    if verdict.unexpected:
        print(f"\n::error::{len(verdict.unexpected)} mutant(s) survived outside the allowlist:\n")
        for name in verdict.unexpected:
            print(f"  [{statuses[name]}] {name}")
            print(f"      inspect with: mutmut show {name}")
        print(
            "\nEach of these is a change to a module that the tests did NOT notice. Write the test "
            "that kills it. Only if the mutant is genuinely equivalent or unreachable, add it to "
            f"{_ALLOWLIST.name} WITH a one-line reason."
        )

    if verdict.stale:
        print(f"\n::error::{len(verdict.stale)} allowlist entry/entries no longer survive:\n")
        for name in verdict.stale:
            print(f"  [{statuses.get(name, 'missing')}] {name}")
        print(
            "\nThese are killed now (or the mutant was renamed by an edit to the module). Remove "
            "them — a stale allowlist silently grows into a blanket exemption."
        )

    if verdict.inconclusive:
        print(
            f"\n::warning::{len(verdict.inconclusive)} allowlisted mutant(s) timed out even when "
            "re-run alone, so this run cannot say whether they still survive:\n"
        )
        for name in verdict.inconclusive:
            print(f"  {name}")

    if verdict.failed:
        return 1

    print("mutation gate: OK — every surviving mutant is a justified allowlist entry.")
    return 0


def main() -> int:
    allowlist = _load_allowlist()
    verdict = judge(_collect_statuses(), allowlist, rerun=_rerun_alone)
    return report(verdict, allowlist)


if __name__ == "__main__":
    raise SystemExit(main())
