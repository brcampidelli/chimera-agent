# Results — the generated test detects half the faults it reaches, false-alarms on 1% of correct patches, and the diff rule flags 4% of accepted work

**2026-09-15.** Study 19, items B3 + B4 + S2, against [`PREREGISTRATION.md`](PREREGISTRATION.md).
Model `deepseek-v4-flash-0731` for the patches and the generated tests; everything else
deterministic. Spend **US$ 0.17** (budget US$ 2). Raw: `results/patches.jsonl` and
`results/patches-mistral.jsonl` (the two arms of the labelled history, post-patch file contents
included; `patches-all.jsonl` is their concatenation and what the readers take), `results/generated.jsonl`
(the test modules), `results/readings-raw.json` (the modules as generated) and `results/readings.json`
(through the shipped stripper), `results/s2.json`. Reproduce: `run_patches.py` per arm, then
`run_gate.py --generate`, `--read [--raw-modules]`, `--s2`.

## The labelled history (step 1, with Amendments 1 and 2)

| source | patches | correct | incorrect | unpatched |
|---|---:|---:|---:|---:|
| `deepseek-v4-flash-0731`, no gate, no hidden test in the workspace | 38 (stopped at 38/60, see Amendment 2) | **38** | 0 | 0 |
| `mistral-small-3.2-24b-instruct`, same (Amendment 1) | 60 | 40 | **18** | 2 |
| **history** | 98 | 78 | 18 | 2 |

The flash arm never produced a wrong patch on these tasks — 38/38 — which is why the weak-model arm
exists: a history with one label measures nothing about TNR. Every patch stores the post-patch
files, so the readings below re-run from the artefact.

## B4 — the generated test, on both sides (step 2 + 3)

One module per task, generated once from the prompt on the buggy base's digest (24 of 30 tasks
returned one; the six that did not are listed in `readings.json`), **154 test functions** in all,
read on 78 patches whose task has a module (64 correct, 14 incorrect; the two arms contribute 32
correct patches each).

| metric | value | Wilson 95% |
|---|---:|---|
| **FDR** — fails on the buggy base (could have detected the bug) | **81/154 = 0.526** | [0.45, 0.60] |
| **FTR** — reaches the fault region on the buggy base (coverage) | **123/147 = 0.837** | [0.77, 0.89] |
| reaches the fault and does **not** fail | 46/147 = 0.313 | — |
| **false alarm** — fails a *correct* patch, per (test, correct patch) pair | **4/402 = 0.010** | [0.00, 0.03] |
| tests that false-alarm on ≥ 1 correct patch | 2/147 | — |

The fault region is the union of base lines any correct patch removed, changed or inserted around
(the insert case was added after the first read left three tasks with an empty region — a missing
guard changes no base line, and its fault is where the guard went). FTR is over the 147 tests on
tasks with at least one correct patch.

**P1 held twice.** FDR replicates `bench/spec_test_vacuity`'s 52% on a fresh generation (52.6%), and
the fault-trigger side sits 31 pp above it: a third of the tests that execute the buggy line assert
something the bug does not break. That is the paper's FTR ≫ FDR shape, at our scale.

**P2 did not hold, and it is the better outcome.** False alarms were predicted at ≥ 10% of (test,
correct patch) pairs and measured at **1.0%** — two tests in 147, each failing both correct patches
of its task (`fix_parse_amount`: the test demands a `ValueError` on a non-numeric string the prompt
never specified; `fix_group_by`: the test wants a `dict` where the prompt allows any mapping). The
generator over-specifies rarely, and when it does the two correct patches disagree with it the
same way. Registered decision (B4): **false alarm < 10% → the gate stands as shipped.**

## B3 — the gate as a gate, over the labelled patches

| verdict | TPR P(PASS \| correct) | TNR P(FAIL \| incorrect) | correct patches reverted |
|---|---:|---:|---:|
| bilateral (shipped, with the pre-change classification) — **as generated** | 57/64 = 0.891 | **14/14 = 1.000** | **7/64 = 10.9%** |
| module exit code (what shipped before the vacuity gate) — as generated | 57/64 = 0.891 | 14/14 = 1.000 | 7/64 = 10.9% |
| bilateral — **after the fence fix below** | **60/64 = 0.938** | 14/14 = 1.000 | **4/64 = 6.2%** |

The gate failed every wrong patch (**TNR 1.0**, all 14 from the weak model) and passed 57 of 64
correct ones. The seven correct patches it would have **reverted** split in two: four are the two
false-alarming tests above (two patches each), and **three are one module that could not run** —
`fix_totals_match`'s generated file came back as bare Python ending in a ``` line, no opener to
match, so `_strip_fence` left it, the file failed to collect on every tree, and a syntax error in the
gate's own test reverted three correct patches. Fixed in `spec_test.py` (a closer with no opener at
the very end of a module is a boundary, never Python) and tested; the stored module is re-read
through the shipped stripper, which is the third row.

**P3 held as generated and half-held after the fix** (TPR 0.938 > the registered 0.9 ceiling), and
the difference the registration expected between the two verdicts **did not appear**: no module was
all-vacuous on any patch and nothing abstained, so the bilateral classification changed no verdict
on this corpus. What it would change — a wrong patch passing an all-vacuous module — did not occur
in 78 patches.

**Registered decision (B3, round-0):** as generated the gate reverts **10.9%** of correct patches
(> 10%), so `--gen-tests` stays opt-in and its help text now carries the number; after the fence fix
it is 6.2%, and the help text says so in the same sentence. Per model: the flash patches and the
weak model's correct patches were passed at the same rate (30/32 each) — the false alarms are about
the *test*, not the patch's author.

**What this cannot show** (as registered): one small-Python venue, k ≤ 2, 14 wrong patches from one
weak model; FTR's region is drawn from our own correct patches; nothing about repository-scale work.


## S2 — the diff rule, measured on its false alarms before it reviews anything

`chimera/governance/diff_rules.py`: a **new sink called with a non-literal argument**, found by `ast`
on the post-patch file and restricted to the lines the patch added or changed. No model. It fired
**10/10** on the sabotage set (one sink family each, variable argument) and **0/10** on the same ten
with a literal argument — the tests in `tests/test_a_new_sink_with_a_variable_argument_is_a_review.py`,
which fail when the rule is made inert.

**The accepted-patch history.** The factorial kept every post-edit workspace; 557 were read and diffed
against their task fixtures (552 factorial solves plus the five pilot rows in the same tree):

| history | patches | flagged | FPR | Wilson 95% |
|---|---:|---:|---:|---|
| factorial, **accepted** (oracle ≥ 0.8) | 325 | **13** | **4.0%** | [0.02, 0.07] |
| factorial, not accepted (< 0.8) | 232 | 56 | 24.1% | [0.19, 0.30] |
| local_lift, correct patches (step 1) | 78 | 0 | 0.0% | [0.00, 0.05] |
| local_lift, incorrect patches | 18 | 0 | 0.0% | [0.00, 0.18] |

**Registered decision (S2 wiring): FPR ≤ 10% on the accepted history → the rule ships as a pause for
sign-off that a surface opts into (`AutonomousAgent(pause_on_diff_flags=True)`), with the flags on
every attempt receipt regardless (`Attempt.diff_flags`, `AttemptReceipt.diff_flags`).** P4 held.

What the 4% is. Every accepted flag sits on one of five tasks whose prompt asks for exactly the
thing flagged: `051-sql-query-report` and `080-schema-roundtrip-conversion` write their reports to
a path the task names (`open(os.path.join(out_dir, "analysis.md"), "w")`), `022-local-rest-api-summary`
fetches the local API it is told to summarise (`requests.get(url)`), `087-cli-parser-bug-tests`
writes a regression test that runs the CLI under test (`subprocess.run([sys.executable, "-m",
"csvtool.cli", *args])`), `085-flaky-test-root-cause` runs pytest from a helper. Each is a line a
person should see once and approve in a second — which is what a REVIEW is for, and why this is a
pause and not a block. Six of the thirteen are `open`-only; without that sink the rate is 7/325 =
2.2%, reported as a description, not as the rule (the registration named `open` in write mode and
the number stands with it).

The **six-fold asymmetry** — 24% of the *not*-accepted patches flagged against 4% of the accepted —
was not predicted and is worth one sentence: the low-scoring patches on `042` and `087` wrote extra
output files and extra test harnesses the oracle did not want, so on this corpus the rule's flags
correlate with *doing more than asked*, which is the SWEADV shape seen from the benign side. It is a
correlation on 557 patches from one model on 23 tasks, and nothing here says it transfers.

**What this cannot show** (as registered): the rule's true-positive rate on real adversarial
patches — no such corpus here; the sabotage set proves the guard fires, not what it would catch.

## What ships

- `chimera/governance/diff_rules.py` — the rule; `flag_change` / `flag_snapshots` read full texts,
  never the receipt's clipped patch.
- `AutonomousAgent`: `diff_flags` computed beside the per-attempt diffs and recorded on the
  attempt; `pause_on_diff_flags` (default off) holds a flagged success for sign-off through the
  same checkpoint `pause_on_taint` uses, with the flags in the reason. A person approves or
  denies; nothing is blocked.
- `AttemptReceipt.diff_flags` — the record-only surface the kernel docstring said a judge never
  had: the flag is on the receipt of every run whether or not anything paused.
- Twelve tests (the sabotage set, the diff-line parser, the loop's three behaviours).
- `chimera/core/spec_test.py` — `_strip_fence` drops a closing fence with no opener at the end of a
  module (the collection failure that reverted three correct patches); test in
  `tests/test_a_fence_inside_the_payload_is_content.py`.
- `chimera solve --gen-tests` — the help text carries the measured numbers.
- `bench/test_gate_two_sided/` — registration with two amendments, `run_patches.py` (append-only,
  resumable), `run_gate.py` (`--generate`, `--read [--raw-modules]`, `--s2`), the four result
  files, this file.

