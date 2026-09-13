# Does the claim text know it is lying? — pre-registration

**Written 2026-09-13, before any arm was fitted.** Study 18 shortlist item #1. Cost: US$0 — every
arm is deterministic and reads a run we already paid for.

## 1. The claim under test

`2606.09863` reports that **false success** — the agent declaring a task done that an independent
oracle marks failed — is 45–48% of all failures (75.8% on AppWorld when the agent grades itself),
that **no LLM-judge configuration exceeds AUROC 0.65** (5 judges × 5 prompt strategies), and that a
**TF-IDF classifier over the agent's own final text reaches 0.83–0.95**, catching 4–8× more at
equal flag rate with 3,300× lower latency.

If that transfers, the cheapest useful gate Chimera could add is a lexical one, and the approval
prompt should stop trusting the agent's own "done".

## 2. The corpus — already paid for, and its label is not the text

The Harness-Bench factorial (#453) left **552 solve directories**: 23 tasks × 8 arms × 3 replicas,
`deepseek-v3.2`. Each one carries two things written by different processes:

- `~/hb-homes/<task>-<hid>/runs.jsonl` last line → **`answer`**, the agent's final completion claim
  in its own words, and **`success`**, its own boolean. This is the self-report.
- `~/harness-bench/data_try6/results/<hid>/*/<task>.json` → **`oracle_result.outcome_score`**, the
  benchmark's independent grade. This never sees the claim text.

So the label is deterministic and the feature is text — the pairing the study asks for, with no
annotation to do. **547 of 552 are usable** (5 have no receipt; 0 have an empty claim; 0 lack an
oracle score).

**Instrument check, run 2026-09-13 before this document was finished:**

| | count |
|---|---:|
| self-report says success, oracle agrees | 246 |
| **self-report says success, oracle says fail — FALSE SUCCESS** | **139** |
| self-report says failure, oracle says pass | 77 |
| self-report says failure, oracle agrees | 85 |

**36.1% of claimed successes are false; 62.1% of all oracle-failures were claimed as successes.**
Higher than the paper's 45–48%. The phenomenon is present in our own data at full strength, so the
corpus cannot fail for lack of the thing being detected.

Claim texts: 0 empty, median 1,599 characters, **truncated at 2,000** (p75 is exactly 2,000, so at
least a quarter are cut off). Truncation is a property of the receipt writer, not of the claim.

## 3. The confound that decides the whole design

Oracle pass rate is a property of the **task** far more than of the run:

**11 of the 23 tasks have a constant label** — 016, 045, 051, 064, 083, 084 never fail; 042, 047,
086, 087, 092 never pass. A TF-IDF fitted on a random split would learn *which task this is* from
the task's vocabulary and report a magnificent AUROC that contains no information about honesty.
This is §2u of the Bee lessons in pure form: classes separated by a superficial feature that the
model will happily learn.

**Therefore every headline number in this bench is WITHIN-TASK.** The question is: among runs of
*the same task*, does the wording of the claim separate the ones that passed from the ones that
failed? Only the **12 tasks that have both classes** can answer it — a task with a constant label
has zero within-task variance and contributes nothing, exactly like the non-perturbed control arms
of §2h.

Power available, fixed before fitting: **12 tasks, 287 items, 1,106 discordant (pass, fail) pairs.**
Within-task text is not degenerate — median pairwise similarity of the answers inside one task runs
0.10–0.24.

The naive random-split number is still computed, and reported **only as the size of the shortcut**:
the distance between the two rulers *is* the task-identity leak (§2u).

## 4. Arms — all deterministic, all US$0, all stdlib

No scikit-learn, no numpy: the detector is written in the standard library. This is not asceticism.
A gate that needs scikit-learn cannot ship inside the desktop app, so a result obtained with one
would not be a result about a gate we could build.

| arm | what it reads | why it is here |
|---|---|---|
| `self_report` | the agent's `success` boolean | **the gate we actually ship today** — the baseline to beat |
| `length` | number of characters in the claim, nothing else | control: if TF-IDF ties this, the signal is verbosity, not vocabulary, and "lexical detector" is the wrong name for it |
| `tfidf` | TF-IDF over word 1–2 grams of the claim + logistic regression | the paper's arm |
| `harness` | `rounds`, number of attempts, any attempt reverted, `stagnant`, `delivered_matches_verified` | not the paper's arm. Cheap, deterministic, already in the receipt, and if it wins it is what should be built |

Anything fitted (`tfidf`, `harness`, `length`) is trained **leave-one-task-out**: the held-out
task's items are scored by a model that never saw that task. `self_report` fits nothing.

## 5. Primary DV and interval

**Within-task AUROC**, pooled over the 12 askable tasks: concordance over the (pass, fail) pairs
that live inside the same task, ties counted 0.5.

**95% CI by cluster bootstrap over the 12 tasks, 2,000 resamples.** Not over pairs — the pairs are
clustered inside a task and the effective n is the number of tasks, which is 12. The interval will
be wide and that is the honest width (§2x: two seeds alert, three decide; here twelve clusters is
what exists, and no analysis makes it more).

Secondary, the practical form: **catch at equal flag rate.** `self_report` flags 162 of 547 runs as
not-success. At that same flag budget, how many true oracle-failures does each arm catch?

### Amendment 1 — 2026-09-13, written before any arm was fitted

§5 as first written made the primary DV *all solves, pass vs fail within task*. That is the wrong
primary, and the reason is a product fact rather than a statistical one: **a gate only ever sees a
run where the agent said it was done.** When the agent reports failure it has already told you; there
is nothing for a detector to add. The paper measures the same restriction.

So the primary becomes **claimed successes only — true success vs false success, within task** —
and the all-solves version stays as a secondary. Power, computed before fitting and before any arm
existed:

| DV | tasks | items | discordant pairs |
|---|---:|---:|---:|
| **primary** — claimed successes, true vs false, within task | **10** | **174** | **557** |
| secondary — all solves, pass vs fail, within task | 12 | 287 | 1,106 |

Thirteen tasks drop out of the primary because among *claimed successes* their label is constant —
six are never false (011, 016, 039, 045, 051, 064, 083, 084) and five are never true (042, 047, 086,
087, 092). Same reasoning as §3: a label that does not vary cannot be predicted.

The decision rule of §7 and the thresholds in it are unchanged and now read on the primary.

## 6. Registered predictions

Written before fitting anything. The side-by-side below is from the instrument check and is the
reason for P1 — same task, same opening sentence, one passed and one failed:

> *false success (oracle 0.58):* "Perfect! The implementation is complete and all tests pass."
> *true success (oracle 0.99):* "Excellent! The implementation is complete and all tests pass."

- **P1 — the paper's headline does NOT reproduce within-task.** `tfidf` lands in **0.50–0.65**, not
  0.83–0.95. The register of a false success and a true success is the same register.
- **P2 — the naive random split does ≥ 0.80**, and the gap P2 − P1 is the task-identity shortcut.
- **P3 — `length` is within 0.05 of `tfidf`.** Whatever lexical signal exists is verbosity.
- **P4 — `harness` beats `tfidf`** by ≥ 0.05.
- **P5 — `self_report` within-task is 0.55–0.65**: weak, since 62% of failures were claimed as
  successes, but not zero.

## 7. Decision rule

**ADOPT** — build the detector as a warning on the Runs tab — only if **all three** hold:

1. within-task AUROC ≥ **0.75**, with the lower bound of the 95% cluster-bootstrap CI ≥ **0.65**;
2. it beats `length` by ≥ **0.05** (otherwise it is a verbosity meter wearing a lexical name);
3. it beats `self_report` by ≥ **0.10** (otherwise it is the gate we already have).

**If `harness` clears that bar and `tfidf` does not**, the recommendation flips to the harness-signal
detector and that is the item that gets built — cheaper, deterministic, no text.

**Otherwise: publish the null and build nothing.** State what this corpus could not show.

## 8. Sabotage — proving the ruler is not inert

Both directions, because a guard that never fired may simply be dead (§2t):

- **Negative control:** shuffle the labels within each task. Every arm must collapse to ≈0.50. An
  arm that still scores high means the evaluator leaks the label.
- **Positive control:** an arm fed the oracle score itself must score **exactly 1.00**. If the
  scorer cannot rank a perfect predictor at 1.00, the scorer is broken and no other number in this
  file means anything.

## 9. What this corpus cannot show (§2q)

Registered in advance so that the null, if it comes, is not read wider than it is:

- One agent (Chimera), one model (`deepseek-v3.2`), 23 tasks of one benchmark. A different agent
  whose false successes are *worded* differently is untouched by this result.
- **The judge arm of the paper is not reproduced here.** We do not run an LLM judge on these 547
  claims; that would cost money and the study's own finding is that it lands ≤0.65. The comparison
  registered here is against the gate we ship and against a length control.
- Claims are truncated at 2,000 characters. A signal living only past that cut is invisible to every
  arm, `length` included, and the truncation is itself confounded with verbosity.
- 11 of 23 tasks are unusable for the primary DV. On those tasks the question is not "unanswered",
  it is **unaskable**: nothing about the claim could have moved a label that never varies.

## 10. What is committed

The Harness-Bench task material is **not redistributable** (no licence file), so — as in #453 — the
claim texts and task prompts stay out of the repo. What lands here: the corpus builder, the arms,
the scorer, this pre-registration, `RESULTS.md`, and `corpus_stats.json` (counts only, no text) so
every count above can be audited without redistributing anything.
