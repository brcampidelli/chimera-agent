# Results — half of the generated spec tests pass on the buggy code they were written to catch

Run 2026-09-11 · 30 `fix_*` tasks of `bench/local_lift`, the production extractor and generator
on the default model (`openrouter/deepseek/deepseek-v4-flash-0731`, temperature 0) · US$ 0.25 ·
prereg `PREREGISTRATION.md` (committed before the first call) · raw `results/2026-09-11.jsonl`,
table `results/2026-09-11-report.md`.

## The numbers

- Instrument: the task's hidden test fails on the buggy base in **30/30** tasks.
- The generator produced a module on **20/30** tasks (see below for the other ten).
- **122 test functions; 58 pass on the buggy base — 48%** (Wilson 95% [0.39, 0.56]). A test that
  passes on the code the task exists to fix could not have detected the bug; it is vacuous with
  respect to that task, and under the shipped verifier its PASS counted as `evidence="verifier"`
  like any other.
- Tasks where **every** generated test passes on the buggy base: **0/20**. Each module had at
  least one function that fails on the base, so the module-level exit code the shipped verifier
  reads would have been red on every one of these workspaces had the worker changed nothing.

Per task, between 1 of 5 (`fix_average`, `fix_flatten`) and 4 of 5 (`fix_remove_suffix`) of the
functions pass on the base; `fix_index_of` 4/6, `fix_chunk_list` 5/8, `fix_parse_query` 4/7.

## The prediction, and which half held

Registered: *30–60% of generated functions pass on the buggy base* — **held at 48%**. Registered:
*at least 1 in 5 tasks has an all-passing module* — **did not hold: 0 of 20**. The generator
writes one test per requirement and the requirements come from the task prompt, which names the
bug; at least one requirement per task is the bug itself, and its test fails on the base. The
vacuity is at the function level: half of what the module asserts is true before and after the
fix, and a verdict that counts those as evidence is half air.

## What the gate does with this

`SpecTestVerifier` now takes the attempt's pre-change snapshot (`base_snapshot`, handed in by the
verify-or-revert loop) and classifies each test by its two outcomes: **discriminating** (fails
before, passes after — the evidence), **vacuous** (passes on both — excluded and counted),
**regression** (passes before, fails after — fails the attempt), **failing** (fails on both —
unchanged). If no test is discriminating the verifier **abstains**, as it already does when nothing
runnable was generated, so the caller falls back to its other gates instead of accepting a green
made of air. Without a snapshot the behaviour is byte-identical to before. On this corpus the
gate would have excluded 58 of 122 functions from every verdict and abstained on none of the 20
modules.

## Ten tasks with no module — recorded, not explained away

Two tasks (`fix_divisors`, `fix_validate_rows`) had **no requirements extracted**, so the
generator was never called. Eight had requirements and the generator returned **nothing** —
`generate` keeps a reply only if it contains `def test`, and these contained nothing. A re-probe
of `fix_percentile` after the run produced a nine-test module (881 characters, 4,064 completion
tokens, most of them reasoning). The empty reply is route- or run-dependent, not a property of the
task, and the shipped path treats it as a silent abstain. Named here as a follow-up: the
generator should retry once on an empty reply and record `finish_reason`, because the reasoning
model behind the default tier can spend its whole completion budget thinking
(`bench/blind_audit/corpus.py::WORKER_MAX_TOKENS` records the same trap on the delegation path).

## What this cannot show

Only tasks with a pre-existing workspace; the `create X` half of the corpus cannot be vacuous by
construction. One generator model, one temperature. The **pass-after** half of ExecCritic's rule
is not measured: the corpus has hidden tests, not reference solutions, so nothing here says how
many of the 64 base-failing tests are *wrong* rather than *discriminating* — a test that fails
before the fix and still fails after a correct fix is a bad test, and this bench cannot tell it
from a good one.
