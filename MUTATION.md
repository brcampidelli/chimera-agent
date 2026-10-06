# Mutation testing — the gate on the modules that, if wrong, make Chimera lie

A passing test suite proves the tests *run*, not that they *can fail*. This project has shipped
vacuous tests — ones that stayed green under a mutation that broke the code — more than once. Mutation
testing is the check that green means something: it changes the source in small ways (`==` → `!=`,
`and` → `or`, `x` → `x + 1`) and asks whether any test notices. A mutant no test kills is a hole.

## Scope — narrow on purpose

Mutating all of `chimera` would take hours and nobody would read the result. The gate targets the modules
where a silent bug is a **credibility** bug — the code behind the claims Chimera makes about itself — and,
since study 30 (S30-37), the modules where a silent bug is a **security** bug: the ones that decide whether
something the model chose happens on your machine or reaches someone else.

| module | why it's in scope |
|---|---|
| `chimera/eval/paired.py` | the McNemar/Wilson statistic behind every published benchmark number |
| `chimera/api/version_api.py` | the "update available" signal shown in the app |
| `chimera/core/verify.py` | the pass/fail authority of verify-or-revert |
| `chimera/evolution/diff_gate.py` | the "did it actually change anything" gate |
| `chimera/api/runs.py` | receipt building — the reverted/success flags the UI reports |
| `chimera/sandbox/confirm.py` | the host-exec gate: run on the host, or refuse |
| `chimera/governance/audit.py` | the hash-chained decision log, and the `verify()` that calls it intact |
| `chimera/governance/recipient.py` | "was this address ever seen?" before a send |
| `chimera/governance/ledger.py` | taint: what the run read, and what that narrows |
| `chimera/governance/ledger_tool.py` | the wrapper that applies the narrowing to every tool call |
| `chimera/governance/shared_approval.py` | one answer per proposal across a crew's workers |
| `chimera/governance/policy.py` | allow / ask / deny for a tool call |

The scope lives in `[tool.mutmut]` in `pyproject.toml` (`only_mutate`).

### The tests the gate runs — and why that list is itself tested

mutmut maps each test in `pytest_add_cli_args_test_selection` to the functions it calls and, per mutant,
re-runs only those. **A test file not on that list does not exist for the gate**, however well it tests the
module. That is how the gate rotted between 2026-07 and 2026-10: the list was the eight files written with
the original five modules, the modules grew new code tested in new files, and the weekly run reported ~450
survivors that the full suite in fact kills. `tests/test_the_mutation_gate_sees_the_tests_of_its_modules.py`
now fails when a test file naming an in-scope module is neither selected nor excluded with a reason (the
slow ones are: `tests/test_api.py` alone is ~65s), and when a selected test `chdir`s without being
deselected (mutmut 3.6 resolves its hit recorder against the current directory, so one such test aborts the
whole run).

## Running it

`mutmut` forks per mutant, so it is POSIX-only. On Windows, run it under WSL; CI runs it on Linux.

```bash
mutmut run                        # mutate + test; always exits 0, so it can't gate on its own
python scripts/mutation_gate.py   # THIS is the gate: nonzero if any survivor is unexplained
```

The last full run (2026-10-06, twelve modules): **3,167 mutants, 3,015 killed (95.2%), 152 survivors — every
one an allowlisted equivalent** (see below). The six governance modules entered the gate in study 30 (S30-37);
with the tests that existed when they did, and with the same allowlist rules, their scores were:

| module | mutants | before (killed) | after (killed) | survivors left, all equivalent |
|---|---:|---:|---:|---:|
| `governance/audit.py` | 415 | 72.0% | 96.1% | 16 |
| `governance/recipient.py` | 35 | 82.9% | 97.1% | 1 |
| `governance/ledger.py` | 777 | 68.7% | 95.5% | 35 |
| `governance/ledger_tool.py` | 525 | 69.7% | 94.1% | 31 |
| `governance/shared_approval.py` | 56 | 80.4% | 89.3% | 6 |
| `governance/policy.py` | 235 | 73.2% | 100% | 0 |

Two things the new tests found were not test gaps. `AuditLog.record`'s backoff between lock attempts could not
execute (every mutant of the sleep survived because the line was dead; fixed in its own commit). And four
`runs.py` entries had been allowlisted as "unkillable without locale hacking" — a single invalid byte written
into a receipt kills all four, so they were removed. A run takes ~35 minutes in WSL; under a machine busy with
other test suites, a handful of mutants time out instead of surviving — re-run those alone before trusting a
"stale entry" report.

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
default is to kill the mutant. The current 152 entries are all genuinely equivalent, and each was
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
- **Falsy for falsy** (`= False` → `= None`, a `getattr` default of `""` → `None` followed by `or ""`): every
  read is a truth test or the same normalisation.
- **A constant no input can reach**: an upper-case `XX` fallback compared against text that was lower-cased
  first; a dict used as an ordered set whose values are never read; the message of an `AssertionError`
  after a loop whose last attempt always returns.
- **Refusal prose**: the wording of the sentences a refused call returns. Their presence and the remedies
  they name are asserted; their capitalisation is not.
- **A branch that cannot be live today**, pinned by a test that fails the day it becomes live
  (`test_no_send_tool_is_also_one_the_sequence_check_assesses`), so the entry cannot outlive its reason.

If you're unsure whether a survivor is equivalent, it isn't — write the test.
