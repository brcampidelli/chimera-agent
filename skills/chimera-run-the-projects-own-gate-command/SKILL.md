---
name: chimera-run-the-projects-own-gate-command
description: Run the project's verification command literally — same scope, same flags. The near-identical variant you improvise lies in both directions.
version: 0.1.0
kind: pattern
stage: verify
topic: devops
triggers:
- about to run lint or type checks
- verifying before a commit or PR
- CI failed but it passed locally
- adding a scope argument or a strictness flag
- reporting the gate as green
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are about to run the project's quality gate — lint, type check, tests — and report the result as
evidence that a change is safe. The temptation is to narrow it ("I only touched `chimera/`") or to
tighten it ("`--strict` is surely better").

It does **not** apply to exploration. Running `pytest tests/test_governed_surfaces.py -x` while
iterating on one file is the right thing to do; the rule is about what you may then *call the gate*.
A narrowed run is a debugging aid, never the verification.

## Do

1. Find where the command is written down, and name ONE source as canonical. In Chimera that is the
   `check` target in the `Makefile` — `make check` — because a target is executable and a prose line
   is a copy of one. The three checks it runs are `ruff check .`, `mypy chimera` and `pytest -q`.
2. Run the canonical form byte-for-byte. No added path argument, no added flag, no substituted
   runner. Beware that a prose copy may differ in ways that look cosmetic and are not: `Makefile:21`
   wraps it as `uv run --no-sync`, `CONTRIBUTING.md:105` as `uv run --extra dev --extra desktop`.
   Same tool, different environments — which is exactly why one of them has to be the source.
3. If two sources disagree, treat the CI workflow as authoritative, run that, and fix the stale doc in
   the same PR — `CONTRIBUTING.md` carried a wrong `mypy --strict` line for a while, and a stale
   instruction propagates to everyone who reads it.
4. If the canonical command cannot run in your environment, say the gate did not run. On Windows the
   local `.venv` is broken (litellm wants Rust/MSVC), so the gate runs in WSL — moving the *shell* is
   allowed, editing the *command* is not.
5. Report the command and its output verbatim, not a summary of how it went.

## Avoid

Two near-misses, both real, both in one session, and note they fail in **opposite** directions:

```bash
mypy --strict chimera      # WRONG — lies toward failure
mypy chimera               # right
```
The explicit flag overrides the project's own `warn_unused_ignores = false` in `pyproject.toml` and
reports roughly fifteen `unused-ignore` errors in files nobody touched. That nearly became a bug
report filed against clean code.

```bash
ruff check chimera tests   # WRONG — lies toward success
ruff check .               # right
```
The narrowed scope does not report a B023 (a closure capturing a loop variable) that `ruff check .`
does, because scope changes which files — and therefore which per-directory rule settings — are in
play. CI failed on a PR whose local "gate" was green.

The shared mechanism: **the flag decides which config wins, and the scope decides which rules fire.**
Neither is a cosmetic difference, and both variants look close enough to the real command that the
output reads as authoritative.

## Check

Point at the file and the line where your command is written. `Makefile:21`, `CONTRIBUTING.md:105`.
If you cannot, you improvised it.

The binary question: could the string you typed be pasted into the `check` recipe with zero edits? If
your command has an argument or a flag the recipe does not have, the answer is no, and what you ran
was a different check that happens to share a name.

## Risk

The canonical command is usually the slowest one, and a rule that forbids the fast variant nudges
toward running nothing at all. That is a worse outcome than a narrowed run honestly labelled.

Measure the cost before deciding the rule is cheap, and re-measure as the suite grows. The first
draft of this card said Chimera's full suite was "about fifteen seconds" — a number that was true
once and had drifted to roughly **100 seconds** across four measured runs by the time the card was
written. A card about not improvising commands is a poor place to publish a figure that does not
survive running the command.

And the recipe itself can be wrong. Following it verbatim means inheriting its blind spots: `make
check` will not tell you that a test reads a gitignored `bench/local_lift/results/paired.json` that a
fresh clone does not have. The fix for a bad gate is to change the recipe in a PR, not to quietly run
a better private one — a private improvement protects exactly one person and leaves CI, and everybody
else, on the old command.
