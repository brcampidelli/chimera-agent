# Mutation testing — the gate on the modules that, if wrong, make Chimera lie

A passing test suite proves the tests *run*, not that they *can fail*. This project has shipped
vacuous tests — ones that stayed green under a mutation that broke the code — more than once. Mutation
testing is the check that green means something: it changes the source in small ways (`==` → `!=`,
`and` → `or`, `x` → `x + 1`) and asks whether any test notices. A mutant no test kills is a hole.

## Scope — narrow on purpose

Mutating all of `chimera` would take hours and nobody would read the result. The gate targets the five
modules where a silent bug is a **credibility** bug — the code behind the claims Chimera makes about
itself:

| module | why it's in scope |
|---|---|
| `chimera/eval/paired.py` | the McNemar/Wilson statistic behind every published benchmark number |
| `chimera/api/version_api.py` | the "update available" signal shown in the app |
| `chimera/core/verify.py` | the pass/fail authority of verify-or-revert |
| `chimera/evolution/diff_gate.py` | the "did it actually change anything" gate |
| `chimera/api/runs.py` | receipt building — the reverted/success flags the UI reports |

The scope lives in `[tool.mutmut]` in `pyproject.toml` (`only_mutate`). The test universe is restricted
to those modules' focused test files so a run is seconds, not the full ~95s suite — `tests/test_api.py`
alone is ~65s and is deliberately excluded (every `runs.py` entry point it covers is tested directly by
`tests/test_runs.py`).

## Running it

`mutmut` forks per mutant, so it is POSIX-only. On Windows, run it under WSL; CI runs it on Linux.

```bash
mutmut run                        # mutate + test; always exits 0, so it can't gate on its own
python scripts/mutation_gate.py   # THIS is the gate: nonzero if any survivor is unexplained
```

The last full run: **379 mutants, ~95% killed, 18 survivors — all verified equivalent** (see below).

## The gate, and why the allowlist can't rot

`mutmut run` reports survivors but always exits 0. `scripts/mutation_gate.py` turns that into a
pass/fail:

- **every surviving / no-tests mutant must be in `scripts/mutation_allowlist.toml`** — an un-allowlisted
  survivor fails CI. That's a change to the code no test noticed: write the test that kills it.
- **every allowlist entry must still be a live survivor** — if a mutant is now killed (a test caught up)
  or was renamed by an edit, its stale entry fails CI too. So the allowlist cannot quietly grow into a
  blanket "ignore everything" — it stays pinned to reality.

## What belongs in the allowlist — equivalent mutants only

A mutant belongs there **only when it cannot change observable behaviour**, with a one-line reason.
Reaching for the allowlist instead of writing a test is how mutation testing gets defeated; the honest
default is to kill the mutant. The current 18 entries are all genuinely equivalent, and each was
classified by reading the source — not by "the test missed it, oh well". The categories:

- **HTTP header-name case** (`User-Agent` → `user-agent`): header names are case-insensitive (RFC 7230).
- **Codec-name alias** (`utf-8` → `UTF-8`): Python codec names are case-insensitive aliases.
- **File encoding on Linux** (`encoding="utf-8"` → `None`): identical on the CI platform's UTF-8 locale;
  the explicit form is correct defensive code for Windows (cp1252) but is unkillable on Linux without
  faking a locale, which we don't.
- **Redundant `zip(strict=True)`**: `compare_paired` raises on a length mismatch *before* the zip, so
  `strict=` has no reachable effect.
- **A dead `or ""` branch**: guarded by a short-circuit that never lets the fallback run.
- **Error-message wording**: asserting on the text of a `ValueError` is a bad test; the raise is tested.
- **A None-guarded set op** (`&` → `|` in `diff_snapshots`): the union's extra one-sided files hit the
  `None` guard and are skipped, so the result is identical — verified empirically, not assumed.

If you're unsure whether a survivor is equivalent, it isn't — write the test.
