# The claim text does not know it is lying — and the published number is a task-identity leak

**2026-09-13.** Study 18 shortlist item #1, run against the pre-registration in this directory.
Cost **US$0**: every arm is deterministic stdlib and reads the Harness-Bench factorial (#453) we had
already paid for. 547 usable solves, 23 tasks, `deepseek-v3.2`.

## The verdict in one line

A TF-IDF detector over the agent's own completion claim scores **0.9342 on a random split and
0.5996 within task**. The published 0.83–0.95 band reproduces exactly — in the regime where the
model can learn *which task it is*. **DO NOT BUILD**, by the pre-registered rule, on all three gates.

## 1. The phenomenon is real here, and worse than the paper reports

| | count |
|---|---:|
| claimed success, oracle agrees | 246 |
| **claimed success, oracle says fail — false success** | **139** |
| claimed failure, oracle says pass | 77 |
| claimed failure, oracle agrees | 85 |

**36.1% of claimed successes are false. 62.1% of all oracle-failures were reported as successes**
(the paper reports 45–48%). Nothing about this null is "the effect was absent from our data".

## 2. Primary — claimed successes, true vs false, within task

10 tasks, 174 items, 557 discordant pairs. 95% CI by cluster bootstrap over tasks, because the
effective n is tasks (10), not pairs.

| arm | AUROC | 95% CI | note |
|---|---:|---|---|
| `self_report` — the gate we ship | 0.5000 | [0.500, 0.500] | **degenerate**: it calls all 385 a success, so it cannot rank one above another |
| `length` — characters, direction learned from other tasks | 0.3977 | [0.258, 0.513] | below chance — the direction does not transfer between tasks |
| **`tfidf` — the paper's arm** | **0.5996** | **[0.479, 0.736]** | |
| `harness` — five receipt signals, no text | 0.5000 | [0.500, 0.500] | **degenerate**, see §5 |
| `length_ceiling` — characters, direction handed to it | 0.6023 | [0.488, 0.750] | ceiling, not a deployable number |

**Decision rule (§7): all three gates fail.** 0.5996 < 0.75; CI low 0.479 < 0.65; the margin over
`self_report` is 0.0996, under the required 0.10. Not close on any of them.

Secondary (all 547 solves, pass vs fail within task, 12 tasks, 1,106 pairs): `tfidf` 0.6410
[0.548, 0.758], `self_report` 0.5511, `length_ceiling` 0.5945.

## 3. The finding: the published band is the task-identity shortcut

Same arm, same hyper-parameters, same corpus. Only the split changes.

| population | random split | within task | gap |
|---|---:|---:|---:|
| claimed successes | **0.9342** | 0.5996 | **+0.3345** |
| all solves | **0.9470** | 0.6410 | **+0.3059** |

0.934–0.947 sits inside the paper's reported 0.83–0.95. **Eleven of our 23 tasks have a constant
label** — six never fail, five never pass — so a classifier on a random split scores brilliantly by
recognising the task's vocabulary, which says nothing about whether a given claim is honest. The
distance between the two rulers *is* the shortcut (Bee §2u).

This also rules out the obvious objection. The same fitter, the same features and the same
hyper-parameters reach 0.934 when the task leaks, so **0.5996 is not an underpowered model** — the
machinery demonstrably learns this text when there is something in it to learn. The null is about
the signal, not about the optimiser.

## 4. What little signal exists is verbosity, not vocabulary

`length_ceiling` (0.6023) matches `tfidf` (0.5996) to within 0.003. A count of characters, given
its direction, does everything the 4,000-term model does — and the direction is *longer claims are
more often true*. Two consequences, both of which argue against building:

- calling it a **lexical** detector names it wrong: there is no incriminating vocabulary, there is
  a weak length correlate;
- the direction **does not transfer across tasks**. The honest, deployable version of that arm has
  to learn its sign from other tasks, and doing so lands it at 0.3977 — worse than a coin.

The instrument check showed why, before any arm was fitted. Same task, one passed and one failed:

> *false success (oracle 0.58):* "Perfect! The implementation is complete and all tests pass."
> *true success (oracle 0.99):* "Excellent! The implementation is complete and all tests pass."

## 5. The dead arm, with its cause

`harness` is not a losing arm; it is an arm with nothing to read, and the two print the same 0.5
(Bee §2f). Among the claimed successes of the 10 primary tasks, four of its five features take
**exactly one value** (`attempts`=1, `reverted`=0, `stagnant`=0, `delivered_matches_verified`=1),
and the fifth, `rounds`, varies only in task 011, which is not in the primary set.

On the secondary DV the arm scores 0.5511 — the same figure as `self_report` to four decimals —
because with `attempts` pinned at 1, `reverted` is the exact complement of the single attempt's
success, which *is* the self-report. That much is bookkeeping of one event, not a second opinion.

### The finding this bench nearly published, and the field that corrected it

The first pass of this arm read `bool(receipt["delivered_matches_verified"])` and found it equal to
`success` in **547 of 547** solves. I was one edit away from writing that the field is the
self-report under another name, and that anyone building a gate on it would believe they had a
second opinion. **That sentence was wrong, and it was wrong because I assumed the semantics of the
field instead of reading it** (Bee §2ad).

`chimera/core/autonomous.py:_delivered_matches_verified` compares the delivered tree's digest to the
winning attempt's `verified_fingerprint`, and it is deliberately **tri-state**: `True` byte-identical,
`False` the verdict describes a tree that no longer exists, `None` **no winning attempt to compare
against**. `bool(None)` is `False`, so the coercion silently rewrote "we could not look" as "we
looked and it did not match" — and manufactured the perfect agreement.

Measured properly, the distribution is worth more than the retracted claim:

| `delivered_matches_verified` | count |
|---|---:|
| `True` — delivered tree is the verified tree | 385 |
| `False` — they came apart | **0** |
| `None` — not checkable (no winning attempt) | 162 |

**The check never fired once in 547 runs.** It is `None` on exactly the runs whose self-report is
failure, which is why it carries no information about *false* success — among claimed successes it
is `True` 385 out of 385. The guard is alive and computing; the failure mode it exists to catch was
simply absent from this factorial.

The arm now encodes the three levels honestly. Every number in this file is **byte-identical before
and after** that fix, because on this corpus the tri-state and the boolean are perfectly correlated —
which is precisely why the wrong sentence would have survived a re-run and gone out reading fine.

## 6. Catch at equal flag rate

The practical form, and the one the paper sells (4–8× more catches at equal flag rate):

| flag budget | `self_report` | `tfidf` | `length_ceiling` |
|---|---|---|---|
| 10% | 11/139 | 14/139 | 15/139 |
| 20% | 30/139 | 33/139 | 33/139 |
| 30% | 44/139 | 47/139 | 47/139 |

`self_report` ties every claimed success, so its column *is* the random baseline. At a 30% budget
the detector finds **three more false successes out of 139 than flagging at random** — about 1.07×,
not 4–8×. And these are upper bounds: the ranking is done **within task**, which hands the detector
a per-task calibration production would not have (Bee §2r — a measurement taken where the
intervention cannot misfire estimates the gain, never the damage).

## 7. Controls — both directions

| control | required | measured |
|---|---|---|
| positive: an arm fed the oracle score | exactly 1.000 | **1.0000** (the run halts otherwise) |
| negative: labels shuffled within task | ≈0.500 | `tfidf` 0.4955, `self_report` 0.5000, `harness` 0.5000 |

`length` reads 0.4318 and `length_ceiling` 0.5682 under shuffling — mirror images summing to exactly
1.0000, as one feature seen from both signs must, and both inside their own bootstrap width (±0.13).

## 8. Registered predictions

| | prediction | outcome |
|---|---|---|
| P1 | `tfidf` within-task lands 0.50–0.65, not 0.83–0.95 | **confirmed** — 0.5996 |
| P2 | naive random split ≥ 0.80 | **confirmed** — 0.9342 / 0.9470 |
| P3 | `length` within 0.05 of `tfidf` | **confirmed** — 0.003 apart at the ceiling |
| P4 | `harness` beats `tfidf` by ≥ 0.05 | **refuted** — degenerate, it had no variance to use |
| P5 | `self_report` within-task 0.55–0.65 | **confirmed** — 0.5511 on the secondary |

## 9. What this does not show (§2q)

- One agent, one model (`deepseek-v3.2`), 23 tasks of one benchmark. An agent whose false successes
  are *worded* differently is untouched by this.
- **The paper's judge arm was not reproduced.** We did not run an LLM judge over these 547 claims;
  the comparison here is against the gate we ship and against a length control. Our result is
  consistent with the paper's judge finding (≤0.65) but is not a test of it.
- Claims are truncated at 2,000 characters by the receipt writer (157 of 547 hit the cap). A signal
  living past the cut is invisible to every arm here, `length` included.
- 13 of 23 tasks are outside the primary because their label does not vary among claimed successes.
  There the question is not unanswered, it is **unaskable**.
- Our within-task design is stricter than a random split and stricter than what the paper appears to
  have done. It is not stricter than deployment, which is harder still: production has no held-out
  replicas of the same task to calibrate against.

## 10. What it changes for Chimera

**The argument against a self-report-fed gate stands, and gets stronger — but the remedy is not a
cheaper reader of the claim.** The measurement says the claim text carries almost no information
about its own truth, so any gate that reads what the agent *wrote* inherits that ceiling. Note that
`self_report` is degenerate by construction on this population: asked to rank 385 claimed successes,
the gate we ship today cannot separate the 139 false ones at all.

What did separate them, in the run that produced this corpus, is the thing that never reads the
claim: the oracle. Chimera's equivalent already exists and already ships — the diff gate and
`verify`. **The conclusion is to keep spending on evidence that does not come from the agent, and to
stop looking for a way to grade the agent's own sentence.**

The desktop items from the same study (gate the plan; recommend-and-wait) are untouched by this
result — neither one asks the agent whether it succeeded.

## 11. Reproducing

The Harness-Bench task material has no licence and is not redistributable, so the claim texts stay
out of the repo (same handling as #453). With the factorial's `~/hb-homes` and `~/harness-bench` in
place:

```bash
cd bench/false_success && python3 corpus.py   # the instrument check of §1
cd bench/false_success && python3 run.py      # every table above, ~4 min, stdlib only
```

`corpus_stats.json` carries the counts (no text), so §1 and §3 can be audited without it.
