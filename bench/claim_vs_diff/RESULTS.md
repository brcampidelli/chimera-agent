# The claim against its own diff: the only leak-free signal either bench has found — and still not enough

**2026-09-13.** Study 18 shortlist item #13, run against the pre-registration in this directory.
**US$0**: same Harness-Bench corpus as `bench/false_success` (#457), same scorer, deterministic
stdlib arms.

## The verdict in one line

Comparing what the agent **said** against what it **changed** beats reading the claim alone — 0.6643
against 0.5996 — and it is the only arm across both benches whose number **does not move between a
random split and leave-one-task-out**. It still misses the pre-registered bar: **DO NOT BUILD**.

## 1. Primary — claimed successes, true vs false, within task

10 tasks, 174 items, 557 discordant pairs. Identical population to #457, so these sit beside that
bench's numbers with no adjustment.

| arm | reads | AUROC | 95% CI | random split | **gap** |
|---|---|---:|---|---:|---:|
| `claim_tfidf` | the claim alone | 0.5996 | [0.478, 0.729] | 0.9342 | **+0.3345** |
| `evidence` | trace/diff counts, never the claim | 0.4022 | [0.227, 0.529] | 0.5339 | +0.1317 |
| **`overlap`** | **claim ∩ what CHANGED** | **0.6643** | **[0.532, 0.792]** | 0.6621 | **−0.0022** |
| `trace_overlap` | claim ∩ what was TOUCHED | 0.5189 | [0.385, 0.681] | 0.6141 | +0.0952 |
| `combined` | all of the above | 0.5512 | [0.446, 0.663] | 0.6895 | +0.1383 |

## 2. The finding is the gap column

`PROTOCOL.md` §7 was written from #457, where one TF-IDF scored 0.9342 on a random split and 0.5996
held-out — a +0.33 leak that was entirely task identity. Run the same split here and **`overlap` has
no gap: 0.6621 random, 0.6643 held-out, 0.002 apart.**

That is a different kind of number from everything else in these two benches. A feature that scores
the same whether or not the model has seen the task before is not recognising the task; it is reading
the individual solve. Everything else leaks: the claim text +0.33, the raw counts +0.13, the trace
comparison +0.10, all of them together +0.14.

So what survives contact with §7 is what #457 pointed at — evidence that does not come from the agent
alone — used not as a replacement for the claim but as **something to check the claim against**.

## 3. And it is still not enough to ship

0.6643 with a lower bound of 0.532 does not clear the registered bar (≥0.70, CI low ≥0.60), and it
beats the claim-text baseline by 0.065 rather than the required 0.10. At a 30% flag budget it finds
**48 of 139** false successes, where flagging at random finds about 42 and the claim-text arm 47.

**A deviation, stated with the fact that decides it.** §8 of the pre-registration wrote the ADOPT
rule for an arm that *never reads the claim* — `evidence` or `combined` — because P2 predicted the
counts would be the strong ones. `overlap` reads the claim, so by the letter of that rule it was
never eligible, and it is also the best arm in the table. The verdict is the same under either
reading: `overlap` fails the numeric bar on its own terms. Registering the rule against the wrong
arms is a miss in the pre-registration, not a result.

## 4. The sharper instrument was worse — and that is the interesting part

Amendment 1 added `trace_overlap` after the discovery in §7 below: match the claim's filenames not
against the diff but against **everything the run actually touched**, taken from the tool arguments.
Strictly more information. P6 predicted it would win.

It lost, by a lot: **0.5189 against `overlap`'s 0.6643**, and its leak went from −0.002 to +0.095.

The mechanism is legible once the number exists. `overlap` discriminates on *the claim naming a file
that never changed* — a sentence describing work that did not land. Widening "touched" to include
every file **read** destroys exactly that contrast: a claim's filenames are almost always somewhere
in a trace that read dozens of files, so the difference collapses toward zero for honest and
dishonest claims alike.

**Mentioning a file you only read is not the signal. Claiming a file that never changed is.** More
evidence made the comparison worse because it was the wrong evidence, and nothing but running it
would have said so.

`combined` fell too, 0.5691 → 0.5512, as more anti-predictive features were added to the pile. P3
registered that combining would add ≤0.03; across both runs it subtracts.

## 5. Two arms went the wrong way, and the same wrong way as #457

`evidence` — execution calls, write calls, tool calls, changed files, patch lines — reads **0.4022**,
below chance. Its fitted direction does not transfer between tasks: what "more effort" means on a
task the model has seen does not predict the same thing on one it has not. #457 measured its length
arm at **0.3977** doing the same thing. Two different "how much work was done" feature sets, two
benches, both landing at ~0.40 within task.

## 6. The rule, as counts

The one clean contradiction — *the claim asserts a check came back clean, and the trace shows the run
executed nothing* — over all 385 claimed successes:

| | |
|---|---:|
| fired | **8** |
| correct (the run really had failed) | 5 |
| false alarms | 3 |
| precision | 0.625 |
| recall of the 139 false successes | **0.036** |

Registered bar was precision ≥0.80 on ≥15 firings. **DO NOT SHIP.** A rule that is right five times
in eight, on eight cases out of 385, is a coin with a good story.

And the folk model behind it is backwards in this corpus: **a claim asserting "tests pass" is twice
as common among TRUE successes (50.4%) as among false ones (28.1%)**. The intuition that a wrong
agent dresses its claim in verification language is not what this data shows.

## 7. The field I got wrong, for the second time in two benches

The first run of this bench recorded in its pre-registration that *"traces record tool NAMES, not
ARGUMENTS"*, and listed the sharp version of this check as impossible. **That was false.**
`chimera/core/steplog.py` writes `arguments=clip(args, 400)` — the arguments are all there, **15,250
tool records across the corpus**, carrying exactly what was wanted:

```
read_file  {"path": "in/buggy_code.py"}
run_shell  {"command": "python .../workspace/in/buggy_code.py"}
```

The instrument check had tested `isinstance(arguments, dict)`. They are **JSON strings**. So the
check saw nothing and I wrote down that there was nothing.

This is the same error as `delivered_matches_verified` in #457 — assuming a field's shape instead of
reading its value (Bee §2ad) — twice in two benches on the same corpus. The difference is where it
was caught: that one after the sentence was written, this one before it was published. The habit that
caught both is the same and it costs seconds: **print the value before believing the schema.**

The correction is in Amendment 1, and §4 is what the corrected measurement says.

## 8. Controls — three, and the third is what makes this a comparison

| control | required | measured |
|---|---|---|
| positive: an arm fed the oracle | exactly 1.000 | **1.0000** (the run halts otherwise) |
| negative: labels shuffled within task | ≈0.500 | 0.4955 · 0.4614 · 0.5368 · 0.4165 · 0.4417 |
| **paired: the published baseline must come back** | 0.5996 ± 0.001 | **0.5996, \|delta\| 0.0000** |

The third is why the two benches read side by side. `claim_tfidf` is not a reimplementation — it is
`bench/false_success/detect.py` imported and run on the same rows by the same scorer, and the run
halts if it disagrees with the published figure. It did not disagree at four decimal places.

That reuse also surfaced a real defect in the shared scorer: `shuffled_within_task` rebuilt rows with
`Solve(**{**s.__dict__, ...})`, which names *that module's* class and so only ever worked on that
bench's corpus. Fixed to `dataclasses.replace`. A helper that is secretly local to one corpus is how
two benches' numbers become quietly incomparable while both look fine.

## 9. Registered predictions

| | prediction | outcome |
|---|---|---|
| P1 | `overlap` lands 0.50–0.60 | **refuted** — 0.6643, better than expected |
| P2 | `evidence` beats `overlap`, below 0.70 | **refuted** on the first half (0.4022 vs 0.6643); right on the second |
| P3 | `combined` adds ≤0.03 over the better half | **confirmed**, and it subtracts 0.11 |
| P4 | `claim_tfidf` reproduces 0.5996 ± 0.001 | **confirmed** — 0.0000 |
| P5 | the rule catches ≤10 of 139 | **confirmed** — 5 |
| P6 | `trace_overlap` beats `overlap` | **refuted** — 0.5189 against 0.6643 |
| P7 | `trace_overlap` leaks < 0.05 | **refuted** — +0.0952 |

Four refuted, three confirmed. Every refutation is about which evidence discriminates, and none of
them would have been visible from the pooled marginals the pre-registration deliberately refused to
read (§3).

## 10. What this corpus could not show (§2q)

- **No test output anywhere.** `verify_output` is empty on 547 of 547 — the factorial ran with no
  verify command configured. The half of arXiv 2605.29442 that diffs the summary against a *test
  result* is not reproduced here, and nothing above covers it.
- Traces are present on 546 of 547 solves.
- One agent, one model (`deepseek-v3.2`), 23 tasks of one benchmark; 13 contribute nothing because
  their label does not vary among claimed successes.
- Filenames are matched as **basenames**. A `run_shell` argument carries an absolute sandbox path
  whose directories are named after the task, and matching full paths would have handed these arms
  the very task-identity leak §7 of the protocol exists to stop — so two files with the same basename
  in different directories are one file here.

## 11. What it changes

**Nothing ships, and the thread closes.** Two benches, three mechanisms, one population: the claim
alone reaches 0.5996 and leaks 0.33; the claim against everything it touched reaches 0.5189 and leaks
0.10; the claim against what it actually changed reaches 0.6643 and leaks nothing. None clears a bar
worth putting a warning on, and the deterministic rule catches 5 of 139.

**What is worth carrying forward is a fact about shape, not a detector.** The one leak-free signal
either bench has produced is the narrow comparison — *does the claim name a file that never changed?*
— and it beat the version with strictly more information. When the next attempt at this is made, the
question to ask first is not "what else can we feed it" but "which contrast does the widening
destroy".
