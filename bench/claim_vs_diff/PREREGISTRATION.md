# Does the evidence contradict the claim? — pre-registration

**Written 2026-09-13, before any arm was fitted.** Study 18 shortlist item #13. Cost **US$0**: same
Harness-Bench corpus as `bench/false_success` (#457), same scorer, deterministic arms only.

## 1. Why this question, now

`bench/false_success` measured that the agent's completion claim carries almost no information about
its own truth: **0.5996 within task** against 0.9342 on a random split, on 385 claimed successes of
which 139 are false. Its closing sentence was that the remedy is not a better reader of the claim but
**evidence that does not come from the agent**.

This is that. arXiv 2605.29442 (20,574 real sessions, inaccurate self-report 22.58% and growing)
proposes diffing the agent's written summary against the real diff and the test output. We have the
diff for every solve. We do not have test output — see §3.

**The comparison is paired against a number we already published**, on the same population, the same
10 tasks and the same 557 pairs. That is the point: `claim_tfidf` is re-run here and must reproduce
**0.5996**. If it does not, the apparatus changed and nothing else in this file may be read.

## 2. Instrument check, run before this document was finished

547 solves. Every one carries the claim, the real diff, and a full trace.

| | count |
|---|---:|
| claims naming at least one file-ish token | 491 / 547 (median 4 names) |
| claims naming ≥1 file the diff actually touched | 456 / 491 = 93% |
| claims naming a file the diff did NOT touch | 383 / 491 = 78% |
| diff touched a file the claim never named | 192 / 491 = 39% |
| traces present | **547 / 547** |

Pooled marginals among claimed successes — and §3 says why these do not decide anything:

| | true success (246) | false success (139) |
|---|---:|---:|
| names a file the diff did not touch | 0.71 | 0.64 |
| asserts a verification | 0.50 | **0.28** |
| of those, zero execution calls in the trace | 3 (2%) | 5 (13%) |
| median execution calls | 7 | 8 |
| median write calls | 2 | 3 |
| median tool calls | 28 | 29 |

Two things worth registering before the measurement:

- **The folk model is backwards here.** A claim that asserts "tests pass" is more common among
  **true** successes (50%) than false ones (28%). The intuition that a lying agent dresses its claim
  in verification language is not what this corpus shows.
- **The one clean contradiction is rare.** "Asserts a verification, executed nothing" fires on 8 of
  385 claimed successes, 5 false and 3 true. That is 5 of 139 false successes — a 3.6% catch rate at
  a 62.5% precision whose Wilson interval at n=8 is roughly [30%, 86%]. It is registered as a
  **rule**, not a classifier, and reported as counts rather than as an AUROC it has no power for.

## 3. Why the table above does not settle it

Those marginals are **pooled across tasks**, and `PROTOCOL.md` §7 — written by the bench this one
follows — says a figure computed across a corpus with repeated task structure measures the
structure. Task difficulty varies from 0.00 to 1.00 pass rate here, so a pooled difference mixes
"which tasks are hard" into every column. The flat and backwards columns above are therefore **not
evidence of a null**; they are evidence that the question has to be asked within task.

That is the whole reason this bench runs rather than stopping at §2.

## 4. What this corpus cannot show (§2q), registered in advance

- **No test output.** `verify_output` is empty on 547 of 547: the factorial ran with no verify
  command configured. The half of 2605.29442 that compares the summary against a test result is
  **not reproduced here** and no claim in RESULTS may be read as covering it.
- ~~**Traces record tool NAMES, not ARGUMENTS.** Checking "it says it edited `parser.py`; did it
  ever call `edit_file` on `parser.py`?" is impossible on this data.~~ **WRONG — see Amendment 1.**
  The arguments are recorded, as JSON strings; the instrument check tested for a dict and so saw
  nothing. Kept here rather than deleted because a retracted limitation is part of the record, and
  because the arm it wrongly ruled out turned out to be the *worse* one.
- One agent, one model (`deepseek-v3.2`), 23 tasks of one benchmark.

## 5. Arms — all deterministic, stdlib, US$0

| arm | reads | why |
|---|---|---|
| `claim_tfidf` | the claim text alone | the **published baseline**, re-run. Must reproduce 0.5996 or the run halts |
| `evidence` | trace and diff counts only — execution calls, write calls, tool calls, changed files, patch lines | the claim is never read. If this wins, the gate is a counter and no text is involved |
| `overlap` | claim ∩ diff: files named and not touched, files touched and not named, and the two rates | the actual claim-vs-diff comparison 2605.29442 proposes |
| `combined` | `evidence` + `overlap` | whether the two halves add |
| `contradiction` | the single rule: asserts a verification **and** executed nothing | reported as counts and precision, never as an AUROC |

Anything fitted is trained **leave-one-task-out**, on the same population it is scored on.

### Amendment 1 — 2026-09-13, written after the first run and before the new arm was fitted

**§4 said traces record tool names but not arguments. That was false, and it was false because I
checked the shape of the field instead of reading it.** The instrument check tested
`isinstance(arguments, dict)`; `chimera/core/steplog.py` writes `arguments=clip(args, 400)` — a
**JSON string**. So the check saw nothing and I wrote down that nothing was there.

The arguments are all there: **15,250 tool records across the corpus, every one a string**, and they
carry exactly what the sharp version of this question needs:

```
read_file  {"path": "in/buggy_code.py"}
edit_file  {"new": "def calculate(x, y):\n    if x > 0:  # FIX: ...", "old": "..."}
run_shell  {"command": "python .../workspace/in/buggy_code.py"}
```

This is the second time in two benches that assuming a field's shape produced a wrong sentence about
our own code — `delivered_matches_verified` in #457 was the first (Bee §2ad). That one was caught
after the sentence was written; this one before it was published. The rule that caught both is the
same: **print the value before believing the schema.**

**New arm, registered before fitting:**

| arm | reads | why |
|---|---|---|
| `trace_overlap` | claim ∩ **what the run actually touched**, from filenames in the tool arguments | strictly stronger than `overlap`: the diff shows only what survived, the trace shows everything read, written and executed |

Basenames only. A `run_shell` argument carries an absolute sandbox path whose directory names contain
the task id, and matching on full paths would hand the arm a task-identity leak — the very thing
`PROTOCOL.md` §7 exists to stop.

**P6 — `trace_overlap` beats `overlap`**, because a claim naming a file it only read is invisible to
a diff and visible to a trace. **P7 — its random-split gap stays under 0.05**, like `overlap`'s, since
neither reads anything that identifies the task.

## 6. Primary DV

Identical to #457 so the two are comparable without an adjustment: **within-task AUROC on claimed
successes, true vs false** — 10 tasks, 174 items, 557 discordant pairs — with a 95% CI by cluster
bootstrap over the 10 tasks. The naive random split is computed and reported **only as the size of
the leak** (`PROTOCOL.md` §7).

## 7. Registered predictions

- **P1 — `overlap` lands 0.50–0.60.** The pooled marginals are backwards and small, and nothing in
  them suggests the within-task view rescues a file-name comparison.
- **P2 — `evidence` beats `overlap`**, because counts survive within-task variation better than a
  name match does, but still lands **below 0.70**.
- **P3 — `combined` does not beat the better of its two halves by more than 0.03.**
- **P4 — `claim_tfidf` reproduces 0.5996 ± 0.001.** This is an apparatus check, not a finding.
- **P5 — `contradiction` catches ≤ 10 of the 139 false successes.** Registered so that "it caught
  five" is read against what was expected rather than against zero.

## 8. Decision rule

**ADOPT** — build a deterministic pre-answer check into `verify` — only if an arm that never reads
the claim (`evidence`, or `combined` with `evidence` carrying it) reaches **AUROC ≥ 0.70** with the
lower bound of the 95% CI ≥ **0.60**, and beats `claim_tfidf` by ≥ **0.10**.

**ADOPT THE RULE ALONE** if `contradiction` reaches **precision ≥ 0.80** on ≥ 15 fired cases — a
rule that rarely fires but is right when it does is shippable as a warning; one that is right 5 times
in 8 is not.

**Otherwise: publish the null, and record the observability gap** (§4) as what would have to change
before this question can be asked properly.

## 9. Controls

Same two as #457, and the run halts on either:

- **positive:** an arm fed the oracle score must read exactly 1.000;
- **negative:** labels shuffled within task must put every arm at ≈0.500.

Plus the paired one this bench gets for free: **`claim_tfidf` must reproduce the published 0.5996**
(§1). Three controls, and the third is the one that makes the comparison a comparison.
